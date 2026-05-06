from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject

from app.config import Settings
from app.db.session import session_scope
from app.logging_config import get_logger
from app.services.message_ingestion import ingest_message

log = get_logger(__name__)


class ChatAllowlistMiddleware(BaseMiddleware):
    """Drop events from chats outside the allowlist (when configured)."""

    def __init__(self, allowed_chat_ids: set[int]) -> None:
        self._allowed = allowed_chat_ids

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not self._allowed:
            return await handler(event, data)
        chat = getattr(event, "chat", None)
        if chat is None:
            return await handler(event, data)
        if chat.id not in self._allowed:
            log.warning("chat.not_allowed", chat_id=chat.id)
            return None
        return await handler(event, data)


class MessageIngestionMiddleware(BaseMiddleware):
    """Persist every visible message before handlers run."""

    def __init__(self, settings: Settings, bot_username_provider: Callable[[], str | None]) -> None:
        self._settings = settings
        self._bot_username_provider = bot_username_provider

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message):
            try:
                async with session_scope() as session:
                    await ingest_message(
                        session,
                        event,
                        self._settings,
                        self._bot_username_provider(),
                    )
            except Exception as exc:  # don't break command handling on ingest failure
                log.error("ingest.failed", error=str(exc))
        return await handler(event, data)
