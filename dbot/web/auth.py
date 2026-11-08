"""Identity from the auth proxy (oauth2-proxy), role mapping, and CSRF tokens.

``X-Forwarded-User`` / ``X-Forwarded-Groups`` are trusted only when the TCP peer is in ``DBOT_TRUSTED_PROXIES``.
``/ingest/*`` and ``/healthz`` never read them: the proxy passes client headers through on routes it skips.
"""

import hashlib
import hmac
import ipaddress
import logging
import os
import secrets

from starlette.exceptions import HTTPException
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.types import ASGIApp

from dbot.gateway import Actor

log = logging.getLogger(__name__)
Network = ipaddress.IPv4Network | ipaddress.IPv6Network

VIEW_ROLES = frozenset({"viewer", "analyst", "approver"})
APPROVER_ROLE = "approver"


def is_public(path: str) -> bool:
    return path == "/healthz" or path.startswith("/ingest/")


def trusted_proxies(value: str | None = None) -> list[Network]:
    """Parse ``DBOT_TRUSTED_PROXIES`` (comma-separated IPs/CIDRs, default 127.0.0.1)."""
    raw = os.environ.get("DBOT_TRUSTED_PROXIES", "127.0.0.1") if value is None else value
    return [ipaddress.ip_network(part.strip(), strict=False) for part in raw.split(",") if part.strip()]


def _peer_trusted(host: str | None, networks: list[Network]) -> bool:
    if not host:
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(ip in net for net in networks)


def roles_for(groups: set[str], role_map: dict[str, list[str]]) -> set[str]:
    return {role for role, role_groups in role_map.items() if groups & set(role_groups)}


class ProxyAuthMiddleware(BaseHTTPMiddleware):
    """Sets ``request.state.actor`` for every non-public path, or answers 401/403."""

    def __init__(self, app: ASGIApp, role_map: dict[str, list[str]], proxies: list[Network]) -> None:
        super().__init__(app)
        self.role_map = role_map
        self.proxies = proxies

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if is_public(request.url.path):
            return await call_next(request)
        if not _peer_trusted(request.client.host if request.client else None, self.proxies):
            return PlainTextResponse("unauthenticated", status_code=401)
        user = request.headers.get("x-forwarded-user", "").strip()
        if not user:
            return PlainTextResponse("unauthenticated", status_code=401)
        groups = {g.strip() for g in request.headers.get("x-forwarded-groups", "").split(",") if g.strip()}
        roles = roles_for(groups, self.role_map)
        if not roles:
            return PlainTextResponse("forbidden: no dbot role", status_code=403)
        request.state.actor = Actor(id=user, kind="human", roles=roles)
        return await call_next(request)


def actor(request: Request) -> Actor:
    found: Actor = request.state.actor
    return found


def require_viewer(request: Request) -> Actor:
    a = actor(request)
    if not a.roles & VIEW_ROLES:
        raise HTTPException(403, "forbidden: viewer role required")
    return a


def require_approver(request: Request) -> Actor:
    a = actor(request)
    if APPROVER_ROLE not in a.roles:
        raise HTTPException(403, "forbidden: approver role required")
    return a


class Csrf:
    """Per-user token: HMAC(secret, user). Secret from ``DBOT_CSRF_SECRET``, else random per process."""

    def __init__(self, secret: str | None = None) -> None:
        key = secret if secret is not None else os.environ.get("DBOT_CSRF_SECRET")
        if not key:
            log.warning("DBOT_CSRF_SECRET is unset; using a random per-process CSRF key (forms break on restart)")
        self._key = key.encode("utf-8") if key else secrets.token_bytes(32)

    def token(self, user: str) -> str:
        return hmac.new(self._key, b"csrf\x00" + user.encode("utf-8"), hashlib.sha256).hexdigest()

    def check(self, user: str, token: str) -> bool:
        return hmac.compare_digest(self.token(user).encode("ascii"), token.encode("utf-8"))
