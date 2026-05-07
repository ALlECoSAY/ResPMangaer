from __future__ import annotations

from app.telegram_client.types import TgMessage, TgUser


def display_name(user: TgUser | None) -> str:
    if user is None:
        return "unknown"
    if user.username:
        return f"@{user.username}"
    parts = [p for p in (user.first_name, user.last_name) if p]
    if parts:
        return " ".join(parts)
    return f"user:{user.id}"


def message_thread_id_for(message: TgMessage) -> int:
    """Return ``message_thread_id`` or ``0`` for non-topic chats / general."""
    return int(message.message_thread_id or 0)


def extract_text(message: TgMessage) -> str:
    return message.text or message.caption or ""


def clean_command_text(text: str, command: str | None, bot_username: str | None) -> str:
    """Strip the leading command + bot suffix from a message body."""
    if not text:
        return ""
    stripped = text.strip()
    if not stripped.startswith("/"):
        return stripped
    parts = stripped.split(maxsplit=1)
    if not parts:
        return stripped
    head = parts[0]
    if "@" in head:
        head = head.split("@", 1)[0]
    if command and head.lower() != f"/{command.lower()}":
        return stripped
    return parts[1].strip() if len(parts) > 1 else ""
