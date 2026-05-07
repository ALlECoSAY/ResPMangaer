from __future__ import annotations

from aiogram import Bot, Router
from aiogram.filters import Command
from aiogram.types import Message, MessageReactionUpdated
from sqlalchemy.exc import SQLAlchemyError

from app.auth.access_control import AccessControl
from app.auth.yaml_store import YamlAccessStore
from app.bot.command_handlers import (
    CommandContext,
    handle_ai_command,
    handle_confirm_whitelist_command,
    handle_tldr_command,
    handle_whitelist_command,
)
from app.config import Settings
from app.db.session import session_scope
from app.llm.runtime_config import RuntimeContextConfig
from app.logging_config import get_logger
from app.services.ai_answer_service import AiAnswerService
from app.services.reaction_service import ReactionService
from app.services.tldr_service import TldrService
from app.telegram_client.aiogram_adapter import (
    AiogramTelegramClient,
    message_from_aiogram,
    reaction_update_from_aiogram,
)

log = get_logger(__name__)


def build_router(
    settings: Settings,
    access_control: AccessControl,
    yaml_store: YamlAccessStore,
    ai_service: AiAnswerService,
    tldr_service: TldrService,
    reaction_service: ReactionService,
    runtime_config: RuntimeContextConfig,
    bot_username_provider,
) -> Router:
    router = Router(name="commands")

    def _command_context(message: Message, bot: Bot) -> CommandContext:
        client = AiogramTelegramClient(bot)
        return CommandContext(
            message=message_from_aiogram(message),
            client=client,
            settings=settings,
            access_control=access_control,
            yaml_store=yaml_store,
            ai_service=ai_service,
            tldr_service=tldr_service,
            runtime_config=runtime_config,
            bot_username_provider=bot_username_provider,
        )

    @router.message_reaction()
    async def handle_message_reaction(event: MessageReactionUpdated, bot: Bot) -> None:
        if settings.allowed_chat_ids and event.chat.id not in settings.allowed_chat_ids:
            return
        try:
            async with session_scope() as session:
                await reaction_service.handle_reaction_update(
                    session=session,
                    client=AiogramTelegramClient(bot),
                    event=reaction_update_from_aiogram(event),
                )
        except SQLAlchemyError as exc:
            log.error("reactions.db_error", error=str(exc))
        except Exception as exc:
            log.error("reactions.unexpected", error=str(exc))

    @router.message(Command("ai", ignore_case=True))
    async def handle_ai(message: Message, bot: Bot) -> None:
        await handle_ai_command(_command_context(message, bot))

    @router.message(Command("tldr", ignore_case=True))
    async def handle_tldr(message: Message, bot: Bot) -> None:
        await handle_tldr_command(_command_context(message, bot), "thread")

    @router.message(Command("tldr_all", ignore_case=True))
    async def handle_tldr_all(message: Message, bot: Bot) -> None:
        await handle_tldr_command(_command_context(message, bot), "all")

    @router.message(Command("whitelist", ignore_case=True))
    async def handle_whitelist(message: Message, bot: Bot) -> None:
        await handle_whitelist_command(_command_context(message, bot))

    @router.message(Command("confirm_whitelist", ignore_case=True))
    async def handle_confirm_whitelist(message: Message, bot: Bot) -> None:
        await handle_confirm_whitelist_command(_command_context(message, bot))

    return router
