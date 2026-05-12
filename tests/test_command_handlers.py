from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from app.auth.access_control import AccessControl
from app.auth.yaml_store import YamlAccessStore
from app.bot import command_handlers as command_handlers_module
from app.bot.command_handlers import (
    CommandContext,
    handle_ai_command,
    handle_confirm_whitelist_command,
    handle_help_command,
    handle_stats_command,
    handle_whitelist_command,
)
from app.config import Settings
from app.llm.runtime_config import RuntimeContextConfig
from app.services.auto_delete_config import RuntimeAutoDeleteConfig
from app.services.memory_service import ExplicitMemoryResult
from app.services.stats_config import RuntimeStatsConfig
from app.services.stats_report import StatsReport
from app.services.stats_service import StatsService
from app.telegram_client.types import TgChat, TgMessage, TgUser


@dataclass
class _FakeClient:
    sent_messages: list[dict] = field(default_factory=list)
    sent_photos: list[dict] = field(default_factory=list)
    deleted: list[dict] = field(default_factory=list)
    typing_calls: list[dict] = field(default_factory=list)
    next_message_id: int = 1000

    async def get_self_username(self) -> str | None:
        return "RespManager"

    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        reply_to_message_id: int | None = None,
        message_thread_id: int | None = None,
        formatting_entities: list[object] | None = None,
    ) -> TgMessage | None:
        self.sent_messages.append(
            {
                "chat_id": chat_id,
                "text": text,
                "reply_to_message_id": reply_to_message_id,
                "message_thread_id": message_thread_id,
                "formatting_entities": formatting_entities,
            }
        )
        message_id = self.next_message_id
        self.next_message_id += 1
        msg = _make_message(text=text, thread_id=message_thread_id or 0)
        return TgMessage(
            chat=msg.chat,
            message_id=message_id,
            message_thread_id=msg.message_thread_id,
            from_user=msg.from_user,
            date=msg.date,
            text=msg.text,
            caption=msg.caption,
            content_type=msg.content_type,
            reply_to_message_id=msg.reply_to_message_id,
            reply_to_from_user=msg.reply_to_from_user,
        )

    async def send_photo(
        self,
        chat_id: int,
        image_bytes: bytes,
        *,
        caption: str | None = None,
        reply_to_message_id: int | None = None,
        message_thread_id: int | None = None,
        formatting_entities: list[object] | None = None,
        file_name: str = "stats.png",
    ) -> TgMessage | None:
        self.sent_photos.append(
            {
                "chat_id": chat_id,
                "image_bytes": image_bytes,
                "caption": caption,
                "reply_to_message_id": reply_to_message_id,
                "message_thread_id": message_thread_id,
                "formatting_entities": formatting_entities,
                "file_name": file_name,
            }
        )
        message_id = self.next_message_id
        self.next_message_id += 1
        msg = _make_message(text=caption or "", thread_id=message_thread_id or 0)
        return TgMessage(
            chat=msg.chat,
            message_id=message_id,
            message_thread_id=msg.message_thread_id,
            from_user=msg.from_user,
            date=msg.date,
            text=None,
            caption=caption,
            content_type="photo",
            reply_to_message_id=msg.reply_to_message_id,
            reply_to_from_user=msg.reply_to_from_user,
        )

    async def delete_messages(
        self,
        chat_id: int,
        message_ids: list[int],
    ) -> None:
        self.deleted.append({"chat_id": chat_id, "message_ids": list(message_ids)})

    async def send_typing(
        self,
        chat_id: int,
        *,
        message_thread_id: int | None = None,
    ) -> None:
        self.typing_calls.append(
            {"chat_id": chat_id, "message_thread_id": message_thread_id}
        )

    async def set_reaction(self, chat_id: int, message_id: int, emoji: str) -> None:
        return None


class _FakeSessionScope:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, exc_type, exc, tb):
        return None


class _FakeStatsService:
    enabled = True
    default_lookback_days = 7
    max_message_chars = 20

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def summary(self, session, chat_id: int, lookback):
        self.calls.append({"method": "summary", "chat_id": chat_id, "lookback": lookback})
        return ["Stats", "x" * 30]

    async def user_stats(self, session, chat_id: int, lookback):
        self.calls.append({"method": "users", "chat_id": chat_id, "lookback": lookback})
        return ["Users"]


class _FakeAiService:
    def __init__(self) -> None:
        self.answer_calls = 0

    async def answer(self, *args, **kwargs):
        self.answer_calls += 1
        return None

    def assert_not_awaited(self) -> None:
        assert self.answer_calls == 0


class _FakeMemoryService:
    enabled = True

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def remember_text(self, session, *, chat_id, text, source_message_id=None):
        del session
        self.calls.append(
            {
                "chat_id": chat_id,
                "text": text,
                "source_message_id": source_message_id,
            }
        )
        return ExplicitMemoryResult(updated=True, saved_text=text, user_updates=1)


class _FakeTldrService:
    async def summarize(self, *args, **kwargs):
        return None, "No meaningful recent activity found."


def _make_message(
    *,
    text: str,
    user_id: int = 100,
    username: str | None = "alice",
    reply_to_from_user: TgUser | None = None,
    thread_id: int = 0,
) -> TgMessage:
    return TgMessage(
        chat=TgChat(id=1, type="supergroup", title="Chat", username=None, is_forum=True),
        message_id=10,
        message_thread_id=thread_id,
        from_user=TgUser(
            id=user_id,
            is_bot=False,
            username=username,
            first_name="Alice",
            last_name=None,
            language_code="en",
        ),
        date=datetime(2026, 5, 7, tzinfo=UTC),
        text=text,
        caption=None,
        content_type="text",
        reply_to_message_id=None,
        reply_to_from_user=reply_to_from_user,
    )


def _make_ctx(
    tmp_path: Path,
    *,
    message: TgMessage,
    admins_yaml: str,
    whitelist_yaml: str,
) -> tuple[CommandContext, _FakeClient, _FakeAiService, YamlAccessStore]:
    admins = tmp_path / "admins.yaml"
    whitelist = tmp_path / "whitelist.yaml"
    context_limits = tmp_path / "context_limits.yaml"
    stats = tmp_path / "stats.yaml"
    admins.write_text(admins_yaml, encoding="utf-8")
    whitelist.write_text(whitelist_yaml, encoding="utf-8")
    context_limits.write_text("version: 1\n", encoding="utf-8")
    stats.write_text("version: 1\n", encoding="utf-8")

    store = YamlAccessStore(whitelist_path=whitelist, admins_path=admins)
    access_control = AccessControl(store=store, enabled=True)
    client = _FakeClient()
    ai_service = _FakeAiService()
    tldr_service = _FakeTldrService()
    ctx = CommandContext(
        message=message,
        client=client,
        settings=Settings(_env_file=None),
        access_control=access_control,
        yaml_store=store,
        ai_service=ai_service,
        tldr_service=tldr_service,
        stats_service=StatsService(RuntimeStatsConfig(path=stats)),
        runtime_config=RuntimeContextConfig(path=context_limits),
        bot_username_provider=lambda: "RespManager",
    )
    return ctx, client, ai_service, store


async def test_ai_denied_before_llm_call(tmp_path: Path) -> None:
    ctx, client, ai_service, _store = _make_ctx(
        tmp_path,
        message=_make_message(text="/ai explain this"),
        admins_yaml="version: 1\nadmins: []\n",
        whitelist_yaml="version: 1\nusers: []\n",
    )

    await handle_ai_command(ctx)

    ai_service.assert_not_awaited()
    assert client.sent_messages
    assert "not whitelisted" in client.sent_messages[0]["text"]


async def test_ai_remember_request_updates_memory_without_llm(
    tmp_path: Path,
    monkeypatch,
) -> None:
    ctx, client, ai_service, _store = _make_ctx(
        tmp_path,
        message=_make_message(text="/ai запомни Phoenix2005 зовут Алиса"),
        admins_yaml="version: 1\nadmins: []\n",
        whitelist_yaml="version: 1\nusers:\n  - id: 100\n",
    )
    memory_service = _FakeMemoryService()
    ctx.memory_service = memory_service  # type: ignore[assignment]
    monkeypatch.setattr(command_handlers_module, "session_scope", lambda: _FakeSessionScope())

    await handle_ai_command(ctx)

    ai_service.assert_not_awaited()
    assert memory_service.calls == [
        {
            "chat_id": 1,
            "text": "запомни Phoenix2005 зовут Алиса",
            "source_message_id": 10,
        }
    ]
    assert "Запомнил" in client.sent_messages[0]["text"]


async def test_whitelist_command_prompts_for_confirmation(tmp_path: Path) -> None:
    target_user = TgUser(
        id=555,
        is_bot=False,
        username="target",
        first_name="Target",
        last_name=None,
        language_code="en",
    )
    ctx, client, _ai_service, _store = _make_ctx(
        tmp_path,
        message=_make_message(
            text="/whitelist",
            user_id=200,
            username="admin",
            reply_to_from_user=target_user,
        ),
        admins_yaml="version: 1\nadmins:\n  - id: 200\n",
        whitelist_yaml="version: 1\nusers: []\n",
    )

    await handle_whitelist_command(ctx)

    assert client.sent_messages
    assert "/confirm_whitelist 555" in client.sent_messages[0]["text"]


async def test_confirm_whitelist_writes_yaml(tmp_path: Path) -> None:
    ctx, client, _ai_service, store = _make_ctx(
        tmp_path,
        message=_make_message(text="/confirm_whitelist 777", user_id=200, username="admin"),
        admins_yaml="version: 1\nadmins:\n  - id: 200\n",
        whitelist_yaml="version: 1\nusers: []\n",
    )

    await handle_confirm_whitelist_command(ctx)

    assert "added to whitelist" in client.sent_messages[0]["text"]
    assert 777 in await store.get_whitelisted_user_ids()


async def test_help_command_lists_available_commands(tmp_path: Path) -> None:
    ctx, client, _ai_service, _store = _make_ctx(
        tmp_path,
        message=_make_message(text="/help", user_id=999, username="not_whitelisted"),
        admins_yaml="version: 1\nadmins: []\n",
        whitelist_yaml="version: 1\nusers: []\n",
    )

    await handle_help_command(ctx)

    text = client.sent_messages[0]["text"]
    assert "/ai <question>" in text
    assert "/stats" in text
    assert "/whitelist" in text
    assert client.sent_messages[0]["reply_to_message_id"] == 10


async def test_stats_command_uses_service_and_split_limit(tmp_path: Path, monkeypatch) -> None:
    ctx, client, _ai_service, _store = _make_ctx(
        tmp_path,
        message=_make_message(text="/stats"),
        admins_yaml="version: 1\nadmins: []\n",
        whitelist_yaml="version: 1\nusers:\n  - id: 100\n",
    )
    stats_service = _FakeStatsService()
    ctx.stats_service = stats_service  # type: ignore[assignment]
    monkeypatch.setattr(command_handlers_module, "session_scope", lambda: _FakeSessionScope())

    await handle_stats_command(ctx)

    assert stats_service.calls[0]["method"] == "summary"
    assert len(client.sent_messages) >= 2
    assert client.sent_messages[0]["reply_to_message_id"] == 10


class _ImageStatsService:
    enabled = True
    default_lookback_days = 7
    max_message_chars = 3900
    render_as_images = True

    async def summary(self, session, chat_id: int, lookback):
        return StatsReport(
            title="Chat Stats · last 7d",
            visible_lines=["Messages: 5", "Top chatter: alice (3)"],
            graph_lines=["alice ████ 3", "bob ░░ 1"],
            detail_lines=["alice messaged a lot today"],
        )


async def test_stats_command_sends_image_when_configured(
    tmp_path: Path,
    monkeypatch,
) -> None:
    ctx, client, _ai_service, _store = _make_ctx(
        tmp_path,
        message=_make_message(text="/stats"),
        admins_yaml="version: 1\nadmins: []\n",
        whitelist_yaml="version: 1\nusers:\n  - id: 100\n",
    )
    ctx.stats_service = _ImageStatsService()  # type: ignore[assignment]
    monkeypatch.setattr(command_handlers_module, "session_scope", lambda: _FakeSessionScope())

    async def _fake_render(self, report, *, max_chars):
        from app.services.stats_image_renderer import RenderedStatsImage

        return RenderedStatsImage(
            image_bytes=b"PNG",
            caption="caption",
            caption_entities=[],
            detail_text="Details:\n> alice messaged a lot today",
            detail_entities=[],
        )

    monkeypatch.setattr(
        command_handlers_module.StatsImageRenderer,
        "render",
        _fake_render,
    )

    await handle_stats_command(ctx)

    assert client.sent_photos, "expected photo to be sent"
    assert client.sent_photos[0]["caption"] == "caption"
    assert client.sent_photos[0]["reply_to_message_id"] == 10
    assert any(
        msg["text"].startswith("Details:") for msg in client.sent_messages
    ), "expected detail text follow-up"


async def test_help_command_schedules_auto_delete(tmp_path: Path) -> None:
    ctx, client, _ai_service, _store = _make_ctx(
        tmp_path,
        message=_make_message(text="/help", user_id=999, username="not_whitelisted"),
        admins_yaml="version: 1\nadmins: []\n",
        whitelist_yaml="version: 1\nusers: []\n",
    )

    auto_delete_yaml = tmp_path / "auto_delete.yaml"
    auto_delete_yaml.write_text(
        "version: 1\nauto_delete:\n  help: 1\n",
        encoding="utf-8",
    )
    ctx.auto_delete_config = RuntimeAutoDeleteConfig(path=auto_delete_yaml)

    await handle_help_command(ctx)

    assert client.sent_messages
    sent_message_id = client.sent_messages[0]
    # Wait for the deletion task to fire.
    for _ in range(30):
        if client.deleted:
            break
        await asyncio.sleep(0.05)
    assert client.deleted, "expected auto-delete to fire"
    assert client.deleted[0]["chat_id"] == ctx.message.chat.id
    assert client.deleted[0]["message_ids"], "expected at least one deleted id"
    del sent_message_id


async def test_stats_command_skips_auto_delete_when_no_config(
    tmp_path: Path,
    monkeypatch,
) -> None:
    ctx, client, _ai_service, _store = _make_ctx(
        tmp_path,
        message=_make_message(text="/stats"),
        admins_yaml="version: 1\nadmins: []\n",
        whitelist_yaml="version: 1\nusers:\n  - id: 100\n",
    )
    ctx.stats_service = _FakeStatsService()  # type: ignore[assignment]
    monkeypatch.setattr(command_handlers_module, "session_scope", lambda: _FakeSessionScope())
    ctx.auto_delete_config = None

    await handle_stats_command(ctx)

    await asyncio.sleep(0.1)
    assert not client.deleted


async def test_stats_command_reports_bad_args(tmp_path: Path) -> None:
    ctx, client, _ai_service, _store = _make_ctx(
        tmp_path,
        message=_make_message(text="/stats nope"),
        admins_yaml="version: 1\nadmins: []\n",
        whitelist_yaml="version: 1\nusers:\n  - id: 100\n",
    )

    await handle_stats_command(ctx)

    assert "Usage" in client.sent_messages[0]["text"]
