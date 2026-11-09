from pathlib import Path
from typing import Any

import pytest
import yaml  # type: ignore[import-untyped]
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
    monkeypatch.setenv("DBOT_CONTENT_ROOT", str(tmp_path / "no-content"))
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


def _content_with_credentials(root: Path) -> None:
    integ = root / "Packs" / "P" / "Integrations" / "Cred"
    integ.mkdir(parents=True)
    script = {"type": "python", "subtype": "python3", "script": "-", "commands": [{"name": "ip"}]}
    configuration = [
        {"name": "url", "type": 0},
        {"name": "credentials", "type": 9},
        {"name": "apikey", "type": 4},
    ]
    data = {"name": "Cred", "display": "Cred", "script": script, "configuration": configuration}
    (integ / "Cred.yml").write_text(yaml.safe_dump(data))
    (integ / "Cred.py").write_text("def main():\n    pass\n")


def test_config_check_warns_on_literal_credentials(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _content_with_credentials(tmp_path / "content")
    monkeypatch.setenv("DBOT_CONTENT_ROOT", str(tmp_path / "content"))
    monkeypatch.setenv("DBOT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("DBOT_CONTENT_COMMIT", raising=False)
    monkeypatch.setenv("DBOT_CC_PW", "from-env")
    cfg = tmp_path / "dbot.yaml"
    cfg.write_text(
        "instances:\n"
        "  - name: lit\n"
        "    integration: Cred\n"
        "    params: {url: https://x.example, apikey: literal-apikey-cc1, credentials: {password: literal-pw-cc2}}\n"
        "  - {name: ref, integration: Cred, params: {apikey: env://DBOT_CC_PW, credentials: {password: env://DBOT_CC_PW}}}\n"
    )
    result = CliRunner().invoke(app, ["config", "check", str(cfg)])
    assert result.exit_code == 0
    assert "lit: params.apikey is a literal credential" in result.output
    assert "lit: params.credentials.password is a literal credential" in result.output
    assert "params.url" not in result.output
    assert "ref:" not in result.output
    assert "literal-apikey-cc1" not in result.output
    assert "literal-pw-cc2" not in result.output


def test_audit_verify_missing_path_fails(tmp_path: Path) -> None:
    assert CliRunner().invoke(app, ["audit", "verify", str(tmp_path / "nope")]).exit_code == 1


def test_invoke_rejects_malformed_arg() -> None:
    result = CliRunner().invoke(app, ["invoke", "x", "ip", "--reason", "r", "--arg", "novalue"])
    assert result.exit_code != 0


def test_invoke_requires_a_user(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DBOT_USER", raising=False)
    monkeypatch.setenv("USER", "someone")  # never used as a fallback
    result = CliRunner().invoke(app, ["invoke", "x", "ip", "--reason", "r"])
    assert result.exit_code == 2
    assert "--user or DBOT_USER is required" in result.output


def test_invoke_runs_as_the_given_user(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Any] = []

    class FakeGateway:
        @classmethod
        def from_env(cls) -> "FakeGateway":
            return cls()

        async def invoke(self, *args: Any, **kwargs: Any) -> Any:
            seen.append(args[4])
            from dbot.gateway import Denied

            return Denied(reason="stub")

    monkeypatch.setattr("dbot.gateway.Gateway", FakeGateway)
    monkeypatch.setenv("DBOT_USER", "frank")
    CliRunner().invoke(app, ["invoke", "x", "ip", "--reason", "r"])
    CliRunner().invoke(app, ["invoke", "x", "ip", "--reason", "r", "--user", "grace"])
    assert [(a.id, a.kind, a.on_behalf_of) for a in seen] == [("frank", "human", None), ("grace", "human", None)]
