from __future__ import annotations

from datetime import UTC, datetime

from app.telegram_client.types import TgChat, TgMessage, TgUser
from app.utils.telegram import (
    clean_command_text,
    display_name,
    extract_text,
    message_thread_id_for,
    safe_sender_label,
    strip_notification_mentions,
    user_plain_label,
)


def _message(*, text: str | None = None, caption: str | None = None, thread_id: int = 0) -> TgMessage:
    return TgMessage(
        chat=TgChat(id=1, type="supergroup", title="Chat", username=None, is_forum=True),
        message_id=10,
        message_thread_id=thread_id,
        from_user=TgUser(
            id=7,
            is_bot=False,
            username="alice",
            first_name="Alice",
            last_name=None,
            language_code="en",
        ),
        date=datetime(2026, 5, 7, tzinfo=UTC),
        text=text,
        caption=caption,
        content_type="text" if text else "photo",
        reply_to_message_id=None,
    )


def test_display_name_prefers_full_name() -> None:
    user = TgUser(1, False, "alice", "Alice", "Smith", "en")
    assert display_name(user) == "Alice Smith"


def test_display_name_falls_back_to_full_name() -> None:
    user = TgUser(1, False, None, "Alice", "Smith", "en")
    assert display_name(user) == "Alice Smith"


def test_display_name_never_returns_at_username() -> None:
    user = TgUser(1, False, "alice", None, None, "en")
    assert display_name(user) == "alice"


def test_user_plain_label_uses_user_id_when_nothing_else() -> None:
    user = TgUser(123, False, None, None, None, None)
    assert user_plain_label(user) == "user:123"


def test_user_plain_label_handles_none() -> None:
    assert user_plain_label(None) == "unknown"


def test_safe_sender_label_strips_at_prefix() -> None:
    assert safe_sender_label("@alice") == "alice"


def test_safe_sender_label_passes_plain_names_through() -> None:
    assert safe_sender_label("Alice Smith") == "Alice Smith"
    assert safe_sender_label(None) == "anon"
    assert safe_sender_label("") == "anon"


def test_strip_notification_mentions_removes_at_prefix() -> None:
    assert strip_notification_mentions("hi @alice and @bob_123") == "hi alice and bob_123"


def test_strip_notification_mentions_preserves_command_examples() -> None:
    # /ai@MyBot should not have @MyBot stripped because @ follows a non-space char.
    # The current implementation strips only when preceded by no word/slash boundary;
    # ensure docs-style usage stays intact.
    assert strip_notification_mentions("Use /ai@MyBot to invoke") == "Use /ai@MyBot to invoke"


def test_strip_notification_mentions_empty_string() -> None:
    assert strip_notification_mentions("") == ""


def test_message_thread_id_defaults_to_zero() -> None:
    assert message_thread_id_for(_message()) == 0


def test_extract_text_prefers_caption_when_needed() -> None:
    assert extract_text(_message(text=None, caption="photo caption")) == "photo caption"


def test_clean_command_text_strips_command_prefix() -> None:
    assert clean_command_text("/ai@MyBot hello there", "ai", "MyBot") == "hello there"
