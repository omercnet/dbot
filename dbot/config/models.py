"""Config section Pydantic models — one model per settings tab."""

from typing import Literal

from pydantic import BaseModel, Field


class GeneralConfig(BaseModel):
    """General dbot settings."""

    execution_mode: Literal["inprocess", "subprocess"] = "inprocess"
    audit_log_path: str = "dbot-agent-audit.log"
    content_root: str = ""  # empty = auto-detect from package root


class ProviderConfig(BaseModel):
    """Per-provider configuration (non-secret fields)."""

    base_url: str = Field(default="", title="Base URL", description="Custom API endpoint (empty = provider default)")
    env_var: str = Field(default="", title="API Key Env Var", description="Environment variable for the API key")


class ProviderSpec(BaseModel):
    """Metadata for a known LLM provider."""

    env_var: str = Field(title="Default API Key Env Var")
    needs_base_url: bool = Field(default=False, title="Requires Base URL")
    needs_api_key: bool = Field(default=True, title="Requires API Key")
    base_url_env: str = Field(default="", title="Base URL Env Var")
    description: str = Field(default="")


KNOWN_PROVIDERS: dict[str, ProviderSpec] = {
    "openai": ProviderSpec(
        env_var="OPENAI_API_KEY",
        description="OpenAI (GPT-4o, GPT-4o mini, o1, o3)",
    ),
    "anthropic": ProviderSpec(
        env_var="ANTHROPIC_API_KEY",
        description="Anthropic (Claude 4 Sonnet, Opus, Haiku)",
    ),
    "google": ProviderSpec(
        env_var="GOOGLE_API_KEY",
        description="Google (Gemini 2.5 Pro, Flash)",
    ),
    "groq": ProviderSpec(
        env_var="GROQ_API_KEY",
        description="Groq (Llama, Mixtral — fast inference)",
    ),
    "mistral": ProviderSpec(
        env_var="MISTRAL_API_KEY",
        description="Mistral (Mistral Large, Codestral)",
    ),
    "azure": ProviderSpec(
        env_var="AZURE_OPENAI_API_KEY",
        needs_base_url=True,
        base_url_env="AZURE_OPENAI_ENDPOINT",
        description="Azure OpenAI Service (requires endpoint URL)",
    ),
    "ollama": ProviderSpec(
        env_var="",
        needs_api_key=False,
        needs_base_url=True,
        base_url_env="OLLAMA_BASE_URL",
        description="Ollama (local models, no API key needed)",
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
