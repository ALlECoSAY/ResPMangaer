from __future__ import annotations

from pathlib import Path


def test_telethon_adapter_passes_formatting_entities() -> None:
    source = Path("app/telegram_client/telethon_adapter.py").read_text(encoding="utf-8")

    assert "formatting_entities: list[Any] | None = None" in source
    assert "formatting_entities=formatting_entities" in source
