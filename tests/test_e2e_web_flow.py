"""E2E tests for the full web app flow: configure provider → reload → chat.

Uses PydanticAI TestModel to avoid real LLM calls while testing the complete
settings → reload → chat pipeline through the Starlette HTTP layer.
"""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel
from starlette.testclient import TestClient

from dbot.agent.chat import CHAT_SYSTEM_PROMPT
from dbot.agent.deps import IRDeps
from dbot.agent.guardrails import GuardrailConfig, build_toolset
from dbot.audit import AuditLogger
from dbot.config.db import ConfigDB
from dbot.credentials.store import CredentialStore
from dbot.registry.catalog import Catalog
from dbot.registry.models import CommandDef, IntegrationDef


@pytest.fixture
def catalog() -> Catalog:
    return Catalog(
        [
            IntegrationDef(
                pack="TestPack",
                name="TestInt",
                category="Utilities",
                py_path="/tmp/fake.py",
                commands=[CommandDef(name="test-cmd", description="a test command")],
            ),
        ]
    )


@pytest.fixture
def e2e_app(tmp_path: Path, catalog: Catalog) -> TestClient:
    """Full app using TestModel via Agent.override — closest to production."""
    config_db = ConfigDB(tmp_path / "test.db", tmp_path / ".dbot-key")
    config = GuardrailConfig.chat_default()
    toolset = build_toolset(config)

    agent: Agent[IRDeps, str] = Agent(
        "test",
        system_prompt=CHAT_SYSTEM_PROMPT,
        toolsets=[toolset],  # type: ignore[list-item]
        output_type=str,
        deps_type=IRDeps,
    )

    with agent.override(model=TestModel()):
        deps = IRDeps(
            catalog=catalog,
            credential_store=CredentialStore(),
            executor=AsyncMock(return_value={"success": True, "results": [], "logs": []}),
            audit=AuditLogger(audit_path=tmp_path / "audit.log"),
            guardrails=config,
            model_name="test",
            config_db=config_db,
        )
        app = agent.to_web(
            deps=deps,
            models={"Test Model": "test"},
            instructions=CHAT_SYSTEM_PROMPT,
        )

        from dbot.config.api import init_api_state, make_settings_router

        app.routes[:] = [r for r in app.routes if getattr(r, "path", "") not in ("/", "/{id}")]
        init_api_state(
            config_db=config_db,
            catalog=catalog,
            executor=AsyncMock(return_value={"success": True, "results": [], "logs": []}),
            app=app,
        )
        settings_router = make_settings_router()
        for i, route in enumerate(settings_router.routes):
            app.routes.insert(i, route)

        yield TestClient(app)


class TestProviderCRUD:
    def test_list_providers_returns_known_providers(self, e2e_app: TestClient) -> None:
        r = e2e_app.get("/api/settings/providers")
        assert r.status_code == 200
        providers = r.json()
        assert "openai" in providers
        assert "anthropic" in providers
        assert providers["openai"]["has_key"] is False

    def test_save_provider_key(self, e2e_app: TestClient) -> None:
        r = e2e_app.put(
            "/api/settings/providers/openai",
            json={"api_key": "sk-test-key-123", "base_url": ""},
        )
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

        r2 = e2e_app.get("/api/settings/providers")
        assert r2.json()["openai"]["has_key"] is True

    def test_save_provider_with_base_url(self, e2e_app: TestClient) -> None:
        r = e2e_app.put(
            "/api/settings/providers/openai",
            json={"api_key": "sk-test", "base_url": "https://custom.api.com/v1"},
        )
        assert r.status_code == 200

        r2 = e2e_app.get("/api/settings/providers")
        assert r2.json()["openai"]["base_url"] == "https://custom.api.com/v1"

    def test_delete_provider(self, e2e_app: TestClient) -> None:
        e2e_app.put("/api/settings/providers/groq", json={"api_key": "gsk-test"})
        r = e2e_app.delete("/api/settings/providers/groq")
        assert r.status_code == 200
        assert r.json()["deleted"] is True

        r2 = e2e_app.get("/api/settings/providers")
        assert r2.json()["groq"]["has_key"] is False

    def test_update_existing_provider_key(self, e2e_app: TestClient) -> None:
        e2e_app.put("/api/settings/providers/anthropic", json={"api_key": "sk-old"})
        e2e_app.put("/api/settings/providers/anthropic", json={"api_key": "sk-new"})
        r = e2e_app.get("/api/settings/providers")
        assert r.json()["anthropic"]["has_key"] is True


class TestSettingsSections:
    def test_get_all_settings(self, e2e_app: TestClient) -> None:
        r = e2e_app.get("/api/settings")
        assert r.status_code == 200
        data = r.json()
        assert "llm" in data
        assert "guardrails" in data

    def test_get_llm_section(self, e2e_app: TestClient) -> None:
        r = e2e_app.get("/api/settings/llm")
        assert r.status_code == 200
        data = r.json()
        assert "default_model" in data

    def test_put_llm_section(self, e2e_app: TestClient) -> None:
        r = e2e_app.put("/api/settings/llm", json={"temperature": 0.5})
        assert r.status_code == 200

        r2 = e2e_app.get("/api/settings/llm")
        assert r2.json()["temperature"] == 0.5

    def test_get_guardrails_section(self, e2e_app: TestClient) -> None:
        r = e2e_app.get("/api/settings/guardrails")
        assert r.status_code == 200
        data = r.json()
        assert "chat_max_tool_calls" in data or "autonomous_max_tool_calls" in data


class TestChatE2E:
    def test_chat_streaming_response(self, e2e_app: TestClient) -> None:
        r = e2e_app.post(
            "/api/chat",
            json={
                "trigger": "submit-message",
                "id": "e2e-test",
                "messages": [
                    {"id": "m1", "role": "user", "parts": [{"type": "text", "text": "hello"}]},
                ],
            },
        )
        assert r.status_code == 200
        assert len(r.text) > 0

    def test_configure_endpoint(self, e2e_app: TestClient) -> None:
        r = e2e_app.get("/api/configure")
        assert r.status_code == 200
        data = r.json()
        assert "models" in data
        model_names = [m["name"] for m in data["models"]]
        assert "Test Model" in model_names

    def test_health_endpoint(self, e2e_app: TestClient) -> None:
        r = e2e_app.get("/api/health")
        assert r.status_code == 200

    def test_settings_health(self, e2e_app: TestClient) -> None:
        r = e2e_app.get("/api/settings/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


class TestFullFlow:
    def test_configure_then_chat(self, e2e_app: TestClient) -> None:
        """Full flow: save API key → verify configured → send chat message."""
        r = e2e_app.put(
            "/api/settings/providers/openai",
            json={"api_key": "sk-test-e2e"},
        )
        assert r.status_code == 200

        providers = e2e_app.get("/api/settings/providers").json()
        assert providers["openai"]["has_key"] is True

        r = e2e_app.post(
            "/api/chat",
            json={
                "trigger": "submit-message",
                "id": "flow-test",
                "messages": [
                    {"id": "m1", "role": "user", "parts": [{"type": "text", "text": "investigate 1.2.3.4"}]},
                ],
            },
        )
        assert r.status_code == 200

    def test_packs_visible(self, e2e_app: TestClient) -> None:
        r = e2e_app.get("/api/packs")
        assert r.status_code == 200
        packs = r.json()
        assert len(packs) > 0
        assert packs[0]["pack"] == "TestPack"
