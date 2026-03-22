"""Default config values — single source of truth for factory reset."""

from pydantic import BaseModel

from dbot.config.models import GeneralConfig, GuardrailsConfig, LLMConfig, PacksConfig

DEFAULT_GENERAL = GeneralConfig()

DEFAULT_LLM = LLMConfig(
    available_models={
        "GPT-4o": "openai:gpt-4o",
        "GPT-4o mini": "openai:gpt-4o-mini",
        "Claude Sonnet": "anthropic:claude-sonnet-4-5",
        "Claude Haiku": "anthropic:claude-haiku-3-5",
    },
)

DEFAULT_GUARDRAILS = GuardrailsConfig()

DEFAULT_PACKS = PacksConfig()

SECTION_DEFAULTS: dict[str, BaseModel] = {
    "general": DEFAULT_GENERAL,
    "llm": DEFAULT_LLM,
    "guardrails": DEFAULT_GUARDRAILS,
    "packs": DEFAULT_PACKS,
}
