#!/usr/bin/env python3
"""Tests for the plain-text conversion of streamed Telegram HTML.

Run:
    .venv/bin/python test_telegram_plain.py
"""

from __future__ import annotations

from telegram_format import (
    MarkdownPlainStream,
    PlainTextStream,
    markdown_to_plain_text,
    strip_telegram_html,
    strip_telegram_html_fragment,
)


def test_strip_keeps_spaces() -> None:
    assert strip_telegram_html_fragment("<b>Hi</b> Sir") == "Hi Sir"
    assert strip_telegram_html("<b>Hi</b> Sir") == "Hi Sir"


def test_tag_split_across_chunks() -> None:
    stream = PlainTextStream()
    out = ""
    for chunk in ["Hello <b", ">World</", "b> and <i>more", "</i>"]:
        out += stream.feed(chunk)
    out += stream.flush()
    assert out == "Hello World and more"


def test_entities_are_unescaped() -> None:
    assert strip_telegram_html_fragment("Tom &amp; Jerry") == "Tom & Jerry"


def test_stream_keeps_leading_spaces() -> None:
    stream = PlainTextStream()
    out = ""
    for chunk in ["Sir", " Mario", " is", " here"]:
        out += stream.feed(chunk)
    assert out == "Sir Mario is here"


def test_markdown_to_plain_text_strips_formatting() -> None:
    markdown = (
        "## Plan\n\n"
        "**Bold** and *italic* and ~~gone~~ and ||secret||.\n\n"
        "- one\n- two\n\n"
        "A [source](https://example.com) and `inline`.\n"
    )
    text = markdown_to_plain_text(markdown, keep_urls=False)
    assert "##" not in text and "**" not in text
    assert "Bold and italic and gone and secret." in text
    assert "• one" in text and "• two" in text
    assert "A source and inline." in text


def test_markdown_to_plain_text_can_keep_urls() -> None:
    text = markdown_to_plain_text("See [the docs](https://example.com/docs).", keep_urls=True)
    assert text == "See the docs (https://example.com/docs)."


def test_markdown_to_plain_text_flattens_tables() -> None:
    table = (
        "| Name | Price |\n"
        "|------|-------|\n"
        "| Pixel 9 | ₦95,000 |\n"
        "| Infinix Hot 50 | ₦90,000 |"
    )
    text = markdown_to_plain_text(table)
    assert "|" not in text and "---" not in text
    assert "• Name: Pixel 9 — Price: ₦95,000" in text
    assert "• Name: Infinix Hot 50 — Price: ₦90,000" in text


def test_markdown_plain_stream_handles_split_bold_and_tables() -> None:
    stream = MarkdownPlainStream()
    out = ""
    for chunk in (
        "The president is ** Bola",
        " Tinubu **.\n",
        "| Name | Price |\n",
        "|------|-------|\n",
        "| Pixel | ₦95k |\n",
        "Done.\n",
    ):
        out += stream.feed(chunk)
    out += stream.flush()
    assert out == (
        "The president is Bola Tinubu.\n"
        "• Name: Pixel — Price: ₦95k\n"
        "Done.\n"
    ), repr(out)


def test_markdown_plain_stream_holds_unclosed_fence() -> None:
    stream = MarkdownPlainStream()
    first = stream.feed("Here:\n```python\nprint('hi')\n")
    assert first == "Here:\n", repr(first)
    rest = stream.feed("```\nDone.\n")
    rest += stream.flush()
    assert "print('hi')" in rest, repr(rest)
    assert "```" not in rest


if __name__ == "__main__":
    for name, func in sorted(globals().items()):
        if name.startswith("test_") and callable(func):
            func()
    print("telegram plain-text tests passed")
