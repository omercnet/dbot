"""Server-rendered HTML. Every interpolated value goes through ``esc``."""

import html
import json
from datetime import UTC, datetime
from typing import Any

from dbot.gateway import Actor

_CSS = """
body{font-family:system-ui,sans-serif;margin:0;color:#1d1d1f;background:#f6f6f7}
header{background:#1d2433;color:#fff;padding:.6rem 1.2rem;display:flex;gap:1.2rem;align-items:center}
header a{color:#cfd8ea;text-decoration:none}header .who{margin-left:auto;font-size:.85rem;color:#aab}
main{padding:1.2rem;max-width:1200px}
table{border-collapse:collapse;width:100%;background:#fff}
th,td{border:1px solid #ddd;padding:.4rem .6rem;text-align:left;vertical-align:top;font-size:.9rem}
th{background:#eef0f4}
pre{background:#fff;border:1px solid #ddd;padding:.6rem;overflow:auto;white-space:pre-wrap;font-size:.85rem;margin:0}
form{display:inline}button{padding:.25rem .7rem;cursor:pointer}
.approve{background:#1e7f3c;color:#fff;border:0}.deny{background:#b3261e;color:#fff;border:0}
.muted{color:#777}.badge{font-weight:600;text-transform:uppercase}
"""


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def ts(value: float | None) -> str:
    if value is None:
        return ""
    return datetime.fromtimestamp(value, UTC).strftime("%Y-%m-%d %H:%M:%SZ")


def pretty(value: Any) -> str:
    """Indented JSON in an escaped <pre> block."""
    return f"<pre>{esc(json.dumps(value, indent=2, sort_keys=True, default=str))}</pre>"


def page(title: str, body: str, actor: Actor | None) -> str:
    who = f'<span class="who">{esc(actor.id)} ({esc(", ".join(sorted(actor.roles)))})</span>' if actor else ""
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        f"<title>{esc(title)} - dbot</title><style>{_CSS}</style></head><body>"
        '<header><strong>dbot</strong><a href="/alerts">Alerts</a><a href="/approvals">Approvals</a>'
        f"{who}</header><main><h1>{esc(title)}</h1>{body}</main></body></html>"
    )


def table(headers: list[str], rows: list[list[str]], empty: str) -> str:
    """``rows`` cells must already be escaped HTML."""
    if not rows:
        return f'<p class="muted">{esc(empty)}</p>'
    head = "".join(f"<th>{esc(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"
