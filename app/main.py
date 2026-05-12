from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass

from app.auth.access_control import AccessControl
from app.auth.yaml_store import YamlAccessStore
from app.bot.command_handlers import (
    CommandContext,
    handle_ai_command,
    handle_confirm_whitelist_command,
    handle_help_command,
    handle_memory_command,
    handle_memory_forget_command,
    handle_memory_refresh_command,
    handle_memory_user_command,
    handle_stats_command,
    handle_tldr_command,
    handle_whitelist_command,
)
from app.bot.commands import parse_command
from app.config import Settings, get_settings
from app.db.session import dispose_engine, init_engine, session_scope
from app.llm.activity_config import RuntimeActivityConfig
from app.llm.context_builder import ContextBuilder
from app.llm.memory_config import RuntimeMemoryConfig
from app.llm.openrouter_client import OpenRouterClient
from app.llm.reactions_config import RuntimeReactionsConfig
from app.llm.runtime_config import RuntimeContextConfig
from app.logging_config import configure_logging, get_logger
from app.services.activity_poller import ActivityPoller
from app.services.activity_service import ActivityService
from app.services.ai_answer_service import AiAnswerService
from app.services.auto_delete_config import RuntimeAutoDeleteConfig
from app.services.memory_poller import MemoryPoller
from app.services.memory_service import (
    MemoryService,
    format_explicit_memory_result,
    is_explicit_memory_request,
)
from app.services.reaction_poller import ReactionPoller
from app.services.reaction_service import ReactionService
from app.services.stats_config import RuntimeStatsConfig
from app.services.stats_service import StatsService
from app.services.tldr_service import TldrService


@dataclass
class AppServices:
    yaml_store: YamlAccessStore
    access_control: AccessControl
    ai_service: AiAnswerService
    tldr_service: TldrService
    stats_service: StatsService
    activity_service: ActivityService
    activity_poller: ActivityPoller
    reaction_service: ReactionService
    reaction_poller: ReactionPoller
    memory_service: MemoryService
    memory_poller: MemoryPoller
    runtime_context_config: RuntimeContextConfig
    runtime_memory_config: RuntimeMemoryConfig
    runtime_stats_config: RuntimeStatsConfig
    runtime_auto_delete_config: RuntimeAutoDeleteConfig


def build_services(settings: Settings) -> AppServices:
    yaml_store = YamlAccessStore(
        whitelist_path=settings.whitelist_yaml_path,
        admins_path=settings.admins_yaml_path,
    )
    access_control = AccessControl(store=yaml_store, enabled=settings.access_control_enabled)
    openrouter = OpenRouterClient(
        api_key=settings.openrouter_api_key,
        base_url=settings.openrouter_base_url,
        model=settings.openrouter_model,
        site_url=settings.openrouter_site_url,
        site_name=settings.openrouter_site_name,
    )
    runtime_context_config = RuntimeContextConfig(path=settings.context_limits_yaml_path)
    runtime_memory_config = RuntimeMemoryConfig(path=settings.memory_yaml_path)
    context_builder = ContextBuilder(runtime_context_config, runtime_memory_config)
    ai_service = AiAnswerService(settings, context_builder, openrouter)
    tldr_service = TldrService(settings, openrouter, runtime_context_config)
    runtime_stats_config = RuntimeStatsConfig(path=settings.stats_yaml_path)
    stats_service = StatsService(runtime_stats_config)
    runtime_auto_delete_config = RuntimeAutoDeleteConfig(
        path=settings.auto_delete_yaml_path
    )
    activity_config = RuntimeActivityConfig(path=settings.activity_yaml_path)
    activity_service = ActivityService(
        settings=settings,
        config=activity_config,
        runtime_config=runtime_context_config,
        client=openrouter,
    )
    activity_poller = ActivityPoller(
        settings=settings,
        config=activity_config,
        activity_service=activity_service,
    )
    reactions_config = RuntimeReactionsConfig(path=settings.reactions_yaml_path)
    reaction_service = ReactionService(
        settings=settings,
        config=reactions_config,
        runtime_config=runtime_context_config,
        client=openrouter,
    )
    reaction_poller = ReactionPoller(
        settings=settings,
        config=reactions_config,
        reaction_service=reaction_service,
    )
    memory_service = MemoryService(
        settings=settings,
        config=runtime_memory_config,
        client=openrouter,
    )
    memory_poller = MemoryPoller(
        settings=settings,
        config=runtime_memory_config,
        memory_service=memory_service,
    )
    return AppServices(
        yaml_store=yaml_store,
        access_control=access_control,
        ai_service=ai_service,
        tldr_service=tldr_service,
        stats_service=stats_service,
        activity_service=activity_service,
        activity_poller=activity_poller,
        reaction_service=reaction_service,
        reaction_poller=reaction_poller,
        memory_service=memory_service,
        memory_poller=memory_poller,
        runtime_context_config=runtime_context_config,
        runtime_memory_config=runtime_memory_config,
        runtime_stats_config=runtime_stats_config,
        runtime_auto_delete_config=runtime_auto_delete_config,
    )


async def run_user_api(settings: Settings, services: AppServices) -> int:
    from telethon import events
    from telethon.tl import types as tl_types
    from telethon.utils import get_peer_id

    from app.services.message_ingestion import ingest_message
    from app.telegram_client.telethon_adapter import TelethonUserClient

    log = get_logger("app.main.user")
    client = TelethonUserClient(
        session_path=settings.telegram_user_session_path,
        api_id=settings.telegram_api_id or 0,
        api_hash=settings.telegram_api_hash,
    )
    await client.connect()
    if not await client.is_authorized():
        log.error(
            "startup.user_session_missing",
            session_path=str(settings.telegram_user_session_path),
        )
        await client.disconnect()
        return 2

    me = await client.raw_client.get_me()
    username = await client.get_self_username()
    log.info("user.identity", id=getattr(me, "id", None), username=username)

    def bot_username_provider() -> str | None:
        return username

    @client.raw_client.on(events.NewMessage(incoming=True))
    async def handle_new_message(event) -> None:
        tg_message = await client.message_to_tg_message(event.message)
        if settings.allowed_chat_ids and tg_message.chat.id not in settings.allowed_chat_ids:
            return

        try:
            async with session_scope() as session:
                await ingest_message(
                    session,
                    tg_message,
                    settings,
                    bot_username_provider(),
                )
        except Exception as exc:
            log.error("ingest.failed", error=str(exc))

        parsed = parse_command(tg_message.text, bot_username_provider())
        if parsed is None:
            raw_text = tg_message.text or tg_message.caption or ""
            if is_explicit_memory_request(raw_text):
                user_id = tg_message.from_user.id if tg_message.from_user else None
                decision = await services.access_control.can_use_ai_commands(user_id)
                if not decision.allowed:
                    await client.send_message(
                        tg_message.chat.id,
                        decision.reason or "denied",
                        reply_to_message_id=tg_message.message_id,
                        message_thread_id=tg_message.message_thread_id or None,
                    )
                    return
                if not services.memory_service.enabled:
                    await client.send_message(
                        tg_message.chat.id,
                        "Memory is disabled right now.",
                        reply_to_message_id=tg_message.message_id,
                        message_thread_id=tg_message.message_thread_id or None,
                    )
                    return
                try:
                    async with session_scope() as session:
                        result = await services.memory_service.remember_text(
                            session,
                            chat_id=tg_message.chat.id,
                            text=raw_text,
                            source_message_id=tg_message.message_id,
                        )
                    await client.send_message(
                        tg_message.chat.id,
                        format_explicit_memory_result(result),
                        reply_to_message_id=tg_message.message_id,
                        message_thread_id=tg_message.message_thread_id or None,
                    )
                except Exception as exc:
                    log.error("memory_explicit.failed", error=str(exc))
                    await client.send_message(
                        tg_message.chat.id,
                        "I could not update memory right now.",
                        reply_to_message_id=tg_message.message_id,
                        message_thread_id=tg_message.message_thread_id or None,
                    )
                return
            try:
                async with session_scope() as session:
                    await services.activity_service.handle_incoming_message(
                        session, client, tg_message
                    )
            except Exception as exc:
                log.error("activity.incoming_handle_failed", error=str(exc))
            return

        ctx = CommandContext(
            message=tg_message,
            client=client,
            settings=settings,
            access_control=services.access_control,
            yaml_store=services.yaml_store,
            ai_service=services.ai_service,
            tldr_service=services.tldr_service,
            stats_service=services.stats_service,
            memory_service=services.memory_service,
            runtime_config=services.runtime_context_config,
            bot_username_provider=bot_username_provider,
            auto_delete_config=services.runtime_auto_delete_config,
        )

        if parsed.command == "ai":
            await handle_ai_command(ctx)
        elif parsed.command == "tldr":
            await handle_tldr_command(ctx, "thread")
        elif parsed.command == "tldr_all":
            await handle_tldr_command(ctx, "all")
        elif parsed.command == "stats":
            await handle_stats_command(ctx)
        elif parsed.command == "memory":
            await handle_memory_command(ctx)
        elif parsed.command == "memory_user":
            await handle_memory_user_command(ctx)
        elif parsed.command == "memory_forget":
            await handle_memory_forget_command(ctx)
        elif parsed.command == "memory_refresh":
            await handle_memory_refresh_command(ctx)
        elif parsed.command == "help":
            await handle_help_command(ctx)
        elif parsed.command == "whitelist":
            await handle_whitelist_command(ctx)
        elif parsed.command == "confirm_whitelist":
            await handle_confirm_whitelist_command(ctx)

    @client.raw_client.on(events.Raw)
    async def handle_raw_update(update) -> None:
        if not services.reaction_service.enabled:
            return
        if not isinstance(update, tl_types.UpdateMessageReactions):
            return

        peer = getattr(update, "peer", None)
        message_id = int(getattr(update, "msg_id", 0) or 0)
        if peer is None or message_id <= 0:
            return

        try:
            chat_id = int(get_peer_id(peer))
        except Exception as exc:
            log.warning("reactions.user_peer_resolve_failed", error=str(exc))
            return

        if settings.allowed_chat_ids and chat_id not in settings.allowed_chat_ids:
            log.info(
                "reactions.user_raw_update_skipped_chat",
                chat_id=chat_id,
                message_id=message_id,
            )
            return

        log.info(
            "reactions.user_raw_update_received",
            chat_id=chat_id,
            message_id=message_id,
        )

        try:
            snapshot = await client.fetch_message_reaction_snapshot(
                chat_id=chat_id,
                message_id=message_id,
                trigger_emojis=services.reaction_service.trigger_emojis,
                limit_per_emoji=services.reaction_service.fetch_limit_per_emoji,
            )
        except Exception as exc:
            log.error(
                "reactions.user_snapshot_fetch_failed",
                chat_id=chat_id,
                message_id=message_id,
                error=str(exc),
            )
            return

        if snapshot is None:
            return

        try:
            async with session_scope() as session:
                await services.reaction_service.handle_reaction_snapshot(
                    session, client, snapshot
                )
        except Exception as exc:
            log.error(
                "reactions.user_snapshot_handle_failed",
                chat_id=chat_id,
                message_id=message_id,
                error=str(exc),
            )

    log.info("startup.user_runtime")
    services.activity_poller.start(client)
    services.reaction_poller.start(client)
    services.memory_poller.start()
    try:
        await client.run_until_disconnected()
    finally:
        await services.memory_poller.stop()
        await services.activity_poller.stop()
        await services.reaction_poller.stop()
        await client.disconnect()
    return 0


async def run() -> int:
    settings = get_settings()
    configure_logging(settings.log_level)
    log = get_logger("app.main")

    missing = settings.require_secrets()
    if missing:
        log.error("startup.missing_secrets", missing=missing)
        return 2

    init_engine(settings.database_url)
    services = build_services(settings)

    try:
        return await run_user_api(settings, services)
    finally:
        await dispose_engine()


def main() -> None:
    try:
        code = asyncio.run(run())
    except KeyboardInterrupt:
        code = 0
    sys.exit(code)


if __name__ == "__main__":
    main()
