"""Settings API routes — Starlette handlers for /api/settings/*."""

import logging
from pathlib import Path
from typing import Any

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route, Router

logger = logging.getLogger("dbot.config.api")


async def get_all_settings(request: Request) -> JSONResponse:
    """GET /api/settings — return all config sections."""
    db = request.app.state.config_db
    sections = db.get_all_sections()
    return JSONResponse(sections)


async def get_section(request: Request) -> JSONResponse:
    """GET /api/settings/{section} — return one config section."""
    section = request.path_params["section"]
    db = request.app.state.config_db
    data = db.get_section(section)
    return JSONResponse(data)


async def put_section(request: Request) -> JSONResponse:
    """PUT /api/settings/{section} — update a config section."""
    section = request.path_params["section"]
    db = request.app.state.config_db
    try:
        body = await request.json()
        db.set_section(section, body)
        return JSONResponse({"status": "ok", "section": section})
    except Exception as e:
        return JSONResponse({"status": "error", "detail": str(e)}, status_code=400)


async def list_credentials(request: Request) -> JSONResponse:
    """GET /api/settings/credentials — list packs + param names (NO values)."""
    db = request.app.state.config_db
    packs = db.get_all_credential_packs()
    result: dict[str, list[str]] = {}
    for pack in packs:
        result[pack] = db.get_credential_params(pack)
    return JSONResponse(result)


async def put_credentials(request: Request) -> JSONResponse:
    """PUT /api/settings/credentials/{pack} — set credentials for a pack."""
    pack = request.path_params["pack"]
    db = request.app.state.config_db
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
    db = request.app.state.config_db
    db.delete_pack_credentials(pack)
    return JSONResponse({"status": "ok", "pack": pack, "deleted": True})


async def test_connection(request: Request) -> JSONResponse:
    """POST /api/settings/credentials/{pack}/test — test credentials by calling test-module."""
    pack = request.path_params["pack"]
    db = request.app.state.config_db
    catalog = request.app.state.catalog
    executor = request.app.state.executor

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
    catalog = request.app.state.catalog
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


async def settings_health(request: Request) -> JSONResponse:
    """GET /api/settings/health — config system health."""
    db = request.app.state.config_db
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


async def settings_page(request: Request) -> HTMLResponse:
    """GET /settings — serve the settings HTML page."""
    html_path = Path(__file__).parent / "settings.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Settings page not found</h1>", status_code=404)


def make_settings_router() -> Router:
    """Create the settings API router.

    IMPORTANT: Route ordering matters — literal paths before parameterized.
    /api/settings/credentials MUST come before /api/settings/{section}.
    """
    return Router(
        routes=[
            Route("/api/settings/credentials/{pack}/test", test_connection, methods=["POST"]),
            Route("/api/settings/credentials/{pack}", put_credentials, methods=["PUT"]),
            Route("/api/settings/credentials/{pack}", delete_credentials, methods=["DELETE"]),
            Route("/api/settings/credentials", list_credentials, methods=["GET"]),
            Route("/api/settings/health", settings_health, methods=["GET"]),
            Route("/api/settings/{section}", get_section, methods=["GET"]),
            Route("/api/settings/{section}", put_section, methods=["PUT"]),
            Route("/api/settings", get_all_settings, methods=["GET"]),
            Route("/api/packs", list_packs, methods=["GET"]),
            Route("/settings", settings_page, methods=["GET"]),
        ]
    )
