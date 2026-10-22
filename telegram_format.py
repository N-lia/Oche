"""Convert agent replies into Telegram HTML.

Telegram Bot API exposes text formatting through ``parse_mode`` on
``sendMessage`` / ``editMessageText`` (and the caption fields of media
methods), or through an explicit ``entities`` array. We use ``parse_mode="HTML"``
instead of ``MarkdownV2`` because only ``&``, ``<`` and ``>`` need escaping and
a formatting failure can safely fall back to plain text.

Two input modes are handled by :func:`normalize_agent_html`, which every
send-path routes through:

- **Native HTML** (the system prompt asks the model to author replies as
  Telegram HTML directly): allowed tags are trusted and emitted canonically,
  stray ``&`` / ``<`` / ``>`` are escaped, unknown tags are stripped, links are
  scheme-checked, mismatched nesting is rebalanced, and unclosed tags are
  closed at the end.
- **Markdown fallback**: replies without any Telegram tag are converted by
  :func:`markdown_to_telegram_html` like before.

Guarantees provided to callers:
- Output is always valid Telegram HTML: raw model markup can never be mistaken
  for an injection, and every open tag is matched or closed.
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


_MERMAID_BLOCK_RE = re.compile(
    r"<pre><code\s+class=\"language-mermaid\">\s*.*?\s*</code></pre>", re.S | re.I
)
# Models also emit the diagram as a bare <pre><code> block without the
# language class; a body starting with a Mermaid diagram keyword is a strong
# tell, and upgrading those keeps the miniapp renderer and the voice strip
# working on every alias shape the model invents.
_BARE_MERMAID_RE = re.compile(
    r"<pre><code([^>]*)>(\s*(?:flowchart|sequenceDiagram|stateDiagram(?:-v2)?"
    r"|classDiagram|erDiagram|graph(?:[ \t]+(?:TD|TB|LR|RL|BT))?|mindmap"
    r"|pie|gantt|journey|timeline)\b)",
    re.I,
)


def _canonicalize_bare_mermaid(text: str) -> str:
    """Add class="language-mermaid" to bare <pre><code> diagram blocks."""
    if not _BARE_MERMAID_RE.search(text):
        return text

    def _upgrade(match: re.Match[str]) -> str:
        attrs = match.group(1)
        if "class=" in attrs:
            return match.group(0)
        return f'<pre><code class="language-mermaid">{match.group(2)}'

    return _BARE_MERMAID_RE.sub(_upgrade, text)


_MERMAID_BLOCK_ALIAS_RE = re.compile(
    r"<(?:mermaid|diagram)>\s*(.*?)\s*</(?:mermaid|diagram)>", re.S | re.I
)


def strip_diagrams_for_voice(text: str) -> str:
    """Drop Mermaid diagram blocks: they are visual, never spoken.

    Call this on NORMALIZED agent HTML (the canonical
    ``<pre><code class="language-mermaid">`` shape); a raw ```mermaid fence
    turns into that shape via :func:`normalize_agent_html`.
    """
    if not text:
        return ""
    return _MERMAID_BLOCK_RE.sub(" ", text)


def voice_text_for_speech(text: str) -> str:
    """The ONE conversion from agent HTML to speakable plain text.

    Every TTS path must use this instead of composing its own chain:
    normalize first (so markdown slips and diagram aliases converge), drop
    visual-only diagram sources, then strip tags for the synthesis engine.
    """
    if not text:
        return ""
    try:
        normalized = normalize_agent_html(text)
    except Exception:
        normalized = text
    if "language-mermaid" in normalized:
        normalized = strip_diagrams_for_voice(normalized)
    spoken = strip_telegram_html(normalized)
    return re.sub(r"[ \t]{2,}", " ", re.sub(r"\n{3,}", "\n\n", spoken)).strip()


# ---------------------------------------------------------------------------
# Native HTML pass-through: the model authors Telegram HTML directly.
# ---------------------------------------------------------------------------

_ALIASES = {"strong": "b", "em": "i", "ins": "u", "del": "s", "strike": "s"}
_NATIVE_TAGS = frozenset({
    "b", "i", "u", "s", "code", "pre", "a", "blockquote", "tg-spoiler", "br",
})
# Detection regex: any Telegram tag (or common HTML tag the model may slip in)
# turns on the native path. Anything else goes through markdown conversion.
_NATIVE_TAG_RE = re.compile(
    r"<(/?)(?:b|strong|i|em|u|ins|s|strike|code|pre|a|blockquote|tg-spoiler|br|span"
    r"|p|div|ul|ol|li|h[1-6]|font|small|sub|sup|html|body)\b", re.I
)
_ANY_TAG_RE = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9\-]*)((?:\s[^<>]*)?)\s*(/?)>")
_CODE_CLASS_RE = re.compile(r'class\s*=\s*("language-[a-z0-9_+\-]+"|\'language-[a-z0-9_+\-]+\')', re.I)
_HREF_RE = re.compile(r'''href\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))''', re.I)
# Bare `&` runs on ESCAPED text, so the entity references are already canonical.
_BARE_AMP_RE = re.compile(r"&(?!amp;|lt;|gt;|quot;|apos;|nbsp;|#\d{1,6};|#x[0-9a-fA-F]{1,6};)")
_ENTITY_PRESENT_RE = re.compile(r"&(?:amp|lt|gt|quot|apos|#\d{1,6};|#x[0-9a-fA-F]{1,6});")
_WRAP_FENCE_RE = re.compile(r"^```[A-Za-z0-9_+\-]*[ \t]*\n(.*?)[ \t]*```[ \t]*$", re.S)


def normalize_agent_html(text: str) -> str:
    """Trust allowed Telegram tags in model output; repair everything else.

    The system prompt asks the model to author replies as Telegram HTML. Models
    still slip sometimes, so this sanitizer keeps the *intent* while making the
    result parseable: malformed tags are canonicalised, unknown tags stripped,
    stray characters escaped, nesting rebalanced, and unclosed tags closed.
    Replies without any tag fall back to markdown conversion.
    """
    text = (text or "").strip()
    if not text:
        return ""
    text = _strip_dangling_tag(text)
    # Models love wrapping the whole reply in a ```html fence even when told
    # not to; unwrap a single trims-everything fence.
    wrapped = _WRAP_FENCE_RE.match(text)
    if wrapped and _NATIVE_TAG_RE.search(wrapped.group(1)):
        text = wrapped.group(1).strip()
        text = _strip_dangling_tag(text) or text
    # Models also invent a <mermaid> tag for diagrams; canonicalise it into
    # the pre/code language block before any other pass sees an unknown tag.
    # Substituted plainly (no stash): the native path keeps pre/code bodies
    # verbatim, so the escaped Mermaid source survives untouched.
    text = _MERMAID_BLOCK_ALIAS_RE.sub(
        lambda m: (
            f"<pre><code class=\"language-mermaid\">"
            f"{html.escape(html.unescape(m.group(1)).strip())}</code></pre>"
        ),
        text,
    )
    # Same story for a bare <pre><code> block whose body starts with a
    # Mermaid diagram keyword: Telegram renders it fine, but the miniapp
    # renderer mounts only language-mermaid blocks, so give it the class.
    text = _canonicalize_bare_mermaid(text)
    if not _NATIVE_TAG_RE.search(text):
        if _ENTITY_PRESENT_RE.search(text):
            # Entity-only reply (no tags): the model authored references, so
            # unescape before the markdown converter re-escapes everything.
            text = html.unescape(text)
        return markdown_to_telegram_html(text)
    return _normalize_native(text)


def _escape_prose(fragment: str) -> str:
    """Escape stray markup characters in a non-tag region.

    Character references the model already emitted (``&amp;`` …) pass through;
    a bare ``&``, leftover ``<`` and ``>`` become entities per the Telegram
    HTML spec.
    """
    if not fragment:
        return ""
    fragment = fragment.replace("<", "&lt;").replace(">", "&gt;")
    return _BARE_AMP_RE.sub("&amp;", fragment)


def _canonical_open(name: str, rest: str) -> str | None:
    """Build the Telegram-sanctioned form of an allowed opening tag.

    ``None`` means the tag must be dropped entirely (bad link target).
    """
    if name == "a":
        href_match = _HREF_RE.search(rest)
        url = next((g for g in href_match.groups() if g is not None), "") if href_match else ""
        if not _SAFE_URL_SCHEMES_RE.match(url.strip()):
            return None
        url = html.unescape(url.strip()).replace('"', "&quot;")
        url = _BARE_AMP_RE.sub("&amp;", url)
        return f'<a href="{url}">'
    if name == "code":
        class_match = _CODE_CLASS_RE.search(rest)
        if class_match:
            lang = html.unescape(class_match.group(1)).strip("'\"").removeprefix("language-")
            return f"<code{_language_class(lang)}>"
        return "<code>"
    return f"<{name}>"


def _normalize_native(text: str) -> str:
    """Single-pass scan: tags emitted canonically, nesting kept balanced."""
    out: list[str] = []
    stack: list[str] = []
    idx = 0

    for match in _ANY_TAG_RE.finditer(text):
        out.append(_native_prose(text[idx: match.start()], stack))
        slash, raw_name, rest, tail = match.group(1), match.group(2).lower(), match.group(3), match.group(4)
        name = _ALIASES.get(raw_name, raw_name)

        if name not in _NATIVE_TAGS:
            # Unknown tag: map a spoiler span, keep list items as bullets, drop the rest.
            if raw_name == "span" and "tg-spoiler" in rest:
                if not slash:
                    out.append("<tg-spoiler>")
                    stack.append("tg-spoiler")
            elif name == "li" and not slash:
                out.append("• ")
            idx = match.end()
            continue

        if name == "br":
            out.append("<br>")
            idx = match.end()
            continue

        if slash:  # closing tag
            if stack and stack[-1] == name:
                stack.pop()
                out.append(f"</{name}>")
            elif name in stack:
                # Mismatched nesting: close everything opened after it first.
                position = len(stack) - 1 - stack[::-1].index(name)
                for late in reversed(stack[position + 1:]):
                    out.append(f"</{late}>")
                out.append(f"</{name}>")
                del stack[position:]
            # else: unmatched closer — drop it.
        else:
            canonical = _canonical_open(name, rest)
            if canonical is not None:
                out.append(canonical)
                stack.append(name)
        idx = match.end()

    out.append(_native_prose(text[idx:], stack))
    return "".join(out) + "".join(f"</{n}>" for n in reversed(stack))


_NATIVE_CODE_SPAN_RE = re.compile(r"`([^`\n]+?)`")


def _native_prose(fragment: str, stack: list[str]) -> str:
    """Escape a prose region and convert markdown slips the model still drops.

    The reply briefing says "no markdown", but models routinely fall back to
    ``**bold**``, ``*italic*``, ``# headings``, ``- bullets``, ``~~strike~~``,
    ``||spoiler||``, ``[label](url)`` and ``backticks`` — often mixed INTO an
    otherwise-HTML reply. Apply the same inline/bullet/heading/quote transforms
    the markdown converter knows, on the already-escaped prose. Verbatim code
    content (``<pre>``/``<code>`` bodies) is escaped only.
    """
    escaped = _escape_prose(fragment)
    if not escaped or any(tag in ("pre", "code") for tag in stack):
        return escaped
    escaped = _BULLET_RE.sub(r"\1• ", escaped)
    escaped = _wrap_line_markup(escaped)
    escaped = _NATIVE_CODE_SPAN_RE.sub(r"<code>\1</code>", escaped)
    escaped = _STRIKE_RE.sub(r"<s>\1</s>", escaped)
    escaped = _BOLD_RE.sub(r"<b>\1</b>", escaped)
    escaped = _ITALIC_ASTERISK_RE.sub(r"<i>\1</i>", escaped)
    escaped = _ITALIC_UNDERSCORE_RE.sub(r"<i>\1</i>", escaped)
    escaped = _SPOILER_RE.sub(r"<tg-spoiler>\1</tg-spoiler>", escaped)
    escaped = _LINK_RE.sub(_link_replacement, escaped)
    return escaped


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


_DANGLING_TAG_RE = re.compile(r"<(?:[a-zA-Z/][^<>]*)?$")


def _strip_dangling_tag(prefix: str) -> str:
    """Drop a trailing incomplete ``<a href=...`` fragment.

    Only strips tails that look like a partial tag (letter or slash right
    after ``<``); plain prose like ``<3`` stays untouched.
    """
    last_open = prefix.rfind("<")
    last_close = prefix.rfind(">")
    if last_open > last_close:
        tail = prefix[last_open:]
        if _DANGLING_TAG_RE.search(tail):
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
    return truncate_telegram_html(normalize_agent_html(text), TELEGRAM_TEXT_LIMIT - 96)


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
    """Split an agent reply into <=4096-char Telegram HTML message chunks."""
    text = (text or "").strip()
    if not text:
        return [""]
    full = normalize_agent_html(text)
    if len(full) <= TELEGRAM_TEXT_LIMIT:
        return [full] if full else [""]

    chunks: list[str] = []
    for source in _split_source_lines(text, 3400):
        chunk_html = normalize_agent_html(source)
        if len(chunk_html) <= TELEGRAM_TEXT_LIMIT:
            chunks.append(chunk_html)
            continue
        # Escaping blew up (wall of & / <); bisect the source recursively.
        left, right = _bisect_source(source)
        for part in (left, right):
            part_html = normalize_agent_html(part)
            if len(part_html) <= TELEGRAM_TEXT_LIMIT:
                chunks.append(part_html)
            else:
                chunks.append(truncate_telegram_html(part_html, TELEGRAM_TEXT_LIMIT, ""))
    return [chunk for chunk in chunks if chunk]


def _bisect_source(source: str) -> tuple[str, str]:
    """Halve ``source`` at a clean boundary (newline, then space)."""
    midpoint = len(source) // 2
    split_at = source.find("\n", midpoint)
    if split_at == -1 or split_at == len(source) - 1:
        split_at = source.rfind("\n", 0, midpoint)
    if split_at <= 0:
        # No newlines at all (single-line HTML reply): cut at a space instead.
        split_at = source.rfind(" ", 0, midpoint)
    if split_at <= 0:
        split_at = midpoint  # pathological: one enormous token, hard cut
    left, right = source[: split_at + 1], source[split_at + 1 :]
    if not right:
        return source, ""
    if _fence_open(left):
        # Right half continues an unclosed fence; reopen it for a clean <pre>.
        right = "```\n" + right
    return left, right


def _fence_open(text: str) -> bool:
    count = sum(1 for line in text.splitlines() if line.strip().startswith("```"))
    return count % 2 == 1
