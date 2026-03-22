"""Tests for React SPA static file serving and route integration.

Verifies:
- Built UI dist/ is served correctly (index.html, assets)
- SPA catch-all routes non-API paths to index.html
- API routes (/api/settings/*) are not captured by the SPA catch-all
- App works without dist/ directory (graceful degradation)
"""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.testclient import TestClient

from dbot.config.api import init_api_state, make_settings_router
from dbot.config.db import ConfigDB
from dbot.registry.catalog import Catalog
from dbot.registry.models import CommandDef, IntegrationDef


@pytest.fixture
def config_db(tmp_path: Path) -> ConfigDB:
    return ConfigDB(tmp_path / "test.db", tmp_path / ".dbot-key")


def _build_spa_app(ui_dist: Path, config_db: ConfigDB) -> Starlette:
    """Build a minimal Starlette app with SPA + settings routes.

    Mirrors the mounting logic in dbot.agent.web.create_app() without
    bootstrapping the full dbot stack (no content submodule needed).
    """
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

    # Start with settings routes (same order as create_app)
    settings_router = make_settings_router()
    app = Starlette(routes=list(settings_router.routes))
    init_api_state(config_db=config_db, catalog=catalog, executor=executor, app=app)

    # Mount SPA — same logic as web.py
    if ui_dist.is_dir():
        assets_dir = ui_dist / "assets"
        if assets_dir.is_dir():
            app.routes.append(Mount("/assets", app=StaticFiles(directory=str(assets_dir)), name="spa-assets"))

        spa_index = str(ui_dist / "index.html")

        async def spa_fallback(request: Request) -> FileResponse:
            return FileResponse(spa_index)

        app.routes.append(Route("/{path:path}", spa_fallback, methods=["GET"]))
        app.routes.append(Route("/", spa_fallback, methods=["GET"]))

    return app


@pytest.fixture
def ui_dist(tmp_path: Path) -> Path:
    """Create a fake UI dist directory with index.html and a JS asset."""
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text(
        '<!doctype html><html><body><div id="root"></div><script src="/assets/index-abc123.js"></script></body></html>',
        encoding="utf-8",
    )
    assets = dist / "assets"
    assets.mkdir()
    (assets / "index-abc123.js").write_text(
        'console.log("dbot SPA");',
        encoding="utf-8",
    )
    return dist


@pytest.fixture
def spa_app(ui_dist: Path, config_db: ConfigDB) -> TestClient:
    """Test client with SPA + settings routes mounted."""
    return TestClient(_build_spa_app(ui_dist, config_db))


@pytest.fixture
def no_spa_app(tmp_path: Path, config_db: ConfigDB) -> TestClient:
    """Test client WITHOUT dist/ — SPA not mounted."""
    fake_dist = tmp_path / "nonexistent"
    return TestClient(_build_spa_app(fake_dist, config_db))


class TestSPAServing:
    def test_root_serves_index_html(self, spa_app: TestClient) -> None:
        r = spa_app.get("/")
        assert r.status_code == 200
        assert '<div id="root">' in r.text

    def test_spa_route_serves_index_html(self, spa_app: TestClient) -> None:
        """Client-side routes like /chat/123 should serve index.html."""
        r = spa_app.get("/chat/123")
        assert r.status_code == 200
        assert '<div id="root">' in r.text

    def test_deep_spa_route_serves_index_html(self, spa_app: TestClient) -> None:
        """Deeply nested SPA routes should also serve index.html."""
        r = spa_app.get("/settings/providers/openai/edit")
        assert r.status_code == 200
        assert '<div id="root">' in r.text

    def test_assets_served(self, spa_app: TestClient) -> None:
        r = spa_app.get("/assets/index-abc123.js")
        assert r.status_code == 200
        assert "dbot SPA" in r.text

    def test_nonexistent_asset_404(self, spa_app: TestClient) -> None:
        r = spa_app.get("/assets/nonexistent.js")
        assert r.status_code == 404

    def test_index_html_content_type(self, spa_app: TestClient) -> None:
        r = spa_app.get("/")
        assert "text/html" in r.headers["content-type"]

    def test_js_asset_content_type(self, spa_app: TestClient) -> None:
        r = spa_app.get("/assets/index-abc123.js")
        content_type = r.headers["content-type"]
        assert "javascript" in content_type or "text/plain" in content_type


class TestAPIPassthrough:
    """API routes must NOT be captured by the SPA catch-all."""

    def test_settings_providers(self, spa_app: TestClient) -> None:
        r = spa_app.get("/api/settings/providers")
        assert r.status_code == 200
        data = r.json()
        assert "openai" in data

    def test_settings_health(self, spa_app: TestClient) -> None:
        r = spa_app.get("/api/settings/health")
        assert r.status_code == 200
        data = r.json()
        assert "status" in data

    def test_settings_all(self, spa_app: TestClient) -> None:
        r = spa_app.get("/api/settings")
        assert r.status_code == 200
        assert isinstance(r.json(), dict)

    def test_packs_list(self, spa_app: TestClient) -> None:
        r = spa_app.get("/api/packs")
        assert r.status_code == 200

    def test_provider_put(self, spa_app: TestClient) -> None:
        r = spa_app.put("/api/settings/providers/openai", json={"api_key": "sk-test"})
        assert r.status_code == 200


class TestNoDistFallback:
    """When dist/ doesn't exist, app should still work (settings only)."""

    def test_settings_still_work(self, no_spa_app: TestClient) -> None:
        r = no_spa_app.get("/api/settings/providers")
        assert r.status_code == 200
        assert "openai" in r.json()

    def test_root_not_found(self, no_spa_app: TestClient) -> None:
        """Without SPA, root should 404 (no route matches)."""
        r = no_spa_app.get("/")
        assert r.status_code in (404, 405)
