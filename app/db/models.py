from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class TelegramChat(Base):
    __tablename__ = "telegram_chats"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    username: Mapped[str | None] = mapped_column(Text)
    is_forum: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class TelegramThread(Base):
    __tablename__ = "telegram_threads"
    __table_args__ = (
        UniqueConstraint("chat_id", "message_thread_id", name="uq_thread_chat_topic"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("telegram_chats.id", ondelete="CASCADE"),
        nullable=False,
    )
    message_thread_id: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default="0", nullable=False
    )
    title: Mapped[str | None] = mapped_column(Text)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class TelegramUser(Base):
    __tablename__ = "telegram_users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    is_bot: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    username: Mapped[str | None] = mapped_column(Text)
    first_name: Mapped[str | None] = mapped_column(Text)
    last_name: Mapped[str | None] = mapped_column(Text)
    language_code: Mapped[str | None] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class TelegramMessage(Base):
    __tablename__ = "telegram_messages"
    __table_args__ = (
        UniqueConstraint("chat_id", "message_id", name="uq_message_chat_id"),
        Index(
            "idx_messages_chat_thread_date",
            "chat_id",
            "message_thread_id",
            "telegram_date",
        ),
        Index("idx_messages_chat_date", "chat_id", "telegram_date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("telegram_chats.id", ondelete="CASCADE"),
        nullable=False,
    )
    thread_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("telegram_threads.id", ondelete="CASCADE"),
        nullable=False,
    )
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_thread_id: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default="0", nullable=False
    )
    sender_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("telegram_users.id"), nullable=True
    )
    sender_display_name: Mapped[str | None] = mapped_column(Text)
    is_bot_message: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    is_command: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    command_name: Mapped[str | None] = mapped_column(String(64))
    text: Mapped[str | None] = mapped_column(Text)
    clean_text: Mapped[str | None] = mapped_column(Text)
    caption: Mapped[str | None] = mapped_column(Text)
    content_type: Mapped[str] = mapped_column(
        String(32), default="text", server_default="text", nullable=False
    )
    reply_to_message_id: Mapped[int | None] = mapped_column(BigInteger)
    telegram_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    chat: Mapped[TelegramChat] = relationship(lazy="noload")
    thread: Mapped[TelegramThread] = relationship(lazy="noload")
    sender: Mapped[TelegramUser | None] = relationship(lazy="noload")


class TelegramMessageReaction(Base):
    __tablename__ = "telegram_message_reactions"
    __table_args__ = (
        UniqueConstraint(
            "chat_id",
            "message_id",
            "user_id",
            "emoji",
            name="uq_reaction_unique",
        ),
        Index("idx_reaction_chat_msg", "chat_id", "message_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("telegram_chats.id", ondelete="CASCADE"),
        nullable=False,
    )
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    emoji: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class TelegramActivityReplyState(Base):
    __tablename__ = "telegram_activity_reply_states"
    __table_args__ = (
        UniqueConstraint(
            "chat_id",
            "message_thread_id",
            name="uq_activity_reply_state_chat_thread",
        ),
        Index(
            "idx_activity_reply_state_chat_thread",
            "chat_id",
            "message_thread_id",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_thread_id: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default="0", nullable=False
    )
    last_reply_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_bot_message_id: Mapped[int | None] = mapped_column(BigInteger)
    last_target_message_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class TelegramReactionState(Base):
    __tablename__ = "telegram_reaction_states"
    __table_args__ = (
        UniqueConstraint(
            "chat_id",
            "message_id",
            name="uq_reaction_state_chat_msg",
        ),
        Index("idx_reaction_state_chat_msg", "chat_id", "message_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    last_distinct_trigger_users: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    last_evaluated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_reply_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class MemoryChatProfile(Base):
    __tablename__ = "memory_chat_profiles"

    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("telegram_chats.id", ondelete="CASCADE"),
        primary_key=True,
    )
    summary: Mapped[str | None] = mapped_column(Text)
    stable_facts: Mapped[list | dict | None] = mapped_column(JSONB)
    current_projects: Mapped[list | dict | None] = mapped_column(JSONB)
    decisions: Mapped[list | dict | None] = mapped_column(JSONB)
    open_questions: Mapped[list | dict | None] = mapped_column(JSONB)
    source_until_message_id: Mapped[int | None] = mapped_column(BigInteger)
    source_until_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class MemoryThreadProfile(Base):
    __tablename__ = "memory_thread_profiles"

    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("telegram_chats.id", ondelete="CASCADE"),
        primary_key=True,
    )
    message_thread_id: Mapped[int] = mapped_column(
        BigInteger,
        default=0,
        server_default="0",
        primary_key=True,
    )
    title: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    decisions: Mapped[list | dict | None] = mapped_column(JSONB)
    action_items: Mapped[list | dict | None] = mapped_column(JSONB)
    open_questions: Mapped[list | dict | None] = mapped_column(JSONB)
    key_participants: Mapped[list | dict | None] = mapped_column(JSONB)
    source_until_message_id: Mapped[int | None] = mapped_column(BigInteger)
    source_until_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class MemoryUserProfile(Base):
    __tablename__ = "memory_user_profiles"

    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("telegram_chats.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("telegram_users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    display_name: Mapped[str | None] = mapped_column(Text)
    aliases: Mapped[list | dict | None] = mapped_column(JSONB)
    profile_summary: Mapped[str | None] = mapped_column(Text)
    expertise: Mapped[list | dict | None] = mapped_column(JSONB)
    stated_preferences: Mapped[list | dict | None] = mapped_column(JSONB)
    interaction_style: Mapped[str | None] = mapped_column(Text)
    evidence_message_ids: Mapped[list | dict | None] = mapped_column(JSONB)
    confidence: Mapped[float | None] = mapped_column(Float)
    source_until_message_id: Mapped[int | None] = mapped_column(BigInteger)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class BotIdentityProfile(Base):
    __tablename__ = "bot_identity_profiles"

    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("telegram_chats.id", ondelete="CASCADE"),
        primary_key=True,
    )
    display_name: Mapped[str | None] = mapped_column(Text)
    avatar_file_id: Mapped[str | None] = mapped_column(Text)
    avatar_prompt: Mapped[str | None] = mapped_column(Text)
    avatar_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    personality_prompt: Mapped[str | None] = mapped_column(Text)
    personality_version: Mapped[int] = mapped_column(
        Integer, default=1, server_default="1", nullable=False
    )
    personality_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    last_self_update_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    self_update_reason: Mapped[str | None] = mapped_column(Text)
    pending_proposal: Mapped[list | dict | None] = mapped_column(JSONB)
    metadata_json: Mapped[list | dict | None] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class LlmInteraction(Base):
    __tablename__ = "llm_interactions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_thread_id: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default="0", nullable=False
    )
    request_message_id: Mapped[int | None] = mapped_column(BigInteger)
    command_name: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_tokens_estimate: Mapped[int | None] = mapped_column(Integer)
    completion_tokens_estimate: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
