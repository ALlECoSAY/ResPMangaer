from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
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
