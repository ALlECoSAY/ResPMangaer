from __future__ import annotations

from app.services.telegram_links import message_link, user_link


def test_public_chat_message_link() -> None:
    assert (
        message_link(chat_id=-1001234567890, chat_username="public_chat", message_id=42)
        == "https://t.me/public_chat/42"
    )


def test_private_supergroup_message_link() -> None:
    assert (
        message_link(chat_id=-1001234567890, message_id=42)
        == "https://t.me/c/1234567890/42"
    )


def test_private_forum_topic_message_link() -> None:
    assert (
        message_link(chat_id=-1001234567890, message_thread_id=99, message_id=42)
        == "https://t.me/c/1234567890/99/42"
    )


def test_username_user_link() -> None:
    assert user_link("@alice") == "https://t.me/alice"


def test_user_without_username_has_no_link() -> None:
    assert user_link(None) is None
