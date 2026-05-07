from __future__ import annotations

from pathlib import Path

from app.config import Settings


def test_user_mode_requires_allowlist_and_session(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        telegram_api_id=123,
        telegram_api_hash="hash",
        telegram_user_session_path=tmp_path / "missing.session",
        openrouter_api_key="or-key",
    )
    missing = settings.require_secrets()
    assert "TELEGRAM_ALLOWED_CHAT_IDS or ALLOW_UNSAFE_ALL_CHATS=true" in missing
    assert any(item.startswith("TELEGRAM_USER_SESSION_PATH") for item in missing)


def test_user_mode_allows_unsafe_override(tmp_path: Path) -> None:
    session_path = tmp_path / "telegram_user.session"
    session_path.write_text("session", encoding="utf-8")
    settings = Settings(
        _env_file=None,
        telegram_api_id=123,
        telegram_api_hash="hash",
        telegram_user_session_path=session_path,
        allow_unsafe_all_chats=True,
        openrouter_api_key="or-key",
    )
    assert settings.require_secrets() == []


def test_user_mode_requires_api_credentials(tmp_path: Path) -> None:
    session_path = tmp_path / "telegram_user.session"
    session_path.write_text("session", encoding="utf-8")
    settings = Settings(
        _env_file=None,
        telegram_user_session_path=session_path,
        allow_unsafe_all_chats=True,
        openrouter_api_key="or-key",
    )
    missing = settings.require_secrets()
    assert "TELEGRAM_API_ID" in missing
    assert "TELEGRAM_API_HASH" in missing
