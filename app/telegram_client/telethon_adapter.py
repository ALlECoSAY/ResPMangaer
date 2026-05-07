from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from telethon import TelegramClient
from telethon.tl import functions, types
from telethon.utils import get_peer_id

from app.logging_config import get_logger
from app.telegram_client.client import TelegramClientProtocol
from app.telegram_client.types import TgChat, TgMessage, TgUser

log = get_logger(__name__)


def user_from_telethon(user: Any) -> TgUser | None:
    if user is None:
        return None
    return TgUser(
        id=int(user.id),
        is_bot=bool(getattr(user, "bot", False)),
        username=getattr(user, "username", None),
        first_name=getattr(user, "first_name", None),
        last_name=getattr(user, "last_name", None),
        language_code=getattr(user, "lang_code", None),
    )


def _chat_type(chat: Any) -> str:
    if isinstance(chat, types.User):
        return "private"
    if isinstance(chat, types.Chat):
        return "group"
    if isinstance(chat, types.Channel):
        if getattr(chat, "broadcast", False):
            return "channel"
        return "supergroup"
    return "unknown"


def chat_from_telethon(chat: Any, *, fallback_id: int | None = None) -> TgChat:
    if chat is None and fallback_id is None:
        raise ValueError("Telethon message did not expose chat metadata.")
    title = getattr(chat, "title", None)
    username = getattr(chat, "username", None)
    return TgChat(
        id=int(fallback_id if fallback_id is not None else get_peer_id(chat)),
        type=_chat_type(chat),
        title=title,
        username=username,
        is_forum=bool(getattr(chat, "forum", False)),
    )


def _message_date(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _message_content_type(message: Any) -> str:
    if getattr(message, "text", None) and getattr(message, "media", None) is None:
        return "text"
    if getattr(message, "photo", None):
        return "photo"
    if getattr(message, "video", None):
        return "video"
    if getattr(message, "voice", None):
        return "voice"
    if getattr(message, "audio", None):
        return "audio"
    if getattr(message, "document", None):
        return "document"
    if getattr(message, "sticker", None):
        return "sticker"
    if getattr(message, "gif", None):
        return "animation"
    if getattr(message, "poll", None):
        return "poll"
    if getattr(message, "geo", None):
        return "location"
    if getattr(message, "action", None):
        return "service"
    return "other"


def _topic_title(message: Any) -> str | None:
    action = getattr(message, "action", None)
    if action is None:
        return None
    title = getattr(action, "title", None)
    if title:
        return str(title)
    return getattr(action, "name", None)


def _normalized_thread_id(message: Any) -> int:
    reply_to = getattr(message, "reply_to", None)
    reply_to_top_id = int(getattr(reply_to, "reply_to_top_id", 0) or 0)
    if reply_to_top_id:
        return reply_to_top_id

    top_msg_id = int(getattr(message, "reply_to_top_id", 0) or 0)
    if top_msg_id:
        return top_msg_id

    if type(getattr(message, "action", None)).__name__ == "MessageActionTopicCreate":
        return int(message.id)

    reply_to_msg_id = int(getattr(reply_to, "reply_to_msg_id", 0) or 0)
    if bool(getattr(reply_to, "forum_topic", False)) and reply_to_msg_id:
        return reply_to_msg_id

    return 0


class TelethonUserClient(TelegramClientProtocol):
    def __init__(
        self,
        *,
        session_path: Path,
        api_id: int,
        api_hash: str,
    ) -> None:
        self._client = TelegramClient(str(session_path), api_id, api_hash)
        self._self_username: str | None = None

    @property
    def raw_client(self) -> TelegramClient:
        return self._client

    async def connect(self) -> None:
        await self._client.connect()

    async def disconnect(self) -> None:
        await self._client.disconnect()

    async def is_authorized(self) -> bool:
        return await self._client.is_user_authorized()

    async def run_until_disconnected(self) -> None:
        await self._client.run_until_disconnected()

    async def get_self_username(self) -> str | None:
        if self._self_username is None:
            me = await self._client.get_me()
            self._self_username = getattr(me, "username", None)
        return self._self_username

    async def message_to_tg_message(self, message: Any) -> TgMessage:
        chat = getattr(message, "chat", None)
        if chat is None:
            chat = await message.get_chat()
        sender = getattr(message, "sender", None)
        if sender is None:
            sender = await message.get_sender()

        reply_to_message_id = int(getattr(message, "reply_to_msg_id", 0) or 0) or None
        reply_to_from_user: TgUser | None = None
        if reply_to_message_id is not None:
            try:
                reply_message = await message.get_reply_message()
            except Exception as exc:
                log.warning(
                    "telethon.reply_lookup_failed",
                    message_id=getattr(message, "id", None),
                    error=str(exc),
                )
            else:
                if reply_message is not None:
                    reply_sender = getattr(reply_message, "sender", None)
                    if reply_sender is None:
                        reply_sender = await reply_message.get_sender()
                    reply_to_from_user = user_from_telethon(reply_sender)

        thread_id = _normalized_thread_id(message)
        chat_id = int(getattr(message, "chat_id", 0) or get_peer_id(chat))

        log.debug(
            "telethon.thread_mapping",
            chat_id=chat_id,
            message_id=getattr(message, "id", None),
            message_thread_id=thread_id,
            reply_to_top_id=getattr(getattr(message, "reply_to", None), "reply_to_top_id", None),
            reply_to_msg_id=reply_to_message_id,
        )

        body = getattr(message, "message", None)
        content_type = _message_content_type(message)
        if content_type == "text":
            text = body
            caption = None
        else:
            text = None
            caption = body

        return TgMessage(
            chat=chat_from_telethon(chat, fallback_id=chat_id),
            message_id=int(message.id),
            message_thread_id=thread_id,
            from_user=user_from_telethon(sender),
            date=_message_date(getattr(message, "date", None)),
            text=text,
            caption=caption,
            content_type=content_type,
            reply_to_message_id=reply_to_message_id,
            reply_to_from_user=reply_to_from_user,
            is_topic_message=thread_id > 0,
            topic_title=_topic_title(message),
        )

    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        reply_to_message_id: int | None = None,
        message_thread_id: int | None = None,
    ) -> TgMessage | None:
        reply_target = reply_to_message_id
        if reply_target is None and message_thread_id is not None and message_thread_id > 0:
            reply_target = message_thread_id
        sent = await self._client.send_message(
            entity=chat_id,
            message=text,
            reply_to=reply_target,
        )
        return await self.message_to_tg_message(sent)

    async def send_typing(
        self,
        chat_id: int,
        *,
        message_thread_id: int | None = None,
    ) -> None:
        del message_thread_id
        async with self._client.action(chat_id, "typing"):
            await asyncio.sleep(0.2)

    async def set_reaction(
        self,
        chat_id: int,
        message_id: int,
        emoji: str,
    ) -> None:
        await self._client(
            functions.messages.SendReactionRequest(
                peer=chat_id,
                msg_id=message_id,
                big=False,
                add_to_recent=False,
                reaction=[types.ReactionEmoji(emoticon=emoji)],
            )
        )
