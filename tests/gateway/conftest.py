from pathlib import Path
from typing import Any

import pytest

from dbot.catalog import Catalog, CommandDef, IntegrationDef
from dbot.control.approvals import Approvals
from dbot.control.audit import AuditLog
from dbot.control.config import DbotConfig
from dbot.gateway import Actor, Gateway
from dbot.sandbox.types import CommandResult, Entry, ExecRequest

CANARY = "canary-SECRET-7f3a9c"


class FakeSandbox:
    """Records calls. By default echoes the resolved api key back, like a chatty integration error would."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, ExecRequest]] = []
        self.result: CommandResult | None = None

    async def run(self, integration: IntegrationDef, req: ExecRequest) -> CommandResult:
        self.calls.append((integration.name, req))
        if self.result is not None:
            return self.result
        key = req.params["credentials"]["password"]
        return CommandResult(
            status="ok",
            entries=[Entry(contents={"ip": req.args.get("ip")}, human_readable=f"looked up with key {key}")],
            context={"IP": {"Address": req.args.get("ip")}},
            logs=[f"GET https://api.example.com/?key={key}"],
        )


def actor(*roles: str, id: str = "alice", kind: str = "human", on_behalf_of: str | None = None) -> Actor:
    return Actor(id=id, kind=kind, roles=set(roles), on_behalf_of=on_behalf_of)  # type: ignore[arg-type]


@pytest.fixture
def catalog() -> Catalog:
    fake = IntegrationDef(
        pack="Fake",
        name="Fake",
        display="Fake Intel",
        description="threat intel lookups",
        py_path="/dev/null",
        commands=[
            CommandDef(name="ip", description="Reputation of an IP address"),
            CommandDef(name="fake-block-ip", description="Block an IP address"),
            CommandDef(name="fake-purge", description="Purge stuff", dangerous=True),
            CommandDef(name="test-module"),
        ],
    )
    other = IntegrationDef(pack="Other", name="Other", py_path="/dev/null", commands=[CommandDef(name="ip")])
    return Catalog([fake, other])


@pytest.fixture
def config() -> DbotConfig:
    raw: dict[str, Any] = {
        "instances": [
            {
                "name": "fake",
                "integration": "Fake",
                "params": {"url": "https://api.example.com", "credentials": {"password": "env://DBOT_TEST_CANARY"}},
            },
            {"name": "fake-off", "integration": "Fake", "enabled": False},
            {"name": "fake-nokey", "integration": "Fake", "params": {"credentials": {"password": "env://DBOT_NOPE"}}},
        ]
    }
    return DbotConfig.model_validate(raw)


@pytest.fixture
def sandbox() -> FakeSandbox:
    return FakeSandbox()


@pytest.fixture
def audit_dir(tmp_path: Path) -> Path:
    return tmp_path / "audit"


@pytest.fixture
def gateway(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    config: DbotConfig,
    catalog: Catalog,
    sandbox: FakeSandbox,
    audit_dir: Path,
) -> Gateway:
    monkeypatch.setenv("DBOT_TEST_CANARY", CANARY)
    monkeypatch.delenv("DBOT_NOPE", raising=False)
    monkeypatch.delenv("DBOT_EGRESS_PROXY", raising=False)
    return Gateway(config, catalog, sandbox, Approvals(tmp_path / "dbot.db"), AuditLog(audit_dir))
