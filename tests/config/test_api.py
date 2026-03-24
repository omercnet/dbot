"""Tests for settings API routes."""

from unittest.mock import AsyncMock

from starlette.testclient import TestClient

from dbot.config.api import make_settings_router
from dbot.config.db import ConfigDB
from dbot.registry.catalog import Catalog
from dbot.registry.models import CommandDef, IntegrationDef


def _make_test_app(config_db: ConfigDB) -> TestClient:
    """Create a test client with settings routes."""
    from starlette.applications import Starlette

    from dbot.config.api import init_api_state

    catalog = Catalog(
        [
            IntegrationDef(
                pack="TestPack",
                name="TestIntegration",
                category="Utilities",
                py_path="/tmp/fake.py",
                commands=[CommandDef(name="test-cmd", description="Test")],
            ),
        ]
    )
    executor = AsyncMock(return_value={"success": True, "results": [], "logs": []})

    # Set module-level state for API handlers
    init_api_state(config_db=config_db, catalog=catalog, executor=executor)

    router = make_settings_router()
    app = Starlette(routes=router.routes)
    return TestClient(app)


class TestGetSettings:
    def test_get_all(self, config_db: ConfigDB) -> None:
        client = _make_test_app(config_db)
        resp = client.get("/api/settings")
        assert resp.status_code == 200
        data = resp.json()
        assert "general" in data
        assert "llm" in data

    def test_get_section(self, config_db: ConfigDB) -> None:
        client = _make_test_app(config_db)
        resp = client.get("/api/settings/general")
        assert resp.status_code == 200
        assert resp.json()["execution_mode"] == "inprocess"


class TestPutSettings:
    def test_put_section(self, config_db: ConfigDB) -> None:
        client = _make_test_app(config_db)
        resp = client.put(
            "/api/settings/general",
            json={"execution_mode": "subprocess", "audit_log_path": "new.log", "content_root": ""},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        # Verify persisted
        data = config_db.get_section("general")
        assert data["execution_mode"] == "subprocess"

    def test_put_invalid_data(self, config_db: ConfigDB) -> None:
        client = _make_test_app(config_db)
        resp = client.put(
            "/api/settings/general",
            json={"execution_mode": "bad_value"},
        )
        assert resp.status_code == 400


class TestCredentialRoutes:
    def test_list_empty(self, config_db: ConfigDB) -> None:
        client = _make_test_app(config_db)
        resp = client.get("/api/settings/credentials")
        assert resp.status_code == 200
        assert resp.json() == {}

    def test_put_and_list(self, config_db: ConfigDB) -> None:
        client = _make_test_app(config_db)
        resp = client.put(
            "/api/settings/credentials/VirusTotal",
            json={"apikey": "test-key-123"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

        # List should show pack + param names (no values)
        resp = client.get("/api/settings/credentials")
        data = resp.json()
        assert "VirusTotal" in data
        assert "apikey" in data["VirusTotal"]

    def test_delete(self, config_db: ConfigDB) -> None:
        client = _make_test_app(config_db)
        client.put("/api/settings/credentials/Pack", json={"key": "val"})
        resp = client.delete("/api/settings/credentials/Pack")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True
        # Verify gone
        resp = client.get("/api/settings/credentials")
        assert "Pack" not in resp.json()

    def test_credential_route_ordering(self, config_db: ConfigDB) -> None:
        """Regression: /api/settings/credentials must not be captured by /api/settings/{section}."""
        client = _make_test_app(config_db)
        resp = client.get("/api/settings/credentials")
        assert resp.status_code == 200
        # Should be credentials list, not a config section
        assert isinstance(resp.json(), dict)


class TestPacksRoute:
    def test_list_packs(self, config_db: ConfigDB) -> None:
        client = _make_test_app(config_db)
        resp = client.get("/api/packs")
        assert resp.status_code == 200
        packs = resp.json()
        assert len(packs) == 1
        assert packs[0]["pack"] == "TestPack"
        assert packs[0]["commands"] == 1


class TestSettingsPageRemoved:
    def test_legacy_settings_route_removed(self, config_db: ConfigDB) -> None:
        client = _make_test_app(config_db)
        resp = client.get("/settings")
        assert resp.status_code in (404, 405)


class TestHealthRoute:
    def test_health(self, config_db: ConfigDB) -> None:
        client = _make_test_app(config_db)
        resp = client.get("/api/settings/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


class TestChatRoutes:
    def test_list_chats_empty(self, config_db: ConfigDB) -> None:
        client = _make_test_app(config_db)
        resp = client.get("/api/chats")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_put_get_and_list_chat(self, config_db: ConfigDB) -> None:
        client = _make_test_app(config_db)
        payload = {
            "title": "SOC Session",
            "messages": [{"id": "m1", "role": "user", "parts": [{"type": "text", "text": "hello"}]}],
        }

        put_resp = client.put("/api/chats/chat-123", json=payload)
        assert put_resp.status_code == 200
        assert put_resp.json()["status"] == "ok"

        get_resp = client.get("/api/chats/chat-123")
        assert get_resp.status_code == 200
        get_data = get_resp.json()
        assert get_data["id"] == "chat-123"
        assert get_data["title"] == "SOC Session"
        assert get_data["messages"] == payload["messages"]

        list_resp = client.get("/api/chats")
        assert list_resp.status_code == 200
        list_data = list_resp.json()
        assert len(list_data) == 1
        assert list_data[0]["id"] == "chat-123"
        assert list_data[0]["title"] == "SOC Session"
        assert list_data[0]["message_count"] == 1

    def test_get_chat_not_found(self, config_db: ConfigDB) -> None:
        client = _make_test_app(config_db)
        resp = client.get("/api/chats/missing")
        assert resp.status_code == 404

    def test_delete_chat(self, config_db: ConfigDB) -> None:
        client = _make_test_app(config_db)
        client.put("/api/chats/chat-delete", json={"title": "Delete me", "messages": []})
        delete_resp = client.delete("/api/chats/chat-delete")
        assert delete_resp.status_code == 200
        assert delete_resp.json()["deleted"] is True
        get_resp = client.get("/api/chats/chat-delete")
        assert get_resp.status_code == 404
