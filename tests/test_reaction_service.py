from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.config import Settings
from app.llm.openrouter_client import LlmResponse
from app.services import reaction_service as reaction_service_module
from app.services.reaction_service import ReactionService
from app.telegram_client.types import (
    TgMessageReactionSnapshot,
    TgReactionActor,
    TgReactionUpdate,
    TgUser,
)


class _FakeReactionsConfig:
    def __init__(
        self,
        *,
        enabled: bool = True,
        min_distinct_users: int = 3,
        reply_chance: float = 1.0,
        context_before: int = 2,
        context_after: int = 2,
        cooldown_seconds: int = 0,
        bot_emoji: str = "🔥",
        trigger_emojis: tuple[str, ...] = (),
        fetch_limit_per_emoji: int = 200,
        ignore_custom_reactions: bool = True,
    ) -> None:
        self.enabled = enabled
        self.min_distinct_users = min_distinct_users
        self.reply_chance = reply_chance
        self.context_before = context_before
        self.context_after = context_after
        self.cooldown_seconds = cooldown_seconds
        self.bot_emoji = bot_emoji
        self.trigger_emojis = trigger_emojis
        self.fetch_limit_per_emoji = fetch_limit_per_emoji
        self.ignore_custom_reactions = ignore_custom_reactions

    def emoji_is_trigger(self, emoji: str) -> bool:
        if not self.trigger_emojis:
            return True
        return emoji in self.trigger_emojis


class _FakeRuntimeConfig:
    max_reply_chars = 4000


class _FakeOpenRouter:
    def __init__(self, response_text: str = "Heh, fair point.") -> None:
        self._response_text = response_text
        self.calls: list[tuple[str, str]] = []

    async def complete(self, system_prompt: str, user_prompt: str) -> LlmResponse:
        self.calls.append((system_prompt, user_prompt))
        return LlmResponse(
            text=self._response_text,
            model="test-model",
            prompt_tokens=10,
            completion_tokens=5,
            latency_ms=42,
        )


class _FakeTelegramClient:
    def __init__(self) -> None:
        self.send_message = AsyncMock()
        self.set_reaction = AsyncMock()


@dataclass
class _FakeRepoState:
    distinct_users: int = 0
    target_message: Any | None = None
    before_rows: list = field(default_factory=list)
    after_rows: list = field(default_factory=list)
    replace_calls: list[dict] = field(default_factory=list)
    upsert_user_calls: list = field(default_factory=list)
    record_llm_calls: list[dict] = field(default_factory=list)


@pytest.fixture
def patched_repo(monkeypatch):
    state = _FakeRepoState()

    async def _replace(session, *, chat_id, message_id, user_id, new_emojis):
        state.replace_calls.append(
            dict(
                chat_id=chat_id,
                message_id=message_id,
                user_id=user_id,
                new_emojis=list(new_emojis),
            )
        )

    async def _count(session, *, chat_id, message_id, only_emojis=None):
        return state.distinct_users

    async def _fetch_target(session, *, chat_id, message_id):
        return state.target_message

    async def _fetch_around(
        session,
        *,
        chat_id,
        message_thread_id,
        target_telegram_date,
        target_message_id,
        before,
        after,
    ):
        return state.before_rows[-before:], state.after_rows[:after]

    async def _upsert_user(session, data):
        state.upsert_user_calls.append(data)

    async def _record(session, **kwargs):
        state.record_llm_calls.append(kwargs)

    monkeypatch.setattr(
        reaction_service_module, "replace_user_reactions", _replace
    )
    monkeypatch.setattr(
        reaction_service_module, "count_distinct_reaction_users", _count
    )
    monkeypatch.setattr(
        reaction_service_module,
        "fetch_message_by_chat_message_id",
        _fetch_target,
    )
    monkeypatch.setattr(
        reaction_service_module, "fetch_messages_around", _fetch_around
    )
    monkeypatch.setattr(reaction_service_module, "upsert_user", _upsert_user)
    monkeypatch.setattr(
        reaction_service_module, "record_llm_interaction", _record
    )
    return state


def _make_emoji(emoji: str):
    return SimpleNamespace(type="emoji", emoji=emoji)


def _make_event(
    *,
    chat_id: int,
    message_id: int,
    user_id: int,
    old: list[str],
    new: list[str],
):
    return TgReactionUpdate(
        chat_id=chat_id,
        message_id=message_id,
        user=TgUser(
            id=user_id,
            is_bot=False,
            username="alice",
            first_name="A",
            last_name=None,
            language_code="en",
        ),
        old_emojis=list(old),
        new_emojis=list(new),
    )


def _make_bot_event(
    *,
    chat_id: int,
    message_id: int,
    user_id: int,
    old: list[str],
    new: list[str],
):
    event = _make_event(
        chat_id=chat_id,
        message_id=message_id,
        user_id=user_id,
        old=old,
        new=new,
    )
    return TgReactionUpdate(
        chat_id=event.chat_id,
        message_id=event.message_id,
        user=TgUser(
            id=event.user.id,
            is_bot=True,
            username=event.user.username,
            first_name=event.user.first_name,
            last_name=event.user.last_name,
            language_code=event.user.language_code,
        ),
        old_emojis=event.old_emojis,
        new_emojis=event.new_emojis,
    )


def _make_target_row(message_id: int, thread_id: int = 0):
    return SimpleNamespace(
        chat_id=1,
        message_id=message_id,
        message_thread_id=thread_id,
        thread_id=1,
        telegram_date=datetime(2026, 5, 6, 12, 0, tzinfo=UTC),
        clean_text="The thing we are reacting to",
        text="The thing we are reacting to",
        caption=None,
        sender_display_name="alice",
        content_type="text",
    )


class _FakeSession:
    async def flush(self) -> None:
        return None


def _make_service(
    *,
    config: _FakeReactionsConfig,
    rng_value: float = 0.0,
) -> tuple[ReactionService, _FakeOpenRouter]:
    settings = Settings(_env_file=None)
    rng = random.Random()
    rng.random = lambda: rng_value  # type: ignore[method-assign]
    client = _FakeOpenRouter()
    svc = ReactionService(
        settings=settings,
        config=config,  # type: ignore[arg-type]
        runtime_config=_FakeRuntimeConfig(),  # type: ignore[arg-type]
        client=client,  # type: ignore[arg-type]
        rng=rng,
    )
    return svc, client


async def test_disabled_no_op(patched_repo):
    cfg = _FakeReactionsConfig(enabled=False)
    svc, client = _make_service(config=cfg)
    tg_client = _FakeTelegramClient()
    event = _make_event(
        chat_id=1, message_id=10, user_id=99, old=[], new=["🔥"]
    )
    await svc.handle_reaction_update(_FakeSession(), tg_client, event)
    assert client.calls == []
    tg_client.send_message.assert_not_awaited()
    assert patched_repo.replace_calls == []


async def test_bot_reaction_is_ignored(patched_repo):
    cfg = _FakeReactionsConfig(min_distinct_users=1, reply_chance=1.0)
    patched_repo.distinct_users = 1
    svc, client = _make_service(config=cfg)
    tg_client = _FakeTelegramClient()
    event = _make_bot_event(
        chat_id=1, message_id=10, user_id=99, old=[], new=["🔥"]
    )
    await svc.handle_reaction_update(_FakeSession(), tg_client, event)
    assert client.calls == []
    tg_client.send_message.assert_not_awaited()
    assert patched_repo.replace_calls == []


async def test_persists_reactions_even_below_threshold(patched_repo):
    cfg = _FakeReactionsConfig(min_distinct_users=10)
    patched_repo.distinct_users = 1
    svc, client = _make_service(config=cfg)
    tg_client = _FakeTelegramClient()
    event = _make_event(
        chat_id=1, message_id=10, user_id=99, old=[], new=["🔥"]
    )
    await svc.handle_reaction_update(_FakeSession(), tg_client, event)
    assert len(patched_repo.replace_calls) == 1
    assert patched_repo.replace_calls[0]["new_emojis"] == ["🔥"]
    assert client.calls == []
    tg_client.send_message.assert_not_awaited()


async def test_triggers_reply_when_threshold_met(patched_repo):
    cfg = _FakeReactionsConfig(min_distinct_users=3, reply_chance=1.0)
    patched_repo.distinct_users = 3
    patched_repo.target_message = _make_target_row(10, thread_id=42)
    patched_repo.before_rows = [_make_target_row(8), _make_target_row(9)]
    patched_repo.after_rows = [_make_target_row(11), _make_target_row(12)]
    svc, client = _make_service(config=cfg, rng_value=0.0)
    tg_client = _FakeTelegramClient()
    event = _make_event(
        chat_id=1, message_id=10, user_id=99, old=[], new=["🔥"]
    )
    await svc.handle_reaction_update(_FakeSession(), tg_client, event)
    assert len(client.calls) == 1
    tg_client.send_message.assert_awaited_once()
    send_kwargs = tg_client.send_message.await_args.kwargs
    assert send_kwargs["chat_id"] == 1
    assert send_kwargs["reply_to_message_id"] == 10
    assert send_kwargs["message_thread_id"] == 42
    tg_client.set_reaction.assert_awaited_once()
    react_kwargs = tg_client.set_reaction.await_args.kwargs
    assert react_kwargs["chat_id"] == 1
    assert react_kwargs["message_id"] == 10
    # Recorded interaction for observability
    assert patched_repo.record_llm_calls
    assert patched_repo.record_llm_calls[0]["command_name"] == "reaction_reply"


async def test_dice_can_skip_reply(patched_repo):
    cfg = _FakeReactionsConfig(min_distinct_users=3, reply_chance=0.1)
    patched_repo.distinct_users = 5
    patched_repo.target_message = _make_target_row(10)
    svc, client = _make_service(config=cfg, rng_value=0.99)
    tg_client = _FakeTelegramClient()
    event = _make_event(
        chat_id=1, message_id=10, user_id=99, old=[], new=["🔥"]
    )
    await svc.handle_reaction_update(_FakeSession(), tg_client, event)
    assert client.calls == []
    tg_client.send_message.assert_not_awaited()


async def test_no_qualifying_added_emoji_does_not_evaluate(patched_repo):
    # User had 🔥 before AND now -> nothing newly added; threshold check skipped.
    cfg = _FakeReactionsConfig(min_distinct_users=1, reply_chance=1.0)
    patched_repo.distinct_users = 100
    svc, client = _make_service(config=cfg, rng_value=0.0)
    tg_client = _FakeTelegramClient()
    event = _make_event(
        chat_id=1, message_id=10, user_id=99, old=["🔥"], new=["🔥"]
    )
    await svc.handle_reaction_update(_FakeSession(), tg_client, event)
    assert client.calls == []
    # And no DB writes either since old==new
    assert patched_repo.replace_calls == []


async def test_filtered_trigger_emojis(patched_repo):
    cfg = _FakeReactionsConfig(
        min_distinct_users=1,
        reply_chance=1.0,
        trigger_emojis=("🔥",),
    )
    patched_repo.distinct_users = 5
    patched_repo.target_message = _make_target_row(10)
    svc, client = _make_service(config=cfg, rng_value=0.0)
    tg_client = _FakeTelegramClient()
    # User reacts with 👍 — not in trigger list, should not evaluate.
    event = _make_event(
        chat_id=1, message_id=10, user_id=99, old=[], new=["👍"]
    )
    await svc.handle_reaction_update(_FakeSession(), tg_client, event)
    assert client.calls == []


@dataclass
class _FakeSnapshotRepoState:
    distinct_users: int = 0
    target_message: Any | None = None
    before_rows: list = field(default_factory=list)
    after_rows: list = field(default_factory=list)
    snapshot_calls: list[dict] = field(default_factory=list)
    upsert_user_calls: list = field(default_factory=list)
    record_llm_calls: list[dict] = field(default_factory=list)
    state: Any | None = None
    upsert_state_calls: list[dict] = field(default_factory=list)


@pytest.fixture
def patched_snapshot_repo(monkeypatch):
    state = _FakeSnapshotRepoState()

    async def _replace_snapshot(session, *, chat_id, message_id, rows):
        state.snapshot_calls.append(
            dict(chat_id=chat_id, message_id=message_id, rows=list(rows))
        )

    async def _count(session, *, chat_id, message_id, only_emojis=None):
        return state.distinct_users

    async def _fetch_target(session, *, chat_id, message_id):
        return state.target_message

    async def _fetch_around(
        session,
        *,
        chat_id,
        message_thread_id,
        target_telegram_date,
        target_message_id,
        before,
        after,
    ):
        return state.before_rows[-before:], state.after_rows[:after]

    async def _upsert_user(session, data):
        state.upsert_user_calls.append(data)

    async def _record(session, **kwargs):
        state.record_llm_calls.append(kwargs)

    async def _get_state(session, chat_id, message_id):
        return state.state

    async def _upsert_state(
        session,
        chat_id,
        message_id,
        *,
        last_distinct_trigger_users,
        last_evaluated_at=None,
        last_reply_at=None,
    ):
        call = dict(
            chat_id=chat_id,
            message_id=message_id,
            last_distinct_trigger_users=last_distinct_trigger_users,
            last_evaluated_at=last_evaluated_at,
            last_reply_at=last_reply_at,
        )
        state.upsert_state_calls.append(call)
        # Simulate persistence so next get returns latest
        state.state = SimpleNamespace(
            chat_id=chat_id,
            message_id=message_id,
            last_distinct_trigger_users=last_distinct_trigger_users,
            last_evaluated_at=last_evaluated_at,
            last_reply_at=(
                last_reply_at
                if last_reply_at is not None
                else (state.state.last_reply_at if state.state else None)
            ),
        )

    monkeypatch.setattr(
        reaction_service_module,
        "replace_message_reactions_snapshot",
        _replace_snapshot,
    )
    monkeypatch.setattr(
        reaction_service_module, "count_distinct_reaction_users", _count
    )
    monkeypatch.setattr(
        reaction_service_module,
        "fetch_message_by_chat_message_id",
        _fetch_target,
    )
    monkeypatch.setattr(
        reaction_service_module, "fetch_messages_around", _fetch_around
    )
    monkeypatch.setattr(reaction_service_module, "upsert_user", _upsert_user)
    monkeypatch.setattr(
        reaction_service_module, "record_llm_interaction", _record
    )
    monkeypatch.setattr(
        reaction_service_module, "get_reaction_state", _get_state
    )
    monkeypatch.setattr(
        reaction_service_module, "upsert_reaction_state", _upsert_state
    )
    return state


def _make_actor(user_id: int, emojis: list[str], *, is_bot: bool = False):
    return TgReactionActor(
        user=TgUser(
            id=user_id,
            is_bot=is_bot,
            username=f"u{user_id}",
            first_name=f"U{user_id}",
            last_name=None,
            language_code="en",
        ),
        emojis=list(emojis),
    )


def _make_snapshot(
    *,
    chat_id: int,
    message_id: int,
    actors: list[TgReactionActor],
    counts: dict[str, int] | None = None,
):
    if counts is None:
        counts = {}
        for a in actors:
            for e in a.emojis:
                counts[e] = counts.get(e, 0) + 1
    return TgMessageReactionSnapshot(
        chat_id=chat_id,
        message_id=message_id,
        actors=list(actors),
        counts=dict(counts),
    )


async def test_snapshot_disabled_no_op(patched_snapshot_repo):
    cfg = _FakeReactionsConfig(enabled=False)
    svc, client = _make_service(config=cfg)
    tg_client = _FakeTelegramClient()
    snap = _make_snapshot(
        chat_id=1,
        message_id=10,
        actors=[_make_actor(1, ["🔥"]), _make_actor(2, ["🔥"])],
    )
    await svc.handle_reaction_snapshot(_FakeSession(), tg_client, snap)
    assert client.calls == []
    assert patched_snapshot_repo.snapshot_calls == []


async def test_snapshot_below_threshold_persists_only(patched_snapshot_repo):
    cfg = _FakeReactionsConfig(min_distinct_users=5, reply_chance=1.0)
    patched_snapshot_repo.distinct_users = 2
    svc, client = _make_service(config=cfg)
    tg_client = _FakeTelegramClient()
    snap = _make_snapshot(
        chat_id=1,
        message_id=10,
        actors=[_make_actor(1, ["🔥"]), _make_actor(2, ["🔥"])],
    )
    await svc.handle_reaction_snapshot(_FakeSession(), tg_client, snap)
    assert client.calls == []
    tg_client.send_message.assert_not_awaited()
    assert len(patched_snapshot_repo.snapshot_calls) == 1
    assert patched_snapshot_repo.upsert_state_calls
    last = patched_snapshot_repo.upsert_state_calls[-1]
    assert last["last_distinct_trigger_users"] == 2
    assert last["last_reply_at"] is None


async def test_snapshot_threshold_met_with_chance_one_replies(patched_snapshot_repo):
    cfg = _FakeReactionsConfig(min_distinct_users=3, reply_chance=1.0)
    patched_snapshot_repo.distinct_users = 3
    patched_snapshot_repo.target_message = _make_target_row(10, thread_id=42)
    svc, client = _make_service(config=cfg, rng_value=0.0)
    tg_client = _FakeTelegramClient()
    snap = _make_snapshot(
        chat_id=1,
        message_id=10,
        actors=[
            _make_actor(1, ["🔥"]),
            _make_actor(2, ["🔥"]),
            _make_actor(3, ["🔥"]),
        ],
    )
    await svc.handle_reaction_snapshot(_FakeSession(), tg_client, snap)
    assert len(client.calls) == 1
    tg_client.send_message.assert_awaited_once()
    send_kwargs = tg_client.send_message.await_args.kwargs
    assert send_kwargs["chat_id"] == 1
    assert send_kwargs["reply_to_message_id"] == 10
    assert send_kwargs["message_thread_id"] == 42
    # state must record last_reply_at
    last = patched_snapshot_repo.upsert_state_calls[-1]
    assert last["last_reply_at"] is not None


async def test_snapshot_chance_zero_never_replies(patched_snapshot_repo):
    cfg = _FakeReactionsConfig(min_distinct_users=2, reply_chance=0.0)
    patched_snapshot_repo.distinct_users = 5
    patched_snapshot_repo.target_message = _make_target_row(10)
    svc, client = _make_service(config=cfg, rng_value=0.0)
    tg_client = _FakeTelegramClient()
    snap = _make_snapshot(
        chat_id=1,
        message_id=10,
        actors=[_make_actor(i, ["🔥"]) for i in range(1, 6)],
    )
    await svc.handle_reaction_snapshot(_FakeSession(), tg_client, snap)
    assert client.calls == []
    tg_client.send_message.assert_not_awaited()


async def test_snapshot_same_count_does_not_re_evaluate(patched_snapshot_repo):
    cfg = _FakeReactionsConfig(min_distinct_users=3, reply_chance=1.0)
    patched_snapshot_repo.distinct_users = 3
    patched_snapshot_repo.target_message = _make_target_row(10)
    # State already shows count=3 from earlier; second update with same
    # count must not roll the dice or call the LLM.
    patched_snapshot_repo.state = SimpleNamespace(
        chat_id=1,
        message_id=10,
        last_distinct_trigger_users=3,
        last_evaluated_at=None,
        last_reply_at=None,
    )
    svc, client = _make_service(config=cfg, rng_value=0.0)
    tg_client = _FakeTelegramClient()
    snap = _make_snapshot(
        chat_id=1,
        message_id=10,
        actors=[_make_actor(i, ["🔥"]) for i in range(1, 4)],
    )
    await svc.handle_reaction_snapshot(_FakeSession(), tg_client, snap)
    assert client.calls == []
    tg_client.send_message.assert_not_awaited()


async def test_snapshot_persistent_cooldown_blocks(patched_snapshot_repo):
    cfg = _FakeReactionsConfig(
        min_distinct_users=2, reply_chance=1.0, cooldown_seconds=600
    )
    patched_snapshot_repo.distinct_users = 5
    patched_snapshot_repo.target_message = _make_target_row(10)
    patched_snapshot_repo.state = SimpleNamespace(
        chat_id=1,
        message_id=10,
        last_distinct_trigger_users=3,
        last_evaluated_at=None,
        last_reply_at=datetime.now(UTC),
    )
    svc, client = _make_service(config=cfg, rng_value=0.0)
    tg_client = _FakeTelegramClient()
    snap = _make_snapshot(
        chat_id=1,
        message_id=10,
        actors=[_make_actor(i, ["🔥"]) for i in range(1, 6)],
    )
    await svc.handle_reaction_snapshot(_FakeSession(), tg_client, snap)
    assert client.calls == []
    tg_client.send_message.assert_not_awaited()


async def test_snapshot_skips_bot_actors(patched_snapshot_repo):
    cfg = _FakeReactionsConfig(min_distinct_users=1, reply_chance=1.0)
    patched_snapshot_repo.distinct_users = 1
    patched_snapshot_repo.target_message = _make_target_row(10)
    svc, _ = _make_service(config=cfg, rng_value=0.0)
    tg_client = _FakeTelegramClient()
    snap = _make_snapshot(
        chat_id=1,
        message_id=10,
        actors=[
            _make_actor(1, ["🔥"]),
            _make_actor(99, ["🔥"], is_bot=True),
        ],
    )
    await svc.handle_reaction_snapshot(_FakeSession(), tg_client, snap)
    # Only one user upserted: the human one
    assert len(patched_snapshot_repo.upsert_user_calls) == 1
    assert patched_snapshot_repo.upsert_user_calls[0].id == 1
    rows = patched_snapshot_repo.snapshot_calls[0]["rows"]
    assert rows == [(1, ["🔥"])]


async def test_snapshot_empty_is_ignored(patched_snapshot_repo):
    cfg = _FakeReactionsConfig(min_distinct_users=1, reply_chance=1.0)
    svc, client = _make_service(config=cfg)
    tg_client = _FakeTelegramClient()
    snap = _make_snapshot(chat_id=1, message_id=10, actors=[], counts={})
    await svc.handle_reaction_snapshot(_FakeSession(), tg_client, snap)
    assert patched_snapshot_repo.snapshot_calls == []
    assert client.calls == []


async def test_cooldown_blocks_second_reply(patched_repo):
    cfg = _FakeReactionsConfig(
        min_distinct_users=1,
        reply_chance=1.0,
        cooldown_seconds=600,
    )
    patched_repo.distinct_users = 3
    patched_repo.target_message = _make_target_row(10)
    svc, client = _make_service(config=cfg, rng_value=0.0)
    tg_client = _FakeTelegramClient()
    e1 = _make_event(chat_id=1, message_id=10, user_id=1, old=[], new=["🔥"])
    e2 = _make_event(chat_id=1, message_id=10, user_id=2, old=[], new=["🔥"])
    await svc.handle_reaction_update(_FakeSession(), tg_client, e1)
    await svc.handle_reaction_update(_FakeSession(), tg_client, e2)
    assert len(client.calls) == 1
    tg_client.send_message.assert_awaited_once()
