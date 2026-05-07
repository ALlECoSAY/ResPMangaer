from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import reaction_poller as reaction_poller_module
from app.services.reaction_poller import ReactionPoller
from app.telegram_client.types import (
    TgMessageReactionSnapshot,
    TgReactionActor,
    TgUser,
)


class _FakeReactionsConfig:
    def __init__(
        self,
        *,
        poll_enabled: bool = True,
        poll_interval_seconds: int = 1,
        poll_window_minutes: int = 60,
        poll_max_messages_per_tick: int = 5,
    ) -> None:
        self.poll_enabled = poll_enabled
        self.poll_interval_seconds = poll_interval_seconds
        self.poll_window_minutes = poll_window_minutes
        self.poll_max_messages_per_tick = poll_max_messages_per_tick


class _FakeReactionService:
    def __init__(self, *, enabled: bool = True) -> None:
        self.enabled = enabled
        self.trigger_emojis: tuple[str, ...] = ()
        self.fetch_limit_per_emoji = 100
        self.snapshot_calls: list[TgMessageReactionSnapshot] = []

    async def handle_reaction_snapshot(self, session, client, snapshot):
        self.snapshot_calls.append(snapshot)


class _FakeSettings:
    @property
    def allowed_chat_ids(self) -> set[int]:
        return {-1001}


def _make_snapshot(chat_id: int, message_id: int) -> TgMessageReactionSnapshot:
    actor = TgReactionActor(
        user=TgUser(
            id=1,
            is_bot=False,
            username="u",
            first_name="U",
            last_name=None,
            language_code="en",
        ),
        emojis=["🔥"],
    )
    return TgMessageReactionSnapshot(
        chat_id=chat_id,
        message_id=message_id,
        actors=[actor],
        counts={"🔥": 1},
    )


@pytest.fixture
def patched_session(monkeypatch):
    @asynccontextmanager
    async def _ctx():
        yield object()

    monkeypatch.setattr(reaction_poller_module, "session_scope", _ctx)


async def test_tick_dispatches_to_handler(monkeypatch, patched_session):
    candidates = [(-1001, 11), (-1001, 12)]

    async def _fetch(session, chat_ids, since, stale_before, limit):
        assert chat_ids == [-1001]
        assert limit == 5
        return list(candidates)

    monkeypatch.setattr(
        reaction_poller_module, "fetch_messages_for_reaction_poll", _fetch
    )

    cfg = _FakeReactionsConfig()
    svc = _FakeReactionService()
    poller = ReactionPoller(
        settings=_FakeSettings(),  # type: ignore[arg-type]
        config=cfg,  # type: ignore[arg-type]
        reaction_service=svc,  # type: ignore[arg-type]
    )

    client = SimpleNamespace(
        fetch_message_reaction_snapshot=AsyncMock(
            side_effect=[_make_snapshot(-1001, 11), _make_snapshot(-1001, 12)]
        )
    )
    await poller._tick(client)

    assert [s.message_id for s in svc.snapshot_calls] == [11, 12]
    client.fetch_message_reaction_snapshot.assert_awaited()
    assert client.fetch_message_reaction_snapshot.await_count == 2


async def test_tick_skips_handler_for_none_snapshot(
    monkeypatch, patched_session
):
    async def _fetch(session, chat_ids, since, stale_before, limit):
        return [(-1001, 11)]

    monkeypatch.setattr(
        reaction_poller_module, "fetch_messages_for_reaction_poll", _fetch
    )

    cfg = _FakeReactionsConfig()
    svc = _FakeReactionService()
    poller = ReactionPoller(
        settings=_FakeSettings(),  # type: ignore[arg-type]
        config=cfg,  # type: ignore[arg-type]
        reaction_service=svc,  # type: ignore[arg-type]
    )
    client = SimpleNamespace(
        fetch_message_reaction_snapshot=AsyncMock(return_value=None)
    )
    await poller._tick(client)
    assert svc.snapshot_calls == []


async def test_tick_swallows_fetch_error(monkeypatch, patched_session):
    async def _fetch(session, chat_ids, since, stale_before, limit):
        return [(-1001, 11), (-1001, 12)]

    monkeypatch.setattr(
        reaction_poller_module, "fetch_messages_for_reaction_poll", _fetch
    )

    cfg = _FakeReactionsConfig()
    svc = _FakeReactionService()
    poller = ReactionPoller(
        settings=_FakeSettings(),  # type: ignore[arg-type]
        config=cfg,  # type: ignore[arg-type]
        reaction_service=svc,  # type: ignore[arg-type]
    )
    client = SimpleNamespace(
        fetch_message_reaction_snapshot=AsyncMock(
            side_effect=[RuntimeError("boom"), _make_snapshot(-1001, 12)]
        )
    )
    await poller._tick(client)
    # Second message still processed despite first failing
    assert [s.message_id for s in svc.snapshot_calls] == [12]


async def test_run_skips_when_poll_disabled(monkeypatch, patched_session):
    fetch_calls = []

    async def _fetch(*args, **kwargs):
        fetch_calls.append(1)
        return []

    monkeypatch.setattr(
        reaction_poller_module, "fetch_messages_for_reaction_poll", _fetch
    )

    cfg = _FakeReactionsConfig(poll_enabled=False, poll_interval_seconds=1)
    svc = _FakeReactionService()
    poller = ReactionPoller(
        settings=_FakeSettings(),  # type: ignore[arg-type]
        config=cfg,  # type: ignore[arg-type]
        reaction_service=svc,  # type: ignore[arg-type]
    )
    client = SimpleNamespace(fetch_message_reaction_snapshot=AsyncMock())

    poller.start(client)
    await asyncio.sleep(0.05)
    await poller.stop()
    assert fetch_calls == []


async def test_run_skips_when_service_disabled(monkeypatch, patched_session):
    fetch_calls = []

    async def _fetch(*args, **kwargs):
        fetch_calls.append(1)
        return []

    monkeypatch.setattr(
        reaction_poller_module, "fetch_messages_for_reaction_poll", _fetch
    )

    cfg = _FakeReactionsConfig(poll_enabled=True, poll_interval_seconds=1)
    svc = _FakeReactionService(enabled=False)
    poller = ReactionPoller(
        settings=_FakeSettings(),  # type: ignore[arg-type]
        config=cfg,  # type: ignore[arg-type]
        reaction_service=svc,  # type: ignore[arg-type]
    )
    client = SimpleNamespace(fetch_message_reaction_snapshot=AsyncMock())
    poller.start(client)
    await asyncio.sleep(0.05)
    await poller.stop()
    assert fetch_calls == []
