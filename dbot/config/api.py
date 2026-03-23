"""Settings API routes — Starlette handlers for /api/settings/*."""

import logging
from pathlib import Path
from typing import Any

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route, Router

logger = logging.getLogger("dbot.config.api")

# Module-level state — set by init_api_state() at startup
_config_db: Any = None
_catalog: Any = None
_executor: Any = None
_starlette_app: Any = None  # reference to the main app for hot-reload


def init_api_state(config_db: Any, catalog: Any, executor: Any, app: Any = None) -> None:
    """Set module-level state for API handlers. Called once at startup."""
    global _config_db, _catalog, _executor, _starlette_app
    _config_db = config_db
    _catalog = catalog
    _executor = executor
    _starlette_app = app


async def get_all_settings(request: Request) -> JSONResponse:
    """GET /api/settings — return all config sections."""
    db = _config_db
    sections = db.get_all_sections()
    return JSONResponse(sections)


async def get_schema(request: Request) -> JSONResponse:
    """GET /api/settings/schema — return JSON schemas for all config sections."""
    from dbot.config.models import SECTION_MODELS

    schemas = {}
    for name, model_cls in SECTION_MODELS.items():
        schemas[name] = model_cls.model_json_schema()
    return JSONResponse(schemas)


async def list_models(request: Request) -> JSONResponse:
    """GET /api/settings/models — list user-configured models."""
    db = _config_db
    llm_config = db.get_section("llm")
    return JSONResponse(llm_config.get("available_models", {}))


async def put_model(request: Request) -> JSONResponse:
    """PUT /api/settings/models — add or update a model. Body: {name, provider, model}"""
    db = _config_db
    body = await request.json()
    display_name = body.get("name", "").strip()
    provider = body.get("provider", "").strip()
    model = body.get("model", "").strip()
    if not display_name or not provider or not model:
        return JSONResponse({"error": "name, provider, and model are required"}, status_code=400)
    model_id = f"{provider}:{model}"
    llm_config = db.get_section("llm")
    models = llm_config.get("available_models", {})
    models[display_name] = model_id
    llm_config["available_models"] = models
    db.set_section("llm", llm_config)
    return JSONResponse({"status": "ok", "name": display_name, "model_id": model_id})


async def delete_model(request: Request) -> JSONResponse:
    """DELETE /api/settings/models/{name} — remove a model."""
    display_name = request.path_params["name"]
    db = _config_db
    llm_config = db.get_section("llm")
    models = llm_config.get("available_models", {})
    models.pop(display_name, None)
    llm_config["available_models"] = models
    db.set_section("llm", llm_config)
    return JSONResponse({"status": "ok", "deleted": display_name})


async def get_section(request: Request) -> JSONResponse:
    """GET /api/settings/{section} — return one config section."""
    section = request.path_params["section"]
    db = _config_db
    data = db.get_section(section)
    return JSONResponse(data)


async def put_section(request: Request) -> JSONResponse:
    """PUT /api/settings/{section} — update a config section."""
    section = request.path_params["section"]
    db = _config_db
    try:
        body = await request.json()
        db.set_section(section, body)
        return JSONResponse({"status": "ok", "section": section})
    except Exception as e:
        return JSONResponse({"status": "error", "detail": str(e)}, status_code=400)


async def list_credentials(request: Request) -> JSONResponse:
    """GET /api/settings/credentials — list packs + param names (NO values)."""
    db = _config_db
    packs = db.get_all_credential_packs()
    result: dict[str, list[str]] = {}
    for pack in packs:
        result[pack] = db.get_credential_params(pack)
    return JSONResponse(result)


async def put_credentials(request: Request) -> JSONResponse:
    """PUT /api/settings/credentials/{pack} — set credentials for a pack."""
    pack = request.path_params["pack"]
    db = _config_db
    try:
        body = await request.json()
        if not isinstance(body, dict):
            return JSONResponse(
                {"status": "error", "detail": "Expected JSON object with param_name: value pairs"},
                status_code=400,
            )
        db.set_pack_credentials(pack, body)
        return JSONResponse({"status": "ok", "pack": pack, "params": list(body.keys())})
    except Exception as e:
        return JSONResponse({"status": "error", "detail": str(e)}, status_code=400)


async def delete_credentials(request: Request) -> JSONResponse:
    """DELETE /api/settings/credentials/{pack} — remove all credentials for a pack."""
    pack = request.path_params["pack"]
    db = _config_db
    db.delete_pack_credentials(pack)
    return JSONResponse({"status": "ok", "pack": pack, "deleted": True})


async def test_connection(request: Request) -> JSONResponse:
    """POST /api/settings/credentials/{pack}/test — test credentials by calling test-module."""
    pack = request.path_params["pack"]
    db = _config_db
    catalog = _catalog
    executor = _executor

    # Find the integration
    integration = None
    for integ in catalog._integrations.values():
        if integ.pack == pack:
            integration = integ
            break

    if not integration:
        return JSONResponse(
            {"status": "error", "detail": f"Pack '{pack}' not found in catalog"},
            status_code=404,
        )

    # Get decrypted credentials
    params = db.get_decrypted_pack(pack)

    try:
        import asyncio

        result = await asyncio.wait_for(
            executor(Path(integration.py_path), "test-module", {}, params),
            timeout=15.0,
        )
        success = result.get("success", False)
        return JSONResponse(
            {
                "status": "ok" if success else "failed",
                "pack": pack,
                "result": result.get("results", []),
                "error": result.get("error"),
            }
        )
    except TimeoutError:
        return JSONResponse(
            {"status": "error", "detail": "Connection test timed out (15s)"},
            status_code=504,
        )
    except Exception as e:
        return JSONResponse(
            {"status": "error", "detail": str(e)},
            status_code=500,
        )


async def list_packs(request: Request) -> JSONResponse:
    """GET /api/packs — list indexed packs with command counts."""
    catalog = _catalog
    packs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for integ in catalog._integrations.values():
        if integ.pack not in seen:
            seen.add(integ.pack)
            packs.append(
                {
                    "pack": integ.pack,
                    "name": integ.name,
                    "category": integ.category,
                    "commands": len(integ.commands),
                }
            )
    packs.sort(key=lambda p: p["pack"])
    return JSONResponse(packs)


async def get_pack_params(request: Request) -> JSONResponse:
    """GET /api/packs/{pack}/params — returns ParamDef metadata for a pack."""
    pack = request.path_params["pack"]
    catalog = _catalog
    integration = next((i for i in catalog._integrations.values() if i.pack == pack), None)
    if not integration:
        return JSONResponse({"error": f"Pack '{pack}' not found"}, status_code=404)
    # Return non-hidden params (no credential values — only metadata)
    params = [p.model_dump() for p in integration.params if not p.hidden]
    return JSONResponse({"pack": pack, "params": params})


async def get_pack_readme(request: Request) -> JSONResponse:
    """GET /api/packs/{pack}/readme — returns description + README content."""
    pack = request.path_params["pack"]
    catalog = _catalog
    integration = next((i for i in catalog._integrations.values() if i.pack == pack), None)
    if not integration:
        return JSONResponse({"error": f"Pack '{pack}' not found"}, status_code=404)

    content_root = Path(__file__).parent.parent.parent / "content"
    py_path = Path(integration.py_path)
    integ_dir = py_path.parent
    desc_md = integ_dir / f"{integration.name}_description.md"
    readme_md = content_root / "Packs" / pack / "README.md"

    description = desc_md.read_text(encoding="utf-8") if desc_md.exists() else ""
    readme = readme_md.read_text(encoding="utf-8") if readme_md.exists() else ""

    return JSONResponse(
        {
            "pack": pack,
            "description": description or integration.description,
            "readme": readme,
            "display": integration.display,
            "category": integration.category,
        }
    )


async def settings_health(request: Request) -> JSONResponse:
    """GET /api/settings/health — config system health."""
    db = _config_db
    general = db.get_section("general")
    content_root = Path(general.get("content_root") or "content")
    return JSONResponse(
        {
            "status": "ok",
            "db_path": str(db._db_path),
            "content_root_exists": content_root.exists(),
            "credential_packs": len(db.get_all_credential_packs()),
        }
    )


async def list_providers(request: Request) -> JSONResponse:
    """GET /api/settings/providers — list only CONFIGURED providers."""
    db = _config_db
    from dbot.config.models import KNOWN_PROVIDERS

    stored_keys = db.get_all_provider_keys()
    llm_config = db.get_section("llm")
    providers_config = llm_config.get("providers", {})

    result = {}
    for provider in stored_keys:
        spec = KNOWN_PROVIDERS.get(provider)
        result[provider] = {
            "has_key": True,
            "base_url": providers_config.get(provider, {}).get("base_url", ""),
            "description": spec.description if spec else "",
        }
    for provider, cfg in providers_config.items():
        if provider not in result and cfg.get("base_url"):
            spec = KNOWN_PROVIDERS.get(provider)
            result[provider] = {
                "has_key": provider in stored_keys,
                "base_url": cfg.get("base_url", ""),
                "description": spec.description if spec else "",
            }
    return JSONResponse(result)


async def available_providers(request: Request) -> JSONResponse:
    """GET /api/settings/providers/available — all known providers with UI-facing specs."""
    from dbot.config.models import KNOWN_PROVIDERS

    db = _config_db
    stored_keys = db.get_all_provider_keys()
    llm_config = db.get_section("llm")
    providers_config = llm_config.get("providers", {})

    result = {}
    for name, spec in KNOWN_PROVIDERS.items():
        result[name] = {
            "description": spec.description,
            "needs_api_key": spec.needs_api_key,
            "needs_base_url": spec.needs_base_url,
            "api_key_label": spec.api_key_label,
            "base_url_label": spec.base_url_label,
            "base_url_placeholder": spec.base_url_placeholder,
            "extra_fields": [f.model_dump(exclude={"env_var"}) for f in spec.extra_fields],
            "configured": name in stored_keys or bool(providers_config.get(name, {}).get("base_url")),
        }
    return JSONResponse(result)


async def put_provider(request: Request) -> JSONResponse:
    """PUT /api/settings/providers/{provider} — set provider key + config."""
    provider = request.path_params["provider"]
    db = _config_db
    try:
        body = await request.json()
        api_key = body.get("api_key")
        base_url = body.get("base_url", "")
        import os

        from dbot.config.models import KNOWN_PROVIDERS

        spec = KNOWN_PROVIDERS.get(provider)

        if api_key:
            db.set_provider_key(provider, api_key)
            env_var = spec._env_var if spec else ""
            if env_var:
                os.environ[env_var] = api_key

        if base_url:
            base_url_env = (spec._base_url_env if spec else "") or f"{provider.upper()}_BASE_URL"
            os.environ[base_url_env] = base_url

        # Handle extra fields (e.g., Azure api_version)
        extra_data: dict[str, str] = {}
        if spec:
            for field in spec.extra_fields:
                val = body.get(field.label.lower().replace(" ", "_"), "")
                if val:
                    extra_data[field.label.lower().replace(" ", "_")] = val
                    if field.env_var:
                        os.environ[field.env_var] = val

        from dbot.config.models import ProviderConfig

        llm_config = db.get_section("llm")
        providers = llm_config.get("providers", {})
        providers[provider] = ProviderConfig(base_url=base_url, **extra_data).model_dump()
        llm_config["providers"] = providers
        db.set_section("llm", llm_config)

        return JSONResponse({"status": "ok", "provider": provider, "needs_reload": True})
    except Exception as e:
        return JSONResponse({"status": "error", "detail": str(e)}, status_code=400)


async def delete_provider(request: Request) -> JSONResponse:
    """DELETE /api/settings/providers/{provider} — remove provider key + config."""
    provider = request.path_params["provider"]
    db = _config_db
    db.delete_provider_key(provider)

    # Remove from LLM config providers section
    llm_config = db.get_section("llm")
    providers = llm_config.get("providers", {})
    providers.pop(provider, None)
    llm_config["providers"] = providers
    db.set_section("llm", llm_config)

    return JSONResponse({"status": "ok", "provider": provider, "deleted": True})


async def settings_page(request: Request) -> HTMLResponse:
    """GET /settings — serve the settings HTML page."""
    html_path = Path(__file__).parent / "settings.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Settings page not found</h1>", status_code=404)


async def reload_app(request: Request) -> JSONResponse:
    """POST /api/reload — rebuild the chat UI with current config."""
    if _starlette_app is None:
        return JSONResponse({"status": "error", "detail": "No app reference"}, status_code=500)

    try:
        from pydantic_ai import Agent

        from dbot.agent.chat import CHAT_SYSTEM_PROMPT
        from dbot.agent.deps import IRDeps
        from dbot.agent.guardrails import GuardrailConfig, build_toolset
        from dbot.audit import AuditLogger
        from dbot.credentials.store import CredentialStore
        from dbot.runtime.executor import execute_inprocess

        config = GuardrailConfig.chat_default()
        toolset = build_toolset(config)

        llm_config = _config_db.get_section("llm")
        available_models = llm_config.get("available_models", {})
        default_model = llm_config.get("default_model", "openai:gpt-4o")

        agent: Agent[IRDeps, str] = Agent(
            default_model,
            system_prompt=CHAT_SYSTEM_PROMPT,
            toolsets=[toolset],  # type: ignore[list-item]
            output_type=str,
            deps_type=IRDeps,
        )

        cred_store = CredentialStore()
        for pack in _config_db.get_all_credential_packs_filtered():
            cred_store._credentials[pack] = _config_db.get_decrypted_pack(pack)

        deps = IRDeps(
            catalog=_catalog,
            credential_store=cred_store,
            executor=execute_inprocess,
            audit=AuditLogger(),
            guardrails=config,
        )

        chat_app = agent.to_web(
            deps=deps,
            models=available_models,
            instructions=CHAT_SYSTEM_PROMPT,
        )

        # Hot-swap: remove old / and chat routes, add new ones
        _starlette_app.routes[:] = [
            r
            for r in _starlette_app.routes
            if getattr(r, "path", "") not in ("/", "/{id}", "/api/chat", "/api/configure", "/api/health")
        ]
        for route in chat_app.routes:
            _starlette_app.routes.append(route)

        return JSONResponse({"status": "ok", "reloaded": True})
    except Exception as e:
        import traceback

        return JSONResponse(
            {"status": "error", "detail": str(e), "traceback": traceback.format_exc()},
            status_code=500,
        )


def make_settings_router() -> Router:
    """Create the settings API router.

    IMPORTANT: Route ordering matters — literal paths before parameterized.
    /api/settings/credentials MUST come before /api/settings/{section}.
    """
    return Router(
        routes=[
            Route("/api/reload", reload_app, methods=["POST"]),
            Route("/api/settings/providers/{provider}", put_provider, methods=["PUT"]),
            Route("/api/settings/providers/{provider}", delete_provider, methods=["DELETE"]),
            Route("/api/settings/providers/available", available_providers, methods=["GET"]),
            Route("/api/settings/providers", list_providers, methods=["GET"]),
            Route("/api/settings/credentials/{pack}/test", test_connection, methods=["POST"]),
            Route("/api/settings/credentials/{pack}", put_credentials, methods=["PUT"]),
            Route("/api/settings/credentials/{pack}", delete_credentials, methods=["DELETE"]),
            Route("/api/settings/credentials", list_credentials, methods=["GET"]),
            Route("/api/settings/models/{name}", delete_model, methods=["DELETE"]),
            Route("/api/settings/models", list_models, methods=["GET"]),
            Route("/api/settings/models", put_model, methods=["PUT"]),
            Route("/api/settings/health", settings_health, methods=["GET"]),
            Route("/api/settings/{section}", get_section, methods=["GET"]),
            Route("/api/settings/{section}", put_section, methods=["PUT"]),
            Route("/api/settings", get_all_settings, methods=["GET"]),
            Route("/api/packs/{pack}/params", get_pack_params, methods=["GET"]),
            Route("/api/packs/{pack}/readme", get_pack_readme, methods=["GET"]),
            Route("/api/packs", list_packs, methods=["GET"]),
            Route("/api/settings/schema", get_schema, methods=["GET"]),
            Route("/settings", settings_page, methods=["GET"]),
        ]
    )
