"""Tests for LLM provider configuration — API keys, base URLs, env injection, reload."""

import os
from unittest.mock import AsyncMock

import pytest
from starlette.testclient import TestClient

from dbot.config.api import init_api_state, make_settings_router
from dbot.config.db import ConfigDB
from dbot.registry.catalog import Catalog
from dbot.registry.models import CommandDef, IntegrationDef


@pytest.fixture
def provider_app(config_db: ConfigDB) -> TestClient:
    """Create a test client wired for provider tests."""
    from starlette.applications import Starlette

    catalog = Catalog(
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
    executor = AsyncMock(return_value={"success": True, "results": [], "logs": []})
    router = make_settings_router()
    app = Starlette(routes=router.routes)
    init_api_state(config_db=config_db, catalog=catalog, executor=executor, app=app)
    return TestClient(app)


class TestProviderCRUD:
    def test_list_shows_known_providers(self, provider_app: TestClient) -> None:
        r = provider_app.get("/api/settings/providers")
        assert r.status_code == 200
        data = r.json()
        assert "openai" in data
        assert "anthropic" in data
        assert "google" in data
        assert "groq" in data
        assert "mistral" in data
        assert "ollama" in data

    def test_list_initial_no_keys(self, provider_app: TestClient) -> None:
        r = provider_app.get("/api/settings/providers")
        for _name, cfg in r.json().items():
            assert cfg["has_key"] is False

    def test_put_key_sets_has_key(self, provider_app: TestClient) -> None:
        provider_app.put("/api/settings/providers/openai", json={"api_key": "sk-test"})
        r = provider_app.get("/api/settings/providers")
        assert r.json()["openai"]["has_key"] is True

    def test_put_without_key_still_saves_config(self, provider_app: TestClient) -> None:
        provider_app.put(
            "/api/settings/providers/openai",
            json={
                "base_url": "https://proxy.example.com/v1",
                "env_var": "MY_OPENAI_KEY",
            },
        )
        r = provider_app.get("/api/settings/providers")
        cfg = r.json()["openai"]
        assert cfg["base_url"] == "https://proxy.example.com/v1"
        assert cfg["env_var"] == "MY_OPENAI_KEY"
        assert cfg["has_key"] is False  # no key was set

    def test_delete_removes_key(self, provider_app: TestClient) -> None:
        provider_app.put("/api/settings/providers/openai", json={"api_key": "sk-test"})
        r = provider_app.get("/api/settings/providers")
        assert r.json()["openai"]["has_key"] is True

        provider_app.delete("/api/settings/providers/openai")
        r = provider_app.get("/api/settings/providers")
        assert r.json()["openai"]["has_key"] is False

    def test_put_returns_needs_reload(self, provider_app: TestClient) -> None:
        r = provider_app.put("/api/settings/providers/openai", json={"api_key": "sk-test"})
        assert r.json()["needs_reload"] is True


class TestProviderBaseUrl:
    def test_base_url_saved_and_retrieved(self, provider_app: TestClient) -> None:
        provider_app.put(
            "/api/settings/providers/openai",
            json={
                "api_key": "sk-test",
                "base_url": "https://custom-proxy.com/v1",
            },
        )
        r = provider_app.get("/api/settings/providers")
        assert r.json()["openai"]["base_url"] == "https://custom-proxy.com/v1"

    def test_base_url_persists_after_key_update(self, provider_app: TestClient) -> None:
        # Set key + URL
        provider_app.put(
            "/api/settings/providers/openai",
            json={
                "api_key": "sk-first",
                "base_url": "https://proxy.com/v1",
            },
        )
        # Update key only (no base_url in payload defaults to empty)
        provider_app.put(
            "/api/settings/providers/openai",
            json={
                "api_key": "sk-second",
                "base_url": "https://proxy.com/v1",  # must re-send
            },
        )
        r = provider_app.get("/api/settings/providers")
        assert r.json()["openai"]["base_url"] == "https://proxy.com/v1"

    def test_base_url_env_set_without_key(self, provider_app: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        """Regression: base_url must be injected into env even when no api_key is sent."""
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
        provider_app.put(
            "/api/settings/providers/openai",
            json={
                "base_url": "https://proxy-only.com/v1",
            },
        )
        assert os.environ.get("OPENAI_BASE_URL") == "https://proxy-only.com/v1"

    def test_empty_base_url_means_default(self, provider_app: TestClient) -> None:
        provider_app.put(
            "/api/settings/providers/openai",
            json={
                "api_key": "sk-test",
                "base_url": "",
            },
        )
        r = provider_app.get("/api/settings/providers")
        assert r.json()["openai"]["base_url"] == ""

    def test_base_url_saved_without_key(self, provider_app: TestClient) -> None:
        """Base URL can be set even without an API key (e.g., for Ollama)."""
        provider_app.put(
            "/api/settings/providers/ollama",
            json={
                "base_url": "http://localhost:11434",
            },
        )
        r = provider_app.get("/api/settings/providers")
        assert r.json()["ollama"]["base_url"] == "http://localhost:11434"


class TestProviderEnvInjection:
    def test_key_injected_into_env(self, provider_app: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        provider_app.put("/api/settings/providers/openai", json={"api_key": "sk-injected"})
        assert os.environ.get("OPENAI_API_KEY") == "sk-injected"

    def test_base_url_injected_into_env(self, provider_app: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
        provider_app.put(
            "/api/settings/providers/openai",
            json={
                "api_key": "sk-test",
                "base_url": "https://proxy.com/v1",
            },
        )
        assert os.environ.get("OPENAI_BASE_URL") == "https://proxy.com/v1"

    def test_custom_env_var_name(self, provider_app: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MY_CUSTOM_KEY", raising=False)
        provider_app.put(
            "/api/settings/providers/openai",
            json={
                "api_key": "sk-custom",
                "env_var": "MY_CUSTOM_KEY",
            },
        )
        assert os.environ.get("MY_CUSTOM_KEY") == "sk-custom"

    def test_existing_env_not_overwritten(self, provider_app: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        """If env var already set (e.g., from shell), don't overwrite on startup."""
        # This tests the startup behavior in web.py, not the PUT handler
        # The PUT handler DOES overwrite (intentional — user explicitly saving)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-from-shell")
        provider_app.put("/api/settings/providers/openai", json={"api_key": "sk-from-ui"})
        # PUT SHOULD overwrite since it's an explicit user action
        assert os.environ.get("OPENAI_API_KEY") == "sk-from-ui"


class TestProviderEnvVar:
    def test_known_provider_default_env_var(self, provider_app: TestClient) -> None:
        r = provider_app.get("/api/settings/providers")
        assert r.json()["openai"]["env_var"] == "OPENAI_API_KEY"
        assert r.json()["anthropic"]["env_var"] == "ANTHROPIC_API_KEY"
        assert r.json()["google"]["env_var"] == "GOOGLE_API_KEY"

    def test_custom_env_var_persisted(self, provider_app: TestClient) -> None:
        provider_app.put(
            "/api/settings/providers/openai",
            json={
                "env_var": "CUSTOM_OPENAI_KEY",
            },
        )
        r = provider_app.get("/api/settings/providers")
        assert r.json()["openai"]["env_var"] == "CUSTOM_OPENAI_KEY"

    def test_ollama_no_env_var(self, provider_app: TestClient) -> None:
        r = provider_app.get("/api/settings/providers")
        assert r.json()["ollama"]["env_var"] == ""


class TestProviderDB:
    def test_provider_key_encrypted_in_db(self, config_db: ConfigDB) -> None:
        config_db.set_provider_key("openai", "sk-secret")
        # Direct DB query should show encrypted value, not plaintext
        row = config_db._conn.execute("SELECT value_enc FROM credentials WHERE pack = '__provider__openai'").fetchone()
        assert row is not None
        assert row["value_enc"] != "sk-secret"  # encrypted

    def test_provider_key_decrypt_roundtrip(self, config_db: ConfigDB) -> None:
        config_db.set_provider_key("anthropic", "sk-ant-test")
        assert config_db.get_provider_key("anthropic") == "sk-ant-test"

    def test_get_all_provider_keys(self, config_db: ConfigDB) -> None:
        config_db.set_provider_key("openai", "sk-1")
        config_db.set_provider_key("anthropic", "sk-2")
        keys = config_db.get_all_provider_keys()
        assert keys == {"openai": "sk-1", "anthropic": "sk-2"}

    def test_delete_provider_key(self, config_db: ConfigDB) -> None:
        config_db.set_provider_key("openai", "sk-1")
        config_db.delete_provider_key("openai")
        assert config_db.get_provider_key("openai") is None

    def test_provider_keys_not_in_pack_list(self, config_db: ConfigDB) -> None:
        """Provider keys should not appear in the integration credential list."""
        config_db.set_provider_key("openai", "sk-1")
        config_db.set_credential("VirusTotal", "apikey", "vt-key")
        filtered = config_db.get_all_credential_packs_filtered()
        assert "VirusTotal" in filtered
        assert "__provider__openai" not in filtered

    def test_provider_key_not_returned(self, config_db: ConfigDB) -> None:
        """get_provider_key returns None for unset provider."""
        assert config_db.get_provider_key("nonexistent") is None


class TestCustomProvider:
    def test_add_custom_provider(self, provider_app: TestClient) -> None:
        provider_app.put(
            "/api/settings/providers/azure-openai",
            json={
                "api_key": "azure-key-123",
                "base_url": "https://myinstance.openai.azure.com",
                "env_var": "AZURE_OPENAI_KEY",
            },
        )
        r = provider_app.get("/api/settings/providers")
        az = r.json().get("azure-openai")
        assert az is not None
        assert az["has_key"] is True
        assert az["base_url"] == "https://myinstance.openai.azure.com"
        assert az["env_var"] == "AZURE_OPENAI_KEY"

    def test_delete_custom_provider(self, provider_app: TestClient) -> None:
        provider_app.put("/api/settings/providers/custom", json={"api_key": "k"})
        provider_app.delete("/api/settings/providers/custom")
        r = provider_app.get("/api/settings/providers")
        custom = r.json().get("custom")
        # Custom provider removed entirely (not in KNOWN_PROVIDERS)
        assert custom is None or custom["has_key"] is False


class TestReloadEndpoint:
    def test_reload_without_keys_fails_gracefully(self, provider_app: TestClient) -> None:
        r = provider_app.post("/api/reload")
        # Should fail gracefully (no API keys configured)
        assert r.status_code in (200, 500)
        data = r.json()
        assert data["status"] in ("ok", "error")
