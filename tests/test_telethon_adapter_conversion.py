from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from app.telegram_client.telethon_adapter import TelethonUserClient


class _FakeTelethonMessage:
    def __init__(
        self,
        *,
        message_id: int,
        chat_id: int,
        text: str | None,
        media=None,
        reply_to_msg_id: int | None = None,
        reply_to_top_id: int | None = None,
        reply_message=None,
    ) -> None:
        self.id = message_id
        self.chat_id = chat_id
        self.message = text
        self.media = media
        self.date = datetime(2026, 5, 7, tzinfo=UTC)
        self.chat = SimpleNamespace(title="Test Chat", username=None, forum=True)
        self.sender = SimpleNamespace(
            id=42,
            bot=False,
            username="alice",
            first_name="Alice",
            last_name=None,
            lang_code="en",
        )
        self.reply_to_msg_id = reply_to_msg_id
        self.reply_to = SimpleNamespace(
            reply_to_top_id=reply_to_top_id,
            reply_to_msg_id=reply_to_msg_id,
            forum_topic=bool(reply_to_top_id or reply_to_msg_id),
        )
        self._reply_message = reply_message
        self.photo = media if media == "photo" else None
        self.video = None
        self.voice = None
        self.audio = None
        self.document = None
        self.sticker = None
        self.gif = None
        self.poll = None
        self.geo = None
        self.action = None

    async def get_chat(self):
        return self.chat

    async def get_sender(self):
        return self.sender

    async def get_reply_message(self):
        return self._reply_message


class _FakeReplyMessage:
    def __init__(self) -> None:
        self.sender = SimpleNamespace(
            id=77,
            bot=False,
            username="reply_target",
            first_name="Reply",
            last_name="Target",
            lang_code="en",
        )

    async def get_sender(self):
        return self.sender


async def test_text_message_converts_cleanly(tmp_path: Path) -> None:
    client = TelethonUserClient(
        session_path=tmp_path / "test.session",
        api_id=1,
        api_hash="hash",
    )
    message = _FakeTelethonMessage(message_id=11, chat_id=-1001, text="hello")

    converted = await client.message_to_tg_message(message)

    assert converted.text == "hello"
    assert converted.caption is None
    assert converted.message_thread_id == 0


async def test_caption_message_converts_cleanly(tmp_path: Path) -> None:
    client = TelethonUserClient(
        session_path=tmp_path / "test.session",
        api_id=1,
        api_hash="hash",
    )
    message = _FakeTelethonMessage(
        message_id=12,
        chat_id=-1001,
        text="caption",
        media="photo",
    )

    converted = await client.message_to_tg_message(message)

    assert converted.text is None
    assert converted.caption == "caption"
    assert converted.content_type == "photo"


async def test_reply_target_user_and_message_id_are_preserved(tmp_path: Path) -> None:
    client = TelethonUserClient(
        session_path=tmp_path / "test.session",
        api_id=1,
        api_hash="hash",
    )
    message = _FakeTelethonMessage(
        message_id=13,
        chat_id=-1001,
        text="reply",
        reply_to_msg_id=99,
        reply_message=_FakeReplyMessage(),
    )

    converted = await client.message_to_tg_message(message)

    assert converted.reply_to_message_id == 99
    assert converted.reply_to_from_user is not None
    assert converted.reply_to_from_user.id == 77


async def test_topic_messages_use_stable_normalized_thread_id(tmp_path: Path) -> None:
    client = TelethonUserClient(
        session_path=tmp_path / "test.session",
        api_id=1,
        api_hash="hash",
    )
    message = _FakeTelethonMessage(
        message_id=14,
        chat_id=-1001,
        text="topic message",
        reply_to_msg_id=101,
        reply_to_top_id=500,
    )

    converted = await client.message_to_tg_message(message)

    assert converted.message_thread_id == 500
    assert converted.is_topic_message is True
