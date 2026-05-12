from __future__ import annotations

import base64

import httpx

from app.logging_config import get_logger

log = get_logger(__name__)


class ImageGenerationError(RuntimeError):
    """Raised when the image-generation API call fails."""


class ImageGenerationClient:
    """Minimal OpenAI-compatible image generation client.

    Calls the `images/generations` endpoint and returns raw PNG bytes.
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout: float = 120.0,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    async def generate_avatar(
        self,
        prompt: str,
        *,
        size: str = "1024x1024",
        model: str | None = None,
    ) -> bytes:
        if not self._api_key:
            raise ImageGenerationError(
                "IMAGE_GENERATION_API_KEY is not configured."
            )
        payload = {
            "model": model or self._model,
            "prompt": prompt,
            "size": size,
            "n": 1,
            "response_format": "b64_json",
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        url = f"{self._base_url}/images/generations"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as http:
                response = await http.post(url, json=payload, headers=headers)
        except httpx.RequestError as exc:
            raise ImageGenerationError(f"image generation request failed: {exc}") from exc

        if response.status_code >= 400:
            raise ImageGenerationError(
                f"image generation HTTP {response.status_code}: "
                f"{response.text[:500]}"
            )
        body = response.json()
        data = body.get("data") or []
        if not data:
            raise ImageGenerationError("image generation response empty.")
        first = data[0]
        b64 = first.get("b64_json")
        if not b64:
            raise ImageGenerationError("image generation response missing b64_json.")
        try:
            return base64.b64decode(b64)
        except (ValueError, TypeError) as exc:
            raise ImageGenerationError(f"could not decode image bytes: {exc}") from exc
