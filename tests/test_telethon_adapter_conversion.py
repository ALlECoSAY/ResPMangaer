from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.telegram_client.telethon_adapter import (
    TelethonUserClient,
    reaction_emoji_from_telethon,
)


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


def test_reaction_emoji_from_telethon_extracts_emoji() -> None:
    reaction = SimpleNamespace(emoticon="🔥")
    assert reaction_emoji_from_telethon(reaction) == "🔥"


def test_reaction_emoji_from_telethon_returns_none_for_custom() -> None:
    # Custom emojis have no `emoticon`, only `document_id`.
    reaction = SimpleNamespace(document_id=123)
    assert reaction_emoji_from_telethon(reaction) is None


def test_reaction_emoji_from_telethon_handles_none() -> None:
    assert reaction_emoji_from_telethon(None) is None


def _make_peer_reaction(*, user_id: int, emoji: str | None, document_id: int | None = None):
    if emoji is not None:
        reaction = SimpleNamespace(emoticon=emoji)
    elif document_id is not None:
        reaction = SimpleNamespace(document_id=document_id)
    else:
        reaction = None
    return SimpleNamespace(
        peer_id=SimpleNamespace(user_id=user_id),
        reaction=reaction,
        date=datetime(2026, 5, 7, tzinfo=UTC),
    )


def _make_tl_user(user_id: int):
    return SimpleNamespace(
        id=user_id,
        bot=False,
        username=f"u{user_id}",
        first_name=f"U{user_id}",
        last_name=None,
        lang_code="en",
    )


class _FakeTelethonResponse:
    def __init__(self, reactions: list, users: list) -> None:
        self.reactions = reactions
        self.users = users


async def test_fetch_snapshot_groups_emojis_per_user(tmp_path: Path) -> None:
    client = TelethonUserClient(
        session_path=tmp_path / "test.session",
        api_id=1,
        api_hash="hash",
    )
    fake_raw = AsyncMock()
    fake_raw.get_input_entity = AsyncMock(return_value="peer-handle")

    response_fire = _FakeTelethonResponse(
        reactions=[
            _make_peer_reaction(user_id=1, emoji="🔥"),
            _make_peer_reaction(user_id=2, emoji="🔥"),
        ],
        users=[_make_tl_user(1), _make_tl_user(2)],
    )
    response_thumbs = _FakeTelethonResponse(
        reactions=[
            _make_peer_reaction(user_id=1, emoji="👍"),
        ],
        users=[_make_tl_user(1)],
    )

    fake_raw.side_effect = [response_fire, response_thumbs]
    client._client = fake_raw  # type: ignore[assignment]

    snap = await client.fetch_message_reaction_snapshot(
        chat_id=-1001,
        message_id=42,
        trigger_emojis=("🔥", "👍"),
        limit_per_emoji=50,
    )

    assert snap is not None
    assert snap.chat_id == -1001
    assert snap.message_id == 42
    by_user = {actor.user.id: sorted(actor.emojis) for actor in snap.actors}
    assert by_user == {1: sorted(["🔥", "👍"]), 2: ["🔥"]}
    assert snap.counts == {"🔥": 2, "👍": 1}


async def test_fetch_snapshot_skips_custom_reactions(tmp_path: Path) -> None:
    client = TelethonUserClient(
        session_path=tmp_path / "test.session",
        api_id=1,
        api_hash="hash",
    )
    fake_raw = AsyncMock()
    fake_raw.get_input_entity = AsyncMock(return_value="peer-handle")
    response = _FakeTelethonResponse(
        reactions=[
            _make_peer_reaction(user_id=1, emoji=None, document_id=42),
            _make_peer_reaction(user_id=2, emoji="🔥"),
        ],
        users=[_make_tl_user(1), _make_tl_user(2)],
    )
    fake_raw.side_effect = [response]
    client._client = fake_raw  # type: ignore[assignment]

    snap = await client.fetch_message_reaction_snapshot(
        chat_id=-1001,
        message_id=42,
        trigger_emojis=(),
    )
    assert snap is not None
    assert snap.counts == {"🔥": 1}
    assert len(snap.actors) == 1
    assert snap.actors[0].user.id == 2


async def test_fetch_snapshot_empty_response(tmp_path: Path) -> None:
    client = TelethonUserClient(
        session_path=tmp_path / "test.session",
        api_id=1,
        api_hash="hash",
    )
    fake_raw = AsyncMock()
    fake_raw.get_input_entity = AsyncMock(return_value="peer-handle")
    response = _FakeTelethonResponse(reactions=[], users=[])
    fake_raw.side_effect = [response]
    client._client = fake_raw  # type: ignore[assignment]

    snap = await client.fetch_message_reaction_snapshot(
        chat_id=-1001,
        message_id=42,
    )
    assert snap is not None
    assert snap.actors == []
    assert snap.counts == {}


async def test_fetch_snapshot_handles_request_failure(tmp_path: Path) -> None:
    client = TelethonUserClient(
        session_path=tmp_path / "test.session",
        api_id=1,
        api_hash="hash",
    )
    fake_raw = AsyncMock()
    fake_raw.get_input_entity = AsyncMock(return_value="peer-handle")
    fake_raw.side_effect = RuntimeError("permission denied")
    client._client = fake_raw  # type: ignore[assignment]

    snap = await client.fetch_message_reaction_snapshot(
        chat_id=-1001,
        message_id=42,
        trigger_emojis=("🔥",),
    )
    # Per-filter failure does not raise; snapshot is empty.
    assert snap is not None
    assert snap.actors == []
    assert snap.counts == {}


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
