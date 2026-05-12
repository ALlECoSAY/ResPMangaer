from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.db.repositories import MemoryRefreshCandidate
from app.services import memory_poller as memory_poller_module
from app.services.memory_poller import MemoryPoller


class _FakeMemoryConfig:
    enabled = True
    poll_enabled = True
    poll_interval_seconds = 1
    poll_max_chats_per_tick = 4
    update_min_new_messages = 5
    update_min_interval_minutes = 60
    trigger_keywords = ("todo",)
    update_reaction_min_count = 3


@dataclass
class _FakeMemoryService:
    enabled: bool = True
    calls: list[dict] = field(default_factory=list)

    async def refresh_thread(
        self,
        session,
        *,
        chat_id,
        message_thread_id,
        skip_threshold=False,
    ):
        del session
        self.calls.append(
            {
                "chat_id": chat_id,
                "message_thread_id": message_thread_id,
                "skip_threshold": skip_threshold,
            }
        )
        return SimpleNamespace(
            updated=True,
            new_message_count=10,
            skipped_reason=None,
        )


class _FakeSettings:
    @property
    def allowed_chat_ids(self) -> set[int]:
        return {-1001}


@pytest.fixture
def patched_session(monkeypatch):
    @asynccontextmanager
    async def _ctx():
        yield object()

    monkeypatch.setattr(memory_poller_module, "session_scope", _ctx)


async def test_tick_dispatches_memory_candidates(monkeypatch, patched_session):
    async def _fetch(
        session,
        *,
        chat_ids,
        min_new_messages,
        stale_before,
        trigger_keywords,
        reaction_min_count,
        limit,
    ):
        del session, stale_before
        assert chat_ids == [-1001]
        assert min_new_messages == 5
        assert trigger_keywords == ("todo",)
        assert reaction_min_count == 3
        assert limit == 4
        return [
            MemoryRefreshCandidate(
                chat_id=-1001,
                message_thread_id=0,
                new_message_count=6,
                latest_message_id=100,
                latest_message_date=datetime(2026, 5, 11, tzinfo=UTC),
            )
        ]

    monkeypatch.setattr(
        memory_poller_module,
        "fetch_memory_refresh_candidates",
        _fetch,
    )

    svc = _FakeMemoryService()
    poller = MemoryPoller(
        settings=_FakeSettings(),  # type: ignore[arg-type]
        config=_FakeMemoryConfig(),  # type: ignore[arg-type]
        memory_service=svc,  # type: ignore[arg-type]
    )

    await poller._tick()

    assert svc.calls == [
        {
            "chat_id": -1001,
            "message_thread_id": 0,
            "skip_threshold": True,
        }
    ]
