from __future__ import annotations


def internal_chat_id(chat_id: int) -> str:
    raw = str(abs(chat_id))
    if raw.startswith("100") and len(raw) > 3:
        return raw[3:]
    return raw


def message_link(
    *,
    chat_id: int,
    message_id: int,
    chat_username: str | None = None,
    message_thread_id: int | None = None,
) -> str:
    username = (chat_username or "").strip().lstrip("@")
    if username:
        return f"https://t.me/{username}/{message_id}"

    internal_id = internal_chat_id(chat_id)
    if message_thread_id is not None and message_thread_id > 0:
        return f"https://t.me/c/{internal_id}/{message_thread_id}/{message_id}"
    return f"https://t.me/c/{internal_id}/{message_id}"


def user_link(username: str | None) -> str | None:
    username = (username or "").strip().lstrip("@")
    if not username:
        return None
    return f"https://t.me/{username}"
