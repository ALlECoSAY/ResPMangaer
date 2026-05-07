from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

from app.auth.access_control import AccessControl
from app.auth.yaml_store import YamlAccessStore
from app.bot.command_handlers import (
    CommandContext,
    handle_ai_command,
    handle_confirm_whitelist_command,
    handle_whitelist_command,
)
from app.config import Settings
from app.llm.runtime_config import RuntimeContextConfig
from app.services.ai_answer_service import AiAnswerService
from app.services.tldr_service import TldrService
from app.telegram_client.types import TgChat, TgMessage, TgUser


@dataclass
class _FakeClient:
    sent_messages: list[dict] = field(default_factory=list)
    typing_calls: list[dict] = field(default_factory=list)

    async def get_self_username(self) -> str | None:
        return "RespManager"

    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        reply_to_message_id: int | None = None,
        message_thread_id: int | None = None,
    ) -> TgMessage | None:
        self.sent_messages.append(
            {
                "chat_id": chat_id,
                "text": text,
                "reply_to_message_id": reply_to_message_id,
                "message_thread_id": message_thread_id,
            }
        )
        return _make_message(text=text, thread_id=message_thread_id or 0)

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
) -> tuple[CommandContext, _FakeClient, AsyncMock, YamlAccessStore]:
    admins = tmp_path / "admins.yaml"
    whitelist = tmp_path / "whitelist.yaml"
    context_limits = tmp_path / "context_limits.yaml"
    admins.write_text(admins_yaml, encoding="utf-8")
    whitelist.write_text(whitelist_yaml, encoding="utf-8")
    context_limits.write_text("version: 1\n", encoding="utf-8")

    store = YamlAccessStore(whitelist_path=whitelist, admins_path=admins)
    access_control = AccessControl(store=store, enabled=True)
    client = _FakeClient()
    ai_service = AsyncMock(spec=AiAnswerService)
    tldr_service = AsyncMock(spec=TldrService)
    ctx = CommandContext(
        message=message,
        client=client,
        settings=Settings(_env_file=None),
        access_control=access_control,
        yaml_store=store,
        ai_service=ai_service,
        tldr_service=tldr_service,
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

    ai_service.answer.assert_not_awaited()
    assert client.sent_messages
    assert "not whitelisted" in client.sent_messages[0]["text"]


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
