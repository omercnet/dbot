import json

from tests.web.conftest import VIEWER, Env, sign

ALERT = {
    "id": "ext-1",
    "source": "siem",
    "severity": "high",
    "title": "Suspicious login",
    "observables": [{"type": "ip", "value": "203.0.113.7"}, {"type": "user", "value": "bob"}],
    "raw": {"rule": "R1"},
}


def post(env: Env, body: bytes, sig: str | None = None, source: str = "generic", **headers: str):  # type: ignore[no-untyped-def]
    h = {"Content-Type": "application/json", **headers}
    if sig is not None:
        h["X-Dbot-Signature"] = sig
    return env.client.post(f"/ingest/{source}", content=body, headers=h)


def test_good_signature_enqueues_and_replay_is_duplicate(env: Env) -> None:
    body = json.dumps(ALERT).encode()
    r = post(env, body, sign(body))
    assert r.status_code == 202
    alert_id = r.json()["alert_id"]
    rec = env.store.get(alert_id)
    assert rec is not None
    assert rec.status == "queued"
    assert (rec.source, rec.ext_id, rec.severity) == ("generic", "ext-1", "high")
    assert rec.alert()["observables"] == ALERT["observables"]

    replay = post(env, body, sign(body))
    assert replay.status_code == 200
    assert replay.json() == {"duplicate": True}
    assert len(env.store.list()) == 1
    results = [e["result"] for e in env.audit() if e["type"] == "ingest"]
    assert results == ["accepted", "duplicate"]


def test_bad_or_missing_signature_is_rejected(env: Env) -> None:
    body = json.dumps(ALERT).encode()
    assert post(env, body, sign(body, "wrong-secret")).status_code == 401
    assert post(env, body).status_code == 401
    assert post(env, body, "sha1=" + sign(body)[7:]).status_code == 401
    # Signature over a different body (tampered payload).
    assert post(env, body, sign(body + b" ")).status_code == 401
    assert post(env, body, "sha256=not-hex").status_code == 401
    assert env.store.list() == []
    events = [e for e in env.audit() if e["type"] == "ingest"]
    assert len(events) == 5
    assert {e["result"] for e in events} == {"rejected"}
    assert all("bad signature" in e["reason"] for e in events)


def test_secret_never_reaches_audit(env: Env) -> None:
    body = json.dumps(ALERT).encode()
    post(env, body, sign(body))
    post(env, body, sign(body, "x"))
    text = "".join(p.read_text() for p in env.audit_dir.glob("audit-*.jsonl"))
    assert "test-ingest-secret-not-real" not in text


def test_unknown_source_404(env: Env) -> None:
    body = json.dumps(ALERT).encode()
    assert post(env, body, sign(body), source="nope").status_code == 404
    assert env.audit() == []


def test_invalid_alerts_422(env: Env) -> None:
    bad = [
        {k: v for k, v in ALERT.items() if k != "id"},
        {**ALERT, "id": ""},
        {**ALERT, "observables": [{"type": "mac", "value": "x"}]},
        {**ALERT, "observables": [{"type": "ip"}]},
        {**ALERT, "observables": [{"type": "ip", "value": f"10.0.0.{i % 250}"} for i in range(101)]},
        ["not", "an", "object"],
    ]
    for alert in bad:
        body = json.dumps(alert).encode()
        assert post(env, body, sign(body)).status_code == 422, alert
    body = b"{not json"
    assert post(env, body, sign(body)).status_code == 422
    assert env.store.list() == []
    assert env.audit() == []


def test_exactly_100_observables_and_numeric_id_accepted(env: Env) -> None:
    alert = {**ALERT, "id": 42, "observables": [{"type": "host", "value": f"h{i}"} for i in range(100)]}
    body = json.dumps(alert).encode()
    r = post(env, body, sign(body))
    assert r.status_code == 202
    rec = env.store.get(r.json()["alert_id"])
    assert rec is not None
    assert rec.ext_id == "42"


def test_body_over_1mb_rejected(env: Env) -> None:
    body = json.dumps({**ALERT, "raw": "x" * (1024 * 1024)}).encode()
    assert post(env, body, sign(body)).status_code == 413
    assert env.store.list() == []
    assert env.audit() == []


def test_ingest_ignores_identity_headers(env: Env) -> None:
    # No proxy auth on ingest: forged identity headers neither help nor hurt; only the HMAC matters.
    body = json.dumps(ALERT).encode()
    assert post(env, body, sign(body, "wrong"), **VIEWER).status_code == 401
    assert post(env, body, sign(body), **VIEWER).status_code == 202


def test_metrics_count_ingest_results(env: Env) -> None:
    body = json.dumps(ALERT).encode()
    post(env, body, sign(body))
    post(env, body, sign(body))
    post(env, body, sign(body, "bad"))
    post(env, body, sign(body), source="nope")
    big = json.dumps({**ALERT, "raw": "x" * (1024 * 1024)}).encode()
    post(env, big, sign(big))
    post(env, b"[]", sign(b"[]"))
    text = env.client.get("/metrics", headers=VIEWER).text
    assert 'dbot_ingest_total{result="accepted"} 1' in text
    assert 'dbot_ingest_total{result="duplicate"} 1' in text
    assert 'dbot_ingest_total{result="rejected"} 4' in text
    assert 'dbot_alerts{status="queued"} 1' in text
    assert "dbot_approvals_pending 0" in text
    # Only accepted, duplicate and the known-source bad signature are audited, one line each, no body.
    events = env.audit()
    assert [(e["result"], e.get("reason")) for e in events] == [
        ("accepted", None),
        ("duplicate", None),
        ("rejected", "bad signature"),
    ]
    assert all("Suspicious login" not in json.dumps(e) for e in events)
