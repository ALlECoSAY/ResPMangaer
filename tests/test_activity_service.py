from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.llm.prompt_config import RuntimePromptConfig
from app.services import activity_service as activity_service_module
from app.services.activity_service import ActivityService
from app.telegram_client.types import TgChat, TgMessage, TgUser


class _FakeActivityConfig:
    def __init__(
        self,
        *,
        enabled: bool = True,
        min_messages: int = 3,
        window_minutes: int = 30,
        max_context_messages: int = 10,
        reply_chance: float = 1.0,
        reply_on_direct_reply_chance: float = 1.0,
        reply_on_follow_up_chance: float = 0.5,
        cooldown_seconds: int = 0,
        follow_up_window_seconds: int = 300,
        allowed: bool = True,
    ) -> None:
        self.enabled = enabled
        self.min_messages = min_messages
        self.window_minutes = window_minutes
        self.max_context_messages = max_context_messages
        self.reply_chance = reply_chance
        self.reply_on_direct_reply_chance = reply_on_direct_reply_chance
        self.reply_on_follow_up_chance = reply_on_follow_up_chance
        self.cooldown_seconds = cooldown_seconds
        self.follow_up_window_seconds = follow_up_window_seconds
        self._allowed = allowed

    def hour_is_allowed(self, hour: int) -> bool:
        del hour
        return self._allowed


class _FakeRuntimeConfig:
    max_reply_chars = 4000


class _FakeSettings:
    log_prompts = False
    openrouter_model = "test-model"


class _FakeOpenRouter:
    def __init__(self, response_text: str = "Yeah, that tracks.") -> None:
        self._response_text = response_text
        self.calls: list[tuple[str, str]] = []

    async def complete(self, system_prompt: str, user_prompt: str):
        self.calls.append((system_prompt, user_prompt))
        return SimpleNamespace(
            text=self._response_text,
            model="test-model",
            prompt_tokens=9,
            completion_tokens=4,
            latency_ms=33,
        )


class _FakeTelegramClient:
    def __init__(self) -> None:
        self.send_message = AsyncMock(
            return_value=TgMessage(
                chat=TgChat(
                    id=1,
                    type="supergroup",
                    title="Test",
                    username=None,
                ),
                message_id=900,
                message_thread_id=42,
                from_user=None,
                date=datetime(2026, 5, 10, 12, 0, tzinfo=UTC),
                text="Yeah, that tracks.",
                caption=None,
                content_type="text",
                reply_to_message_id=103,
            )
        )


@dataclass
class _FakeRepoState:
    count: int = 0
    rows: list = field(default_factory=list)
    activity_state: object | None = None
    record_llm_calls: list[dict] = field(default_factory=list)
    upsert_state_calls: list[dict] = field(default_factory=list)


class _FakeSession:
    pass


def _make_row(
    message_id: int,
    *,
    text: str,
    thread_id: int = 42,
    is_bot: bool = False,
    is_command: bool = False,
):
    return SimpleNamespace(
        chat_id=1,
        message_id=message_id,
        message_thread_id=thread_id,
        telegram_date=datetime(2026, 5, 10, 12, message_id % 60, tzinfo=UTC),
        clean_text=text,
        text=text,
        caption=None,
        sender_display_name=f"user{message_id}",
        content_type="text",
        is_bot_message=is_bot,
        is_command=is_command,
    )


def _make_incoming(
    *,
    message_id: int = 120,
    reply_to_message_id: int | None = 900,
    date: datetime | None = None,
) -> TgMessage:
    return TgMessage(
        chat=TgChat(id=1, type="supergroup", title="Test", username=None),
        message_id=message_id,
        message_thread_id=42,
        from_user=TgUser(
            id=55,
            is_bot=False,
            username="alice",
            first_name="Alice",
            last_name=None,
            language_code="en",
        ),
        date=date or datetime(2026, 5, 10, 12, 5, tzinfo=UTC),
        text="wait, what did you mean?",
        caption=None,
        content_type="text",
        reply_to_message_id=reply_to_message_id,
    )


def _make_service(
    *,
    config: _FakeActivityConfig,
    rng_value: float = 0.0,
) -> tuple[ActivityService, _FakeOpenRouter]:
    rng = random.Random()
    rng.random = lambda: rng_value  # type: ignore[method-assign]
    llm = _FakeOpenRouter()
    prompt_config = RuntimePromptConfig(path=Path("/tmp/__nonexistent_prompts.yaml"))
    svc = ActivityService(
        settings=_FakeSettings(),  # type: ignore[arg-type]
        config=config,  # type: ignore[arg-type]
        runtime_config=_FakeRuntimeConfig(),  # type: ignore[arg-type]
        client=llm,  # type: ignore[arg-type]
        prompt_config=prompt_config,
        rng=rng,
    )
    return svc, llm


def _patch_repo(monkeypatch, state: _FakeRepoState) -> None:
    async def _count(session, chat_id, message_thread_id, since, **kwargs):
        del session, chat_id, message_thread_id, since, kwargs
        return state.count

    async def _last(session, chat_id, message_thread_id, limit, since=None):
        del session, chat_id, message_thread_id, limit, since
        return list(state.rows)

    async def _get_state(session, chat_id, message_thread_id):
        del session, chat_id, message_thread_id
        return state.activity_state

    async def _upsert_state(session, chat_id, message_thread_id, **kwargs):
        del session
        call = dict(
            chat_id=chat_id,
            message_thread_id=message_thread_id,
            **kwargs,
        )
        state.upsert_state_calls.append(call)
        state.activity_state = SimpleNamespace(
            chat_id=chat_id,
            message_thread_id=message_thread_id,
            last_reply_at=kwargs["last_reply_at"],
            last_bot_message_id=kwargs["last_bot_message_id"],
            last_target_message_id=kwargs["last_target_message_id"],
        )

    async def _record(session, **kwargs):
        del session
        state.record_llm_calls.append(kwargs)

    monkeypatch.setattr(
        activity_service_module, "fetch_recent_message_count", _count
    )
    monkeypatch.setattr(activity_service_module, "fetch_last_messages", _last)
    monkeypatch.setattr(
        activity_service_module, "get_activity_reply_state", _get_state
    )
    monkeypatch.setattr(
        activity_service_module, "upsert_activity_reply_state", _upsert_state
    )
    monkeypatch.setattr(activity_service_module, "record_llm_interaction", _record)


async def test_random_reply_triggers_when_activity_threshold_met(monkeypatch):
    state = _FakeRepoState(
        count=3,
        rows=[
            _make_row(101, text="small talk"),
            _make_row(102, text="does this plan make sense?"),
            _make_row(103, text="this is a much more substantive point about it"),
        ],
    )
    _patch_repo(monkeypatch, state)
    svc, llm = _make_service(config=_FakeActivityConfig(), rng_value=0.0)
    tg_client = _FakeTelegramClient()

    await svc.maybe_trigger_random_reply(
        _FakeSession(),
        tg_client,
        chat_id=1,
        message_thread_id=42,
    )

    assert len(llm.calls) == 1
    assert ">>> [2026-05-10 12:42] user102" in llm.calls[0][1]
    tg_client.send_message.assert_awaited_once()
    send_kwargs = tg_client.send_message.await_args.kwargs
    assert send_kwargs["chat_id"] == 1
    assert send_kwargs["reply_to_message_id"] == 102
    assert send_kwargs["message_thread_id"] == 42
    assert state.record_llm_calls[0]["command_name"] == "activity_reply"
    assert state.upsert_state_calls[0]["last_bot_message_id"] == 900


async def test_random_reply_skips_when_dice_loses(monkeypatch):
    state = _FakeRepoState(count=10, rows=[_make_row(101, text="hello?")])
    _patch_repo(monkeypatch, state)
    svc, llm = _make_service(
        config=_FakeActivityConfig(reply_chance=0.1), rng_value=0.99
    )
    tg_client = _FakeTelegramClient()

    await svc.maybe_trigger_random_reply(
        _FakeSession(),
        tg_client,
        chat_id=1,
        message_thread_id=42,
    )

    assert llm.calls == []
    tg_client.send_message.assert_not_awaited()
    assert state.upsert_state_calls == []


async def test_direct_reply_to_activity_message_answers(monkeypatch):
    last_reply_at = datetime(2026, 5, 10, 12, 0, tzinfo=UTC)
    state = _FakeRepoState(
        rows=[
            _make_row(900, text="previous bot reply", is_bot=True),
            _make_row(120, text="wait, what did you mean?"),
        ],
        activity_state=SimpleNamespace(
            chat_id=1,
            message_thread_id=42,
            last_reply_at=last_reply_at,
            last_bot_message_id=900,
            last_target_message_id=103,
        ),
    )
    _patch_repo(monkeypatch, state)
    svc, llm = _make_service(config=_FakeActivityConfig(), rng_value=0.0)
    tg_client = _FakeTelegramClient()

    handled = await svc.handle_incoming_message(
        _FakeSession(),
        tg_client,
        _make_incoming(message_id=120, reply_to_message_id=900),
    )

    assert handled is True
    assert len(llm.calls) == 1
    assert state.record_llm_calls[0]["command_name"] == "activity_direct_reply"
    assert tg_client.send_message.await_args.kwargs["reply_to_message_id"] == 120


async def test_activity_prompt_strips_at_username_in_sender(monkeypatch):
    row = _make_row(102, text="does this plan make sense?")
    row.sender_display_name = "@alice"
    state = _FakeRepoState(count=3, rows=[row])
    _patch_repo(monkeypatch, state)
    svc, llm = _make_service(config=_FakeActivityConfig(), rng_value=0.0)
    tg_client = _FakeTelegramClient()

    await svc.maybe_trigger_random_reply(
        _FakeSession(),
        tg_client,
        chat_id=1,
        message_thread_id=42,
    )

    assert len(llm.calls) == 1
    prompt = llm.calls[0][1]
    assert "@alice" not in prompt
    assert "alice" in prompt


async def test_activity_reply_strips_at_username_before_sending(monkeypatch):
    state = _FakeRepoState(
        count=3,
        rows=[_make_row(102, text="does this plan make sense?")],
    )
    _patch_repo(monkeypatch, state)
    svc, llm = _make_service(config=_FakeActivityConfig(), rng_value=0.0)
    llm._response_text = "Nice point, @alice — agreed."
    tg_client = _FakeTelegramClient()

    await svc.maybe_trigger_random_reply(
        _FakeSession(),
        tg_client,
        chat_id=1,
        message_thread_id=42,
    )

    sent_text = tg_client.send_message.await_args.kwargs["text"]
    assert "@alice" not in sent_text
    assert "alice" in sent_text


async def test_plain_follow_up_respects_window_and_chance(monkeypatch):
    last_reply_at = datetime(2026, 5, 10, 12, 0, tzinfo=UTC)
    state = _FakeRepoState(
        rows=[_make_row(121, text="also this keeps going")],
        activity_state=SimpleNamespace(
            chat_id=1,
            message_thread_id=42,
            last_reply_at=last_reply_at,
            last_bot_message_id=110,
            last_target_message_id=103,
        ),
    )
    _patch_repo(monkeypatch, state)
    svc, llm = _make_service(
        config=_FakeActivityConfig(reply_on_follow_up_chance=1.0),
        rng_value=0.0,
    )
    tg_client = _FakeTelegramClient()

    handled = await svc.handle_incoming_message(
        _FakeSession(),
        tg_client,
        _make_incoming(
            message_id=121,
            reply_to_message_id=None,
            date=last_reply_at + timedelta(seconds=120),
        ),
    )

    assert handled is True
    assert len(llm.calls) == 1
    assert state.record_llm_calls[0]["command_name"] == "activity_follow_up_reply"
