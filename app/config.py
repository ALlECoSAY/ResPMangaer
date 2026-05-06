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

    # Telegram
    telegram_bot_token: str = Field(default="")
    telegram_allowed_chat_ids: str = Field(default="")
    telegram_enable_command_registration: bool = True
    telegram_register_admin_commands: bool = False

    # Access control
    access_control_enabled: bool = True
    whitelist_yaml_path: Path = Path("/app/config/whitelist.yaml")
    admins_yaml_path: Path = Path("/app/config/admins.yaml")
    context_limits_yaml_path: Path = Path("/app/config/context_limits.yaml")
    reactions_yaml_path: Path = Path("/app/config/reactions.yaml")

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

    # Bot behavior
    bot_language: str = "auto"
    max_same_thread_messages: int = 80
    max_cross_thread_messages: int = 30
    max_context_chars: int = 24_000
    max_reply_chars: int = 3_900
    tldr_activity_gap_minutes: int = 180
    tldr_lookback_hours: int = 48
    # /tldr — current thread only.
    tldr_max_threads: int = 1
    tldr_max_messages_per_thread: int = 200
    # /tldr_all — all threads in the chat.
    tldr_all_max_threads: int = 12
    tldr_all_max_messages_per_thread: int = 120

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
        if not self.telegram_bot_token or self.telegram_bot_token == "replace_me":
            missing.append("TELEGRAM_BOT_TOKEN")
        if not self.openrouter_api_key or self.openrouter_api_key == "replace_me":
            missing.append("OPENROUTER_API_KEY")
        return missing


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
