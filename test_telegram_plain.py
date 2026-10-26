#!/usr/bin/env python3
"""Tests for the plain-text conversion of streamed Telegram HTML.

Run:
    .venv/bin/python test_telegram_plain.py
"""

from __future__ import annotations

from telegram_format import PlainTextStream, strip_telegram_html, strip_telegram_html_fragment


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


if __name__ == "__main__":
    for name, func in sorted(globals().items()):
        if name.startswith("test_") and callable(func):
            func()
    print("telegram plain-text tests passed")
