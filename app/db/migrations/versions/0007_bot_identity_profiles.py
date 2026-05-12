"""bot identity profiles

Revision ID: 0007_bot_identity_profiles
Revises: 0006_chat_scoped_memory
Create Date: 2026-05-12 12:00:00

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_bot_identity_profiles"
down_revision: str | None = "0006_chat_scoped_memory"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "bot_identity_profiles",
        sa.Column(
            "chat_id",
            sa.BigInteger(),
            sa.ForeignKey("telegram_chats.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("display_name", sa.Text()),
        sa.Column("avatar_file_id", sa.Text()),
        sa.Column("avatar_prompt", sa.Text()),
        sa.Column("avatar_updated_at", sa.DateTime(timezone=True)),
        sa.Column("personality_prompt", sa.Text()),
        sa.Column(
            "personality_version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column("personality_updated_at", sa.DateTime(timezone=True)),
        sa.Column("last_self_update_at", sa.DateTime(timezone=True)),
        sa.Column("self_update_reason", sa.Text()),
        sa.Column(
            "pending_proposal",
            postgresql.JSONB(astext_type=sa.Text()),
        ),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(astext_type=sa.Text()),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    op.drop_table("bot_identity_profiles")
