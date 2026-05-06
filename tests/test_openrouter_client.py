from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.llm.openrouter_client import OpenRouterClient, OpenRouterError


def _make_client(monkeypatch, completion_response):
    client = OpenRouterClient(
        api_key="test",
        base_url="https://openrouter.example/api/v1",
        model="x/y",
    )
    client._client.chat.completions.create = AsyncMock(return_value=completion_response)
    return client


async def test_complete_returns_text(monkeypatch):
    fake_response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="hello"),
                finish_reason="stop",
            )
        ],
        model="x/y",
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
    )
    client = _make_client(monkeypatch, fake_response)
    result = await client.complete("sys", "user")
    assert result.text == "hello"
    assert result.prompt_tokens == 10
    assert result.completion_tokens == 5


async def test_complete_empty_raises(monkeypatch):
    fake_response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=""))],
        model="x/y",
        usage=None,
    )
    client = _make_client(monkeypatch, fake_response)
    with pytest.raises(OpenRouterError):
        await client.complete("sys", "user")


async def test_complete_no_choices_raises(monkeypatch):
    fake_response = SimpleNamespace(choices=[], model="x/y", usage=None)
    client = _make_client(monkeypatch, fake_response)
    with pytest.raises(OpenRouterError):
        await client.complete("sys", "user")
