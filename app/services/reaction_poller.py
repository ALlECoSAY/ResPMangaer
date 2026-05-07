from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta

from app.config import Settings
from app.db.repositories import fetch_messages_for_reaction_poll
from app.db.session import session_scope
from app.llm.reactions_config import RuntimeReactionsConfig
from app.logging_config import get_logger
from app.services.reaction_service import ReactionService
from app.telegram_client.client import TelegramClientProtocol

log = get_logger(__name__)


class ReactionPoller:
    """Periodically refreshes reaction snapshots for recently ingested messages.

    Telegram's user-API only pushes ``UpdateMessageReactions`` for messages the
    account has a stake in (sent or reacted-to), so reactions on third-party
    messages need to be discovered by polling.
    """

    def __init__(
        self,
        *,
        settings: Settings,
        config: RuntimeReactionsConfig,
        reaction_service: ReactionService,
    ) -> None:
        self._settings = settings
        self._config = config
        self._reaction_service = reaction_service
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    def start(self, client: TelegramClientProtocol) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(
            self._run(client), name="reaction-poller"
        )

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except TimeoutError:
                self._task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await self._task
            self._task = None

    async def _run(self, client: TelegramClientProtocol) -> None:
        log.info("reactions.poller_started")
        try:
            while not self._stop.is_set():
                interval = max(1, self._config.poll_interval_seconds)
                if not (
                    self._reaction_service.enabled and self._config.poll_enabled
                ):
                    await self._sleep(interval)
                    continue
                try:
                    await self._tick(client)
                except Exception as exc:
                    log.error("reactions.poller_tick_failed", error=str(exc))
                await self._sleep(interval)
        finally:
            log.info("reactions.poller_stopped")

    async def _sleep(self, seconds: int) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except TimeoutError:
            return

    async def _tick(self, client: TelegramClientProtocol) -> None:
        now = datetime.now(UTC)
        since = now - timedelta(minutes=self._config.poll_window_minutes)
        stale_before = now - timedelta(seconds=self._config.poll_interval_seconds)
        limit = self._config.poll_max_messages_per_tick
        allowed = list(self._settings.allowed_chat_ids) or None

        async with session_scope() as session:
            candidates = await fetch_messages_for_reaction_poll(
                session,
                chat_ids=allowed,
                since=since,
                stale_before=stale_before,
                limit=limit,
            )

        if not candidates:
            log.debug("reactions.poller_tick_empty")
            return

        log.info(
            "reactions.poller_tick",
            candidates=len(candidates),
            window_minutes=self._config.poll_window_minutes,
        )

        triggers = self._reaction_service.trigger_emojis
        fetch_limit = self._reaction_service.fetch_limit_per_emoji

        for chat_id, message_id in candidates:
            if self._stop.is_set():
                return
            try:
                snapshot = await client.fetch_message_reaction_snapshot(
                    chat_id=chat_id,
                    message_id=message_id,
                    trigger_emojis=triggers,
                    limit_per_emoji=fetch_limit,
                )
            except Exception as exc:
                log.warning(
                    "reactions.poller_fetch_failed",
                    chat_id=chat_id,
                    message_id=message_id,
                    error=str(exc),
                )
                continue
            if snapshot is None:
                continue
            try:
                async with session_scope() as session:
                    await self._reaction_service.handle_reaction_snapshot(
                        session, client, snapshot
                    )
            except Exception as exc:
                log.error(
                    "reactions.poller_handle_failed",
                    chat_id=chat_id,
                    message_id=message_id,
                    error=str(exc),
                )
