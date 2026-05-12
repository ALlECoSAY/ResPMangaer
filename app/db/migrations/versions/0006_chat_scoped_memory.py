"""chat scoped memory

Revision ID: 0006_chat_scoped_memory
Revises: 0005_memory_profiles
Create Date: 2026-05-12 00:00:00

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_chat_scoped_memory"
down_revision: str | None = "0005_memory_profiles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("DELETE FROM memory_chat_profiles WHERE chat_id IS NULL")
    op.execute(
        """
        DELETE FROM memory_chat_profiles old
        USING memory_chat_profiles keep
        WHERE old.chat_id = keep.chat_id
          AND old.ctid < keep.ctid
        """
    )
    op.alter_column(
        "memory_chat_profiles",
        "chat_id",
        existing_type=sa.BigInteger(),
        nullable=False,
    )
    op.create_primary_key(
        "pk_memory_chat_profiles",
        "memory_chat_profiles",
        ["chat_id"],
    )
    op.execute(
        """
        UPDATE memory_chat_profiles
        SET source_until_message_id = NULL,
            source_until_date = NULL
        """
    )
    op.execute(
        """
        UPDATE memory_thread_profiles
        SET source_until_message_id = NULL,
            source_until_date = NULL
        WHERE message_thread_id = 0
        """
    )


def downgrade() -> None:
    op.drop_constraint(
        "pk_memory_chat_profiles",
        "memory_chat_profiles",
        type_="primary",
    )
    op.alter_column(
        "memory_chat_profiles",
        "chat_id",
        existing_type=sa.BigInteger(),
        nullable=True,
    )
