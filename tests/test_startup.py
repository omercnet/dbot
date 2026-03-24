"""Startup regression tests — catch the bugs that only appear with real content.

These tests boot the full stack against the real content submodule,
catching YAML edge cases (boolean options, int types, etc) that unit
tests with synthetic data miss.
"""

from pathlib import Path

import pytest

CONTENT_ROOT = Path(__file__).parent.parent / "content"


@pytest.fixture(autouse=True)
def _skip_if_no_content() -> None:
    if not (CONTENT_ROOT / "Packs").exists():
        pytest.skip("content submodule not initialized")


class TestFullBootstrap:
    def test_bootstrap_common_modules(self) -> None:
        """CommonServerPython loads without error (distutils shim, etc)."""
        from dbot.runtime.common_server import bootstrap_common_modules

        bootstrap_common_modules(CONTENT_ROOT)

    def test_index_all_packs(self) -> None:
        """Every YAML in the content submodule parses without error."""
        from dbot.runtime.common_server import bootstrap_common_modules

        bootstrap_common_modules(CONTENT_ROOT)

        from dbot.registry.indexer import index_content

        integrations = index_content(CONTENT_ROOT)
        assert len(integrations) > 5
        total_cmds = sum(len(i.commands) for i in integrations)
        assert total_cmds > 50

    def test_catalog_builds_from_real_content(self) -> None:
        """Catalog builds and search works on real indexed content."""
        from dbot.registry.catalog import Catalog
        from dbot.registry.indexer import index_content
        from dbot.runtime.common_server import bootstrap_common_modules

        bootstrap_common_modules(CONTENT_ROOT)
        integrations = index_content(CONTENT_ROOT)
        catalog = Catalog(integrations)
        assert catalog.stats["total_commands"] > 50
        # Search should return results
        results = catalog.search("file hash")
        assert len(results) > 0

    def test_create_web_app(self) -> None:
        """Web app creates without crashing — the ultimate smoke test."""
        from dbot.agent.web import create_app

        app = create_app()
        assert app is not None

    def test_web_app_settings_api(self) -> None:
        """Settings API returns config."""
        from starlette.testclient import TestClient

        from dbot.agent.web import create_app

        app = create_app()
        client = TestClient(app)
        resp = client.get("/api/settings")
        assert resp.status_code == 200
        data = resp.json()
        assert "general" in data
        assert "llm" in data

    def test_web_app_packs_api(self) -> None:
        """Packs API returns indexed packs."""
        from starlette.testclient import TestClient

        from dbot.agent.web import create_app

        app = create_app()
        client = TestClient(app)
        resp = client.get("/api/packs")
        assert resp.status_code == 200
        packs = resp.json()
        assert len(packs) > 5
