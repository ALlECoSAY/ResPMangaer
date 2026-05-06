from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.repositories import record_llm_interaction
from app.llm.context_builder import ContextBuilder
from app.llm.openrouter_client import LlmResponse, OpenRouterClient, OpenRouterError
from app.llm.prompts import AI_SYSTEM_PROMPT, build_ai_user_prompt
from app.logging_config import get_logger

log = get_logger(__name__)


class AiAnswerService:
    def __init__(
        self,
        settings: Settings,
        context_builder: ContextBuilder,
        client: OpenRouterClient,
    ) -> None:
        self._settings = settings
        self._context_builder = context_builder
        self._client = client

    async def answer(
        self,
        session: AsyncSession,
        chat_id: int,
        message_thread_id: int,
        question: str,
        request_message_id: int | None,
    ) -> LlmResponse:
        ctx = await self._context_builder.build_for_ai(
            session=session,
            chat_id=chat_id,
            message_thread_id=message_thread_id,
            question=question,
        )
        user_prompt = build_ai_user_prompt(
            question=question,
            chat_id=chat_id,
            message_thread_id=message_thread_id,
            context_text=ctx.context_text,
        )
        if self._settings.log_prompts:
            log.info("ai.prompt", prompt=user_prompt)

        success = False
        error: str | None = None
        response: LlmResponse | None = None
        try:
            response = await self._client.complete(AI_SYSTEM_PROMPT, user_prompt)
            success = True
            return response
        except OpenRouterError as exc:
            error = str(exc)
            raise
        finally:
            await record_llm_interaction(
                session,
                chat_id=chat_id,
                message_thread_id=message_thread_id,
                request_message_id=request_message_id,
                command_name="ai",
                model=self._settings.openrouter_model,
                prompt_tokens_estimate=response.prompt_tokens if response else None,
                completion_tokens_estimate=response.completion_tokens if response else None,
                latency_ms=response.latency_ms if response else None,
                success=success,
                error=error,
            )
