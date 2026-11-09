"""mitmproxy addon that answers every request from JSON fixtures and never contacts the real host.

Run as ``mitmdump -s replay_addon.py --set replay_fixtures=<dir> --set connection_strategy=lazy``.
Routes are tried in file order; the first match wins. An unmatched request gets HTTP 599 with the
request line in the body, so the integration (and the test) fails loudly instead of hitting the internet.
"""

import json
import logging
from pathlib import Path
from typing import Any

UNMATCHED_STATUS = 599


def load_routes(directory: Path) -> list[dict[str, Any]]:
    routes: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json")):
        routes += json.loads(path.read_text())["routes"]
    return routes


def match(
    routes: list[dict[str, Any]],
    method: str,
    host: str,
    path: str,
    query: dict[str, str],
    headers: dict[str, str],
    body: str,
    form: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    """First route whose method, host and path are equal and whose query/headers/form/body constraints all hold.

    ``query``, ``headers`` and ``form`` in a route are subsets that must be present with equal values (header names
    are case-insensitive; ``form`` is the parsed multipart or urlencoded body); ``body_contains`` is a substring of
    the request body.
    """
    form = form or {}
    lower_headers = {k.lower(): v for k, v in headers.items()}
    for route in routes:
        if (route["method"].upper(), route["host"].lower(), route["path"]) != (method.upper(), host.lower(), path):
            continue
        if any(query.get(k) != v for k, v in route.get("query", {}).items()):
            continue
        if any(lower_headers.get(k.lower()) != v for k, v in route.get("headers", {}).items()):
            continue
        if any(form.get(k) != v for k, v in route.get("form", {}).items()):
            continue
        if route.get("body_contains", "") not in body:
            continue
        return route
    return None


class Replay:
    def __init__(self) -> None:
        self.routes: list[dict[str, Any]] = []

    def load(self, loader: Any) -> None:
        loader.add_option("replay_fixtures", str, "", "Directory of fixture JSON files")

    def configure(self, updated: set[str]) -> None:
        from mitmproxy import ctx

        if "replay_fixtures" in updated and ctx.options.replay_fixtures:
            self.routes = load_routes(Path(ctx.options.replay_fixtures))

    def request(self, flow: Any) -> None:
        from mitmproxy import http

        req = flow.request
        route = match(
            self.routes,
            req.method,
            req.pretty_host,
            req.path.split("?", 1)[0],
            dict(req.query.items()),
            dict(req.headers.items()),
            req.get_text(strict=False) or "",
            _form(req),
        )
        if route is None:
            line = f"{req.method} {req.pretty_url}"
            logging.warning("replay: no fixture for %s", line)
            flow.response = http.Response.make(
                UNMATCHED_STATUS, f"dbot replay: no fixture for {line}", {"Content-Type": "text/plain"}
            )
            return
        resp = route["response"]
        body = resp.get("body", "")
        content = body if isinstance(body, str) else json.dumps(body)
        content_type = "text/plain" if isinstance(body, str) else "application/json"
        headers = {"Content-Type": content_type} | resp.get("headers", {})
        flow.response = http.Response.make(resp.get("status", 200), content, headers)


def _form(req: Any) -> dict[str, str]:
    """The request's multipart or urlencoded form fields as text (empty for any other body)."""
    fields = dict(req.urlencoded_form.items())
    for k, v in req.multipart_form.items():
        fields[k.decode(errors="replace")] = v.decode(errors="replace")
    return fields


addons = [Replay()]
