from __future__ import annotations

from datetime import UTC, datetime

from app.telegram_client.types import TgChat, TgMessage, TgUser
from app.utils.telegram import clean_command_text, display_name, extract_text, message_thread_id_for


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


def test_display_name_prefers_username() -> None:
    user = TgUser(1, False, "alice", "Alice", "Smith", "en")
    assert display_name(user) == "@alice"


def test_display_name_falls_back_to_full_name() -> None:
    user = TgUser(1, False, None, "Alice", "Smith", "en")
    assert display_name(user) == "Alice Smith"


def test_message_thread_id_defaults_to_zero() -> None:
    assert message_thread_id_for(_message()) == 0


def test_extract_text_prefers_caption_when_needed() -> None:
    assert extract_text(_message(text=None, caption="photo caption")) == "photo caption"


def test_clean_command_text_strips_command_prefix() -> None:
    assert clean_command_text("/ai@MyBot hello there", "ai", "MyBot") == "hello there"
