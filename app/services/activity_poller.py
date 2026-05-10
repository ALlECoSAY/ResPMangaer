from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta

from app.config import Settings
from app.db.repositories import fetch_active_threads
from app.db.session import session_scope
from app.llm.activity_config import RuntimeActivityConfig
from app.logging_config import get_logger
from app.services.activity_service import ActivityService
from app.telegram_client.client import TelegramClientProtocol

log = get_logger(__name__)


class ActivityPoller:
    """Periodically scans stored messages for active chat/thread bursts."""

    def __init__(
        self,
        *,
        settings: Settings,
        config: RuntimeActivityConfig,
        activity_service: ActivityService,
    ) -> None:
        self._settings = settings
        self._config = config
        self._activity_service = activity_service
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    def start(self, client: TelegramClientProtocol) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(
            self._run(client), name="activity-poller"
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
        log.info("activity.poller_started")
        try:
            while not self._stop.is_set():
                interval = max(1, self._config.poll_interval_seconds)
                if not (
                    self._activity_service.enabled and self._config.poll_enabled
                ):
                    await self._sleep(interval)
                    continue
                try:
                    await self._tick(client)
                except Exception as exc:
                    log.error("activity.poller_tick_failed", error=str(exc))
                await self._sleep(interval)
        finally:
            log.info("activity.poller_stopped")

    async def _sleep(self, seconds: int) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except TimeoutError:
            return

    async def _tick(self, client: TelegramClientProtocol) -> None:
        now = datetime.now(UTC)
        since = now - timedelta(minutes=self._config.poll_window_minutes)
        allowed = list(self._settings.allowed_chat_ids) or None
        limit = self._config.poll_max_threads_per_tick

        async with session_scope() as session:
            candidates = await fetch_active_threads(
                session,
                chat_ids=allowed,
                since=since,
                min_messages=self._config.min_messages,
                limit=limit,
            )

        if not candidates:
            log.debug("activity.poller_tick_empty")
            return

        log.info(
            "activity.poller_tick",
            candidates=len(candidates),
            window_minutes=self._config.poll_window_minutes,
        )

        for chat_id, message_thread_id, message_count in candidates:
            if self._stop.is_set():
                return
            try:
                async with session_scope() as session:
                    await self._activity_service.maybe_trigger_random_reply(
                        session,
                        client,
                        chat_id=chat_id,
                        message_thread_id=message_thread_id,
                        observed_count=message_count,
                    )
            except Exception as exc:
                log.error(
                    "activity.poller_handle_failed",
                    chat_id=chat_id,
                    message_thread_id=message_thread_id,
                    error=str(exc),
                )
