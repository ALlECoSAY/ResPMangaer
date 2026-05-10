from __future__ import annotations

import re

from app.telegram_client.types import TgMessage, TgUser

_USERNAME_MENTION_RE = re.compile(r"^@([A-Za-z0-9_]{5,32})$")
_NOTIFICATION_MENTION_RE = re.compile(r"(?<![\w/])@([A-Za-z0-9_]{5,32})\b")


def user_plain_label(user: TgUser | None) -> str:
    """Plain-text label safe for outgoing messages and LLM prompts.

    Never starts with ``@``: a leading ``@`` in any plain-text Telegram message
    notifies the named user.
    """
    if user is None:
        return "unknown"
    parts = [p for p in (user.first_name, user.last_name) if p]
    if parts:
        return " ".join(parts)
    if user.username:
        return user.username
    return f"user:{user.id}"


def display_name(user: TgUser | None) -> str:
    return user_plain_label(user)


def safe_sender_label(value: str | None) -> str:
    """Sanitize a stored ``sender_display_name`` before feeding it to a prompt."""
    if not value:
        return "anon"
    stripped = value.strip()
    match = _USERNAME_MENTION_RE.match(stripped)
    if match:
        return match.group(1)
    return stripped


def strip_notification_mentions(text: str) -> str:
    """Remove the ``@`` from bare ``@username`` mentions in LLM-generated text."""
    if not text:
        return text
    return _NOTIFICATION_MENTION_RE.sub(r"\1", text)


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
