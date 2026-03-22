"""Web UI — PydanticAI built-in chat interface for dbot.

Usage:
    dbot-web                          # default: openai:gpt-4o on port 7932
    dbot-web --port 8080              # custom port
    dbot-web --model anthropic:claude-sonnet-4-5

Or programmatically:
    from dbot.agent.web import create_app
    app = create_app()
    uvicorn.run(app, host="127.0.0.1", port=7932)
"""

import os
from pathlib import Path

from pydantic_ai import Agent
from starlette.applications import Starlette

from dbot.agent.chat import CHAT_SYSTEM_PROMPT
from dbot.agent.deps import IRDeps
from dbot.agent.guardrails import GuardrailConfig, build_toolset
from dbot.audit import AuditLogger
from dbot.config.api import make_settings_router
from dbot.config.db import ConfigDB
from dbot.registry.catalog import Catalog
from dbot.registry.indexer import index_content
from dbot.runtime.common_server import bootstrap_common_modules
from dbot.runtime.executor import execute_inprocess


def _bootstrap_deps(
    model: str | None = None,
    audit_log: Path | None = None,
) -> tuple[IRDeps, str, Catalog, ConfigDB]:
    """Bootstrap the full dbot stack and return deps + model name."""
    project_root = Path(__file__).parent.parent.parent
    content_root = project_root / "content"
    config_dir = project_root / "config"
    bootstrap_common_modules(content_root)

    integrations = index_content(content_root)
    catalog = Catalog(integrations)

    # Initialize config DB (creates SQLite + encryption key on first run)
    db_path = config_dir / "dbot.db"
    key_path = config_dir / ".dbot-key"
    config_db = ConfigDB(db_path, key_path)

    # Use DB-backed credential store
    from dbot.credentials.store import CredentialStore

    # Build a CredentialStore that reads from the DB
    cred_store = CredentialStore()  # empty base
    # Populate from DB
    for pack in config_db.get_all_credential_packs():
        cred_store._credentials[pack] = config_db.get_decrypted_pack(pack)

    model_name = model or os.environ.get("DBOT_LLM_MODEL", "openai:gpt-4o")
    config = GuardrailConfig.chat_default()

    deps = IRDeps(
        catalog=catalog,
        credential_store=cred_store,
        executor=execute_inprocess,
        audit=AuditLogger(audit_path=audit_log or Path("dbot-agent-audit.log")),
        guardrails=config,
        model_name=model_name,
    )
    return deps, model_name, catalog, config_db


def create_app(
    model: str | None = None,
    models: dict[str, str] | None = None,
    audit_log: Path | None = None,
) -> Starlette:
    """Create the dbot web UI application with settings."""
    deps, _model_name, catalog, config_db = _bootstrap_deps(model, audit_log)
    config = deps.guardrails
    toolset = build_toolset(config)

    agent: Agent[IRDeps, str] = Agent(
        "test",  # placeholder — to_web() models param provides real models
        system_prompt=CHAT_SYSTEM_PROMPT,
        toolsets=[toolset],
        output_type=str,
        deps_type=IRDeps,
    )

    # Get available models from DB config
    llm_config = config_db.get_section("llm")
    available_models = models or llm_config.get(
        "available_models",
        {
            "GPT-4o": "openai:gpt-4o",
            "GPT-4o mini": "openai:gpt-4o-mini",
            "Claude Sonnet": "anthropic:claude-sonnet-4-5",
        },
    )

    # Create the PydanticAI web app
    # to_web() validates model providers — if no API keys, fall back to settings-only mode
    try:
        starlette_app = agent.to_web(
            deps=deps,
            models=available_models,
            instructions=CHAT_SYSTEM_PROMPT,
        )
    except Exception:
        # Fallback: plain Starlette app (settings work, chat won't until keys configured)
        from starlette.responses import HTMLResponse
        from starlette.routing import Route

        async def _no_model(request):
            return HTMLResponse(
                "<h2>No LLM API key configured</h2>"
                "<p>Go to <a href='/settings'>/settings</a> to configure your LLM provider.</p>"
            )

        starlette_app = Starlette(routes=[Route("/", _no_model)])

    # Mount settings routes onto the app
    settings_router = make_settings_router()
    starlette_app.state.config_db = config_db
    starlette_app.state.catalog = catalog
    starlette_app.state.executor = execute_inprocess
    for route in settings_router.routes:
        starlette_app.routes.insert(0, route)  # insert before catch-all

    return starlette_app


# Lazy default — only created when uvicorn imports this module
def _lazy_app() -> Starlette:
    return create_app()


app: Starlette | None = None


def __getattr__(attr: str) -> Starlette:
    if attr == "app":
        global app
        app = _lazy_app()
        return app
    msg = f"module {__name__!r} has no attribute {attr!r}"
    raise AttributeError(msg)
