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
    handle_tldr_command,
    handle_whitelist_command,
)
from app.bot.commands import parse_command
from app.config import Settings, get_settings
from app.db.session import dispose_engine, init_engine, session_scope
from app.llm.context_builder import ContextBuilder
from app.llm.openrouter_client import OpenRouterClient
from app.llm.reactions_config import RuntimeReactionsConfig
from app.llm.runtime_config import RuntimeContextConfig
from app.logging_config import configure_logging, get_logger
from app.services.ai_answer_service import AiAnswerService
from app.services.reaction_service import ReactionService
from app.services.tldr_service import TldrService


@dataclass
class AppServices:
    yaml_store: YamlAccessStore
    access_control: AccessControl
    ai_service: AiAnswerService
    tldr_service: TldrService
    reaction_service: ReactionService
    runtime_context_config: RuntimeContextConfig


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
    context_builder = ContextBuilder(runtime_context_config)
    ai_service = AiAnswerService(settings, context_builder, openrouter)
    tldr_service = TldrService(settings, openrouter, runtime_context_config)
    reactions_config = RuntimeReactionsConfig(path=settings.reactions_yaml_path)
    reaction_service = ReactionService(
        settings=settings,
        config=reactions_config,
        runtime_config=runtime_context_config,
        client=openrouter,
    )
    return AppServices(
        yaml_store=yaml_store,
        access_control=access_control,
        ai_service=ai_service,
        tldr_service=tldr_service,
        reaction_service=reaction_service,
        runtime_context_config=runtime_context_config,
    )


async def run_user_api(settings: Settings, services: AppServices) -> int:
    from telethon import events

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

    if services.reaction_service.enabled:
        log.warning("reactions.user_mode_not_supported")

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
            return

        ctx = CommandContext(
            message=tg_message,
            client=client,
            settings=settings,
            access_control=services.access_control,
            yaml_store=services.yaml_store,
            ai_service=services.ai_service,
            tldr_service=services.tldr_service,
            runtime_config=services.runtime_context_config,
            bot_username_provider=bot_username_provider,
        )

        if parsed.command == "ai":
            await handle_ai_command(ctx)
        elif parsed.command == "tldr":
            await handle_tldr_command(ctx, "thread")
        elif parsed.command == "tldr_all":
            await handle_tldr_command(ctx, "all")
        elif parsed.command == "whitelist":
            await handle_whitelist_command(ctx)
        elif parsed.command == "confirm_whitelist":
            await handle_confirm_whitelist_command(ctx)

    log.info("startup.user_runtime")
    try:
        await client.run_until_disconnected()
    finally:
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
