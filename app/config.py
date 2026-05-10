from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Telegram user API / MTProto
    telegram_allowed_chat_ids: str = Field(default="")
    telegram_api_id: int | None = None
    telegram_api_hash: str = Field(default="")
    telegram_user_session_path: Path = Path("/app/config/telegram_user.session")
    telegram_user_phone: str = Field(default="")
    telegram_user_2fa_password: str = Field(default="")
    telegram_login_code: str = Field(default="")
    allow_unsafe_all_chats: bool = False

    # YAML configs
    access_control_enabled: bool = True
    whitelist_yaml_path: Path = Path("/app/config/whitelist.yaml")
    admins_yaml_path: Path = Path("/app/config/admins.yaml")
    context_limits_yaml_path: Path = Path("/app/config/context_limits.yaml")
    reactions_yaml_path: Path = Path("/app/config/reactions.yaml")
    stats_yaml_path: Path = Path("/app/config/stats.yaml")

    # OpenRouter
    openrouter_api_key: str = Field(default="")
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_model: str = "openai/gpt-4.1-mini"
    openrouter_site_url: str = ""
    openrouter_site_name: str = "Telegram AI Thread Bot"

    # Database
    postgres_db: str = "telegram_ai_bot"
    postgres_user: str = "telegram_ai_bot"
    postgres_password: str = "telegram_ai_bot_password"
    database_url: str = (
        "postgresql+asyncpg://telegram_ai_bot:telegram_ai_bot_password@postgres:5432/telegram_ai_bot"
    )

    # Safety / privacy
    store_bot_messages: bool = True
    store_command_messages: bool = True
    redact_telegram_user_ids: bool = False

    # Observability
    log_level: str = "INFO"
    log_prompts: bool = False

    @field_validator("log_level")
    @classmethod
    def _normalize_log_level(cls, value: str) -> str:
        return value.upper()

    @property
    def allowed_chat_ids(self) -> set[int]:
        raw = (self.telegram_allowed_chat_ids or "").strip()
        if not raw:
            return set()
        ids: set[int] = set()
        for chunk in raw.split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            try:
                ids.add(int(chunk))
            except ValueError:
                continue
        return ids

    def require_secrets(self) -> list[str]:
        missing: list[str] = []
        if self.telegram_api_id is None:
            missing.append("TELEGRAM_API_ID")
        if not self.telegram_api_hash or self.telegram_api_hash == "replace_me":
            missing.append("TELEGRAM_API_HASH")
        if not self.allow_unsafe_all_chats and not self.allowed_chat_ids:
            missing.append("TELEGRAM_ALLOWED_CHAT_IDS or ALLOW_UNSAFE_ALL_CHATS=true")
        if not self.telegram_user_session_path.exists():
            missing.append(
                f"TELEGRAM_USER_SESSION_PATH ({self.telegram_user_session_path})"
            )
        if not self.openrouter_api_key or self.openrouter_api_key == "replace_me":
            missing.append("OPENROUTER_API_KEY")
        return missing


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
