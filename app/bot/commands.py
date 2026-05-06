from __future__ import annotations

from dataclasses import dataclass

KNOWN_COMMANDS = frozenset({"ai", "tldr", "whitelist"})


@dataclass(frozen=True)
class ParsedCommand:
    command: str
    args: str


def parse_command(text: str | None, bot_username: str | None = None) -> ParsedCommand | None:
    if not text:
        return None
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None
    parts = stripped.split(maxsplit=1)
    if not parts:
        return None
    head = parts[0][1:]  # strip leading '/'
    if "@" in head:
        cmd_part, suffix = head.split("@", 1)
        if bot_username and suffix.lower() != bot_username.lower():
            return None
        head = cmd_part
    head = head.lower()
    if not head:
        return None
    args = parts[1].strip() if len(parts) > 1 else ""
    return ParsedCommand(command=head, args=args)


def is_known_command(name: str) -> bool:
    return name.lower() in KNOWN_COMMANDS
