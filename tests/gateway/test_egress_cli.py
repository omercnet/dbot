from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from dbot.cli import app
from dbot.control.config import DbotConfig
from dbot.egress import allowlist, write_squid_acl


def _config(*instances: dict[str, Any]) -> DbotConfig:
    return DbotConfig.model_validate({"instances": list(instances)})


def test_allowlist_is_union_of_enabled_instances(tmp_path: Path) -> None:
    cfg = _config(
        {"name": "a", "integration": "A", "params": {"url": "https://API.example.com/v2", "key": "env://K"}},
        {"name": "b", "integration": "B", "allow_hosts": ["otx.example.org", "api.example.com"]},
        {"name": "c", "integration": "C", "params": {"url": "https://off.example.net"}, "enabled": False},
    )
    assert allowlist(cfg) == ["api.example.com", "otx.example.org"]
    out = tmp_path / "allowlist.txt"
    write_squid_acl(out, cfg)
    assert out.read_text() == "api.example.com\notx.example.org\n"


@pytest.mark.parametrize("host", ["evil.com\nall", "a b", "http://x", "*.x.com"])
def test_allowlist_rejects_injection(host: str) -> None:
    with pytest.raises(ValueError, match="invalid allow_hosts"):
        allowlist(_config({"name": "a", "integration": "A", "allow_hosts": [host]}))


def test_config_check_reports_without_values(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DBOT_CC_KEY", "canary-cc-value")
    monkeypatch.delenv("DBOT_CC_MISSING", raising=False)
    cfg = tmp_path / "dbot.yaml"
    cfg.write_text(
        "instances:\n"
        "  - {name: good, integration: A, params: {key: env://DBOT_CC_KEY}}\n"
        "  - {name: bad, integration: B, params: {key: env://DBOT_CC_MISSING}}\n"
    )
    result = CliRunner().invoke(app, ["config", "check", str(cfg)])
    assert result.exit_code == 1
    assert "good (A): OK" in result.output
    assert "bad (B): error" in result.output
    assert "DBOT_CC_MISSING" in result.output
    assert "canary-cc-value" not in result.output


def test_audit_verify_missing_path_fails(tmp_path: Path) -> None:
    assert CliRunner().invoke(app, ["audit", "verify", str(tmp_path / "nope")]).exit_code == 1


def test_invoke_rejects_malformed_arg() -> None:
    result = CliRunner().invoke(app, ["invoke", "x", "ip", "--reason", "r", "--arg", "novalue"])
    assert result.exit_code != 0
