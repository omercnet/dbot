"""Tests for config Pydantic models."""

from dbot.config.models import (
    SECTION_MODELS,
    GeneralConfig,
    GuardrailsConfig,
    LLMConfig,
    PacksConfig,
)


class TestGeneralConfig:
    def test_defaults(self) -> None:
        c = GeneralConfig()
        assert c.execution_mode == "inprocess"
        assert c.audit_log_path == "dbot-agent-audit.log"
        assert c.content_root == ""

    def test_subprocess_mode(self) -> None:
        c = GeneralConfig(execution_mode="subprocess")
        assert c.execution_mode == "subprocess"


class TestLLMConfig:
    def test_defaults(self) -> None:
        c = LLMConfig()
        assert c.default_model == "openai:gpt-4o"
        assert c.temperature == 0.0
        assert c.max_tokens == 4096
        assert c.available_models == {}

    def test_custom_models(self) -> None:
        c = LLMConfig(available_models={"GPT": "openai:gpt-4o"})
        assert c.available_models["GPT"] == "openai:gpt-4o"


class TestGuardrailsConfig:
    def test_defaults(self) -> None:
        c = GuardrailsConfig()
        assert c.autonomous_blocked_categories == ["Endpoint"]
        assert c.chat_max_tool_calls == 100
        assert c.autonomous_max_tool_calls == 30

    def test_custom_blocked(self) -> None:
        c = GuardrailsConfig(autonomous_blocked_categories=["Endpoint", "Cloud"])
        assert "Cloud" in c.autonomous_blocked_categories


class TestPacksConfig:
    def test_defaults(self) -> None:
        c = PacksConfig()
        assert c.enabled_packs == []

    def test_custom_packs(self) -> None:
        c = PacksConfig(enabled_packs=["VirusTotal", "Shodan"])
        assert len(c.enabled_packs) == 2


class TestSectionRegistry:
    def test_all_sections_registered(self) -> None:
        assert set(SECTION_MODELS.keys()) == {"general", "llm", "guardrails", "packs"}

    def test_models_are_valid_classes(self) -> None:
        for model_cls in SECTION_MODELS.values():
            instance = model_cls()
            assert instance is not None
