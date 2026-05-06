from __future__ import annotations

import time
from dataclasses import dataclass

from openai import APIError, APITimeoutError, AsyncOpenAI, RateLimitError

from app.logging_config import get_logger

log = get_logger(__name__)


class OpenRouterError(Exception):
    """Raised for any OpenRouter call failure surfaced to the user."""


@dataclass
class LlmResponse:
    text: str
    model: str
    prompt_tokens: int | None
    completion_tokens: int | None
    latency_ms: int


class OpenRouterClient:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        site_url: str = "",
        site_name: str = "",
    ) -> None:
        if not api_key:
            raise ValueError("OPENROUTER_API_KEY is required")
        headers: dict[str, str] = {}
        if site_url:
            headers["HTTP-Referer"] = site_url
        if site_name:
            headers["X-Title"] = site_name
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            default_headers=headers or None,
        )
        self._model = model

    async def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.2,
        timeout: float = 60.0,  # noqa: ASYNC109 (forwarded to OpenAI SDK)
    ) -> LlmResponse:
        started = time.monotonic()
        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=temperature,
                timeout=timeout,
            )
        except APITimeoutError as exc:
            log.error("openrouter.timeout", error=str(exc))
            raise OpenRouterError("LLM timed out") from exc
        except RateLimitError as exc:
            log.error("openrouter.rate_limited", error=str(exc))
            raise OpenRouterError("LLM rate-limited") from exc
        except APIError as exc:
            log.error("openrouter.api_error", error=str(exc))
            raise OpenRouterError("LLM API error") from exc
        except Exception as exc:  # network / unexpected
            log.error("openrouter.unexpected", error=str(exc))
            raise OpenRouterError("LLM unexpected error") from exc

        latency_ms = int((time.monotonic() - started) * 1000)
        choices = response.choices or []
        if not choices:
            raise OpenRouterError("LLM returned no choices")
        message = choices[0].message
        text = (message.content or "").strip()
        if not text:
            raise OpenRouterError("LLM returned empty content")
        usage = response.usage
        return LlmResponse(
            text=text,
            model=response.model or self._model,
            prompt_tokens=getattr(usage, "prompt_tokens", None) if usage else None,
            completion_tokens=getattr(usage, "completion_tokens", None) if usage else None,
            latency_ms=latency_ms,
        )
