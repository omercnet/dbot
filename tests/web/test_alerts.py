from tests.web.conftest import ANALYST, VIEWER, Env

XSS = "<script>alert('x')</script>"


def test_alert_title_and_fields_are_escaped(env: Env) -> None:
    alert_id = env.store.enqueue(
        "generic", f"id-{XSS}", f"<img src=x onerror=1>{XSS}", XSS, [{"type": "url", "value": XSS}], {"k": XSS}
    )
    assert alert_id is not None
    env.store.set_report(alert_id, {"verdict": XSS, "summary": XSS, "evidence": [XSS, {"ref": XSS}]})
    for path in ["/alerts", f"/alerts/{alert_id}"]:
        page = env.client.get(path, headers=VIEWER).text
        assert "<script>" not in page
        assert "<img" not in page
        assert "&lt;script&gt;" in page


def test_report_rendering(env: Env) -> None:
    alert_id = env.store.enqueue("generic", "e1", "low", "Login", [{"type": "ip", "value": "198.51.100.4"}], {})
    assert alert_id is not None
    page = env.client.get(f"/alerts/{alert_id}", headers=ANALYST).text
    assert "No report yet." in page
    assert "198.51.100.4" in page
    env.store.set_report(
        alert_id,
        '{"verdict": "malicious", "summary": "Known bad IP", "confidence": 0.9,'
        ' "evidence": [{"entry": "abuseipdb#1", "detail": "score 100"}],'
        ' "actions_taken": ["enriched ip"], "actions_pending": [{"approval_id": "ap1"}]}',
    )
    page = env.client.get(f"/alerts/{alert_id}", headers=VIEWER).text
    for text in ["malicious", "Known bad IP", "abuseipdb#1", "Actions taken", "enriched ip", "Actions pending", "ap1"]:
        assert text in page, text
    listing = env.client.get("/alerts", headers=VIEWER).text
    assert f'href="/alerts/{alert_id}"' in listing
    assert "malicious" in listing


def test_missing_alert_404(env: Env) -> None:
    assert env.client.get("/alerts/nope", headers=VIEWER).status_code == 404
