from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from app.config import Settings
from app.db.repositories import fetch_memory_refresh_candidates
from app.db.session import session_scope
from app.llm.memory_config import RuntimeMemoryConfig
from app.logging_config import get_logger

if TYPE_CHECKING:
    from app.services.memory_service import MemoryService

log = get_logger(__name__)


class MemoryPoller:
    """Periodically refreshes compact long-term memory from stored messages."""

    def __init__(
        self,
        *,
        settings: Settings,
        config: RuntimeMemoryConfig,
        memory_service: MemoryService,
    ) -> None:
        self._settings = settings
        self._config = config
        self._memory_service = memory_service
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="memory-poller")

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

    async def _run(self) -> None:
        log.info("memory.poller_started")
        try:
            while not self._stop.is_set():
                interval = max(1, self._config.poll_interval_seconds)
                if not (
                    self._memory_service.enabled
                    and self._config.enabled
                    and self._config.poll_enabled
                ):
                    await self._sleep(interval)
                    continue
                try:
                    await self._tick()
                except Exception as exc:
                    log.error("memory.poller_tick_failed", error=str(exc))
                await self._sleep(interval)
        finally:
            log.info("memory.poller_stopped")

    async def _sleep(self, seconds: int) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except TimeoutError:
            return

    async def _tick(self) -> None:
        now = datetime.now(UTC)
        stale_before = now - timedelta(minutes=self._config.update_min_interval_minutes)
        allowed = list(self._settings.allowed_chat_ids) or None
        limit = self._config.poll_max_threads_per_tick

        async with session_scope() as session:
            candidates = await fetch_memory_refresh_candidates(
                session,
                chat_ids=allowed,
                min_new_messages=self._config.update_min_new_messages,
                stale_before=stale_before,
                trigger_keywords=self._config.trigger_keywords,
                reaction_min_count=self._config.update_reaction_min_count,
                limit=limit,
            )

        if not candidates:
            log.debug("memory.poller_tick_empty")
            return

        log.info("memory.poller_tick", candidates=len(candidates))
        for candidate in candidates:
            if self._stop.is_set():
                return
            try:
                async with session_scope() as session:
                    result = await self._memory_service.refresh_thread(
                        session,
                        chat_id=candidate.chat_id,
                        message_thread_id=candidate.message_thread_id,
                        skip_threshold=True,
                    )
                log.info(
                    "memory.refresh_candidate_done",
                    chat_id=candidate.chat_id,
                    message_thread_id=candidate.message_thread_id,
                    updated=result.updated,
                    new_message_count=result.new_message_count,
                    skipped_reason=result.skipped_reason,
                )
            except Exception as exc:
                log.error(
                    "memory.refresh_candidate_failed",
                    chat_id=candidate.chat_id,
                    message_thread_id=candidate.message_thread_id,
                    error=str(exc),
                )
