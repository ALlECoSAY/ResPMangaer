from __future__ import annotations

from pathlib import Path

from app.llm.memory_config import RuntimeMemoryConfig


def test_memory_config_loads_yaml(tmp_path: Path) -> None:
    path = tmp_path / "memory.yaml"
    path.write_text(
        """
        version: 1
        memory:
          enabled: true
          user_profiles_enabled: false
          max_chat_memory_chars: 100
          max_thread_memory_chars: 200
          max_user_memory_chars: 300
          update_min_new_messages: 7
          update_min_interval_minutes: 9
          max_profiles_per_prompt: 2
          summarize_model: test/model
          max_messages_per_update: 11
          user_profile_min_evidence_messages: 4
          update_reaction_min_count: 6
          trigger_keywords: [decided, важно]
          user_api:
            poll_enabled: true
            poll_interval_seconds: 12
            poll_max_chats_per_tick: 3
        """,
        encoding="utf-8",
    )

    cfg = RuntimeMemoryConfig(path)

    assert cfg.enabled is True
    assert cfg.user_profiles_enabled is False
    assert cfg.max_chat_memory_chars == 100
    assert cfg.max_thread_memory_chars == 200
    assert cfg.max_user_memory_chars == 300
    assert cfg.update_min_new_messages == 7
    assert cfg.update_min_interval_minutes == 9
    assert cfg.max_profiles_per_prompt == 2
    assert cfg.summarize_model == "test/model"
    assert cfg.max_messages_per_update == 11
    assert cfg.user_profile_min_evidence_messages == 4
    assert cfg.update_reaction_min_count == 6
    assert cfg.trigger_keywords == ("decided", "важно")
    assert cfg.poll_enabled is True
    assert cfg.poll_interval_seconds == 12
    assert cfg.poll_max_chats_per_tick == 3
    assert cfg.poll_max_threads_per_tick == 3
