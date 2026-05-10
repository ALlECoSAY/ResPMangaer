"""activity reply states

Revision ID: 0004_activity_reply_states
Revises: 0003_reaction_states
Create Date: 2026-05-10 00:00:00

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_activity_reply_states"
down_revision: str | None = "0003_reaction_states"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "telegram_activity_reply_states",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "message_thread_id",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("last_reply_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_bot_message_id", sa.BigInteger(), nullable=True),
        sa.Column("last_target_message_id", sa.BigInteger(), nullable=True),
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
            "message_thread_id",
            name="uq_activity_reply_state_chat_thread",
        ),
    )
    op.create_index(
        "idx_activity_reply_state_chat_thread",
        "telegram_activity_reply_states",
        ["chat_id", "message_thread_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_activity_reply_state_chat_thread",
        table_name="telegram_activity_reply_states",
    )
    op.drop_table("telegram_activity_reply_states")
