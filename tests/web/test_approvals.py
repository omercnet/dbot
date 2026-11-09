import re

from tests.web.conftest import ANALYST, APPROVER, APPROVER2, VIEWER, Env


def request(env: Env, requested_by: str = "agent:worker", on_behalf_of: str | None = None, **args: str):  # type: ignore[no-untyped-def]
    return env.approvals.request(
        "edr", "edr-isolate-host", args or {"host": "web01"}, "contain", requested_by, on_behalf_of
    )


def decide(env: Env, approval_id: str, headers: dict[str, str], verb: str = "approve", csrf: str | None = "auto"):  # type: ignore[no-untyped-def]
    data = {}
    if csrf == "auto":
        data["csrf"] = env.csrf.token(headers["X-Forwarded-User"])
    elif csrf is not None:
        data["csrf"] = csrf
    return env.client.post(f"/approvals/{approval_id}/{verb}", data=data, headers=headers)


def test_pending_list_shows_fields_and_buttons_only_for_approvers(env: Env) -> None:
    a = request(env, on_behalf_of="alert:abc")
    for headers in [VIEWER, ANALYST]:
        page = env.client.get("/approvals", headers=headers).text
        assert a.id in page
        assert "edr-isolate-host" in page
        assert "alert:abc" in page
        assert "<form" not in page
    page = env.client.get("/approvals", headers=APPROVER).text
    assert f'action="/approvals/{a.id}/approve"' in page
    token = re.search(r'name="csrf" value="([0-9a-f]+)"', page)
    assert token is not None
    assert token.group(1) == env.csrf.token("lead")


def test_args_and_reason_are_escaped(env: Env) -> None:
    env.approvals.request("edr", "<script>alert(1)</script>", {"host": "<script>alert(2)</script>"}, "<b>x</b>", "u")
    page = env.client.get("/approvals", headers=VIEWER).text
    assert "<script>" not in page
    assert "<b>x</b>" not in page
    assert "&lt;script&gt;alert(1)" in page
    assert "&lt;script&gt;alert(2)" in page


def test_viewer_and_analyst_cannot_decide(env: Env) -> None:
    a = request(env)
    for headers in [VIEWER, ANALYST]:
        assert decide(env, a.id, headers).status_code == 403
    assert env.approvals.get(a.id).status == "pending"  # type: ignore[union-attr]


def test_approve_requires_valid_csrf(env: Env) -> None:
    a = request(env)
    assert decide(env, a.id, APPROVER, csrf=None).status_code == 403
    assert decide(env, a.id, APPROVER, csrf="0" * 64).status_code == 403
    # Another user's token does not work either.
    assert decide(env, a.id, APPROVER, csrf=env.csrf.token("lead2")).status_code == 403
    assert decide(env, a.id, APPROVER, csrf="\u00e9").status_code == 403
    assert env.approvals.get(a.id).status == "pending"  # type: ignore[union-attr]


def test_approve_and_deny(env: Env) -> None:
    a, b = request(env), request(env)
    r = decide(env, a.id, APPROVER)
    assert (r.status_code, r.headers["location"]) == (303, "/approvals")
    assert env.approvals.get(a.id).status == "approved"  # type: ignore[union-attr]
    assert env.approvals.get(a.id).approver == "lead"  # type: ignore[union-attr]
    assert decide(env, b.id, APPROVER, verb="deny").status_code == 303
    assert env.approvals.get(b.id).status == "denied"  # type: ignore[union-attr]
    # Already decided.
    assert decide(env, a.id, APPROVER2, verb="deny").status_code == 409
    assert env.approvals.get(a.id).status == "approved"  # type: ignore[union-attr]
    assert decide(env, "missing", APPROVER).status_code == 404
    assert decide(env, a.id, APPROVER, verb="explode").status_code == 404
    events = [e for e in env.audit() if e["type"] == "approval_decision"]
    assert [(e["approval_id"], e["approve"], e["result"]) for e in events] == [
        (a.id, True, "ok"),
        (b.id, False, "ok"),
        (a.id, False, "rejected"),
        ("missing", True, "rejected"),
    ]
    assert events[0]["actor"] == "lead"


def test_self_approval_is_403(env: Env) -> None:
    own = request(env, requested_by="lead")
    for_me = request(env, requested_by="agent:mcp", on_behalf_of="lead")
    assert decide(env, own.id, APPROVER).status_code == 403
    assert decide(env, for_me.id, APPROVER, verb="deny").status_code == 403
    assert env.approvals.get(own.id).status == "pending"  # type: ignore[union-attr]
    assert env.approvals.get(for_me.id).status == "pending"  # type: ignore[union-attr]
    # A different approver can still decide them.
    assert decide(env, own.id, APPROVER2).status_code == 303


def enqueue(env: Env, ext_id: str) -> str:
    alert_id = env.store.enqueue("generic", ext_id, "high", "t", [], {})
    assert alert_id is not None
    return alert_id


def test_decision_requeues_waiting_alert(env: Env) -> None:
    for verb in ["approve", "deny"]:
        alert_id = enqueue(env, verb)
        env.store.claim_next()
        env.store.set_status(alert_id, "waiting_approval")
        a = request(env, on_behalf_of=f"alert:{alert_id}")
        assert decide(env, a.id, APPROVER, verb=verb).status_code == 303
        assert env.store.get(alert_id).status == "queued"  # type: ignore[union-attr]
        assert env.audit()[-1]["requeued_alert"] == alert_id


def test_decision_leaves_running_or_finished_alert_alone(env: Env) -> None:
    running = enqueue(env, "r")
    env.store.claim_next()
    done = enqueue(env, "d")
    env.store.set_status(done, "done")
    for alert_id in [running, done]:
        a = request(env, on_behalf_of=f"alert:{alert_id}")
        assert decide(env, a.id, APPROVER).status_code == 303
    assert env.store.get(running).status == "running"  # type: ignore[union-attr]
    assert env.store.get(done).status == "done"  # type: ignore[union-attr]


def test_failed_decision_does_not_requeue(env: Env) -> None:
    alert_id = enqueue(env, "x")
    env.store.set_status(alert_id, "waiting_approval")
    a = request(env, requested_by="lead", on_behalf_of=f"alert:{alert_id}")
    assert decide(env, a.id, APPROVER).status_code == 403
    assert env.store.get(alert_id).status == "waiting_approval"  # type: ignore[union-attr]


def test_pending_gauge(env: Env) -> None:
    request(env)
    request(env)
    assert "dbot_approvals_pending 2" in env.client.get("/metrics", headers=VIEWER).text
