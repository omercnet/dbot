"""Alert list and per-alert IRReport view."""

from typing import Any

from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import HTMLResponse

from dbot.web.auth import require_viewer
from dbot.web.state import state
from dbot.web.store import AlertRecord
from dbot.web.templates import esc, page, pretty, table, ts

LIST_LIMIT = 200


def _verdict(rec: AlertRecord) -> str:
    return str(rec.report.get("verdict", "")) if rec.report else ""


def list_alerts(request: Request) -> HTMLResponse:
    who = require_viewer(request)
    rows = [
        [
            f'<a href="/alerts/{esc(r.id)}">{esc(r.title or r.ext_id)}</a>',
            esc(r.source),
            esc(r.ext_id),
            esc(r.severity),
            esc(r.status),
            f'<span class="badge">{esc(_verdict(r))}</span>',
            esc(ts(r.received_at)),
        ]
        for r in state(request).store.list(LIST_LIMIT)
    ]
    body = table(["Title", "Source", "External ID", "Severity", "Status", "Verdict", "Received"], rows, "No alerts.")
    return HTMLResponse(page("Alerts", body, who))


def _value(v: Any) -> str:
    """Strings as escaped text; anything structured as escaped JSON."""
    return esc(v) if isinstance(v, str | int | float) else pretty(v)


def _items(title: str, items: Any) -> str:
    if not items:
        return ""
    if not isinstance(items, list):
        return f"<h3>{esc(title)}</h3>{_value(items)}"
    lis = "".join(f"<li>{_value(i)}</li>" for i in items)
    return f"<h3>{esc(title)}</h3><ul>{lis}</ul>"


def _report(report: dict[str, Any] | None) -> str:
    if report is None:
        return '<h2>Report</h2><p class="muted">No report yet.</p>'
    parts = [
        "<h2>Report</h2>",
        f'<p>Verdict: <span class="badge">{esc(report.get("verdict", "unknown"))}</span>',
    ]
    if "confidence" in report:
        parts.append(f" (confidence {esc(report['confidence'])})")
    parts.append("</p>")
    if report.get("summary"):
        parts.append(f"<h3>Summary</h3><p>{esc(report['summary'])}</p>")
    parts.append(_items("Evidence", report.get("evidence")))
    for key in sorted(k for k in report if k.startswith("actions")):
        parts.append(_items(key.replace("_", " ").capitalize(), report[key]))
    parts.append(f"<details><summary>Full report JSON</summary>{pretty(report)}</details>")
    return "".join(parts)


def show_alert(request: Request) -> HTMLResponse:
    who = require_viewer(request)
    rec = state(request).store.get(request.path_params["id"])
    if rec is None:
        raise HTTPException(404, "alert not found")
    facts = table(
        ["Field", "Value"],
        [
            ["ID", f"<code>{esc(rec.id)}</code>"],
            ["Source", esc(rec.source)],
            ["External ID", esc(rec.ext_id)],
            ["Severity", esc(rec.severity)],
            ["Status", esc(rec.status)],
            ["Received", esc(ts(rec.received_at))],
            ["Updated", esc(ts(rec.updated_at))],
        ],
        "",
    )
    obs = table(["Type", "Value"], [[esc(o.get("type")), esc(o.get("value"))] for o in rec.observables], "None.")
    body = f"{facts}<h2>Observables</h2>{obs}{_report(rec.report)}<h2>Raw alert</h2>{pretty(rec.raw)}"
    return HTMLResponse(page(rec.title or rec.ext_id, body, who))
