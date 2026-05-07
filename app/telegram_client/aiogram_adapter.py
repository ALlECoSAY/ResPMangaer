from __future__ import annotations

from datetime import UTC, datetime

from aiogram import Bot
from aiogram.types import Message, MessageReactionUpdated, ReactionTypeEmoji

from app.telegram_client.client import TelegramClientProtocol
from app.telegram_client.types import TgChat, TgMessage, TgReactionUpdate, TgUser


def user_from_aiogram(user) -> TgUser | None:
    if user is None:
        return None
    return TgUser(
        id=int(user.id),
        is_bot=bool(user.is_bot),
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        language_code=user.language_code,
    )


def chat_from_aiogram(chat) -> TgChat:
    return TgChat(
        id=int(chat.id),
        type=str(chat.type),
        title=chat.title,
        username=chat.username,
        is_forum=bool(getattr(chat, "is_forum", False)),
    )


def _topic_title_from_aiogram(message: Message) -> str | None:
    if message.is_topic_message and message.reply_to_message:
        created = getattr(message.reply_to_message, "forum_topic_created", None)
        if created is not None:
            return created.name
    created = getattr(message, "forum_topic_created", None)
    if created is not None:
        return created.name
    edited = getattr(message, "forum_topic_edited", None)
    if edited is not None:
        return edited.name
    return None


def _content_type_from_aiogram(message: Message) -> str:
    if message.text:
        return "text"
    if message.photo:
        return "photo"
    if message.video:
        return "video"
    if message.voice:
        return "voice"
    if message.audio:
        return "audio"
    if message.document:
        return "document"
    if message.sticker:
        return "sticker"
    if message.animation:
        return "animation"
    if message.poll:
        return "poll"
    if message.location:
        return "location"
    if message.new_chat_members or message.left_chat_member:
        return "service"
    return "other"


def _telegram_date(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def message_from_aiogram(message: Message) -> TgMessage:
    return TgMessage(
        chat=chat_from_aiogram(message.chat),
        message_id=int(message.message_id),
        message_thread_id=int(message.message_thread_id or 0),
        from_user=user_from_aiogram(message.from_user),
        date=_telegram_date(message.date),
        text=message.text,
        caption=message.caption,
        content_type=_content_type_from_aiogram(message),
        reply_to_message_id=(
            int(message.reply_to_message.message_id)
            if message.reply_to_message is not None
            else None
        ),
        reply_to_from_user=(
            user_from_aiogram(message.reply_to_message.from_user)
            if message.reply_to_message is not None
            else None
        ),
        is_topic_message=bool(message.is_topic_message),
        topic_title=_topic_title_from_aiogram(message),
    )


def _emoji_list(reactions: list) -> list[str]:
    emojis: list[str] = []
    for reaction in reactions or []:
        if getattr(reaction, "type", None) != "emoji":
            continue
        emoji = getattr(reaction, "emoji", None)
        if emoji:
            emojis.append(str(emoji))
    return emojis


def reaction_update_from_aiogram(event: MessageReactionUpdated) -> TgReactionUpdate:
    return TgReactionUpdate(
        chat_id=int(event.chat.id),
        message_id=int(event.message_id),
        user=user_from_aiogram(event.user),
        old_emojis=_emoji_list(event.old_reaction),
        new_emojis=_emoji_list(event.new_reaction),
    )


class AiogramTelegramClient(TelegramClientProtocol):
    def __init__(self, bot: Bot) -> None:
        self._bot = bot
        self._self_username: str | None = None

    async def get_self_username(self) -> str | None:
        if self._self_username is None:
            me = await self._bot.get_me()
            self._self_username = me.username
        return self._self_username

    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        reply_to_message_id: int | None = None,
        message_thread_id: int | None = None,
    ) -> TgMessage | None:
        kwargs: dict[str, int | str] = {"chat_id": chat_id, "text": text}
        if reply_to_message_id is not None:
            kwargs["reply_to_message_id"] = reply_to_message_id
        if message_thread_id is not None and message_thread_id > 0:
            kwargs["message_thread_id"] = message_thread_id
        sent = await self._bot.send_message(**kwargs)
        return message_from_aiogram(sent)

    async def send_typing(
        self,
        chat_id: int,
        *,
        message_thread_id: int | None = None,
    ) -> None:
        kwargs: dict[str, int | str] = {"chat_id": chat_id, "action": "typing"}
        if message_thread_id is not None and message_thread_id > 0:
            kwargs["message_thread_id"] = message_thread_id
        await self._bot.send_chat_action(**kwargs)

    async def set_reaction(
        self,
        chat_id: int,
        message_id: int,
        emoji: str,
    ) -> None:
        await self._bot.set_message_reaction(
            chat_id=chat_id,
            message_id=message_id,
            reaction=[ReactionTypeEmoji(emoji=emoji)],
        )
