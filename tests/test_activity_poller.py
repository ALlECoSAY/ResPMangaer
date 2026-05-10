from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app.services import activity_poller as activity_poller_module
from app.services.activity_poller import ActivityPoller


class _FakeActivityConfig:
    def __init__(
        self,
        *,
        poll_enabled: bool = True,
        poll_interval_seconds: int = 1,
        poll_window_minutes: int = 30,
        poll_max_threads_per_tick: int = 5,
        min_messages: int = 3,
    ) -> None:
        self.poll_enabled = poll_enabled
        self.poll_interval_seconds = poll_interval_seconds
        self.poll_window_minutes = poll_window_minutes
        self.poll_max_threads_per_tick = poll_max_threads_per_tick
        self.min_messages = min_messages


class _FakeActivityService:
    def __init__(self, *, enabled: bool = True) -> None:
        self.enabled = enabled
        self.calls: list[dict] = []

    async def maybe_trigger_random_reply(
        self,
        session,
        client,
        *,
        chat_id,
        message_thread_id,
        observed_count,
    ):
        del session, client
        self.calls.append(
            dict(
                chat_id=chat_id,
                message_thread_id=message_thread_id,
                observed_count=observed_count,
            )
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

    monkeypatch.setattr(activity_poller_module, "session_scope", _ctx)


async def test_tick_dispatches_active_threads(monkeypatch, patched_session):
    async def _fetch(session, chat_ids, since, min_messages, limit):
        del session, since
        assert chat_ids == [-1001]
        assert min_messages == 3
        assert limit == 5
        return [(-1001, 0, 4), (-1001, 42, 9)]

    monkeypatch.setattr(activity_poller_module, "fetch_active_threads", _fetch)

    cfg = _FakeActivityConfig()
    svc = _FakeActivityService()
    poller = ActivityPoller(
        settings=_FakeSettings(),  # type: ignore[arg-type]
        config=cfg,  # type: ignore[arg-type]
        activity_service=svc,  # type: ignore[arg-type]
    )

    await poller._tick(SimpleNamespace())

    assert svc.calls == [
        {"chat_id": -1001, "message_thread_id": 0, "observed_count": 4},
        {"chat_id": -1001, "message_thread_id": 42, "observed_count": 9},
    ]

