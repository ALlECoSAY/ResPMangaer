"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-06 00:00:00

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "telegram_chats",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("title", sa.Text()),
        sa.Column("username", sa.Text()),
        sa.Column("is_forum", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_table(
        "telegram_threads",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "chat_id",
            sa.BigInteger(),
            sa.ForeignKey("telegram_chats.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "message_thread_id",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("title", sa.Text()),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("chat_id", "message_thread_id", name="uq_thread_chat_topic"),
    )

    op.create_table(
        "telegram_users",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("is_bot", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("username", sa.Text()),
        sa.Column("first_name", sa.Text()),
        sa.Column("last_name", sa.Text()),
        sa.Column("language_code", sa.String(length=16)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_table(
        "telegram_messages",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "chat_id",
            sa.BigInteger(),
            sa.ForeignKey("telegram_chats.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "thread_id",
            sa.BigInteger(),
            sa.ForeignKey("telegram_threads.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "message_thread_id",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "sender_user_id",
            sa.BigInteger(),
            sa.ForeignKey("telegram_users.id"),
        ),
        sa.Column("sender_display_name", sa.Text()),
        sa.Column("is_bot_message", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("is_command", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("command_name", sa.String(length=64)),
        sa.Column("text", sa.Text()),
        sa.Column("clean_text", sa.Text()),
        sa.Column("caption", sa.Text()),
        sa.Column(
            "content_type",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'text'"),
        ),
        sa.Column("reply_to_message_id", sa.BigInteger()),
        sa.Column("telegram_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("chat_id", "message_id", name="uq_message_chat_id"),
    )
    op.create_index(
        "idx_messages_chat_thread_date",
        "telegram_messages",
        ["chat_id", "message_thread_id", "telegram_date"],
    )
    op.create_index(
        "idx_messages_chat_date",
        "telegram_messages",
        ["chat_id", "telegram_date"],
    )
    op.execute(
        "CREATE INDEX idx_messages_clean_text_fts "
        "ON telegram_messages USING GIN (to_tsvector('simple', coalesce(clean_text, '')))"
    )

    op.create_table(
        "llm_interactions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "message_thread_id",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("request_message_id", sa.BigInteger()),
        sa.Column("command_name", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("prompt_tokens_estimate", sa.Integer()),
        sa.Column("completion_tokens_estimate", sa.Integer()),
        sa.Column("latency_ms", sa.Integer()),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    op.drop_table("llm_interactions")
    op.execute("DROP INDEX IF EXISTS idx_messages_clean_text_fts")
    op.drop_index("idx_messages_chat_date", table_name="telegram_messages")
    op.drop_index("idx_messages_chat_thread_date", table_name="telegram_messages")
    op.drop_table("telegram_messages")
    op.drop_table("telegram_users")
    op.drop_table("telegram_threads")
    op.drop_table("telegram_chats")
