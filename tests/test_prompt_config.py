from __future__ import annotations

from pathlib import Path

from app.llm.prompt_config import RuntimePromptConfig


def test_defaults_when_prompts_file_missing(tmp_path: Path) -> None:
    cfg = RuntimePromptConfig(path=tmp_path / "missing.yaml")
    assert cfg.personality_enabled is True
    assert cfg.is_personality_injected("ai") is True
    assert cfg.is_personality_injected("memory") is False
    # All required keys exist by default.
    assert cfg.required_keys_missing() == []
    # Defaults can be rendered without explicit values for personality keys.
    rendered = cfg.render_system("ai")
    assert "Answer the user's exact question" in rendered
    assert "@username mentions" in rendered
    # Personality is injected for ai by default.
    assert "witty but not annoying" in rendered.lower()


def test_personality_not_injected_for_memory_or_tldr(tmp_path: Path) -> None:
    cfg = RuntimePromptConfig(path=tmp_path / "missing.yaml")
    memory_system = cfg.render_system("memory")
    tldr_system = cfg.render_system("tldr")
    assert "witty but not annoying" not in memory_system.lower()
    assert "witty but not annoying" not in tldr_system.lower()
    assert "compact long-term memory" in memory_system
    assert "summarize" in tldr_system.lower()


def test_render_ai_user_contains_question_and_context(tmp_path: Path) -> None:
    cfg = RuntimePromptConfig(path=tmp_path / "missing.yaml")
    text = cfg.render_user(
        "ai",
        question="hello?",
        chat_id=42,
        message_thread_id=7,
        context_text="some context",
    )
    assert "hello?" in text
    assert "chat_id=42" in text
    assert "current_thread_id=7" in text
    assert "some context" in text


def test_render_memory_user_escapes_json_braces(tmp_path: Path) -> None:
    cfg = RuntimePromptConfig(path=tmp_path / "missing.yaml")
    text = cfg.render_user(
        "memory",
        chat_memory="(none)",
        thread_memory="(none)",
        messages="m1",
        max_chat_chars=1000,
        max_thread_chars=1000,
        max_user_chars=500,
    )
    # The JSON shape uses literal braces; format() should keep them.
    assert '"chat_summary"' in text
    assert "max_chat_chars" not in text  # placeholder was replaced
    assert "1000 characters" in text


def test_loads_overrides_from_yaml(tmp_path: Path) -> None:
    path = tmp_path / "prompts.yaml"
    path.write_text(
        """
shared:
  no_mentions_rule: "RULE_X"
  default_group_context: "CTX_X"
personality:
  enabled: true
  base_prompt: "PERS_X"
  inject_into:
    ai: true
    activity: false
    memory: false
    tldr: false
    reaction: false
prompts:
  ai:
    system: |
      {personality}|{default_group_context}|{no_mentions_rule}
    user: |
      Q={question}
  tldr:
    system: |
      tldr-{default_group_context}
    user: |
      TS={scope_description}|TC={context_text}
  memory:
    system: |
      memory-sys
    user: |
      memory-user
  activity:
    system: |
      activity-{personality}
    follow_up_system: |
      follow-{personality}
    user: |
      activity-user-{context_text}
  reaction:
    system: |
      reaction-sys
    user: |
      reaction-user-{context_text}-{reactions_summary}
        """.strip(),
        encoding="utf-8",
    )
    cfg = RuntimePromptConfig(path=path)
    assert cfg.required_keys_missing() == []
    ai_sys = cfg.render_system("ai").strip()
    assert ai_sys == "PERS_X|CTX_X|RULE_X"

    # activity is disabled in inject_into → personality should be empty.
    activity_sys = cfg.render_system("activity").strip()
    assert activity_sys == "activity-"


def test_broken_yaml_keeps_previous_data(tmp_path: Path) -> None:
    path = tmp_path / "prompts.yaml"
    path.write_text(
        """
shared:
  no_mentions_rule: "GOOD_RULE"
prompts:
  ai:
    system: "GOOD_SYS"
    user: "Q={question}"
""".strip(),
        encoding="utf-8",
    )
    cfg = RuntimePromptConfig(path=path)
    assert cfg.render_system("ai") == "GOOD_SYS"

    # Now write broken YAML and bump mtime so the loader retries.
    import os
    import time

    time.sleep(0.01)
    path.write_text(": : :\nbad: ::\n  - [", encoding="utf-8")
    os.utime(path, None)
    # Should not raise; should keep last good data.
    assert cfg.render_system("ai") == "GOOD_SYS"


def test_required_keys_missing_when_section_blank(tmp_path: Path) -> None:
    path = tmp_path / "prompts.yaml"
    path.write_text(
        """
prompts:
  ai:
    system: ""
    user: ""
""".strip(),
        encoding="utf-8",
    )
    cfg = RuntimePromptConfig(path=path)
    # Defaults backfill when section omits a field, so ai.system/user should
    # still be present from defaults (we only override when value is non-empty).
    assert cfg.required_keys_missing() == []
