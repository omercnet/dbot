from pathlib import Path
from typing import Any

import pytest

from dbot.catalog.models import CommandDef
from dbot.control import secrets
from dbot.control.config import ConfigError, InstanceConfig, PolicyConfig, default_allow_hosts, load_config
from dbot.control.policy import risk

EXAMPLE = """
instances:
  - name: abuseipdb
    integration: AbuseIPDB
    params: {server: https://api.abuseipdb.com, credentials: {password: env://ABUSEIPDB_API_KEY}}
    allow_hosts: [api.abuseipdb.com]
    enabled: true
policy:
  destructive: ["*block*", "*-create-*"]
  read: ["test-module"]
roles:
  viewer: [soc-viewers]
  approver: [soc-leads]
ingest:
  sources:
    generic: {hmac_secret: env://DBOT_INGEST_SECRET}
agent:
  model: "openai:gpt-5.4-mini"
  max_tool_calls: 20
  wall_clock_s: 300
"""


def _write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "dbot.yaml"
    p.write_text(text, encoding="utf-8")
    return p


# --- config ---


def test_spec_example_loads(tmp_path: Path) -> None:
    cfg = load_config(_write(tmp_path, EXAMPLE))
    inst = cfg.instance("abuseipdb")
    assert inst is not None
    assert inst.params["credentials"]["password"] == "env://ABUSEIPDB_API_KEY"
    assert cfg.ingest.sources["generic"].hmac_secret == "env://DBOT_INGEST_SECRET"
    assert cfg.roles["approver"] == ["soc-leads"]


def test_config_path_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DBOT_CONFIG", str(_write(tmp_path, "instances: []\n")))
    assert load_config().instances == []


def test_duplicate_instance_names_rejected(tmp_path: Path) -> None:
    text = "instances:\n  - {name: a, integration: X}\n  - {name: a, integration: Y}\n"
    with pytest.raises(ConfigError, match="duplicate instance name 'a'"):
        load_config(_write(tmp_path, text))


@pytest.mark.parametrize("roles", ["roles: {analyst: []}", "roles: {analyst: ['']}", "roles: {analyst: null}"])
def test_empty_roles_rejected(tmp_path: Path, roles: str) -> None:
    with pytest.raises(ConfigError, match="roles"):
        load_config(_write(tmp_path, roles + "\n"))


@pytest.mark.parametrize("ref", ["vault://kv/abuse", "aws-sm://prod/key", "VAULT://x"])
def test_unsupported_secret_scheme_rejected(tmp_path: Path, ref: str) -> None:
    text = f"instances:\n  - name: a\n    integration: X\n    params: {{creds: [{{password: '{ref}'}}]}}\n"
    with pytest.raises(ConfigError, match=r"params\.creds\[0\]\.password: unsupported secret scheme"):
        load_config(_write(tmp_path, text))


def test_unsupported_scheme_in_ingest_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="unsupported secret scheme"):
        load_config(_write(tmp_path, "ingest: {sources: {g: {hmac_secret: 'vault://x'}}}\n"))


@pytest.mark.parametrize("ref", ["env://", "env://1BAD", "env://A-B", "file://relative/path"])
def test_malformed_refs_rejected(tmp_path: Path, ref: str) -> None:
    text = f"instances:\n  - {{name: a, integration: X, params: {{k: '{ref}'}}}}\n"
    with pytest.raises(ConfigError, match="invalid"):
        load_config(_write(tmp_path, text))


def test_url_and_literal_params_allowed(tmp_path: Path) -> None:
    text = "instances:\n  - {name: a, integration: X, params: {url: 'https://x.io', k: 'plain:value'}}\n"
    assert load_config(_write(tmp_path, text)).instances[0].params["url"] == "https://x.io"


def test_unknown_keys_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="instancez"):
        load_config(_write(tmp_path, "instancez: []\n"))


def test_validation_error_does_not_echo_literal_credentials(tmp_path: Path) -> None:
    text = "instances:\n  - {name: a, integration: X, params: {apikey: LITERAL-SECRET-123}, bogus: 1}\n"
    with pytest.raises(ConfigError) as exc:
        load_config(_write(tmp_path, text))
    assert "LITERAL-SECRET-123" not in str(exc.value)


def test_yaml_error_does_not_echo_content(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="invalid YAML at line") as exc:
        load_config(_write(tmp_path, "instances:\n  - {apikey: LITERAL-SECRET-123\n"))
    assert "LITERAL-SECRET-123" not in str(exc.value)


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="cannot read"):
        load_config(tmp_path / "nope.yaml")


def test_default_allow_hosts_derived_from_url_params() -> None:
    inst = InstanceConfig(
        name="a",
        integration="X",
        params={
            "server": "https://API.example.com:8443/v1",
            "nested": {"proxy_url": "http://other.example.org"},
            "list": ["https://third.example.net/x", "not a url", "env://FOO"],
            "key": "file:///run/secrets/k",
        },
    )
    assert default_allow_hosts(inst) == ["api.example.com", "other.example.org", "third.example.net"]


def test_explicit_allow_hosts_win() -> None:
    inst = InstanceConfig(name="a", integration="X", params={"server": "https://x.io"}, allow_hosts=[])
    assert default_allow_hosts(inst) == []


# --- secrets ---


def test_resolve_nested(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DBOT_T_KEY", "k-123456")
    (tmp_path / "s").write_text("f-abcdef\n", encoding="utf-8")
    params: dict[str, Any] = {
        "server": "https://x.io",
        "credentials": {"identifier": "user", "password": "env://DBOT_T_KEY"},
        "list": [{"token": f"file://{tmp_path / 's'}"}, 5, True],
    }
    out = secrets.resolve(params)
    assert out == {
        "server": "https://x.io",
        "credentials": {"identifier": "user", "password": "k-123456"},
        "list": [{"token": "f-abcdef"}, 5, True],
    }
    assert params["credentials"]["password"] == "env://DBOT_T_KEY"  # input untouched
    assert {"k-123456", "f-abcdef"} <= secrets.known_values()
    assert "user" not in secrets.known_values()


def test_missing_env_names_reference_not_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DBOT_T_MISSING", raising=False)
    monkeypatch.setenv("DBOT_T_PRESENT", "present-value-xyz")
    with pytest.raises(secrets.SecretError) as exc:
        secrets.resolve({"a": "env://DBOT_T_PRESENT", "b": {"c": "env://DBOT_T_MISSING"}})
    msg = str(exc.value)
    assert "env://DBOT_T_MISSING" in msg
    assert "present-value-xyz" not in msg
    assert exc.value.__cause__ is None


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(secrets.SecretError, match="file:///"):
        secrets.resolve({"k": f"file://{tmp_path / 'absent'}"})


def test_empty_secret_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DBOT_T_EMPTY", "")
    with pytest.raises(secrets.SecretError, match="empty"):
        secrets.resolve({"k": "env://DBOT_T_EMPTY"})


def test_redact_text_and_objects(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DBOT_T_SHORT", "abc-secret")
    monkeypatch.setenv("DBOT_T_LONG", "abc-secret-longer")
    secrets.resolve({"a": "env://DBOT_T_SHORT", "b": "env://DBOT_T_LONG"})
    r = secrets.REDACTED
    assert secrets.redact("x abc-secret-longer y abc-secret") == f"x {r} y {r}"
    assert secrets.redact({"abc-secret": ["Bearer abc-secret", 3, None]}) == {r: [f"Bearer {r}", 3, None]}


# --- policy ---


@pytest.mark.parametrize(
    ("command", "dangerous", "expected"),
    [
        ("test-module", True, "read"),  # read override beats dangerous flag
        ("fw-unblock-ip", False, "destructive"),  # destructive glob
        ("FW-Block-IP", False, "destructive"),  # case-insensitive
        ("ip", True, "destructive"),  # dangerous flag
        ("ip", False, "read"),  # default
        ("jira-create-issue", False, "destructive"),
        ("createissue", False, "read"),  # "*-create-*" needs the dashes
    ],
)
def test_risk(command: str, dangerous: bool, expected: str) -> None:
    policy = PolicyConfig(destructive=["*block*", "*-create-*"], read=["test-module"])
    assert risk(command, policy, CommandDef(name=command, dangerous=dangerous)) == expected


def test_read_override_beats_destructive_glob() -> None:
    policy = PolicyConfig(destructive=["*block*"], read=["fw-blocklist-get"])
    assert risk("fw-blocklist-get", policy) == "read"
    assert risk("fw-block-ip", policy) == "destructive"


def test_default_policy_covers_spec_globs() -> None:
    policy = PolicyConfig()
    assert risk("ad-disable-account", policy) == "destructive"
    assert risk("test-module", policy) == "read"
    assert risk("abuseipdb-check-ip", policy) == "read"
