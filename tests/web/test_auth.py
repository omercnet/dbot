import logging

import pytest
from starlette.applications import Starlette

from dbot.web.auth import Csrf, trusted_proxies
from tests.web.conftest import ANALYST, APPROVER, VIEWER, Env, make_client


def test_untrusted_peer_with_forged_headers_is_401(env: Env) -> None:
    app = env.client.app
    assert isinstance(app, Starlette)
    for peer in [("10.1.2.3", 5555), ("testclient", 50000), ("::1", 1)]:
        c = make_client(app, peer)
        for path in ["/approvals", "/alerts", "/metrics"]:
            assert c.get(path, headers=APPROVER).status_code == 401, (peer, path)


def test_trusted_peer_without_user_is_401(env: Env) -> None:
    assert env.client.get("/alerts").status_code == 401
    assert env.client.get("/alerts", headers={"X-Forwarded-Groups": "soc-leads"}).status_code == 401


def test_user_without_mapped_group_is_403(env: Env) -> None:
    for groups in ["", "random-group", "soc-viewers-x", "SOC-VIEWERS"]:
        r = env.client.get("/alerts", headers={"X-Forwarded-User": "eve", "X-Forwarded-Groups": groups})
        assert r.status_code == 403, groups


def test_each_role_can_view(env: Env) -> None:
    for headers in [VIEWER, ANALYST, APPROVER]:
        assert env.client.get("/alerts", headers=headers).status_code == 200
        assert env.client.get("/approvals", headers=headers).status_code == 200


def test_healthz_needs_no_identity(env: Env) -> None:
    app = env.client.app
    r = make_client(app, ("10.9.9.9", 1)).get("/healthz")
    assert (r.status_code, r.text) == (200, "ok")


def test_trusted_proxies_parsing() -> None:
    nets = trusted_proxies(" 10.0.0.0/8, 192.168.1.5 ,::1")
    assert [str(n) for n in nets] == ["10.0.0.0/8", "192.168.1.5/32", "::1/128"]


def test_csrf_warns_without_secret(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.delenv("DBOT_CSRF_SECRET", raising=False)
    with caplog.at_level(logging.WARNING, logger="dbot.web.auth"):
        a, b = Csrf(), Csrf()
    assert "DBOT_CSRF_SECRET is unset" in caplog.text
    assert a.token("u") != b.token("u")  # random key per instance
    caplog.clear()
    monkeypatch.setenv("DBOT_CSRF_SECRET", "configured-test-key")
    with caplog.at_level(logging.WARNING, logger="dbot.web.auth"):
        c, d = Csrf(), Csrf()
    assert caplog.text == ""
    assert c.token("u") == d.token("u")


def test_cidr_trusted_proxy(env: Env) -> None:
    from dbot.web.app import create_app

    st = env.client.app.state.web  # type: ignore[attr-defined]
    app = create_app(st.config, st.approvals, st.audit, st.store, proxies=trusted_proxies("10.0.0.0/8"))
    assert make_client(app, ("10.200.1.1", 1)).get("/alerts", headers=VIEWER).status_code == 200
    assert make_client(app, ("11.0.0.1", 1)).get("/alerts", headers=VIEWER).status_code == 401
    assert make_client(app, ("127.0.0.1", 1)).get("/alerts", headers=VIEWER).status_code == 401
