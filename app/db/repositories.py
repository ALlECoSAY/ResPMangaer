from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    LlmInteraction,
    TelegramChat,
    TelegramMessage,
    TelegramMessageReaction,
    TelegramThread,
    TelegramUser,
)


@dataclass(frozen=True)
class ChatInput:
    id: int
    type: str
    title: str | None
    username: str | None
    is_forum: bool


@dataclass(frozen=True)
class UserInput:
    id: int
    is_bot: bool
    username: str | None
    first_name: str | None
    last_name: str | None
    language_code: str | None


@dataclass(frozen=True)
class MessageInput:
    chat_id: int
    message_id: int
    message_thread_id: int
    sender_user_id: int | None
    sender_display_name: str | None
    is_bot_message: bool
    is_command: bool
    command_name: str | None
    text: str | None
    clean_text: str | None
    caption: str | None
    content_type: str
    reply_to_message_id: int | None
    telegram_date: datetime


async def upsert_chat(session: AsyncSession, data: ChatInput) -> None:
    stmt = pg_insert(TelegramChat).values(
        id=data.id,
        type=data.type,
        title=data.title,
        username=data.username,
        is_forum=data.is_forum,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[TelegramChat.id],
        set_={
            "type": stmt.excluded.type,
            "title": stmt.excluded.title,
            "username": stmt.excluded.username,
            "is_forum": stmt.excluded.is_forum,
        },
    )
    await session.execute(stmt)


async def upsert_user(session: AsyncSession, data: UserInput) -> None:
    stmt = pg_insert(TelegramUser).values(
        id=data.id,
        is_bot=data.is_bot,
        username=data.username,
        first_name=data.first_name,
        last_name=data.last_name,
        language_code=data.language_code,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[TelegramUser.id],
        set_={
            "is_bot": stmt.excluded.is_bot,
            "username": stmt.excluded.username,
            "first_name": stmt.excluded.first_name,
            "last_name": stmt.excluded.last_name,
            "language_code": stmt.excluded.language_code,
        },
    )
    await session.execute(stmt)


async def upsert_thread(
    session: AsyncSession,
    chat_id: int,
    message_thread_id: int,
    title: str | None,
    seen_at: datetime,
) -> int:
    stmt = pg_insert(TelegramThread).values(
        chat_id=chat_id,
        message_thread_id=message_thread_id,
        title=title,
        first_seen_at=seen_at,
        last_seen_at=seen_at,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[TelegramThread.chat_id, TelegramThread.message_thread_id],
        set_={
            "last_seen_at": stmt.excluded.last_seen_at,
            "title": stmt.excluded.title,
        },
    ).returning(TelegramThread.id)
    result = await session.execute(stmt)
    row = result.first()
    if row is not None:
        return int(row[0])
    fallback = await session.execute(
        select(TelegramThread.id).where(
            TelegramThread.chat_id == chat_id,
            TelegramThread.message_thread_id == message_thread_id,
        )
    )
    value = fallback.scalar_one()
    return int(value)


async def insert_message(
    session: AsyncSession, thread_id: int, data: MessageInput
) -> bool:
    stmt = pg_insert(TelegramMessage).values(
        chat_id=data.chat_id,
        thread_id=thread_id,
        message_id=data.message_id,
        message_thread_id=data.message_thread_id,
        sender_user_id=data.sender_user_id,
        sender_display_name=data.sender_display_name,
        is_bot_message=data.is_bot_message,
        is_command=data.is_command,
        command_name=data.command_name,
        text=data.text,
        clean_text=data.clean_text,
        caption=data.caption,
        content_type=data.content_type,
        reply_to_message_id=data.reply_to_message_id,
        telegram_date=data.telegram_date,
    )
    stmt = stmt.on_conflict_do_nothing(
        index_elements=[TelegramMessage.chat_id, TelegramMessage.message_id]
    ).returning(TelegramMessage.id)
    result = await session.execute(stmt)
    return result.first() is not None


async def fetch_recent_same_thread(
    session: AsyncSession,
    chat_id: int,
    message_thread_id: int,
    limit: int,
) -> list[TelegramMessage]:
    stmt = (
        select(TelegramMessage)
        .where(
            TelegramMessage.chat_id == chat_id,
            TelegramMessage.message_thread_id == message_thread_id,
        )
        .order_by(TelegramMessage.telegram_date.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def fetch_recent_cross_thread(
    session: AsyncSession,
    chat_id: int,
    exclude_thread_id: int,
    limit: int,
    since: datetime | None = None,
) -> list[TelegramMessage]:
    stmt = select(TelegramMessage).where(
        TelegramMessage.chat_id == chat_id,
        TelegramMessage.message_thread_id != exclude_thread_id,
    )
    if since is not None:
        stmt = stmt.where(TelegramMessage.telegram_date >= since)
    stmt = stmt.order_by(TelegramMessage.telegram_date.desc()).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def fetch_messages_for_tldr(
    session: AsyncSession,
    chat_id: int,
    lookback_hours: int,
    exclude_thread_id: int | None,
    only_thread_id: int | None,
    max_messages: int = 4000,
) -> list[TelegramMessage]:
    since = datetime.now(UTC) - timedelta(hours=lookback_hours)
    stmt = select(TelegramMessage).where(
        TelegramMessage.chat_id == chat_id,
        TelegramMessage.telegram_date >= since,
    )
    if only_thread_id is not None:
        stmt = stmt.where(TelegramMessage.message_thread_id == only_thread_id)
    elif exclude_thread_id is not None:
        stmt = stmt.where(TelegramMessage.message_thread_id != exclude_thread_id)
    stmt = stmt.order_by(TelegramMessage.telegram_date.asc()).limit(max_messages)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_thread_titles(
    session: AsyncSession, chat_id: int
) -> dict[int, str | None]:
    stmt = select(TelegramThread.message_thread_id, TelegramThread.title).where(
        TelegramThread.chat_id == chat_id
    )
    result = await session.execute(stmt)
    return {int(row[0]): row[1] for row in result.all()}


async def replace_user_reactions(
    session: AsyncSession,
    chat_id: int,
    message_id: int,
    user_id: int,
    new_emojis: list[str],
) -> None:
    """Replace the set of reactions a single user has on one message.

    Telegram delivers ``MessageReactionUpdated`` with the user's full new set;
    we mirror that in the DB by deleting their previous rows and inserting
    fresh ones.
    """
    await session.execute(
        delete(TelegramMessageReaction).where(
            and_(
                TelegramMessageReaction.chat_id == chat_id,
                TelegramMessageReaction.message_id == message_id,
                TelegramMessageReaction.user_id == user_id,
            )
        )
    )
    if not new_emojis:
        return
    rows = [
        {
            "chat_id": chat_id,
            "message_id": message_id,
            "user_id": user_id,
            "emoji": emoji,
        }
        for emoji in new_emojis
    ]
    stmt = pg_insert(TelegramMessageReaction).values(rows)
    stmt = stmt.on_conflict_do_nothing(
        index_elements=[
            TelegramMessageReaction.chat_id,
            TelegramMessageReaction.message_id,
            TelegramMessageReaction.user_id,
            TelegramMessageReaction.emoji,
        ]
    )
    await session.execute(stmt)


async def count_distinct_reaction_users(
    session: AsyncSession,
    chat_id: int,
    message_id: int,
    only_emojis: list[str] | None = None,
) -> int:
    stmt = select(func.count(func.distinct(TelegramMessageReaction.user_id))).where(
        TelegramMessageReaction.chat_id == chat_id,
        TelegramMessageReaction.message_id == message_id,
    )
    if only_emojis:
        stmt = stmt.where(TelegramMessageReaction.emoji.in_(only_emojis))
    result = await session.execute(stmt)
    return int(result.scalar_one() or 0)


async def fetch_message_by_chat_message_id(
    session: AsyncSession,
    chat_id: int,
    message_id: int,
) -> TelegramMessage | None:
    stmt = select(TelegramMessage).where(
        TelegramMessage.chat_id == chat_id,
        TelegramMessage.message_id == message_id,
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def fetch_messages_around(
    session: AsyncSession,
    chat_id: int,
    message_thread_id: int,
    target_telegram_date: datetime,
    target_message_id: int,
    before: int,
    after: int,
) -> tuple[list[TelegramMessage], list[TelegramMessage]]:
    """Fetch ``before`` messages prior to and ``after`` messages following the
    target message in the same chat+thread.

    Ordering uses (telegram_date, message_id) so duplicates on the second
    boundary are stable. The returned lists are in chronological order.
    """
    before_rows: list[TelegramMessage] = []
    if before > 0:
        stmt = (
            select(TelegramMessage)
            .where(
                TelegramMessage.chat_id == chat_id,
                TelegramMessage.message_thread_id == message_thread_id,
                TelegramMessage.message_id != target_message_id,
            )
            .where(
                (TelegramMessage.telegram_date < target_telegram_date)
                | (
                    (TelegramMessage.telegram_date == target_telegram_date)
                    & (TelegramMessage.message_id < target_message_id)
                )
            )
            .order_by(
                TelegramMessage.telegram_date.desc(),
                TelegramMessage.message_id.desc(),
            )
            .limit(before)
        )
        result = await session.execute(stmt)
        before_rows = list(reversed(result.scalars().all()))

    after_rows: list[TelegramMessage] = []
    if after > 0:
        stmt = (
            select(TelegramMessage)
            .where(
                TelegramMessage.chat_id == chat_id,
                TelegramMessage.message_thread_id == message_thread_id,
                TelegramMessage.message_id != target_message_id,
            )
            .where(
                (TelegramMessage.telegram_date > target_telegram_date)
                | (
                    (TelegramMessage.telegram_date == target_telegram_date)
                    & (TelegramMessage.message_id > target_message_id)
                )
            )
            .order_by(
                TelegramMessage.telegram_date.asc(),
                TelegramMessage.message_id.asc(),
            )
            .limit(after)
        )
        result = await session.execute(stmt)
        after_rows = list(result.scalars().all())

    return before_rows, after_rows


async def record_llm_interaction(
    session: AsyncSession,
    chat_id: int,
    message_thread_id: int,
    request_message_id: int | None,
    command_name: str,
    model: str,
    prompt_tokens_estimate: int | None,
    completion_tokens_estimate: int | None,
    latency_ms: int | None,
    success: bool,
    error: str | None,
) -> None:
    session.add(
        LlmInteraction(
            chat_id=chat_id,
            message_thread_id=message_thread_id,
            request_message_id=request_message_id,
            command_name=command_name,
            model=model,
            prompt_tokens_estimate=prompt_tokens_estimate,
            completion_tokens_estimate=completion_tokens_estimate,
            latency_ms=latency_ms,
            success=success,
            error=error,
        )
    )
