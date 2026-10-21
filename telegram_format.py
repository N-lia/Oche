"""Convert agent Markdown output into Telegram HTML.

Telegram Bot API exposes text formatting through ``parse_mode`` on
``sendMessage`` / ``editMessageText`` (and the caption fields of media
methods), or through an explicit ``entities`` array. We use ``parse_mode="HTML"``
instead of ``MarkdownV2`` because only ``&``, ``<`` and ``>`` need escaping and
a formatting failure can safely fall back to plain text.

Guarantees provided to callers:
- Output is always valid Telegram HTML: escaping happens before tags are
  inserted, code fragments are stashed, and raw model markup can never be
  mistaken for tags.
- :func:`truncate_telegram_html` cuts safely: it never leaves an unclosed tag
  or a dangling ``<``, so streaming previews always parse.
- :func:`split_telegram_text` yields chunks each <= 4096 chars, splitting the
  *source* at line boundaries so tags are rebuilt per chunk instead of torn
  apart.
"""

from __future__ import annotations

import html
import re

TELEGRAM_TEXT_LIMIT = 4096

# ---------------------------------------------------------------------------
# Source-level regexes (run on raw model markdown, before escaping).
# ---------------------------------------------------------------------------

_FENCED_CODE_RE = re.compile(r"^```([A-Za-z0-9_+\-]*)[ \t]*\n(.*?)(^```|\Z)", re.S | re.M)
_INLINE_CODE_RE = re.compile(r"(`{1,3})([^`\n]+?)\1")
_HEADING_TRAILING_MARKS_RE = re.compile(r"^[ \t]{0,3}(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$", re.M)
_HORIZONTAL_RULE_RE = re.compile(r"^[ \t]*(?:[-*_][ \t]*){3,}$", re.M)
_TABLE_SEPARATOR_RE = re.compile(r"^[ \t]*\|?(?:[ \t]*:?-{2,}:?[ \t]*\|)+[ \t]*:?-{2,}:?[ \t]*\|?[ \t]*$", re.M)
_BULLET_RE = re.compile(r"^([ \t]*)[-*+][ \t]+", re.M)
_EXCESS_BLANK_RE = re.compile(r"\n{4,}")

# ---------------------------------------------------------------------------
# Inline regexes (run on ESCAPED text, where & < > are already entities).
# ---------------------------------------------------------------------------

_LINK_RE = re.compile(r"\[([^\]\n]+)\]\(([^)\n]+)\)")
_SAFE_URL_SCHEMES_RE = re.compile(r"^(https?:|tg:|mailto:|ftp:)", re.I)

_STRIKE_RE = re.compile(r"~~(?=\S)([^~\n]+?)(?<=\S)~~")
_BOLD_RE = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", re.S)
_ITALIC_ASTERISK_RE = re.compile(r"(?<!\*)\*(?!\s)([^*\n]+?)(?<!\s)\*(?!\*)")
_ITALIC_UNDERSCORE_RE = re.compile(r"(?<!\w)_(?!\s)([^_\n]+?)(?<!\s)_(?!\w)")
_SPOILER_RE = re.compile(r"\|\|(?=\S)(.+?)(?<=\S)\|\|")

# Applies to escaped text: "> quote" now looks like "> quote".
_BLOCKQUOTE_LINE_RE = re.compile('^[ \\t]*(?:&gt;|>)[ \\t]?(.*)$')
_HEADING_LINE_RE = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+(.+?)[ \t]*$")

_PLACEHOLDER_RE = re.compile("\x00([0-9]+)\x00")
_TAG_RE = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9\-]*)([^<>]*)>")

# Tags Telegram accepts inside parse_mode=HTML, in their closing form.
_PAIR_TAGS = frozenset({
    "b", "strong", "i", "em", "u", "ins", "s", "strike", "del",
    "code", "pre", "a", "blockquote", "tg-spoiler",
})
# Telegram only recognizes these in addition: <span class="tg-spoiler">.


def _language_class(lang: str) -> str:
    lang = (lang or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9_+\-]*", lang or ""):
        return ""
    return f' class="language-{lang}"' if lang else ""


def markdown_to_telegram_html(text: str) -> str:
    """Convert model Markdown into Telegram-parseable HTML."""
    text = (text or "").strip()
    if not text:
        return ""

    placeholders: list[str] = []

    def _stash(fragment: str) -> str:
        token = f"\x00{len(placeholders)}\x00"
        placeholders.append(fragment)
        return token

    # 1. Fenced code blocks -> <pre><code>, content fully escaped now so later
    #    escaping/parsing can never touch it.
    def _fence(match: re.Match) -> str:
        lang, code = match.group(1), match.group(2).rstrip("\n")
        return _stash(f"<pre><code{_language_class(lang)}>{html.escape(code)}</code></pre>")

    text = _FENCED_CODE_RE.sub(_fence, text)

    # 2. Inline code -> <code>.
    text = _INLINE_CODE_RE.sub(lambda match: _stash(f"<code>{html.escape(match.group(2))}</code>"), text)

    # 3. Block-level cleanup that does not insert tags (safe pre-escape).
    text = _HEADING_TRAILING_MARKS_RE.sub(r"\1 \2", text)
    text = _HORIZONTAL_RULE_RE.sub("", text)
    text = _TABLE_SEPARATOR_RE.sub("", text)
    text = _BULLET_RE.sub(r"\1• ", text)
    text = _EXCESS_BLANK_RE.sub("\n\n\n", text)

    # 4. Escape all remaining text; model output can no longer inject tags.
    text = html.escape(text, quote=False)

    # 5. Inline transforms on the escaped text.
    text = _STRIKE_RE.sub(r"<s>\1</s>", text)
    text = _BOLD_RE.sub(r"<b>\1</b>", text)
    text = _ITALIC_ASTERISK_RE.sub(r"<i>\1</i>", text)
    text = _ITALIC_UNDERSCORE_RE.sub(r"<i>\1</i>", text)
    text = _SPOILER_RE.sub(r"<tg-spoiler>\1</tg-spoiler>", text)

    # 6. Line-level markup (headings, blockquotes) — wraps already-escaped lines.
    text = _wrap_line_markup(text)

    # 7. Links last, so [label](url) inside headings/quotes also converts.
    text = _LINK_RE.sub(_link_replacement, text)

    # 8. Restore stashed code fragments.
    return _PLACEHOLDER_RE.sub(lambda match: placeholders[int(match.group(1))] if int(match.group(1)) < len(placeholders) else "", text)


def _wrap_line_markup(text: str) -> str:
    """Wrap ``> quote`` lines in <blockquote> and heading lines in <b>."""
    lines = text.split("\n")
    out: list[str] = []
    quote: list[str] = []
    for line in lines:
        quote_match = _BLOCKQUOTE_LINE_RE.match(line)
        heading_match = _HEADING_LINE_RE.match(line)

        if quote_match and quote_match.group(1).strip():
            quote.append(quote_match.group(1).strip())
            continue

        if heading_match and heading_match.group(1).strip():
            line = f"<b>{heading_match.group(1).strip()}</b>"
        out.append(line)

        # A blank line (or non-quote text) closes an open blockquote.
        if quote and (not line.strip() or not line.lstrip().startswith(">")):
            position = len(out) - 1
            out[position] = f"<blockquote>{'<br>'.join(quote)}</blockquote>"
            if line.strip():
                out.append(line)
                position = len(out) - 1
            quote = []
    if quote:
        out.append(f"<blockquote>{'<br>'.join(quote)}</blockquote>")
    return "\n".join(out)


def _link_replacement(match: re.Match) -> str:
    label = match.group(1).strip()
    url = match.group(2).strip()
    if not _SAFE_URL_SCHEMES_RE.match(url):
        return match.group(0)
    url = url.replace('"', '&quot;')
    return f'<a href="{url}">{label}</a>'


def strip_telegram_html(text: str) -> str:
    """Reduce generated Telegram HTML back to plain text (fallback path)."""
    if not text:
        return ""
    return html.unescape(re.sub(r"<[^>]+>", "", text)).strip()


def truncate_telegram_html(
    html_text: str,
    limit: int = TELEGRAM_TEXT_LIMIT,
    suffix: str = "\n\n[Response continues…]",
) -> str:
    """Trim generated Telegram HTML to ``limit`` chars without breaking tags."""
    html_text = (html_text or "").strip()
    if len(html_text) <= limit:
        return html_text
    target = limit - len(suffix)
    if target < 100:
        return strip_telegram_html(html_text)[: max(limit - 3, 0)] + "…"

    end = len(_strip_dangling_tag(html_text[:target]))
    # Cut at a clean spot, stepping back until prefix+closings fit the budget.
    for _ in range(400):
        if end <= 0:
            break
        prefix = _strip_dangling_tag(html_text[:end]).rstrip()
        closers = _close_open_tags(prefix)
        if len(prefix) + len(closers) <= target and (prefix.strip() or html_text[:target].strip() == ""):
            return prefix + closers + suffix
        end -= 1

    # Gave up on a well-formed cut: strip tags entirely.
    return strip_telegram_html(html_text)[: max(target, 0)] +"…" + suffix


def _strip_dangling_tag(prefix: str) -> str:
    """Drop a trailing incomplete ``<a href=...`` fragment."""
    last_open = prefix.rfind("<")
    last_close = prefix.rfind(">")
    if last_open > last_close:
        return prefix[:last_open]
    return prefix


def _close_open_tags(prefix: str) -> str:
    """Return closing tags for every tag still open in ``prefix``."""
    stack: list[str] = []
    for match in _TAG_RE.finditer(prefix):
        slash, name, rest = match.group(1), match.group(2).lower(), match.group(3)
        if rest.rstrip().endswith("/"):  # self-closing
            continue
        if slash:  # </name>
            for index in range(len(stack) - 1, -1, -1):
                if stack[index] == name:
                    del stack[index]
                    break
        elif name in _PAIR_TAGS:
            stack.append(name)
    return "".join(f"</{name}>" for name in reversed(stack))


def telegram_preview(text: str) -> str:
    """Streaming preview: formatted HTML, safely truncated for editMessageText."""
    return truncate_telegram_html(markdown_to_telegram_html(text), TELEGRAM_TEXT_LIMIT - 96)


def _split_source_lines(text: str, target: int) -> list[str]:
    """Split raw markdown into ~target-char chunks, never breaking code fences."""
    chunks: list[str] = []
    current: list[str] = []
    length = 0
    fence_lang: str | None = None  # None means we are outside any fence

    for line in text.splitlines(True):
        stripped = line.strip()
        is_fence_line = stripped.startswith("```")

        if length + len(line) > target and current:
            if fence_lang is None:
                chunks.append("".join(current))
                current, length = [], 0
            else:
                # Splitting inside a fence: close it, flush, reopen after.
                current.append("```\n")
                chunks.append("".join(current))
                reopened = f"```{fence_lang}\n"
                current, length = [reopened], len(reopened)

        current.append(line)
        length += len(line)

        if is_fence_line:
            if fence_lang is None:
                fence_lang = stripped[3:].strip()  # may be "" for a bare fence
            else:
                fence_lang = None  # this line closes the open fence
    if current:
        chunks.append("".join(current))
    return chunks


def split_telegram_text(text: str) -> list[str]:
    """Split agent markdown into <=4096-char Telegram HTML message chunks."""
    text = (text or "").strip()
    if not text:
        return [""]
    full = markdown_to_telegram_html(text)
    if len(full) <= TELEGRAM_TEXT_LIMIT:
        return [full] if full else [""]

    chunks: list[str] = []
    for source in _split_source_lines(text, 3400):
        chunk_html = markdown_to_telegram_html(source)
        if len(chunk_html) <= TELEGRAM_TEXT_LIMIT:
            chunks.append(chunk_html)
            continue
        # Escaping blew up (wall of & / <); bisect the source recursively.
        left, right = _bisect_source(source)
        for part in (left, right):
            part_html = markdown_to_telegram_html(part)
            if len(part_html) <= TELEGRAM_TEXT_LIMIT:
                chunks.append(part_html)
            else:
                chunks.append(truncate_telegram_html(part_html, TELEGRAM_TEXT_LIMIT, ""))
    return [chunk for chunk in chunks if chunk]


def _bisect_source(source: str) -> tuple[str, str]:
    """Halve ``source`` at a newline boundary, keeping code fences usable."""
    midpoint = len(source) // 2
    split_at = source.find("\n", midpoint)
    if split_at == -1 or split_at == len(source) - 1:
        split_at = source.rfind("\n", 0, midpoint)
    if split_at <= 0:
        return source, ""
    left, right = source[: split_at + 1], source[split_at + 1 :]
    if not left.strip() or not right.strip():
        return source, ""
    if _fence_open(left):
        # Right half continues an unclosed fence; reopen it for a clean <pre>.
        right = "```\n" + right
    return left, right


def _fence_open(text: str) -> bool:
    count = sum(1 for line in text.splitlines() if line.strip().startswith("```"))
    return count % 2 == 1
