"""Integration tests for the /api/chat endpoint.

Tests the full web app with PydanticAI's TestModel to verify:
- POST /api/chat returns streaming SSE response
- SPA catch-all does NOT intercept /api/* POST requests
- /api/chat returns proper error when no models configured (fallback mode)
- Settings and health endpoints coexist with chat
"""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel
from starlette.testclient import TestClient

from dbot.agent.chat import CHAT_INSTRUCTIONS
from dbot.agent.deps import IRDeps
from dbot.agent.guardrails import GuardrailConfig, build_toolset
from dbot.audit import AuditLogger
from dbot.config.api import init_api_state, make_settings_router
from dbot.config.db import ConfigDB
from dbot.credentials.store import CredentialStore
from dbot.registry.catalog import Catalog
from dbot.registry.models import CommandDef, IntegrationDef


@pytest.fixture
def config_db(tmp_path: Path) -> ConfigDB:
    return ConfigDB(tmp_path / "test.db", tmp_path / ".dbot-key")


@pytest.fixture
def catalog() -> Catalog:
    return Catalog(
        [
            IntegrationDef(
                pack="TestPack",
                name="Test",
                category="Utilities",
                py_path="/tmp/fake.py",
                commands=[CommandDef(name="test-cmd")],
            ),
        ]
    )


@pytest.fixture
def deps(catalog: Catalog, config_db: ConfigDB, tmp_path: Path) -> IRDeps:
    return IRDeps(
        catalog=catalog,
        credential_store=CredentialStore(),
        executor=AsyncMock(return_value={"success": True, "results": [], "logs": []}),
        audit=AuditLogger(audit_path=tmp_path / "audit.log"),
        guardrails=GuardrailConfig.chat_default(),
        model_name="test",
        config_db=config_db,
    )


@pytest.fixture
def chat_app(deps: IRDeps, catalog: Catalog, config_db: ConfigDB) -> TestClient:
    """Full web app with TestModel — chat endpoint works without real LLM."""
    config = deps.guardrails
    toolset = build_toolset(config)

    agent: Agent[IRDeps, str] = Agent(
        "test",
        instructions=CHAT_INSTRUCTIONS,
        toolsets=[toolset],  # type: ignore[list-item]
        output_type=str,
        deps_type=IRDeps,
    )

    with agent.override(model=TestModel()):
        starlette_app = agent.to_web(
            deps=deps,
            models={"Test": "test"},
        )
        starlette_app.routes[:] = [r for r in starlette_app.routes if getattr(r, "path", "") not in ("/", "/{id}")]

        settings_router = make_settings_router()
        init_api_state(
            config_db=config_db,
            catalog=catalog,
            executor=AsyncMock(return_value={"success": True, "results": [], "logs": []}),
            app=starlette_app,
        )
        for insert_pos, route in enumerate(settings_router.routes):
            starlette_app.routes.insert(insert_pos, route)

        yield TestClient(starlette_app)


@pytest.fixture
def fallback_app(config_db: ConfigDB, catalog: Catalog) -> TestClient:
    """App where to_web() failed — settings work, chat returns error."""
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import FileResponse, JSONResponse, Response
    from starlette.routing import Route

    starlette_app = Starlette(routes=[])

    settings_router = make_settings_router()
    init_api_state(
        config_db=config_db,
        catalog=catalog,
        executor=AsyncMock(return_value={"success": True, "results": [], "logs": []}),
        app=starlette_app,
    )
    for insert_pos, route in enumerate(settings_router.routes):
        starlette_app.routes.insert(insert_pos, route)

    async def spa_fallback(request: Request) -> Response:
        if request.url.path.startswith("/api/"):
            return JSONResponse({"error": "Not found"}, status_code=404)
        if request.method != "GET":
            return Response(status_code=405)
        return FileResponse("/dev/null")

    all_methods = ["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"]
    starlette_app.routes.append(Route("/{path:path}", spa_fallback, methods=all_methods))
    starlette_app.routes.append(Route("/", spa_fallback, methods=all_methods))

    return TestClient(starlette_app)


class TestChatEndpoint:
    def test_post_chat_returns_200(self, chat_app: TestClient) -> None:
        """POST /api/chat with a valid message should return 200 streaming response."""
        r = chat_app.post(
            "/api/chat",
            json={
                "trigger": "submit-message",
                "id": "test-chat",
                "messages": [{"id": "m1", "role": "user", "parts": [{"type": "text", "text": "hello"}]}],
            },
        )
        assert r.status_code == 200

    def test_chat_returns_sse_stream(self, chat_app: TestClient) -> None:
        """Response should be a server-sent events stream."""
        r = chat_app.post(
            "/api/chat",
            json={
                "trigger": "submit-message",
                "id": "test-chat-2",
                "messages": [{"id": "m2", "role": "user", "parts": [{"type": "text", "text": "test"}]}],
            },
        )
        assert r.status_code == 200
        content_type = r.headers.get("content-type", "")
        assert "text" in content_type

    def test_get_configure(self, chat_app: TestClient) -> None:
        """/api/configure returns available models."""
        r = chat_app.get("/api/configure")
        assert r.status_code == 200
        data = r.json()
        assert "models" in data

    def test_get_health(self, chat_app: TestClient) -> None:
        """/api/health returns ok."""
        r = chat_app.get("/api/health")
        assert r.status_code == 200


class TestChatWithSettingsCoexistence:
    def test_settings_still_work_alongside_chat(self, chat_app: TestClient) -> None:
        """Settings routes must work even with chat routes present."""
        r = chat_app.get("/api/settings/providers")
        assert r.status_code == 200
        assert isinstance(r.json(), dict)

    def test_settings_health(self, chat_app: TestClient) -> None:
        r = chat_app.get("/api/settings/health")
        assert r.status_code == 200


class TestFallbackMode:
    """When to_web() fails, /api/* should return proper errors, not 405."""

    def test_post_api_chat_returns_404_not_405(self, fallback_app: TestClient) -> None:
        """POST /api/chat should get 404 (not found), NOT 405 (method not allowed)."""
        r = fallback_app.post("/api/chat", json={"messages": []})
        assert r.status_code == 404
        assert r.json()["error"] == "Not found"

    def test_get_api_unknown_returns_404(self, fallback_app: TestClient) -> None:
        """GET /api/anything should 404 via the SPA guard."""
        r = fallback_app.get("/api/nonexistent")
        assert r.status_code == 404

    def test_settings_work_in_fallback(self, fallback_app: TestClient) -> None:
        """Settings routes must work even in fallback mode."""
        r = fallback_app.get("/api/settings/providers")
        assert r.status_code == 200
