"""Tests for config defaults."""

from dbot.config.defaults import SECTION_DEFAULTS
from dbot.config.models import SECTION_MODELS


class TestDefaults:
    def test_all_sections_have_defaults(self) -> None:
        for section in SECTION_MODELS:
            assert section in SECTION_DEFAULTS

    def test_defaults_are_valid_models(self) -> None:
        for section, default in SECTION_DEFAULTS.items():
            model_cls = SECTION_MODELS[section]
            assert isinstance(default, model_cls)

    def test_llm_default_has_models(self) -> None:
        assert len(SECTION_DEFAULTS["llm"].available_models) > 0  # type: ignore[union-attr]

    def test_guardrails_default_blocks_endpoint(self) -> None:
        config = SECTION_DEFAULTS["guardrails"]
        assert "Endpoint" in config.autonomous_blocked_categories  # type: ignore[union-attr]
