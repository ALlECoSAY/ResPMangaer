from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class TgUser:
    id: int
    is_bot: bool
    username: str | None
    first_name: str | None
    last_name: str | None
    language_code: str | None = None


@dataclass(frozen=True)
class TgChat:
    id: int
    type: str
    title: str | None
    username: str | None
    is_forum: bool = False


@dataclass(frozen=True)
class TgMessage:
    chat: TgChat
    message_id: int
    message_thread_id: int
    from_user: TgUser | None
    date: datetime
    text: str | None
    caption: str | None
    content_type: str
    reply_to_message_id: int | None
    reply_to_from_user: TgUser | None = None
    is_topic_message: bool = False
    topic_title: str | None = None


@dataclass(frozen=True)
class TgReactionUpdate:
    chat_id: int
    message_id: int
    user: TgUser | None
    old_emojis: list[str]
    new_emojis: list[str]


@dataclass(frozen=True)
class TgReactionActor:
    user: TgUser
    emojis: list[str]


@dataclass(frozen=True)
class TgMessageReactionSnapshot:
    chat_id: int
    message_id: int
    actors: list[TgReactionActor]
    counts: dict[str, int]
