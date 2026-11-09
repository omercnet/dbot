"""Approver page: list pending approvals and approve/deny them."""

from typing import Any

from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from dbot.control.approvals import Approval, ApprovalError
from dbot.control.secrets import redact
from dbot.gateway import Actor
from dbot.web.auth import APPROVER_ROLE, require_approver, require_viewer
from dbot.web.state import WebState, state
from dbot.web.templates import esc, page, pretty, table, ts

ALERT_PREFIX = "alert:"


def list_approvals(request: Request) -> HTMLResponse:
    who = require_viewer(request)
    web = state(request)
    can_decide = APPROVER_ROLE in who.roles
    token = web.csrf.token(who.id)
    rows = []
    for a in web.approvals.pending():
        cells = [
            f"<code>{esc(a.id)}</code>",
            esc(a.instance),
            esc(a.command),
            pretty(redact(a.args)),
            esc(redact(a.reason)),
            esc(a.requested_by),
            esc(a.on_behalf_of or ""),
            esc(ts(a.created_at)),
            esc(ts(a.expires_at)),
        ]
        if can_decide:
            cells.append(_buttons(a, who, token))
        rows.append(cells)
    headers = ["ID", "Instance", "Command", "Args", "Reason", "Requested by", "On behalf of", "Created", "Expires"]
    if can_decide:
        headers.append("Decision")
    body = table(headers, rows, "No pending approvals.")
    return HTMLResponse(page("Pending approvals", body, who))


def _buttons(a: Approval, who: Actor, token: str) -> str:
    if who.id in (a.requested_by, a.on_behalf_of):
        return '<span class="muted">your request</span>'
    out = []
    for verb, label in (("approve", "Approve"), ("deny", "Deny")):
        out.append(
            f'<form method="post" action="/approvals/{esc(a.id)}/{verb}">'
            f'<input type="hidden" name="csrf" value="{esc(token)}">'
            f'<button class="{verb}" type="submit">{label}</button></form>'
        )
    return " ".join(out)


def _decide(web: WebState, approval_id: str, who: Actor, approve: bool) -> tuple[int, str, Approval | None]:
    """Decide, audit, and requeue a linked alert. Returns (http status, message, decided approval)."""
    current = web.approvals.get(approval_id)
    decided: Approval | None = None
    if current is None:
        status, message = 404, "approval not found"
    else:
        try:
            decided = web.approvals.decide(approval_id, who.id, approve)
            status, message = 303, decided.status
        except ApprovalError as e:
            own = who.id in (current.requested_by, current.on_behalf_of)
            status, message = (403 if own else 409), str(e)
    requeued = None
    if decided is not None and decided.on_behalf_of and decided.on_behalf_of.startswith(ALERT_PREFIX):
        alert_id = decided.on_behalf_of[len(ALERT_PREFIX) :]
        # Only an alert parked on approvals resumes; a running or finished one is left alone.
        if web.store.set_status(alert_id, "queued", expect="waiting_approval"):
            requeued = alert_id
    event: dict[str, Any] = {
        "type": "approval_decision",
        "actor": who.id,
        "actor_kind": who.kind,
        "roles": sorted(who.roles),
        "approval_id": approval_id,
        "approve": approve,
        "result": "ok" if decided is not None else "rejected",
        "status": decided.status if decided else None,
        "error": None if decided else message,
        "instance": current.instance if current else None,
        "command": current.command if current else None,
        "requeued_alert": requeued,
    }
    web.audit.append(event)
    return status, message, decided


async def decide(request: Request) -> Response:
    who = require_approver(request)
    web = state(request)
    verb = request.path_params["verb"]
    if verb not in ("approve", "deny"):
        raise HTTPException(404, "not found")
    approve = verb == "approve"
    form = await request.form()
    token = form.get("csrf")
    if not isinstance(token, str) or not web.csrf.check(who.id, token):
        raise HTTPException(403, "forbidden: missing or invalid CSRF token")
    status, message, _ = await run_in_threadpool(_decide, web, request.path_params["id"], who, approve)
    if status == 303:
        return RedirectResponse("/approvals", status_code=303)
    return HTMLResponse(page("Approval not decided", f"<p>{esc(message)}</p>", who), status_code=status)
