"""Config section Pydantic models — one model per settings tab."""

from typing import Literal

from pydantic import BaseModel, Field


class GeneralConfig(BaseModel):
    """General dbot settings."""

    execution_mode: Literal["inprocess", "subprocess"] = "inprocess"
    audit_log_path: str = "dbot-agent-audit.log"
    content_root: str = ""  # empty = auto-detect from package root


class ProviderConfig(BaseModel):
    """Stored per-provider configuration (non-secret fields)."""

    base_url: str = ""


class ProviderSpec(BaseModel):
    """Metadata for a known LLM provider — drives the settings UI."""

    description: str
    needs_api_key: bool = True
    needs_base_url: bool = False
    api_key_label: str = "API Key"
    base_url_label: str = "Base URL"
    base_url_placeholder: str = "https://..."
    # Internal: env var mapping (never sent to UI)
    _env_var: str = ""
    _base_url_env: str = ""


def _spec(
    description: str,
    *,
    env_var: str = "",
    needs_api_key: bool = True,
    needs_base_url: bool = False,
    api_key_label: str = "API Key",
    base_url_label: str = "Base URL",
    base_url_placeholder: str = "https://...",
    base_url_env: str = "",
) -> ProviderSpec:
    s = ProviderSpec(
        description=description,
        needs_api_key=needs_api_key,
        needs_base_url=needs_base_url,
        api_key_label=api_key_label,
        base_url_label=base_url_label,
        base_url_placeholder=base_url_placeholder,
    )
    s._env_var = env_var
    s._base_url_env = base_url_env
    return s


KNOWN_PROVIDERS: dict[str, ProviderSpec] = {
    "openai": _spec(
        "OpenAI (GPT-4o, GPT-4o mini, o1, o3)",
        env_var="OPENAI_API_KEY",
    ),
    "anthropic": _spec(
        "Anthropic (Claude 4 Sonnet, Opus, Haiku)",
        env_var="ANTHROPIC_API_KEY",
    ),
    "google": _spec(
        "Google (Gemini 2.5 Pro, Flash)",
        env_var="GOOGLE_API_KEY",
    ),
    "groq": _spec(
        "Groq (Llama, Mixtral \u2014 fast inference)",
        env_var="GROQ_API_KEY",
    ),
    "mistral": _spec(
        "Mistral (Mistral Large, Codestral)",
        env_var="MISTRAL_API_KEY",
    ),
    "azure": _spec(
        "Azure OpenAI Service",
        env_var="AZURE_OPENAI_API_KEY",
        needs_base_url=True,
        base_url_label="Azure Endpoint",
        base_url_placeholder="https://YOUR-RESOURCE.openai.azure.com",
        base_url_env="AZURE_OPENAI_ENDPOINT",
    ),
    "ollama": _spec(
        "Ollama (local models)",
        needs_api_key=False,
        needs_base_url=True,
        base_url_label="Ollama Server URL",
        base_url_placeholder="http://localhost:11434",
        base_url_env="OLLAMA_BASE_URL",
    ),
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
