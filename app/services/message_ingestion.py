from __future__ import annotations

from datetime import UTC, datetime

from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.commands import parse_command
from app.config import Settings
from app.db.repositories import (
    ChatInput,
    MessageInput,
    UserInput,
    insert_message,
    upsert_chat,
    upsert_thread,
    upsert_user,
)
from app.logging_config import get_logger
from app.utils.telegram import display_name

log = get_logger(__name__)


def _content_type(message: Message) -> str:
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


def _telegram_date(message: Message) -> datetime:
    if message.date is None:
        return datetime.now(UTC)
    if message.date.tzinfo is None:
        return message.date.replace(tzinfo=UTC)
    return message.date


def _clean_text(text: str | None, command_name: str | None, bot_username: str | None) -> str | None:
    if not text:
        return text
    if not command_name:
        return text
    head, _, rest = text.partition(" ")
    head_no_at = head.split("@", 1)[0]
    if head_no_at.lower() == f"/{command_name.lower()}":
        return rest.strip() or None
    return text


async def ingest_message(
    session: AsyncSession,
    message: Message,
    settings: Settings,
    bot_username: str | None,
) -> None:
    chat = message.chat
    if chat is None:
        return

    chat_thread_id = int(message.message_thread_id or 0)
    raw_text = message.text or message.caption
    parsed = parse_command(message.text, bot_username) if message.text else None
    command_name = parsed.command if parsed else None
    is_command = command_name is not None
    is_bot_message = bool(message.from_user and message.from_user.is_bot)

    if is_command and not settings.store_command_messages:
        return
    if is_bot_message and not settings.store_bot_messages:
        return

    await upsert_chat(
        session,
        ChatInput(
            id=chat.id,
            type=chat.type,
            title=chat.title,
            username=chat.username,
            is_forum=bool(getattr(chat, "is_forum", False)),
        ),
    )

    thread_title: str | None = None
    if message.is_topic_message and message.reply_to_message and message.reply_to_message.forum_topic_created:
        thread_title = message.reply_to_message.forum_topic_created.name
    elif message.forum_topic_created:
        thread_title = message.forum_topic_created.name

    telegram_date = _telegram_date(message)
    thread_pk = await upsert_thread(
        session,
        chat_id=chat.id,
        message_thread_id=chat_thread_id,
        title=thread_title,
        seen_at=telegram_date,
    )

    sender_id: int | None = None
    if message.from_user is not None:
        sender_id = message.from_user.id
        await upsert_user(
            session,
            UserInput(
                id=message.from_user.id,
                is_bot=bool(message.from_user.is_bot),
                username=message.from_user.username,
                first_name=message.from_user.first_name,
                last_name=message.from_user.last_name,
                language_code=message.from_user.language_code,
            ),
        )

    sender_name = display_name(message.from_user) if message.from_user else None
    clean = _clean_text(raw_text, command_name, bot_username)

    inserted = await insert_message(
        session,
        thread_id=thread_pk,
        data=MessageInput(
            chat_id=chat.id,
            message_id=message.message_id,
            message_thread_id=chat_thread_id,
            sender_user_id=sender_id,
            sender_display_name=sender_name,
            is_bot_message=is_bot_message,
            is_command=is_command,
            command_name=command_name,
            text=raw_text,
            clean_text=clean,
            caption=message.caption,
            content_type=_content_type(message),
            reply_to_message_id=(
                message.reply_to_message.message_id if message.reply_to_message else None
            ),
            telegram_date=telegram_date,
        ),
    )
    if inserted:
        log.debug(
            "ingest.stored",
            chat_id=chat.id,
            message_thread_id=chat_thread_id,
            message_id=message.message_id,
            command=command_name,
        )
