#!/usr/bin/env python3
"""Tests for Markdown -> Telegram HTML conversion (telegram_format.py).

Run:
    .venv/bin/python test_telegram_format.py
"""

from __future__ import annotations

import re
import sys

import telegram_format as tf


def test_basic_inline_formatting() -> None:
    html = tf.markdown_to_telegram_html("**Bold** and *italic* and ~~gone~~ and ||secret||")
    assert html == "<b>Bold</b> and <i>italic</i> and <s>gone</s> and <tg-spoiler>secret</tg-spoiler>", html


def test_inline_code_escapes_everything() -> None:
    html = tf.markdown_to_telegram_html("Run `x < 3 && y > 2` now")
    assert html == "Run <code>x &lt; 3 &amp;&amp; y &gt; 2</code> now", html


def test_fenced_code_with_language() -> None:
    html = tf.markdown_to_telegram_html("Use:\n```python\ndef f(x):\n    return '<b>'\n```\nDone.")
    assert '<pre><code class="language-python">' in html
    assert "&#x27;&lt;b&gt;&#x27;" in html
    assert html.endswith("</code></pre>\nDone."), html


def test_raw_model_markup_cannot_inject_tags() -> None:
    html = tf.markdown_to_telegram_html("<script>alert(1)</script> & <b>not real</b>")
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "<b>not real</b>" not in html and "&lt;b&gt;not real&lt;/b&gt;" in html


def test_links_safe_schemes_only() -> None:
    html = tf.markdown_to_telegram_html("[ok](https://ex.com/a?b=1&c=2) and [bad](javascript:alert(1))")
    assert '<a href="https://ex.com/a?b=1&amp;c=2">ok</a>' in html, html
    assert "javascript:alert" in html.replace('<a href="', "").replace('">', "") and "<a" not in html.split("bad]")[1][:4]


def test_headings_blockquotes_lists() -> None:
    html = tf.markdown_to_telegram_html("# Title\n\n> quoted one\n> quoted two\n\n- one\n- two")
    assert "<b>Title</b>" in html, html
    assert "<blockquote>quoted one<br>quoted two</blockquote>" in html, html
    assert "• one" in html and "• two" in html, html


def test_snake_case_underscore_not_italic() -> None:
    html = tf.markdown_to_telegram_html("snake_case_word stays plain")
    assert "snake_case_word" in html and "<i>" not in html


def test_truncation_closes_tags_and_respects_limit() -> None:
    text = "Para **one** with `code` here. " * 300
    preview = tf.telegram_preview(text)
    assert len(preview) <= 4000, len(preview)
    assert preview.endswith("[Response continues…]")
    assert not re.search(r"<[a-z]+[^>]*$|</\w*$", preview), preview[-20:]


def test_truncate_extreme_limits() -> None:
    html = tf.markdown_to_telegram_html("A **bold** B *ital* C `mono` D " * 500)
    for limit in (4096, 2000, 999, 500, 300):
        out = tf.truncate_telegram_html(html, limit)
        assert len(out) <= limit, (limit, len(out))
        # Every generated open tag must have a closing tag in the output.
        opens = re.findall(r"<(/?)([a-zA-Z][a-zA-Z0-9\-]*)[^>]*>", out)
        counts: dict[str, int] = {}
        for slash, name in opens:
            counts[name] = counts.get(name, 0) + (-1 if slash else 1)
        for name, delta in counts.items():
            assert delta == 0, (limit, name, delta, out[-80:])


def test_split_chunks_within_limit() -> None:
    text = ("paragraph line with **format** and more text.\n" * 200)
    chunks = tf.split_telegram_text(text)
    assert all(len(c) <= 4096 for c in chunks), [len(c) for c in chunks]
    assert "format" in tf.strip_telegram_html("".join(chunks))


def test_split_preserves_code_fences() -> None:
    text = "```py\n" + "line of code\n" * 300 + "```\n" + ("tail **bold**.\n" * 60)
    chunks = tf.split_telegram_text(text)
    assert all(len(c) <= 4096 for c in chunks), [len(c) for c in chunks]
    assert "<pre>" in chunks[0], chunks[0][:120]
    joined = tf.strip_telegram_html("".join(chunks))
    assert "line of code" in joined and "tail bold." in joined


def test_split_wall_of_escaped_chars() -> None:
    text = "& < > " * 2000
    chunks = tf.split_telegram_text(text)
    assert all(len(c) <= 4096 for c in chunks), [len(c) for c in chunks]
    assert chunks


def test_strip_telegram_html() -> None:
    assert tf.strip_telegram_html("<b>Bold</b> &amp; <code>code</code>") == "Bold & code"


# ---------------------------------------------------------------------------
# normalize_agent_html: native Telegram HTML authored by the model
# ---------------------------------------------------------------------------


def test_native_html_passes_through_unchanged() -> None:
    html = '<b>Bold</b>, <i>italic</i>, <u>u</u>, <s>s</s>, <code>x</code>, <a href="https://x.y">l</a>'
    assert tf.normalize_agent_html(html) == html


def test_native_plain_text_goes_to_markdown_fallback() -> None:
    assert tf.normalize_agent_html("**bold** and `code`") == "<b>bold</b> and <code>code</code>"


def test_native_unclosed_tags_are_closed() -> None:
    assert tf.normalize_agent_html("<b>bold but <i>mix") == "<b>bold but <i>mix</i></b>"


def test_native_unknown_tags_stripped_li_becomes_bullet() -> None:
    assert tf.normalize_agent_html("<p>One</p><li>Item</li>") == "One• Item"


def test_native_stray_angle_brackets_escaped() -> None:
    assert tf.normalize_agent_html("5 < 6 > 4") == "5 &lt; 6 &gt; 4"
    out = tf.normalize_agent_html("x <b>y</b> <3 love")
    assert out == "x <b>y</b> &lt;3 love"


def test_native_bare_ampersand_escaped_but_entities_kept() -> None:
    assert tf.normalize_agent_html("rock &amp; roll, R&D") == "rock &amp; roll, R&amp;D"


def test_native_anchor_normalized_and_scheme_checked() -> None:
    assert tf.normalize_agent_html("<a href='https://x.y'>go</a>") == '<a href="https://x.y">go</a>'
    # javascript: href must be dropped (text kept, no tag)
    assert tf.normalize_agent_html("<a href=\"javascript:alert(1)\">go</a>") == "go"
    # bare & in URLs gets re-escaped
    assert tf.normalize_agent_html('<a href="https://x.y/?a=1&b=2">l</a>') == (
        '<a href="https://x.y/?a=1&amp;b=2">l</a>'
    )


def test_native_html_stewrapped_in_code_fence_is_unwrapped() -> None:
    body = "<b>Answer</b>: done"
    assert tf.normalize_agent_html("```html\n" + body + "\n```") == body


def test_native_mismatched_nesting_rebalanced() -> None:
    out = tf.normalize_agent_html("<i><b>both</i> after</b> end")
    # every tag properly closed, no stray end tags, text preserved
    assert re.fullmatch(r"<i><b>both</b></i> after end", out)


def test_native_attributes_stripped_from_plain_tags() -> None:
    assert tf.normalize_agent_html('<b onclick="evil()">yes</b>') == "<b>yes</b>"


def test_native_span_spoilermapped() -> None:
    out = tf.normalize_agent_html('<span class="tg-spoiler">shh</span>')
    assert out == "<tg-spoiler>shh</tg-spoiler>"


def test_native_code_class_processed() -> None:
    out = tf.normalize_agent_html("<pre><code class='language-python'>x&lt;3</code></pre>")
    assert out == '<pre><code class="language-python">x&lt;3</code></pre>'


def test_native_long_reply_split_keeps_chunks_parseable() -> None:
    body = "<p>" + ("<b>word</b> " * 400) + "</p>"
    chunks = tf.split_telegram_text(body)
    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk) <= 4096
        # no unopened end tags / every tag closed
        assert re.search(r"</(?!(?:b|i|u|s|code|pre|a|blockquote|tg-spoiler)>)[a-zA-Z]+>", chunk) is None


def test_preview_of_streaming_half_tag_never_shows_lt() -> None:
    # accumulated stream cut mid-tag: the dangling "<ta" must not leak verbatim,
    # and the still-open bold tag gets closed for a parseable preview
    assert tf.normalize_agent_html("<b>done <ta") == "<b>done </b>"


def test_wiring_functions_route_through_native() -> None:
    assert tf.telegram_preview("<b>hi</b> there") == tf.truncate_telegram_html(
        "<b>hi</b> there", tf.TELEGRAM_TEXT_LIMIT - 96
    )
    assert tf.split_telegram_text("<b>hi</b>") == ["<b>hi</b>"]


def test_native_markdown_slips_in_prose_are_converted() -> None:
    reply = "Answer below.\n\n<b>Key</b>\n\n**Bold point** and *ital* together."
    out = tf.normalize_agent_html(reply)
    assert "<b>Bold point</b>" in out
    assert "<i>ital</i>" in out
    assert "**" not in out and "*" not in out.replace("<i>", "").replace("</i>", "")


def test_native_code_spans_and_links_converted() -> None:
    out = tf.normalize_agent_html("run `git status` — see <b>docs</b> [here](https://x.y/guide)")
    assert "<code>git status</code>" in out
    assert '<a href="https://x.y/guide">here</a>' in out


def test_native_heading_bullets_and_quotes_converted() -> None:
    out = tf.normalize_agent_html("## Plan\n\n- first\n- second\n\n> quoted\nplain")
    assert "<b>Plan</b>" in out
    assert "• first" in out and "• second" in out
    assert "<blockquote>" in out


def test_native_strikethrough_and_spoiler_converted() -> None:
    out = tf.normalize_agent_html("keep <b>this</b> — ~~gone~~ ||hidden||")
    assert "<s>gone</s>" in out
    assert "<tg-spoiler>hidden</tg-spoiler>" in out


def test_native_markdown_hidden_inside_code_stays_verbatim() -> None:
    out = tf.normalize_agent_html("text <pre><code>keep **this** raw `and this`</code></pre> after")
    assert "keep **this** raw `and this`" in out
    assert out.count("<b>") == 0
    assert out.count("<code>") == 1


def test_mermaid_block_survives_normalization() -> None:
    html_text = (
        "Here is the flow. <pre><code class=\"language-mermaid\">flowchart LR\n"
        "  A[Agent] --> B[TTS]</code></pre> Done."
    )
    out = tf.normalize_agent_html(html_text)
    assert 'class="language-mermaid"' in out
    assert "flowchart LR" in out
    # Prose stays outside the block; the block itself holds entity-escaped
    # Mermaid source (Telegram-safe), decoded by the browser when pele mounts.
    block = tf._MERMAID_BLOCK_RE.search(out)
    assert block and "A[Agent] --&gt; B[TTS]" in block.group(0)


def test_voice_strip_removes_mermaid_not_prose() -> None:
    html_text = (
        "Here is the flow. <pre><code class=\"language-mermaid\">flowchart LR\n"
        "  A[Agent] --> B[TTS]</code></pre> Done."
    )
    out = tf.strip_diagrams_for_voice(tf.normalize_agent_html(html_text))
    spoken = tf.strip_telegram_html(out)
    assert "flowchart" not in spoken and "A[Agent]" not in spoken
    assert "Here is the flow." in spoken and "Done." in spoken


def test_voice_chain_never_speaks_fenced_mermaid() -> None:
    # Raw model output with a markdown-fenced diagram (prompt tells it not to,
    # but the fallback must still be safe for TTS).
    raw = "Flow below.\n```mermaid\nflowchart TD\n A --> B\n```\nThat is all."
    normalized = tf.normalize_agent_html(raw)
    spoken = tf.strip_telegram_html(tf.strip_diagrams_for_voice(normalized))
    assert "flowchart TD" not in spoken and "-->" not in spoken
    assert "Flow below." in spoken and "That is all." in spoken


def main() -> int:
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"{len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
