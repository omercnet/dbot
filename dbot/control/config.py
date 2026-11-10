"""The operator config file (dbot.yaml): models, validation, and loading."""

import os
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from dbot.control.secrets import check_ref, is_secret_ref

DEFAULT_CONFIG_PATH = Path("/etc/dbot/dbot.yaml")
DEFAULT_DATA_DIR = Path("/var/lib/dbot")

# Anything that matches no glob is destructive, so these globs only matter where a read glob also matches;
# destructive wins that tie. Verbs are matched with their dashes where a bare substring would hit read names
# ("*-set-*" not "*set*", which matches "asset"; "*-add-*" not "*add*", which matches "address").
DEFAULT_DESTRUCTIVE = [
    "*block*",
    "*isolate*",
    "*quarantine*",
    "*disable*",
    "*enable*",
    "*delete*",
    "*revoke*",
    "*suspend*",
    "*release*",
    "*contain*",
    "*kill*",
    "*remove*",
    "*reset*",
    "*expire*",
    "*modify*",
    "*unlock*",
    "*send*",
    "*execute*",
    "*upload*",
    "*resolve*",
    "*submit*",
    "*restore*",
    "*-run*",
    "run-*",
    "*-set-*",
    "*-set",
    "set-*",
    "*-add-*",
    "*-add",
    "add-*",
    "*-create-*",
    "*-update-*",
]
# Read allowlist: the generic XSOAR reputation commands and conservative lookup verbs.
DEFAULT_READ = [
    "test-module",
    "ip",
    "domain",
    "url",
    "file",
    "email",
    "cve",
    "endpoint",
    "*-get-*",
    "*-get",
    "*-list-*",
    "*-list",
    "*-search*",
    "*-lookup*",
    "*-query*",
    "*-check-*",
    "*-check",
    "*-info*",
    "*-reputation*",
    "*-enrich*",
]
# Built-in, per integration (catalog name): commands the global read list would let through but that write to the
# vendor. They apply to every instance of that integration; only the instance's own `policy.read` can lift them.
INTEGRATION_DESTRUCTIVE: dict[str, list[str]] = {
    # With an Auth-Key set, test-module posts a public comment on a sample (MalwareBazaar.py test_module).
    "MalwareBazaar": ["test-module"],
}
_URL_SCHEMES = frozenset({"http", "https", "ws", "wss", "ftp", "ftps", "ldap", "ldaps", "tcp", "ssl", "tls"})


class ConfigError(ValueError):
    """The config file is missing or invalid."""


def _check_secret_refs(value: Any, where: str) -> None:
    if isinstance(value, str):
        problem = check_ref(value)
        if problem:
            raise ValueError(f"{where}: {problem}")
    elif isinstance(value, dict):
        for k, v in value.items():
            _check_secret_refs(v, f"{where}.{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            _check_secret_refs(v, f"{where}[{i}]")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PolicyOverride(_Strict):
    """Per-instance globs; they win over the global policy for this instance only."""

    read: list[str] = Field(default_factory=list)
    destructive: list[str] = Field(default_factory=list)


class InstanceConfig(_Strict):
    """One configured integration instance."""

    name: str = Field(min_length=1)
    integration: str = Field(min_length=1)
    params: dict[str, Any] = Field(default_factory=dict)
    allow_hosts: list[str] | None = None
    enabled: bool = True
    policy: PolicyOverride = Field(default_factory=PolicyOverride)

    @field_validator("params")
    @classmethod
    def _params_refs(cls, v: dict[str, Any]) -> dict[str, Any]:
        _check_secret_refs(v, "params")
        return v


class PolicyConfig(_Strict):
    destructive: list[str] = Field(default_factory=lambda: list(DEFAULT_DESTRUCTIVE))
    read: list[str] = Field(default_factory=lambda: list(DEFAULT_READ))


class IngestSource(_Strict):
    hmac_secret: str = Field(min_length=1)

    @field_validator("hmac_secret")
    @classmethod
    def _secret_ref(cls, v: str) -> str:
        _check_secret_refs(v, "hmac_secret")
        return v


class IngestConfig(_Strict):
    sources: dict[str, IngestSource] = Field(default_factory=dict)


class AgentConfig(_Strict):
    model: str = "openai:gpt-5.4-mini"
    max_tool_calls: int = Field(default=20, gt=0)
    wall_clock_s: int = Field(default=300, gt=0)


class DbotConfig(_Strict):
    instances: list[InstanceConfig] = Field(default_factory=list)
    policy: PolicyConfig = Field(default_factory=PolicyConfig)
    roles: dict[str, list[str]] = Field(default_factory=dict)
    ingest: IngestConfig = Field(default_factory=IngestConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)

    @field_validator("roles")
    @classmethod
    def _roles_non_empty(cls, v: dict[str, list[str]]) -> dict[str, list[str]]:
        for role, groups in v.items():
            if not role:
                raise ValueError("role names must be non-empty")
            if not groups or any(not g for g in groups):
                raise ValueError(f"role {role!r} must map to a non-empty list of non-empty group names")
        return v

    @model_validator(mode="after")
    def _unique_instances(self) -> "DbotConfig":
        seen: set[str] = set()
        for inst in self.instances:
            if inst.name in seen:
                raise ValueError(f"duplicate instance name {inst.name!r}")
            seen.add(inst.name)
        return self

    def instance(self, name: str) -> InstanceConfig | None:
        return next((i for i in self.instances if i.name == name), None)


def _hosts(value: Any, out: set[str]) -> None:
    if isinstance(value, str):
        if is_secret_ref(value) or "://" not in value:
            return
        try:
            parts = urlsplit(value.strip())
            host = parts.hostname
        except ValueError:
            return
        if parts.scheme.lower() in _URL_SCHEMES and host:
            out.add(host)
    elif isinstance(value, dict):
        for v in value.values():
            _hosts(v, out)
    elif isinstance(value, list):
        for v in value:
            _hosts(v, out)


def default_allow_hosts(instance: InstanceConfig) -> list[str]:
    """Egress allowlist for an instance: ``allow_hosts`` if set, else hostnames of url-like params (sorted)."""
    if instance.allow_hosts is not None:
        return list(instance.allow_hosts)
    out: set[str] = set()
    _hosts(instance.params, out)
    return sorted(out)


def data_dir() -> Path:
    """``DBOT_DATA_DIR`` (default /var/lib/dbot)."""
    return Path(os.environ.get("DBOT_DATA_DIR") or DEFAULT_DATA_DIR)


def load_config(path: str | Path | None = None) -> DbotConfig:
    """Load and validate dbot.yaml from ``path``, else ``DBOT_CONFIG``, else /etc/dbot/dbot.yaml."""
    p = Path(path or os.environ.get("DBOT_CONFIG") or DEFAULT_CONFIG_PATH)
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except OSError as e:
        raise ConfigError(f"{p}: cannot read config: {e.strerror}") from None
    except yaml.YAMLError as e:
        # Only the position: the default message quotes file content, which may include literal credentials.
        mark = getattr(e, "problem_mark", None)
        where = f" at line {mark.line + 1}, column {mark.column + 1}" if mark is not None else ""
        raise ConfigError(f"{p}: invalid YAML{where}") from None
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigError(f"{p}: top level must be a mapping")
    try:
        return DbotConfig.model_validate(raw)
    except ValidationError as e:
        # Format errors without input values: params may hold literal credentials.
        lines = [
            f"  {'.'.join(str(x) for x in err['loc']) or '<root>'}: {err['msg']}"
            for err in e.errors(include_input=False, include_url=False)
        ]
        raise ConfigError(f"{p}: invalid config:\n" + "\n".join(lines)) from None
