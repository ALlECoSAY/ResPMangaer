"""message reactions

Revision ID: 0002_message_reactions
Revises: 0001_initial
Create Date: 2026-05-06 00:00:01

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_message_reactions"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "telegram_message_reactions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "chat_id",
            sa.BigInteger(),
            sa.ForeignKey("telegram_chats.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("emoji", sa.String(length=64), nullable=False),
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
        sa.UniqueConstraint(
            "chat_id",
            "message_id",
            "user_id",
            "emoji",
            name="uq_reaction_unique",
        ),
    )
    op.create_index(
        "idx_reaction_chat_msg",
        "telegram_message_reactions",
        ["chat_id", "message_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_reaction_chat_msg", table_name="telegram_message_reactions"
    )
    op.drop_table("telegram_message_reactions")
