"""Web UI — dbot React SPA + PydanticAI chat API.

The React SPA (dbot/ui/) is the primary interface. PydanticAI's to_web()
provides the /api/chat endpoint; we replace its HTML shell with our SPA.

Usage:
    dbot-web                          # default: openai:gpt-4o on port 7932
    dbot-web --port 8080              # custom port
    dbot-web --model anthropic:claude-sonnet-4-5

Or programmatically:
    from dbot.agent.web import create_app
    app = create_app()
    uvicorn.run(app, host="127.0.0.1", port=7932)
"""

import logging
import os
from collections.abc import Callable
from pathlib import Path

from pydantic_ai import Agent
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from dbot.agent.chat import CHAT_SYSTEM_PROMPT
from dbot.agent.deps import IRDeps
from dbot.agent.guardrails import GuardrailConfig, build_toolset
from dbot.audit import AuditLogger
from dbot.config.db import ConfigDB
from dbot.registry.catalog import Catalog
from dbot.registry.indexer import index_content
from dbot.runtime.common_server import bootstrap_common_modules
from dbot.runtime.executor import execute_inprocess


def _bootstrap_deps(
    model: str | None = None,
    audit_log: Path | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> tuple[IRDeps, str, Catalog, ConfigDB]:
    """Bootstrap the full dbot stack and return deps + model name."""

    def _status(msg: str) -> None:
        if on_progress:
            on_progress(msg)

    project_root = Path(__file__).parent.parent.parent
    content_root = project_root / "content"
    config_dir = project_root / "config"

    _status("Loading CommonServerPython...")
    bootstrap_common_modules(content_root)

    _status("Indexing integration packs...")
    integrations = index_content(content_root)
    catalog = Catalog(integrations)
    stats = catalog.stats
    _status(f"Indexed {stats['total_commands']} commands from {stats['total_integrations']} integrations")

    _status("Initializing config database...")
    db_path = config_dir / "dbot.db"
    key_path = config_dir / ".dbot-key"
    config_db = ConfigDB(db_path, key_path)

    # Inject LLM provider API keys from DB into environment
    from dbot.config.models import KNOWN_PROVIDERS

    llm_config = config_db.get_section("llm")
    providers_config = llm_config.get("providers", {})
    provider_keys = config_db.get_all_provider_keys()
    for provider, api_key in provider_keys.items():
        prov_cfg = providers_config.get(provider, {})
        spec = KNOWN_PROVIDERS.get(provider)
        env_var = spec._env_var if spec else ""
        if env_var and env_var not in os.environ:
            os.environ[env_var] = api_key

        base_url = prov_cfg.get("base_url", "")
        if base_url:
            base_env = (spec._base_url_env if spec else "") or f"{provider.upper()}_BASE_URL"
            if base_env not in os.environ:
                os.environ[base_env] = base_url

        # Inject extra fields (e.g., Azure api_version)
        if spec:
            for field in spec.extra_fields:
                val = prov_cfg.get(field.label.lower().replace(" ", "_"), "")
                if val and field.env_var and field.env_var not in os.environ:
                    os.environ[field.env_var] = val
    # Use DB-backed credential store
    from dbot.credentials.store import CredentialStore

    # Build a CredentialStore that reads from the DB
    cred_store = CredentialStore()  # empty base
    # Populate from DB
    for pack in config_db.get_all_credential_packs_filtered():
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
        config_db=config_db,
    )
    return deps, model_name, catalog, config_db


def create_app(
    model: str | None = None,
    models: dict[str, str] | None = None,
    audit_log: Path | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> Starlette:
    """Create the dbot web UI application with settings."""
    deps, _model_name, catalog, config_db = _bootstrap_deps(model, audit_log, on_progress)
    if on_progress:
        on_progress("Starting web server...")
    config = deps.guardrails
    toolset = build_toolset(config)

    agent: Agent[IRDeps, str] = Agent(
        "test",  # placeholder — to_web() models param provides real models
        system_prompt=CHAT_SYSTEM_PROMPT,
        toolsets=[toolset],  # type: ignore[list-item]
        output_type=str,
        deps_type=IRDeps,
    )

    from dbot.config.models import KNOWN_PROVIDERS

    llm_config = config_db.get_section("llm")
    no_key_providers = {n for n, s in KNOWN_PROVIDERS.items() if not s.needs_api_key}
    configured_providers = set(config_db.get_all_provider_keys().keys()) | no_key_providers
    # Default models for all known providers — filtered to configured ones below
    default_models = {
        "GPT-4o": "openai:gpt-4o",
        "GPT-4o mini": "openai:gpt-4o-mini",
        "Claude Sonnet": "anthropic:claude-sonnet-4-5",
        "Claude Haiku": "anthropic:claude-haiku-4",
        "Gemini 2.5 Pro": "google:gemini-2.5-pro",
        "Gemini 2.5 Flash": "google:gemini-2.5-flash",
        "Llama 3 70B (Groq)": "groq:llama-3.3-70b-versatile",
        "Mistral Large": "mistral:mistral-large-latest",
        "GPT-4o (Azure)": "azure:gpt-4o",
        "GPT-4o mini (Azure)": "azure:gpt-4o-mini",
    }
    all_models = models or llm_config.get("available_models", {}) or default_models
    available_models = {
        name: model_id for name, model_id in all_models.items() if model_id.split(":")[0] in configured_providers
    }

    logger = logging.getLogger("dbot.web")

    # Create the PydanticAI web app (provides /api/chat + /api/configure + /api/health)
    # to_web() validates model providers — if no API keys, fall back to settings-only mode
    try:
        starlette_app = agent.to_web(
            deps=deps,
            models=available_models,
            instructions=CHAT_SYSTEM_PROMPT,
        )
        # Remove PydanticAI's HTML shell routes — we serve our own SPA
        starlette_app.routes[:] = [r for r in starlette_app.routes if getattr(r, "path", "") not in ("/", "/{id}")]
    except Exception:
        logger.warning("to_web() failed — chat disabled until API keys configured", exc_info=True)
        starlette_app = Starlette(routes=[])

    # Mount settings routes — inject state BEFORE adding routes
    # Initialize API handler state (module-level, avoids Starlette request.app.state issues)
    from dbot.config.api import init_api_state, make_settings_router

    init_api_state(config_db=config_db, catalog=catalog, executor=execute_inprocess, app=starlette_app)

    # Add settings routes — insert all at once to preserve correct order
    # (literal paths like /providers before parameterized /{section})
    settings_router = make_settings_router()
    for insert_pos, route in enumerate(settings_router.routes):
        starlette_app.routes.insert(insert_pos, route)

    # Mount React SPA static files
    ui_dist = Path(__file__).parent.parent / "ui" / "dist"
    if ui_dist.is_dir():
        # Serve built assets (JS, CSS, images) at /assets/
        assets_dir = ui_dist / "assets"
        if assets_dir.is_dir():
            starlette_app.routes.append(Mount("/assets", app=StaticFiles(directory=str(assets_dir)), name="spa-assets"))

        # SPA catch-all: serves index.html for non-API GET requests
        # API paths (/api/*) must NOT be caught here — they should 404 properly
        spa_index = str(ui_dist / "index.html")

        async def spa_fallback(request: Request) -> Response:
            if request.url.path.startswith("/api/"):
                return JSONResponse({"error": "Not found"}, status_code=404)
            if request.method != "GET":
                return Response(status_code=405)
            return FileResponse(spa_index)

        all_methods = ["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"]
        starlette_app.routes.append(Route("/{path:path}", spa_fallback, methods=all_methods))
        starlette_app.routes.append(Route("/", spa_fallback, methods=all_methods))
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
