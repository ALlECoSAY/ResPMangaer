from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, case, delete, false, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    LlmInteraction,
    MemoryChatProfile,
    MemoryThreadProfile,
    MemoryUserProfile,
    TelegramActivityReplyState,
    TelegramChat,
    TelegramMessage,
    TelegramMessageReaction,
    TelegramReactionState,
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
class UserDisplay:
    user_id: int
    username: str | None
    display_name: str


@dataclass(frozen=True)
class ReactedMessageStat:
    message_id: int
    message_thread_id: int
    count: int
    preview: str | None


@dataclass(frozen=True)
class ChatMemoryProfile:
    chat_id: int
    summary: str | None
    stable_facts: list | dict | None
    current_projects: list | dict | None
    decisions: list | dict | None
    open_questions: list | dict | None
    source_until_message_id: int | None
    source_until_date: datetime | None
    updated_at: datetime | None


@dataclass(frozen=True)
class ThreadMemoryProfile:
    chat_id: int
    message_thread_id: int
    title: str | None
    summary: str | None
    decisions: list | dict | None
    action_items: list | dict | None
    open_questions: list | dict | None
    key_participants: list | dict | None
    source_until_message_id: int | None
    source_until_date: datetime | None
    updated_at: datetime | None


@dataclass(frozen=True)
class UserMemoryProfile:
    chat_id: int
    user_id: int
    display_name: str | None
    aliases: list | dict | None
    profile_summary: str | None
    expertise: list | dict | None
    stated_preferences: list | dict | None
    interaction_style: str | None
    evidence_message_ids: list | dict | None
    confidence: float | None
    source_until_message_id: int | None
    updated_at: datetime | None


@dataclass(frozen=True)
class MemoryRefreshCandidate:
    chat_id: int
    message_thread_id: int
    new_message_count: int
    latest_message_id: int | None
    latest_message_date: datetime | None


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


async def fetch_recent_message_count(
    session: AsyncSession,
    chat_id: int,
    message_thread_id: int,
    since: datetime,
    *,
    exclude_bot_messages: bool = True,
    exclude_commands: bool = True,
) -> int:
    stmt = select(func.count(TelegramMessage.id)).where(
        TelegramMessage.chat_id == chat_id,
        TelegramMessage.message_thread_id == message_thread_id,
        TelegramMessage.telegram_date >= since,
    )
    if exclude_bot_messages:
        stmt = stmt.where(TelegramMessage.is_bot_message.is_(False))
    if exclude_commands:
        stmt = stmt.where(TelegramMessage.is_command.is_(False))
    result = await session.execute(stmt)
    return int(result.scalar_one() or 0)


async def fetch_last_messages(
    session: AsyncSession,
    chat_id: int,
    message_thread_id: int,
    limit: int,
    *,
    since: datetime | None = None,
) -> list[TelegramMessage]:
    stmt = select(TelegramMessage).where(
        TelegramMessage.chat_id == chat_id,
        TelegramMessage.message_thread_id == message_thread_id,
    )
    if since is not None:
        stmt = stmt.where(TelegramMessage.telegram_date >= since)
    stmt = (
        stmt.order_by(
            TelegramMessage.telegram_date.desc(),
            TelegramMessage.message_id.desc(),
        )
        .limit(limit)
    )
    result = await session.execute(stmt)
    return list(reversed(result.scalars().all()))


async def fetch_active_threads(
    session: AsyncSession,
    chat_ids: list[int] | None,
    since: datetime,
    min_messages: int,
    limit: int,
) -> list[tuple[int, int, int]]:
    count_expr = func.count(TelegramMessage.id)
    stmt = (
        select(
            TelegramMessage.chat_id,
            TelegramMessage.message_thread_id,
            count_expr.label("message_count"),
        )
        .where(
            TelegramMessage.telegram_date >= since,
            TelegramMessage.is_bot_message.is_(False),
            TelegramMessage.is_command.is_(False),
        )
        .group_by(TelegramMessage.chat_id, TelegramMessage.message_thread_id)
        .having(count_expr >= min_messages)
        .order_by(count_expr.desc())
        .limit(limit)
    )
    if chat_ids:
        stmt = stmt.where(TelegramMessage.chat_id.in_(chat_ids))
    result = await session.execute(stmt)
    return [(int(row[0]), int(row[1]), int(row[2])) for row in result.all()]


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


def _message_stats_filter(chat_id: int, since: datetime | None):
    conditions = [TelegramMessage.chat_id == chat_id]
    if since is not None:
        conditions.append(TelegramMessage.telegram_date >= since)
    return conditions


async def count_messages(
    session: AsyncSession,
    chat_id: int,
    since: datetime | None,
) -> int:
    stmt = select(func.count(TelegramMessage.id)).where(
        *_message_stats_filter(chat_id, since)
    )
    result = await session.execute(stmt)
    return int(result.scalar_one() or 0)


async def count_messages_by_user(
    session: AsyncSession,
    chat_id: int,
    since: datetime | None,
) -> list[tuple[int, int]]:
    stmt = (
        select(TelegramMessage.sender_user_id, func.count(TelegramMessage.id).label("count"))
        .where(
            *_message_stats_filter(chat_id, since),
            TelegramMessage.sender_user_id.is_not(None),
        )
        .group_by(TelegramMessage.sender_user_id)
        .order_by(func.count(TelegramMessage.id).desc())
    )
    result = await session.execute(stmt)
    return [(int(row[0]), int(row[1])) for row in result.all()]


async def fetch_user_display_names(
    session: AsyncSession,
    user_ids: list[int],
) -> dict[int, str]:
    displays = await fetch_user_displays(session, user_ids)
    return {user_id: display.display_name for user_id, display in displays.items()}


async def fetch_user_displays(
    session: AsyncSession,
    user_ids: list[int],
) -> dict[int, UserDisplay]:
    if not user_ids:
        return {}
    stmt = select(
        TelegramUser.id,
        TelegramUser.username,
        TelegramUser.first_name,
        TelegramUser.last_name,
    ).where(TelegramUser.id.in_(user_ids))
    result = await session.execute(stmt)
    displays: dict[int, UserDisplay] = {}
    for user_id, username, first_name, last_name in result.all():
        name_parts = [part for part in (first_name, last_name) if part]
        if name_parts:
            label = " ".join(name_parts)
        elif username:
            label = str(username)
        else:
            label = f"user {int(user_id)}"
        displays[int(user_id)] = UserDisplay(
            user_id=int(user_id),
            username=str(username) if username else None,
            display_name=label,
        )
    return displays


async def count_messages_by_hour(
    session: AsyncSession,
    chat_id: int,
    since: datetime | None,
) -> dict[int, int]:
    hour = func.extract("hour", TelegramMessage.telegram_date)
    stmt = (
        select(hour.label("hour"), func.count(TelegramMessage.id))
        .where(*_message_stats_filter(chat_id, since))
        .group_by(hour)
        .order_by(hour)
    )
    result = await session.execute(stmt)
    return {int(row[0]): int(row[1]) for row in result.all()}


async def count_messages_by_weekday(
    session: AsyncSession,
    chat_id: int,
    since: datetime | None,
) -> dict[int, int]:
    weekday = func.extract("dow", TelegramMessage.telegram_date)
    stmt = (
        select(weekday.label("weekday"), func.count(TelegramMessage.id))
        .where(*_message_stats_filter(chat_id, since))
        .group_by(weekday)
        .order_by(weekday)
    )
    result = await session.execute(stmt)
    return {int(row[0]): int(row[1]) for row in result.all()}


async def count_commands_by_name(
    session: AsyncSession,
    chat_id: int,
    since: datetime | None,
) -> dict[str, int]:
    stmt = (
        select(TelegramMessage.command_name, func.count(TelegramMessage.id))
        .where(
            *_message_stats_filter(chat_id, since),
            TelegramMessage.is_command.is_(True),
            TelegramMessage.command_name.is_not(None),
        )
        .group_by(TelegramMessage.command_name)
        .order_by(func.count(TelegramMessage.id).desc())
    )
    result = await session.execute(stmt)
    return {str(row[0]): int(row[1]) for row in result.all()}


async def count_reactions(
    session: AsyncSession,
    chat_id: int,
    since: datetime | None,
) -> list[tuple[str, int]]:
    stmt = (
        select(TelegramMessageReaction.emoji, func.count(TelegramMessageReaction.id))
        .join(
            TelegramMessage,
            and_(
                TelegramMessage.chat_id == TelegramMessageReaction.chat_id,
                TelegramMessage.message_id == TelegramMessageReaction.message_id,
            ),
        )
        .where(*_message_stats_filter(chat_id, since))
        .group_by(TelegramMessageReaction.emoji)
        .order_by(func.count(TelegramMessageReaction.id).desc())
    )
    result = await session.execute(stmt)
    return [(str(row[0]), int(row[1])) for row in result.all()]


async def top_reacted_messages(
    session: AsyncSession,
    chat_id: int,
    since: datetime | None,
    limit: int,
) -> list[ReactedMessageStat]:
    preview = func.coalesce(
        TelegramMessage.clean_text,
        TelegramMessage.text,
        TelegramMessage.caption,
    )
    stmt = (
        select(
            TelegramMessageReaction.message_id,
            TelegramMessage.message_thread_id,
            func.count(TelegramMessageReaction.id),
            preview,
        )
        .join(
            TelegramMessage,
            and_(
                TelegramMessage.chat_id == TelegramMessageReaction.chat_id,
                TelegramMessage.message_id == TelegramMessageReaction.message_id,
            ),
        )
        .where(*_message_stats_filter(chat_id, since))
        .group_by(
            TelegramMessageReaction.message_id,
            TelegramMessage.message_thread_id,
            preview,
        )
        .order_by(func.count(TelegramMessageReaction.id).desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return [
        ReactedMessageStat(
            message_id=int(row[0]),
            message_thread_id=int(row[1]),
            count=int(row[2]),
            preview=str(row[3]) if row[3] else None,
        )
        for row in result.all()
    ]


async def fetch_messages_for_word_stats(
    session: AsyncSession,
    chat_id: int,
    since: datetime | None,
) -> list[str]:
    stmt = (
        select(
            func.coalesce(
                TelegramMessage.clean_text,
                TelegramMessage.text,
                TelegramMessage.caption,
            )
        )
        .where(*_message_stats_filter(chat_id, since))
        .order_by(TelegramMessage.telegram_date.asc())
    )
    result = await session.execute(stmt)
    return [str(row[0]) for row in result.all() if row[0]]


async def count_media_types(
    session: AsyncSession,
    chat_id: int,
    since: datetime | None,
) -> dict[str, int]:
    stmt = (
        select(TelegramMessage.content_type, func.count(TelegramMessage.id))
        .where(*_message_stats_filter(chat_id, since))
        .group_by(TelegramMessage.content_type)
        .order_by(func.count(TelegramMessage.id).desc())
    )
    result = await session.execute(stmt)
    return {str(row[0]): int(row[1]) for row in result.all()}


async def count_threads(
    session: AsyncSession,
    chat_id: int,
    since: datetime | None,
) -> list[tuple[int, int]]:
    stmt = (
        select(TelegramMessage.message_thread_id, func.count(TelegramMessage.id))
        .where(*_message_stats_filter(chat_id, since))
        .group_by(TelegramMessage.message_thread_id)
        .order_by(func.count(TelegramMessage.id).desc())
    )
    result = await session.execute(stmt)
    return [(int(row[0]), int(row[1])) for row in result.all()]


async def thread_starters(
    session: AsyncSession,
    chat_id: int,
    since: datetime | None,
) -> list[tuple[int, int]]:
    stmt = (
        select(TelegramMessage.sender_user_id, func.count(TelegramMessage.id))
        .where(
            *_message_stats_filter(chat_id, since),
            TelegramMessage.sender_user_id.is_not(None),
            TelegramMessage.reply_to_message_id.is_(None),
        )
        .group_by(TelegramMessage.sender_user_id)
        .order_by(func.count(TelegramMessage.id).desc())
    )
    result = await session.execute(stmt)
    return [(int(row[0]), int(row[1])) for row in result.all()]


async def llm_usage_stats(
    session: AsyncSession,
    chat_id: int,
    since: datetime | None,
) -> tuple[int, int, float]:
    token_total = func.coalesce(
        func.sum(
            func.coalesce(LlmInteraction.prompt_tokens_estimate, 0)
            + func.coalesce(LlmInteraction.completion_tokens_estimate, 0)
        ),
        0,
    )
    stmt = select(
        func.count(LlmInteraction.id),
        token_total,
        func.avg(LlmInteraction.latency_ms),
    ).where(LlmInteraction.chat_id == chat_id)
    if since is not None:
        stmt = stmt.where(LlmInteraction.created_at >= since)
    result = await session.execute(stmt)
    calls, tokens, avg_latency = result.one()
    return int(calls or 0), int(tokens or 0), float(avg_latency or 0.0)


async def get_thread_titles(
    session: AsyncSession, chat_id: int
) -> dict[int, str | None]:
    stmt = select(TelegramThread.message_thread_id, TelegramThread.title).where(
        TelegramThread.chat_id == chat_id
    )
    result = await session.execute(stmt)
    return {int(row[0]): row[1] for row in result.all()}


def _chat_memory_from_row(row: MemoryChatProfile) -> ChatMemoryProfile:
    return ChatMemoryProfile(
        chat_id=int(row.chat_id),
        summary=row.summary,
        stable_facts=row.stable_facts,
        current_projects=row.current_projects,
        decisions=row.decisions,
        open_questions=row.open_questions,
        source_until_message_id=(
            int(row.source_until_message_id)
            if row.source_until_message_id is not None
            else None
        ),
        source_until_date=row.source_until_date,
        updated_at=row.updated_at,
    )


def _thread_memory_from_row(row: MemoryThreadProfile) -> ThreadMemoryProfile:
    return ThreadMemoryProfile(
        chat_id=int(row.chat_id),
        message_thread_id=int(row.message_thread_id),
        title=row.title,
        summary=row.summary,
        decisions=row.decisions,
        action_items=row.action_items,
        open_questions=row.open_questions,
        key_participants=row.key_participants,
        source_until_message_id=(
            int(row.source_until_message_id)
            if row.source_until_message_id is not None
            else None
        ),
        source_until_date=row.source_until_date,
        updated_at=row.updated_at,
    )


def _user_memory_from_row(row: MemoryUserProfile) -> UserMemoryProfile:
    return UserMemoryProfile(
        chat_id=int(row.chat_id),
        user_id=int(row.user_id),
        display_name=row.display_name,
        aliases=row.aliases,
        profile_summary=row.profile_summary,
        expertise=row.expertise,
        stated_preferences=row.stated_preferences,
        interaction_style=row.interaction_style,
        evidence_message_ids=row.evidence_message_ids,
        confidence=float(row.confidence) if row.confidence is not None else None,
        source_until_message_id=(
            int(row.source_until_message_id)
            if row.source_until_message_id is not None
            else None
        ),
        updated_at=row.updated_at,
    )


async def get_chat_memory(
    session: AsyncSession,
    chat_id: int,
) -> ChatMemoryProfile | None:
    result = await session.execute(
        select(MemoryChatProfile).where(MemoryChatProfile.chat_id == chat_id)
    )
    row = result.scalar_one_or_none()
    return _chat_memory_from_row(row) if row is not None else None


async def upsert_chat_memory(
    session: AsyncSession,
    *,
    chat_id: int,
    summary: str | None,
    stable_facts: list | dict,
    current_projects: list | dict,
    decisions: list | dict,
    open_questions: list | dict,
    source_until_message_id: int | None,
    source_until_date: datetime | None,
) -> None:
    values = {
        "chat_id": chat_id,
        "summary": summary,
        "stable_facts": stable_facts,
        "current_projects": current_projects,
        "decisions": decisions,
        "open_questions": open_questions,
        "source_until_message_id": source_until_message_id,
        "source_until_date": source_until_date,
    }
    stmt = pg_insert(MemoryChatProfile).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[MemoryChatProfile.chat_id],
        set_={
            **values,
            "updated_at": func.now(),
        },
    )
    await session.execute(stmt)


async def get_thread_memory(
    session: AsyncSession,
    chat_id: int,
    message_thread_id: int,
) -> ThreadMemoryProfile | None:
    result = await session.execute(
        select(MemoryThreadProfile).where(
            MemoryThreadProfile.chat_id == chat_id,
            MemoryThreadProfile.message_thread_id == message_thread_id,
        )
    )
    row = result.scalar_one_or_none()
    return _thread_memory_from_row(row) if row is not None else None


async def upsert_thread_memory(
    session: AsyncSession,
    *,
    chat_id: int,
    message_thread_id: int,
    title: str | None,
    summary: str | None,
    decisions: list | dict,
    action_items: list | dict,
    open_questions: list | dict,
    key_participants: list | dict,
    source_until_message_id: int | None,
    source_until_date: datetime | None,
) -> None:
    values = {
        "chat_id": chat_id,
        "message_thread_id": message_thread_id,
        "title": title,
        "summary": summary,
        "decisions": decisions,
        "action_items": action_items,
        "open_questions": open_questions,
        "key_participants": key_participants,
        "source_until_message_id": source_until_message_id,
        "source_until_date": source_until_date,
    }
    stmt = pg_insert(MemoryThreadProfile).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[
            MemoryThreadProfile.chat_id,
            MemoryThreadProfile.message_thread_id,
        ],
        set_={
            **values,
            "updated_at": func.now(),
        },
    )
    await session.execute(stmt)


async def get_user_memory(
    session: AsyncSession,
    chat_id: int,
    user_id: int,
) -> UserMemoryProfile | None:
    result = await session.execute(
        select(MemoryUserProfile).where(
            MemoryUserProfile.chat_id == chat_id,
            MemoryUserProfile.user_id == user_id,
        )
    )
    row = result.scalar_one_or_none()
    return _user_memory_from_row(row) if row is not None else None


async def fetch_user_memories_for_prompt(
    session: AsyncSession,
    chat_id: int,
    user_ids: list[int],
    limit: int,
) -> list[UserMemoryProfile]:
    if not user_ids or limit <= 0:
        return []
    stmt = (
        select(MemoryUserProfile)
        .where(
            MemoryUserProfile.chat_id == chat_id,
            MemoryUserProfile.user_id.in_(user_ids),
        )
        .order_by(
            MemoryUserProfile.confidence.desc().nullslast(),
            MemoryUserProfile.updated_at.desc(),
        )
        .limit(limit)
    )
    result = await session.execute(stmt)
    return [_user_memory_from_row(row) for row in result.scalars().all()]


async def upsert_user_memory(
    session: AsyncSession,
    *,
    chat_id: int,
    user_id: int,
    display_name: str | None,
    aliases: list | dict,
    profile_summary: str | None,
    expertise: list | dict,
    stated_preferences: list | dict,
    interaction_style: str | None,
    evidence_message_ids: list | dict,
    confidence: float,
    source_until_message_id: int | None,
) -> None:
    values = {
        "chat_id": chat_id,
        "user_id": user_id,
        "display_name": display_name,
        "aliases": aliases,
        "profile_summary": profile_summary,
        "expertise": expertise,
        "stated_preferences": stated_preferences,
        "interaction_style": interaction_style,
        "evidence_message_ids": evidence_message_ids,
        "confidence": confidence,
        "source_until_message_id": source_until_message_id,
    }
    stmt = pg_insert(MemoryUserProfile).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[MemoryUserProfile.chat_id, MemoryUserProfile.user_id],
        set_={
            **values,
            "updated_at": func.now(),
        },
    )
    await session.execute(stmt)


async def delete_chat_memory(session: AsyncSession, chat_id: int) -> int:
    result = await session.execute(
        delete(MemoryChatProfile).where(MemoryChatProfile.chat_id == chat_id)
    )
    return int(result.rowcount or 0)


async def delete_thread_memory(
    session: AsyncSession,
    chat_id: int,
    message_thread_id: int,
) -> int:
    result = await session.execute(
        delete(MemoryThreadProfile).where(
            MemoryThreadProfile.chat_id == chat_id,
            MemoryThreadProfile.message_thread_id == message_thread_id,
        )
    )
    return int(result.rowcount or 0)


async def delete_user_memory(
    session: AsyncSession,
    chat_id: int,
    user_id: int,
) -> int:
    result = await session.execute(
        delete(MemoryUserProfile).where(
            MemoryUserProfile.chat_id == chat_id,
            MemoryUserProfile.user_id == user_id,
        )
    )
    return int(result.rowcount or 0)


async def delete_all_memory_for_chat(session: AsyncSession, chat_id: int) -> int:
    total = 0
    for model in (MemoryUserProfile, MemoryThreadProfile, MemoryChatProfile):
        result = await session.execute(delete(model).where(model.chat_id == chat_id))
        total += int(result.rowcount or 0)
    return total


async def fetch_messages_for_memory_update(
    session: AsyncSession,
    chat_id: int,
    message_thread_id: int,
    *,
    after_message_id: int | None,
    limit: int,
    latest: bool = False,
) -> list[TelegramMessage]:
    stmt = select(TelegramMessage).where(
        TelegramMessage.chat_id == chat_id,
        TelegramMessage.message_thread_id == message_thread_id,
        TelegramMessage.is_bot_message.is_(False),
        TelegramMessage.is_command.is_(False),
    )
    if after_message_id is not None:
        stmt = stmt.where(TelegramMessage.message_id > after_message_id)
    if latest:
        stmt = stmt.order_by(
            TelegramMessage.telegram_date.desc(),
            TelegramMessage.message_id.desc(),
        ).limit(limit)
        result = await session.execute(stmt)
        return list(reversed(result.scalars().all()))
    stmt = stmt.order_by(
        TelegramMessage.telegram_date.asc(),
        TelegramMessage.message_id.asc(),
    ).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def fetch_memory_refresh_candidates(
    session: AsyncSession,
    *,
    chat_ids: list[int] | None,
    min_new_messages: int,
    stale_before: datetime,
    trigger_keywords: tuple[str, ...],
    reaction_min_count: int,
    limit: int,
) -> list[MemoryRefreshCandidate]:
    memory_subq = (
        select(
            MemoryThreadProfile.chat_id,
            MemoryThreadProfile.message_thread_id,
            MemoryThreadProfile.source_until_message_id,
            MemoryThreadProfile.updated_at,
        )
        .subquery()
    )
    reaction_counts = (
        select(
            TelegramMessageReaction.chat_id,
            TelegramMessageReaction.message_id,
            func.count(func.distinct(TelegramMessageReaction.user_id)).label(
                "reaction_users"
            ),
        )
        .group_by(TelegramMessageReaction.chat_id, TelegramMessageReaction.message_id)
        .subquery()
    )
    body = func.lower(
        func.coalesce(
            TelegramMessage.clean_text,
            TelegramMessage.text,
            TelegramMessage.caption,
            "",
        )
    )
    keyword_conditions = [
        body.like(f"%{keyword.lower()}%")
        for keyword in trigger_keywords
        if keyword.strip()
    ]
    keyword_match = or_(*keyword_conditions) if keyword_conditions else false()
    keyword_hits = func.sum(case((keyword_match, 1), else_=0))
    max_reactions = func.coalesce(func.max(reaction_counts.c.reaction_users), 0)
    new_count = func.count(TelegramMessage.id)
    trigger_conditions = [
        new_count >= min_new_messages,
        (
            (memory_subq.c.updated_at.is_not(None))
            & (memory_subq.c.updated_at < stale_before)
            & (new_count > 0)
        ),
        keyword_hits > 0,
    ]
    if reaction_min_count > 0:
        trigger_conditions.append(max_reactions >= reaction_min_count)

    stmt = (
        select(
            TelegramMessage.chat_id,
            TelegramMessage.message_thread_id,
            new_count.label("new_message_count"),
            func.max(TelegramMessage.message_id).label("latest_message_id"),
            func.max(TelegramMessage.telegram_date).label("latest_message_date"),
        )
        .outerjoin(
            memory_subq,
            and_(
                memory_subq.c.chat_id == TelegramMessage.chat_id,
                memory_subq.c.message_thread_id == TelegramMessage.message_thread_id,
            ),
        )
        .outerjoin(
            reaction_counts,
            and_(
                reaction_counts.c.chat_id == TelegramMessage.chat_id,
                reaction_counts.c.message_id == TelegramMessage.message_id,
            ),
        )
        .where(
            TelegramMessage.is_bot_message.is_(False),
            TelegramMessage.is_command.is_(False),
            (
                memory_subq.c.source_until_message_id.is_(None)
                | (
                    TelegramMessage.message_id
                    > memory_subq.c.source_until_message_id
                )
            ),
        )
        .group_by(
            TelegramMessage.chat_id,
            TelegramMessage.message_thread_id,
            memory_subq.c.updated_at,
        )
        .having(or_(*trigger_conditions))
        .order_by(new_count.desc(), func.max(TelegramMessage.telegram_date).desc())
        .limit(limit)
    )
    if chat_ids:
        stmt = stmt.where(TelegramMessage.chat_id.in_(chat_ids))
    result = await session.execute(stmt)
    return [
        MemoryRefreshCandidate(
            chat_id=int(row[0]),
            message_thread_id=int(row[1]),
            new_message_count=int(row[2]),
            latest_message_id=int(row[3]) if row[3] is not None else None,
            latest_message_date=row[4],
        )
        for row in result.all()
    ]


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


async def replace_message_reactions_snapshot(
    session: AsyncSession,
    chat_id: int,
    message_id: int,
    rows: list[tuple[int, list[str]]],
) -> None:
    """Replace the entire set of reactions for one message with a snapshot.

    Used by the user-API path, which delivers an aggregate reaction update and
    requires re-fetching the full reactor list rather than a per-user diff.
    """
    await session.execute(
        delete(TelegramMessageReaction).where(
            and_(
                TelegramMessageReaction.chat_id == chat_id,
                TelegramMessageReaction.message_id == message_id,
            )
        )
    )
    payload: list[dict] = []
    for user_id, emojis in rows:
        for emoji in emojis:
            if not emoji:
                continue
            payload.append(
                {
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "user_id": user_id,
                    "emoji": emoji,
                }
            )
    if not payload:
        return
    stmt = pg_insert(TelegramMessageReaction).values(payload)
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


@dataclass(frozen=True)
class ActivityReplyState:
    chat_id: int
    message_thread_id: int
    last_reply_at: datetime | None
    last_bot_message_id: int | None
    last_target_message_id: int | None


async def get_activity_reply_state(
    session: AsyncSession,
    chat_id: int,
    message_thread_id: int,
) -> ActivityReplyState | None:
    stmt = select(TelegramActivityReplyState).where(
        TelegramActivityReplyState.chat_id == chat_id,
        TelegramActivityReplyState.message_thread_id == message_thread_id,
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None:
        return None
    return ActivityReplyState(
        chat_id=int(row.chat_id),
        message_thread_id=int(row.message_thread_id),
        last_reply_at=row.last_reply_at,
        last_bot_message_id=(
            int(row.last_bot_message_id)
            if row.last_bot_message_id is not None
            else None
        ),
        last_target_message_id=(
            int(row.last_target_message_id)
            if row.last_target_message_id is not None
            else None
        ),
    )


async def upsert_activity_reply_state(
    session: AsyncSession,
    chat_id: int,
    message_thread_id: int,
    *,
    last_reply_at: datetime,
    last_bot_message_id: int | None,
    last_target_message_id: int | None,
) -> None:
    values: dict = {
        "chat_id": chat_id,
        "message_thread_id": message_thread_id,
        "last_reply_at": last_reply_at,
        "last_bot_message_id": last_bot_message_id,
        "last_target_message_id": last_target_message_id,
    }
    stmt = pg_insert(TelegramActivityReplyState).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[
            TelegramActivityReplyState.chat_id,
            TelegramActivityReplyState.message_thread_id,
        ],
        set_={
            "last_reply_at": last_reply_at,
            "last_bot_message_id": last_bot_message_id,
            "last_target_message_id": last_target_message_id,
        },
    )
    await session.execute(stmt)


@dataclass(frozen=True)
class ReactionState:
    chat_id: int
    message_id: int
    last_distinct_trigger_users: int
    last_evaluated_at: datetime | None
    last_reply_at: datetime | None


async def get_reaction_state(
    session: AsyncSession,
    chat_id: int,
    message_id: int,
) -> ReactionState | None:
    stmt = select(TelegramReactionState).where(
        TelegramReactionState.chat_id == chat_id,
        TelegramReactionState.message_id == message_id,
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None:
        return None
    return ReactionState(
        chat_id=int(row.chat_id),
        message_id=int(row.message_id),
        last_distinct_trigger_users=int(row.last_distinct_trigger_users),
        last_evaluated_at=row.last_evaluated_at,
        last_reply_at=row.last_reply_at,
    )


async def upsert_reaction_state(
    session: AsyncSession,
    chat_id: int,
    message_id: int,
    *,
    last_distinct_trigger_users: int,
    last_evaluated_at: datetime | None = None,
    last_reply_at: datetime | None = None,
) -> None:
    values: dict = {
        "chat_id": chat_id,
        "message_id": message_id,
        "last_distinct_trigger_users": last_distinct_trigger_users,
    }
    update_fields: dict = {
        "last_distinct_trigger_users": last_distinct_trigger_users,
    }
    if last_evaluated_at is not None:
        values["last_evaluated_at"] = last_evaluated_at
        update_fields["last_evaluated_at"] = last_evaluated_at
    if last_reply_at is not None:
        values["last_reply_at"] = last_reply_at
        update_fields["last_reply_at"] = last_reply_at

    stmt = pg_insert(TelegramReactionState).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[
            TelegramReactionState.chat_id,
            TelegramReactionState.message_id,
        ],
        set_=update_fields,
    )
    await session.execute(stmt)


async def fetch_messages_for_reaction_poll(
    session: AsyncSession,
    chat_ids: list[int] | None,
    since: datetime,
    stale_before: datetime,
    limit: int,
) -> list[tuple[int, int]]:
    """Return ``(chat_id, message_id)`` pairs that the reaction poller should
    refresh.

    A message is a candidate if it was sent after ``since`` and either:

    - has no row in ``telegram_reaction_states`` yet, OR
    - was last evaluated before ``stale_before``.
    """
    state_subq = (
        select(
            TelegramReactionState.chat_id,
            TelegramReactionState.message_id,
            TelegramReactionState.last_evaluated_at,
        )
        .subquery()
    )
    stmt = (
        select(TelegramMessage.chat_id, TelegramMessage.message_id)
        .outerjoin(
            state_subq,
            and_(
                state_subq.c.chat_id == TelegramMessage.chat_id,
                state_subq.c.message_id == TelegramMessage.message_id,
            ),
        )
        .where(TelegramMessage.telegram_date >= since)
        .where(
            (state_subq.c.last_evaluated_at.is_(None))
            | (state_subq.c.last_evaluated_at < stale_before)
        )
        .order_by(TelegramMessage.telegram_date.desc())
        .limit(limit)
    )
    if chat_ids:
        stmt = stmt.where(TelegramMessage.chat_id.in_(chat_ids))
    result = await session.execute(stmt)
    return [(int(row[0]), int(row[1])) for row in result.all()]


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
