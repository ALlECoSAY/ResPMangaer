from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.db.repositories import ChatMemoryProfile, ThreadMemoryProfile, UserMemoryProfile
from app.llm.context_builder import ContextBuilder
from app.llm.memory_config import RuntimeMemoryConfig
from app.llm.runtime_config import RuntimeContextConfig


def _builder(path: Path = Path("/nonexistent/context_limits.yaml")) -> ContextBuilder:
    return ContextBuilder(RuntimeContextConfig(path=path))


def _write_runtime_config(
    path: Path,
    *,
    max_cross_thread_messages: int = 30,
    max_context_chars: int = 24_000,
) -> None:
    path.write_text(
        f"""
        version: 1
        context:
          max_chars: {max_context_chars}
        ai:
          max_same_thread_messages: 80
          max_cross_thread_messages: {max_cross_thread_messages}
        """,
        encoding="utf-8",
    )


def _row(
    thread_id: int,
    when: datetime,
    body: str,
    sender: str = "alice",
    sender_user_id: int | None = 100,
):
    return SimpleNamespace(
        chat_id=1,
        message_thread_id=thread_id,
        sender_user_id=sender_user_id,
        telegram_date=when,
        clean_text=body,
        text=body,
        caption=None,
        sender_display_name=sender,
    )


class _StubSession:
    def __init__(self, same, cross, titles):
        self.same = same
        self.cross = cross
        self.titles = titles


@pytest.fixture
def patched_repo(monkeypatch):
    state: dict = {}

    async def _same(session, *, chat_id, message_thread_id, limit):
        return state["same"]

    async def _cross(session, *, chat_id, exclude_thread_id, limit, since=None):
        return state["cross"]

    async def _titles(session, chat_id):
        return state["titles"]

    monkeypatch.setattr("app.llm.context_builder.fetch_recent_same_thread", _same)
    monkeypatch.setattr("app.llm.context_builder.fetch_recent_cross_thread", _cross)
    monkeypatch.setattr("app.llm.context_builder.get_thread_titles", _titles)
    return state


async def test_same_thread_block_ordered_chronologically(patched_repo):
    base = datetime(2026, 5, 6, 12, 0, tzinfo=UTC)
    # Repo returns newest-first.
    patched_repo["same"] = [
        _row(5, base, "newest"),
        _row(5, base - timedelta(minutes=5), "older"),
    ]
    patched_repo["cross"] = []
    patched_repo["titles"] = {}
    ctx = await _builder().build_for_ai(
        session=None, chat_id=1, message_thread_id=5, question="what?"
    )
    assert "older" in ctx.context_text
    assert ctx.context_text.index("older") < ctx.context_text.index("newest")


async def test_cross_thread_capped(patched_repo, tmp_path: Path):
    base = datetime(2026, 5, 6, 12, 0, tzinfo=UTC)
    patched_repo["same"] = []
    patched_repo["cross"] = [
        _row(t, base - timedelta(minutes=t), f"msg{t}") for t in range(1, 200)
    ]
    patched_repo["titles"] = {}
    config_path = tmp_path / "context_limits.yaml"
    _write_runtime_config(config_path, max_cross_thread_messages=5)
    ctx = await _builder(config_path).build_for_ai(
        session=None, chat_id=1, message_thread_id=999, question="msg"
    )
    assert len(ctx.cross_thread_messages) == 5


async def test_context_respects_char_budget(patched_repo, tmp_path: Path):
    base = datetime(2026, 5, 6, 12, 0, tzinfo=UTC)
    patched_repo["same"] = [
        _row(5, base - timedelta(seconds=i), "x" * 200) for i in range(100)
    ]
    patched_repo["cross"] = []
    patched_repo["titles"] = {}
    config_path = tmp_path / "context_limits.yaml"
    _write_runtime_config(config_path, max_context_chars=1000)
    ctx = await _builder(config_path).build_for_ai(
        session=None, chat_id=1, message_thread_id=5, question="hi"
    )
    assert len(ctx.context_text) <= 1500  # budget + small headers


async def test_context_strips_at_username_from_stored_sender(patched_repo):
    base = datetime(2026, 5, 6, 12, 0, tzinfo=UTC)
    patched_repo["same"] = [_row(5, base, "hello", sender="@alice")]
    patched_repo["cross"] = []
    patched_repo["titles"] = {}
    ctx = await _builder().build_for_ai(
        session=None, chat_id=1, message_thread_id=5, question="hi"
    )
    assert "@alice" not in ctx.context_text
    assert "alice" in ctx.context_text


async def test_memory_block_is_inserted_with_budget(monkeypatch, patched_repo, tmp_path: Path):
    base = datetime(2026, 5, 6, 12, 0, tzinfo=UTC)
    patched_repo["same"] = [
        _row(5, base, "fresh question context", sender="alice", sender_user_id=100)
    ]
    patched_repo["cross"] = []
    patched_repo["titles"] = {}

    async def _chat_memory(session, chat_id):
        return ChatMemoryProfile(
            chat_id=chat_id,
            summary="Chat about release work. " + ("x" * 300),
            stable_facts=[],
            current_projects=["release"],
            decisions=["ship small batches"],
            open_questions=[],
            source_until_message_id=10,
            source_until_date=base,
            updated_at=base,
        )

    async def _thread_memory(session, chat_id, message_thread_id):
        return ThreadMemoryProfile(
            chat_id=chat_id,
            message_thread_id=message_thread_id,
            title="Launch",
            summary="Current launch thread summary. " + ("y" * 300),
            decisions=[],
            action_items=["Alice checks deploy"],
            open_questions=[],
            key_participants=["Alice"],
            source_until_message_id=10,
            source_until_date=base,
            updated_at=base,
        )

    async def _user_memories(session, chat_id, user_ids, limit):
        return [
            UserMemoryProfile(
                chat_id=chat_id,
                user_id=100,
                display_name="Alice",
                aliases=[],
                profile_summary="Handles deploys.",
                expertise=["deploys"],
                stated_preferences=["prefers short replies"],
                interaction_style="concise",
                evidence_message_ids=[1, 2, 3],
                confidence=0.8,
                source_until_message_id=10,
                updated_at=base,
            )
        ]

    monkeypatch.setattr("app.llm.context_builder.get_chat_memory", _chat_memory)
    monkeypatch.setattr("app.llm.context_builder.get_thread_memory", _thread_memory)
    monkeypatch.setattr(
        "app.llm.context_builder.fetch_user_memories_for_prompt",
        _user_memories,
    )

    context_path = tmp_path / "context_limits.yaml"
    _write_runtime_config(context_path, max_context_chars=1400)
    memory_path = tmp_path / "memory.yaml"
    memory_path.write_text(
        """
        version: 1
        memory:
          enabled: true
          max_chat_memory_chars: 120
          max_thread_memory_chars: 120
          max_user_memory_chars: 160
          max_profiles_per_prompt: 2
        """,
        encoding="utf-8",
    )

    builder = ContextBuilder(
        RuntimeContextConfig(path=context_path),
        RuntimeMemoryConfig(path=memory_path),
    )
    ctx = await builder.build_for_ai(
        session=None,
        chat_id=1,
        message_thread_id=5,
        question="what should we do?",
    )

    assert "MEMORY:" in ctx.context_text
    assert "Chat memory:" in ctx.context_text
    assert "Chat detail memory: Launch" in ctx.context_text
    assert "prefers short replies" in ctx.context_text
    assert len(ctx.context_text) <= 1700
