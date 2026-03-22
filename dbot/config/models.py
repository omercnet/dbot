"""Config section Pydantic models — one model per settings tab."""

from typing import Literal

from pydantic import BaseModel, Field


class GeneralConfig(BaseModel):
    """General dbot settings."""

    execution_mode: Literal["inprocess", "subprocess"] = "inprocess"
    audit_log_path: str = "dbot-agent-audit.log"
    content_root: str = ""  # empty = auto-detect from package root


class ProviderConfig(BaseModel):
    """LLM provider configuration (non-secret fields only)."""

    base_url: str = ""  # empty = use provider default
    env_var: str = ""  # env var name for the API key (e.g., OPENAI_API_KEY)


# Well-known providers with their default env var names
KNOWN_PROVIDERS: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "google": "GOOGLE_API_KEY",
    "groq": "GROQ_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "ollama": "",  # no key needed
}


class LLMConfig(BaseModel):
    """LLM model configuration."""

    default_model: str = "openai:gpt-4o"
    available_models: dict[str, str] = Field(default_factory=dict)
    temperature: float = 0.0
    max_tokens: int = 4096
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)


class GuardrailsConfig(BaseModel):
    """Agent guardrail settings."""

    chat_max_tool_calls: int = 100
    chat_timeout_seconds: float = 600.0
    autonomous_max_tool_calls: int = 30
    autonomous_timeout_seconds: float = 300.0
    autonomous_blocked_categories: list[str] = Field(default_factory=lambda: ["Endpoint"])
    autonomous_blocked_tools: list[str] = Field(default_factory=list)


class PacksConfig(BaseModel):
    """Which integration packs to index."""

    enabled_packs: list[str] = Field(default_factory=list)  # empty = all


# Section registry — maps section name to model class
SECTION_MODELS: dict[str, type[BaseModel]] = {
    "general": GeneralConfig,
    "llm": LLMConfig,
    "guardrails": GuardrailsConfig,
    "packs": PacksConfig,
}
