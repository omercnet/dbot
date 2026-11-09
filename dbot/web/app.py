"""Starlette app factory and ``dbot serve`` entry point."""

import os

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import RedirectResponse
from starlette.routing import Route

from dbot.control.approvals import Approvals
from dbot.control.audit import AuditLog
from dbot.control.config import DbotConfig, load_config
from dbot.web import alerts, ingest, metrics
from dbot.web import approvals as approval_routes
from dbot.web.auth import Csrf, Network, ProxyAuthMiddleware, trusted_proxies
from dbot.web.state import WebState
from dbot.web.store import AlertStore


def _home(_request: Request) -> RedirectResponse:
    return RedirectResponse("/alerts", status_code=303)


def create_app(
    config: DbotConfig,
    approvals: Approvals,
    audit: AuditLog,
    store: AlertStore,
    *,
    proxies: list[Network] | None = None,
    csrf: Csrf | None = None,
) -> Starlette:
    """``proxies`` defaults to ``DBOT_TRUSTED_PROXIES``; ``csrf`` to ``DBOT_CSRF_SECRET`` or a random key."""
    routes = [
        Route("/", _home),
        Route("/healthz", metrics.healthz),
        Route("/metrics", metrics.metrics),
        Route("/ingest/{source}", ingest.ingest, methods=["POST"]),
        Route("/approvals", approval_routes.list_approvals),
        Route("/approvals/{id}/{verb}", approval_routes.decide, methods=["POST"]),
        Route("/alerts", alerts.list_alerts),
        Route("/alerts/{id}", alerts.show_alert),
    ]
    middleware = [
        Middleware(
            ProxyAuthMiddleware,
            role_map=config.roles,
            proxies=trusted_proxies() if proxies is None else proxies,
        )
    ]
    app = Starlette(routes=routes, middleware=middleware)
    app.state.web = WebState(config=config, approvals=approvals, audit=audit, store=store, csrf=csrf or Csrf())
    return app


def main(host: str | None = None, port: int | None = None) -> None:
    """Serve on ``host``/``port``, else ``DBOT_HOST``/``DBOT_PORT`` (default 0.0.0.0:8080)."""
    import uvicorn

    app = create_app(load_config(), Approvals(), AuditLog(), AlertStore())
    uvicorn.run(
        app,
        host=host or os.environ.get("DBOT_HOST", "0.0.0.0"),  # noqa: S104
        port=port or int(os.environ.get("DBOT_PORT", "8080")),
        # The peer address must stay the real TCP peer: identity trust depends on it.
        proxy_headers=False,
        server_header=False,
    )
