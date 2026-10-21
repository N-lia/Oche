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


def main() -> int:
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"{len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
