"""memory profiles

Revision ID: 0005_memory_profiles
Revises: 0004_activity_reply_states
Create Date: 2026-05-11 00:00:00

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_memory_profiles"
down_revision: str | None = "0004_activity_reply_states"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _jsonb_column(name: str) -> sa.Column:
    return sa.Column(
        name,
        postgresql.JSONB(astext_type=sa.Text()),
        nullable=False,
        server_default=sa.text("'[]'::jsonb"),
    )


def upgrade() -> None:
    op.create_table(
        "memory_chat_profiles",
        sa.Column(
            "chat_id",
            sa.BigInteger(),
            sa.ForeignKey("telegram_chats.id", ondelete="CASCADE"),
        ),
        sa.Column("summary", sa.Text()),
        _jsonb_column("stable_facts"),
        _jsonb_column("current_projects"),
        _jsonb_column("decisions"),
        _jsonb_column("open_questions"),
        sa.Column("source_until_message_id", sa.BigInteger()),
        sa.Column("source_until_date", sa.DateTime(timezone=True)),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_table(
        "memory_thread_profiles",
        sa.Column(
            "chat_id",
            sa.BigInteger(),
            sa.ForeignKey("telegram_chats.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "message_thread_id",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("title", sa.Text()),
        sa.Column("summary", sa.Text()),
        _jsonb_column("decisions"),
        _jsonb_column("action_items"),
        _jsonb_column("open_questions"),
        _jsonb_column("key_participants"),
        sa.Column("source_until_message_id", sa.BigInteger()),
        sa.Column("source_until_date", sa.DateTime(timezone=True)),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint(
            "chat_id",
            "message_thread_id",
            name="pk_memory_thread_profiles",
        ),
    )
    op.create_index(
        "idx_memory_thread_updated",
        "memory_thread_profiles",
        ["chat_id", "updated_at"],
    )

    op.create_table(
        "memory_user_profiles",
        sa.Column(
            "chat_id",
            sa.BigInteger(),
            sa.ForeignKey("telegram_chats.id", ondelete="CASCADE"),
        ),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("telegram_users.id", ondelete="CASCADE"),
        ),
        sa.Column("display_name", sa.Text()),
        _jsonb_column("aliases"),
        sa.Column("profile_summary", sa.Text()),
        _jsonb_column("expertise"),
        _jsonb_column("stated_preferences"),
        sa.Column("interaction_style", sa.Text()),
        _jsonb_column("evidence_message_ids"),
        sa.Column("confidence", sa.REAL(), nullable=False, server_default=sa.text("0")),
        sa.Column("source_until_message_id", sa.BigInteger()),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("chat_id", "user_id", name="pk_memory_user_profiles"),
    )
    op.create_index(
        "idx_memory_user_chat_updated",
        "memory_user_profiles",
        ["chat_id", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_memory_user_chat_updated", table_name="memory_user_profiles")
    op.drop_table("memory_user_profiles")
    op.drop_index("idx_memory_thread_updated", table_name="memory_thread_profiles")
    op.drop_table("memory_thread_profiles")
    op.drop_table("memory_chat_profiles")
