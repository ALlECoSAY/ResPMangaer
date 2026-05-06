from __future__ import annotations

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.types import BotCommand

from app.auth.access_control import AccessControl
from app.auth.yaml_store import YamlAccessStore
from app.bot.handlers import build_router
from app.bot.middleware import ChatAllowlistMiddleware, MessageIngestionMiddleware
from app.config import Settings
from app.logging_config import get_logger
from app.services.ai_answer_service import AiAnswerService
from app.services.tldr_service import TldrService

log = get_logger(__name__)


class BotState:
    def __init__(self) -> None:
        self.bot_username: str | None = None

    def get_username(self) -> str | None:
        return self.bot_username


async def configure_bot(
    settings: Settings,
    access_control: AccessControl,
    yaml_store: YamlAccessStore,
    ai_service: AiAnswerService,
    tldr_service: TldrService,
) -> tuple[Bot, Dispatcher, BotState]:
    bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=None),
    )
    state = BotState()

    me = await bot.get_me()
    state.bot_username = me.username
    log.info("bot.identity", id=me.id, username=me.username)

    dp = Dispatcher()
    dp.message.middleware(ChatAllowlistMiddleware(settings.allowed_chat_ids))
    dp.message.middleware(MessageIngestionMiddleware(settings, state.get_username))

    router = build_router(
        settings=settings,
        access_control=access_control,
        yaml_store=yaml_store,
        ai_service=ai_service,
        tldr_service=tldr_service,
        bot_username_provider=state.get_username,
    )
    dp.include_router(router)

    if settings.telegram_enable_command_registration:
        public_commands = [
            BotCommand(command="ai", description="Ask the AI using current thread context."),
            BotCommand(command="tldr", description="Summarize recent activity in other threads."),
        ]
        if settings.telegram_register_admin_commands:
            public_commands.append(
                BotCommand(command="add_whitelist", description="Admin: add a user to the whitelist.")
            )
        try:
            await bot.set_my_commands(public_commands)
        except Exception as exc:
            log.error("commands.register_failed", error=str(exc))

    return bot, dp, state
