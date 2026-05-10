from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.llm.context_builder import ContextBuilder
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


def _row(thread_id: int, when: datetime, body: str, sender: str = "alice"):
    return SimpleNamespace(
        chat_id=1,
        message_thread_id=thread_id,
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
