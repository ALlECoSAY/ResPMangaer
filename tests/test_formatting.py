from __future__ import annotations

from app.bot.formatting import split_for_telegram


def test_split_short_message_unchanged():
    assert split_for_telegram("hello", 100) == ["hello"]


def test_split_long_message_into_chunks():
    text = ("a" * 500 + "\n") * 10
    chunks = split_for_telegram(text, 1000)
    assert all(len(c) <= 1000 for c in chunks)
    joined = "\n".join(chunks)
    # roughly preserves content
    assert joined.replace("\n", "").count("a") == text.replace("\n", "").count("a")


def test_split_at_newline_when_possible():
    text = "line1\nline2\nline3"
    chunks = split_for_telegram(text, 12)
    assert all(len(c) <= 12 for c in chunks)
    assert len(chunks) >= 2
