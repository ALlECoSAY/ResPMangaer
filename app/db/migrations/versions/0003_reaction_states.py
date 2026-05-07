"""reaction states

Revision ID: 0003_reaction_states
Revises: 0002_message_reactions
Create Date: 2026-05-07 00:00:00

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_reaction_states"
down_revision: str | None = "0002_message_reactions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "telegram_reaction_states",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "last_distinct_trigger_users",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("last_evaluated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_reply_at", sa.DateTime(timezone=True), nullable=True),
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
            name="uq_reaction_state_chat_msg",
        ),
    )
    op.create_index(
        "idx_reaction_state_chat_msg",
        "telegram_reaction_states",
        ["chat_id", "message_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_reaction_state_chat_msg", table_name="telegram_reaction_states"
    )
    op.drop_table("telegram_reaction_states")
