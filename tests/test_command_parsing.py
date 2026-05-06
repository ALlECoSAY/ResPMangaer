from __future__ import annotations

from app.bot.commands import parse_command


def test_parse_simple():
    assert parse_command("/ai hello").command == "ai"
    assert parse_command("/ai hello").args == "hello"


def test_parse_uppercase_tldr():
    p = parse_command("/TLDR")
    assert p is not None and p.command == "tldr" and p.args == ""


def test_parse_with_bot_suffix_matching():
    p = parse_command("/ai@MyBot hello there", bot_username="MyBot")
    assert p is not None and p.command == "ai" and p.args == "hello there"


def test_parse_with_bot_suffix_mismatch():
    assert parse_command("/ai@OtherBot hi", bot_username="MyBot") is None


def test_parse_strips_whitespace():
    p = parse_command("  /tldr 24h ")
    assert p is not None and p.command == "tldr" and p.args == "24h"


def test_parse_whitelist():
    p = parse_command("/whitelist")
    assert p is not None and p.command == "whitelist" and p.args == ""


def test_parse_no_slash():
    assert parse_command("hello /ai") is None


def test_parse_empty():
    assert parse_command("") is None
    assert parse_command(None) is None
