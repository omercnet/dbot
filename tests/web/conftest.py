import hashlib
import hmac
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from dbot.control.approvals import Approvals
from dbot.control.audit import AuditLog
from dbot.control.config import DbotConfig
from dbot.web.app import create_app
from dbot.web.auth import Csrf, trusted_proxies
from dbot.web.store import AlertStore

SECRET = "test-ingest-secret-not-real"
PROXY = ("127.0.0.1", 1234)

VIEWER = {"X-Forwarded-User": "vera", "X-Forwarded-Groups": "soc-viewers"}
ANALYST = {"X-Forwarded-User": "ana", "X-Forwarded-Groups": "soc-analysts"}
APPROVER = {"X-Forwarded-User": "lead", "X-Forwarded-Groups": "other, soc-leads"}
APPROVER2 = {"X-Forwarded-User": "lead2", "X-Forwarded-Groups": "soc-leads"}


@dataclass
class Env:
    client: TestClient
    approvals: Approvals
    store: AlertStore
    csrf: Csrf
    audit_dir: Path

    def audit(self) -> list[dict[str, Any]]:
        return [json.loads(line) for p in sorted(self.audit_dir.glob("audit-*.jsonl")) for line in p.open()]


def sign(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def make_client(app: Any, peer: tuple[str, int] = PROXY) -> TestClient:
    return TestClient(app, base_url="http://localhost", client=peer, follow_redirects=False)


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Env:
    monkeypatch.setenv("DBOT_TEST_INGEST_SECRET", SECRET)
    config = DbotConfig.model_validate(
        {
            "roles": {"viewer": ["soc-viewers"], "analyst": ["soc-analysts"], "approver": ["soc-leads"]},
            "ingest": {"sources": {"generic": {"hmac_secret": "env://DBOT_TEST_INGEST_SECRET"}}},
        }
    )
    approvals = Approvals(tmp_path / "dbot.db")
    store = AlertStore(tmp_path / "dbot.db")
    csrf = Csrf("test-csrf-key")
    app = create_app(
        config, approvals, AuditLog(tmp_path / "audit"), store, proxies=trusted_proxies("127.0.0.1"), csrf=csrf
    )
    return Env(make_client(app), approvals, store, csrf, tmp_path / "audit")
