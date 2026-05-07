# Migration Progress

Tracking implementation of the Telegram User API migration and stabilization.

## Status legend

- [ ] not started
- [~] in progress
- [x] done

## Baseline

- [x] Captured the pre-migration quality baseline.
- [x] `ruff check .` passed in the local venv.
- [~] `pytest` still gets hard-killed in this sandbox before emitting diagnostics; follow-up validation is still needed in a roomier environment.
- [~] `mypy` is now installed, but full type-check runs are also being hard-killed in this sandbox.

## Migration Waves

- [x] Wave 0: added repo migration docs and recorded baseline status.
- [x] Wave 1: introduced framework-neutral Telegram DTOs and client protocol.
- [x] Wave 2: refactored ingestion, formatting, and Telegram helpers to use shared DTOs.
- [x] Wave 3: extracted shared command business logic and replaced callback whitelist confirmation with `/confirm_whitelist`.
- [x] Wave 4: added Telethon dependency plus user-mode configuration and startup validation.
- [x] Wave 5: added a Telethon adapter with normalized topic/thread mapping helpers.
- [x] Wave 6: added a one-off Telegram session bootstrap tool.
- [x] Docker login flow: added a dedicated `telegram-auth` Compose service and env-driven login-code support.
- [x] Wave 7: added the Telethon runtime and `NewMessage` routing.
- [x] Wave 8: refactored reactions onto the shared transport layer and documented that user-mode reactions are currently disabled.
- [x] Wave 9: updated `.env.example`, README, and operational guidance for user-mode deployment.
- [~] Wave 10: expanded tests for config, command flow, helpers, and Telethon conversion; final full-suite validation still pending in a refreshed environment.
- [x] Wave 11: removed aiogram, Bot API runtime, and bot-mode settings after stabilization.
