from __future__ import annotations

import asyncio
import sys

from app.auth.access_control import AccessControl
from app.auth.yaml_store import YamlAccessStore
from app.bot.dispatcher import configure_bot
from app.config import get_settings
from app.db.session import dispose_engine, init_engine
from app.llm.context_builder import ContextBuilder
from app.llm.openrouter_client import OpenRouterClient
from app.logging_config import configure_logging, get_logger
from app.services.ai_answer_service import AiAnswerService
from app.services.tldr_service import TldrService


async def run() -> int:
    settings = get_settings()
    configure_logging(settings.log_level)
    log = get_logger("app.main")

    missing = settings.require_secrets()
    if missing:
        log.error("startup.missing_secrets", missing=missing)
        return 2

    init_engine(settings.database_url)

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
    context_builder = ContextBuilder(settings)
    ai_service = AiAnswerService(settings, context_builder, openrouter)
    tldr_service = TldrService(settings, openrouter)

    bot, dp, _state = await configure_bot(
        settings=settings,
        access_control=access_control,
        yaml_store=yaml_store,
        ai_service=ai_service,
        tldr_service=tldr_service,
    )

    log.info("startup.polling")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()
        await dispose_engine()
    return 0


def main() -> None:
    try:
        code = asyncio.run(run())
    except KeyboardInterrupt:
        code = 0
    sys.exit(code)


if __name__ == "__main__":
    main()
