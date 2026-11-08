"""Shared objects the route handlers read from ``request.app.state.web``."""

from collections import Counter
from dataclasses import dataclass, field

from starlette.requests import Request

from dbot.control.approvals import Approvals
from dbot.control.audit import AuditLog
from dbot.control.config import DbotConfig
from dbot.web.auth import Csrf
from dbot.web.store import AlertStore


@dataclass
class WebState:
    config: DbotConfig
    approvals: Approvals
    audit: AuditLog
    store: AlertStore
    csrf: Csrf
    ingest_counts: Counter[str] = field(default_factory=Counter)  # accepted | rejected | duplicate


def state(request: Request) -> WebState:
    web: WebState = request.app.state.web
    return web
