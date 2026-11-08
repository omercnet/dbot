"""Gateway security boundaries, called directly (no agent)."""

import base64
import json
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest
from typer.testing import CliRunner

from dbot.catalog import Catalog, CommandDef, IntegrationDef
from dbot.cli import app
from dbot.control.audit import verify
from dbot.control.config import InstanceConfig, PolicyOverride
from dbot.control.secrets import REDACTED
from dbot.gateway import Denied, Gateway, Pending
from dbot.sandbox.types import CommandResult
from tests.gateway.conftest import CANARY, FakeSandbox, actor

REASON = "triage alert 42"


def audit_records(audit_dir: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for p in sorted(audit_dir.glob("audit-*.jsonl")) for line in p.read_text().splitlines()]


def audit_text(audit_dir: Path) -> str:
    return "".join(p.read_text() for p in audit_dir.glob("audit-*.jsonl"))


async def test_read_runs_and_is_audited(gateway: Gateway, sandbox: FakeSandbox, audit_dir: Path) -> None:
    result = await gateway.invoke("fake", "ip", {"ip": "1.2.3.4"}, REASON, actor("analyst"))
    assert isinstance(result, CommandResult)
    assert result.status == "ok"
    assert len(sandbox.calls) == 1
    integration, req = sandbox.calls[0]
    assert integration == "Fake"
    assert req.command == "ip"
    assert req.args == {"ip": "1.2.3.4"}
    assert req.params["credentials"]["password"] == CANARY  # resolved for the sandbox only
    [rec] = audit_records(audit_dir)
    assert rec["type"] == "invoke"
    assert rec["status"] == "ok"
    assert rec["risk"] == "read"
    assert rec["actor"] == "alice"
    assert rec["instance"] == "fake"
    assert rec["args"] == {"ip": "1.2.3.4"}
    assert "params" not in rec
    assert isinstance(rec["duration_ms"], int)


async def test_full_tool_name_is_accepted(gateway: Gateway, sandbox: FakeSandbox) -> None:
    result = await gateway.invoke("fake", "Fake.ip", {}, REASON, actor("analyst"))
    assert isinstance(result, CommandResult)
    assert sandbox.calls[0][1].command == "ip"


async def test_egress_proxy_from_env(gateway: Gateway, sandbox: FakeSandbox, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DBOT_EGRESS_PROXY", "http://squid:3128")
    await gateway.invoke("fake", "ip", {}, REASON, actor("analyst"))
    req = sandbox.calls[0][1]
    assert req.proxy == "http://squid:3128"
    assert req.params["proxy"] is True
    assert "insecure" not in req.params
    assert "proxy" not in gateway.config.instances[0].params  # config itself is untouched


async def test_no_egress_proxy_leaves_params(gateway: Gateway, sandbox: FakeSandbox) -> None:
    await gateway.invoke("fake", "ip", {}, REASON, actor("analyst"))
    req = sandbox.calls[0][1]
    assert req.proxy is None
    assert "proxy" not in req.params


@pytest.mark.parametrize("command", ["fake-block-ip", "fake-purge"])  # glob policy and yml dangerous flag
async def test_destructive_without_approval_is_pending_and_never_runs(
    gateway: Gateway, sandbox: FakeSandbox, audit_dir: Path, command: str
) -> None:
    result = await gateway.invoke("fake", command, {"ip": "6.6.6.6"}, REASON, actor("analyst"))
    assert isinstance(result, Pending)
    assert result.risk == "destructive"
    assert sandbox.calls == []
    approval = gateway.approvals.get(result.approval_id)
    assert approval is not None
    assert approval.status == "pending"
    assert approval.requested_by == "alice"
    assert audit_records(audit_dir)[-1]["status"] == "approval_required"


async def test_agent_pending_records_on_behalf_of(gateway: Gateway) -> None:
    agent = actor("agent", id="agent:responder", kind="agent", on_behalf_of="alert:7")
    result = await gateway.invoke("fake", "fake-block-ip", {"ip": "6.6.6.6"}, REASON, agent)
    assert isinstance(result, Pending)
    approval = gateway.approvals.get(result.approval_id)
    assert approval is not None
    assert approval.on_behalf_of == "alert:7"


async def test_instance_policy_gates_command_on_that_instance_only(gateway: Gateway, sandbox: FakeSandbox) -> None:
    gated = InstanceConfig(
        name="fake-gated",
        integration="Fake",
        params={"credentials": {"password": "env://DBOT_TEST_CANARY"}},
        policy=PolicyOverride(destructive=["ip"]),
    )
    gateway.config.instances.append(gated)
    pending = await gateway.invoke("fake-gated", "ip", {"ip": "1.2.3.4"}, REASON, actor("analyst"))
    assert isinstance(pending, Pending)
    assert pending.risk == "destructive"
    assert sandbox.calls == []
    result = await gateway.invoke("fake", "ip", {"ip": "1.2.3.4"}, REASON, actor("analyst"))
    assert isinstance(result, CommandResult)
    assert result.status == "ok"
    assert len(sandbox.calls) == 1


async def _approved(gateway: Gateway, args: dict[str, Any]) -> str:
    pending = await gateway.invoke("fake", "fake-block-ip", args, REASON, actor("analyst"))
    assert isinstance(pending, Pending)
    decided = gateway.decide_approval(pending.approval_id, actor("approver", id="bob"), approve=True)
    assert not isinstance(decided, Denied)
    assert decided.status == "approved"
    return pending.approval_id


async def test_approved_runs_exactly_once(gateway: Gateway, sandbox: FakeSandbox) -> None:
    args = {"ip": "6.6.6.6"}
    approval_id = await _approved(gateway, args)
    result = await gateway.invoke("fake", "fake-block-ip", args, REASON, actor("analyst"), approval_id)
    assert isinstance(result, CommandResult)
    assert result.status == "ok"
    assert len(sandbox.calls) == 1

    again = await gateway.invoke("fake", "fake-block-ip", args, REASON, actor("analyst"), approval_id)
    assert isinstance(again, Denied)
    assert "consumed" in again.reason
    assert len(sandbox.calls) == 1


async def test_approval_does_not_cover_changed_args(gateway: Gateway, sandbox: FakeSandbox) -> None:
    approval_id = await _approved(gateway, {"ip": "6.6.6.6"})
    result = await gateway.invoke("fake", "fake-block-ip", {"ip": "8.8.8.8"}, REASON, actor("analyst"), approval_id)
    assert isinstance(result, Denied)
    assert "args" in result.reason
    assert sandbox.calls == []


async def test_pending_or_unknown_approval_is_denied(gateway: Gateway, sandbox: FakeSandbox) -> None:
    pending = await gateway.invoke("fake", "fake-block-ip", {}, REASON, actor("analyst"))
    assert isinstance(pending, Pending)
    not_yet = await gateway.invoke("fake", "fake-block-ip", {}, REASON, actor("analyst"), pending.approval_id)
    assert isinstance(not_yet, Denied)
    assert "pending" in not_yet.reason
    unknown = await gateway.invoke("fake", "fake-block-ip", {}, REASON, actor("analyst"), "nope")
    assert isinstance(unknown, Denied)
    assert "does not exist" in unknown.reason
    assert sandbox.calls == []


async def test_decide_requires_approver_and_not_self(gateway: Gateway) -> None:
    pending = await gateway.invoke("fake", "fake-block-ip", {}, REASON, actor("analyst", "approver"))
    assert isinstance(pending, Pending)
    assert isinstance(gateway.decide_approval(pending.approval_id, actor("analyst", id="carol"), True), Denied)
    self_ok = gateway.decide_approval(pending.approval_id, actor("approver"), True)  # alice requested it
    assert isinstance(self_ok, Denied)
    assert "own request" in self_ok.reason
    approval = gateway.approvals.get(pending.approval_id)
    assert approval is not None
    assert approval.status == "pending"


async def test_viewer_is_denied(gateway: Gateway, sandbox: FakeSandbox) -> None:
    for command in ("ip", "fake-block-ip"):
        result = await gateway.invoke("fake", command, {}, REASON, actor("viewer"))
        assert isinstance(result, Denied)
    assert sandbox.calls == []
    assert gateway.approvals.pending() == []


async def test_unknown_instance_and_command(gateway: Gateway, sandbox: FakeSandbox) -> None:
    unknown = await gateway.invoke("nope", "ip", {}, REASON, actor("analyst"))
    assert isinstance(unknown, Denied)
    assert "does not exist" in unknown.reason
    assert "fake" in unknown.reason
    disabled = await gateway.invoke("fake-off", "ip", {}, REASON, actor("analyst"))
    assert isinstance(disabled, Denied)
    assert "disabled" in disabled.reason
    bad_cmd = await gateway.invoke("fake", "domain", {}, REASON, actor("analyst"))
    assert isinstance(bad_cmd, Denied)
    assert "fake-block-ip" in bad_cmd.reason
    assert sandbox.calls == []


@pytest.mark.parametrize("reason", ["", "   "])
async def test_empty_reason_is_denied(gateway: Gateway, sandbox: FakeSandbox, reason: str) -> None:
    result = await gateway.invoke("fake", "ip", {}, reason, actor("analyst"))
    assert isinstance(result, Denied)
    assert "reason" in result.reason
    assert sandbox.calls == []


async def test_oversized_args_are_denied(gateway: Gateway, sandbox: FakeSandbox) -> None:
    result = await gateway.invoke("fake", "ip", {"ip": "x" * 70_000}, REASON, actor("analyst"))
    assert isinstance(result, Denied)
    assert "bytes" in result.reason
    assert sandbox.calls == []


async def test_unresolvable_secret_is_denied_without_running(gateway: Gateway, sandbox: FakeSandbox) -> None:
    result = await gateway.invoke("fake-nokey", "ip", {}, REASON, actor("analyst"))
    assert isinstance(result, Denied)
    assert "credentials" in result.reason
    assert "DBOT_NOPE" not in result.reason
    assert sandbox.calls == []


async def test_sandbox_exception_becomes_sandbox_error(gateway: Gateway, sandbox: FakeSandbox) -> None:
    async def boom(*_: object) -> CommandResult:
        raise RuntimeError(f"docker died {CANARY}")

    sandbox.run = boom  # type: ignore[method-assign,assignment]
    result = await gateway.invoke("fake", "ip", {}, REASON, actor("analyst"))
    assert isinstance(result, CommandResult)
    assert result.error_kind == "sandbox"
    assert CANARY not in result.model_dump_json()


async def test_canary_never_leaks(gateway: Gateway, sandbox: FakeSandbox, audit_dir: Path) -> None:
    outputs: list[str] = []
    ok = await gateway.invoke("fake", "ip", {"ip": "1.1.1.1"}, REASON, actor("analyst"))
    outputs.append(ok.model_dump_json())
    sandbox.result = CommandResult(status="error", error_kind="integration", error=f"401 bad key {CANARY}")
    err = await gateway.invoke("fake", "ip", {}, REASON, actor("analyst"))
    outputs.append(err.model_dump_json())
    pending = await gateway.invoke("fake", "fake-block-ip", {"note": CANARY}, REASON, actor("analyst"))
    outputs.append(pending.model_dump_json())
    for denied in (
        await gateway.invoke("fake", "ip", {}, "", actor("analyst")),
        await gateway.invoke("fake", "ip", {}, REASON, actor("viewer")),
        await gateway.invoke("fake-nokey", "ip", {}, REASON, actor("analyst")),
        await gateway.invoke("fake", "fake-block-ip", {}, REASON, actor("analyst"), "nope"),
    ):
        assert isinstance(denied, Denied)
        outputs.append(denied.model_dump_json())
    assert isinstance(pending, Pending)
    assert all(CANARY not in o for o in outputs)
    assert "REDACTED" in outputs[0]
    assert "REDACTED" in outputs[1]
    text = audit_text(audit_dir)
    assert CANARY not in text
    assert len(audit_records(audit_dir)) == 7


async def test_audit_verify_after_mixed_run(gateway: Gateway, sandbox: FakeSandbox, audit_dir: Path) -> None:
    await gateway.invoke("fake", "ip", {"ip": "1.1.1.1"}, REASON, actor("analyst"))
    await gateway.invoke("fake", "ip", {}, REASON, actor("viewer"))
    approval_id = await _approved(gateway, {"ip": "6.6.6.6"})
    await gateway.invoke("fake", "fake-block-ip", {"ip": "6.6.6.6"}, REASON, actor("analyst"), approval_id)
    await gateway.invoke("fake", "fake-block-ip", {"ip": "6.6.6.6"}, REASON, actor("analyst"), approval_id)
    types = [r["type"] for r in audit_records(audit_dir)]
    assert types.count("approval_decision") == 1
    assert types.count("invoke") == 5

    assert verify(audit_dir) == (True, None)
    runner = CliRunner()
    assert runner.invoke(app, ["audit", "verify", str(audit_dir)]).exit_code == 0

    path = next(audit_dir.glob("audit-*.jsonl"))
    lines = path.read_text().splitlines()
    tampered = json.loads(lines[1])
    tampered["status"] = "ok"
    lines[1] = json.dumps(tampered, sort_keys=True, separators=(",", ":"))
    path.write_text("\n".join(lines) + "\n")
    result = runner.invoke(app, ["audit", "verify", str(audit_dir)])
    assert result.exit_code == 1


def test_list_instances_never_exposes_params(gateway: Gateway) -> None:
    rows = {r["name"]: r for r in gateway.list_instances()}
    assert rows["fake"] == {
        "name": "fake",
        "integration": "Fake",
        "display": "Fake Intel",
        "enabled": True,
        "configured": True,
        "problem": None,
    }
    assert rows["fake-off"]["enabled"] is False
    assert rows["fake-nokey"]["configured"] is False
    text = json.dumps(list(rows.values()))
    assert CANARY not in text
    assert "env://" not in text
    assert "api.example.com" not in text


async def test_test_module_is_implicit_and_policed(gateway: Gateway, sandbox: FakeSandbox, audit_dir: Path) -> None:
    # "Other" does not list test-module in its YAML; XSOAR still supports it on every integration.
    gateway.config.instances.append(InstanceConfig(name="other", integration="Other"))
    result = await gateway.invoke("other", "test-module", {}, REASON, actor("analyst"))
    assert isinstance(result, CommandResult)
    assert sandbox.calls[0][0] == "Other"
    assert sandbox.calls[0][1].command == "test-module"
    rec = audit_records(audit_dir)[-1]
    assert rec["command"] == "test-module"
    assert rec["risk"] == "read"

    viewer = await gateway.invoke("other", "test-module", {}, REASON, actor("viewer"))
    assert isinstance(viewer, Denied)
    still_unknown = await gateway.invoke("other", "test-modules", {}, REASON, actor("analyst"))
    assert isinstance(still_unknown, Denied)

    gateway.config.policy.read = []
    gateway.config.policy.destructive = ["test-module"]
    pending = await gateway.invoke("other", "test-module", {}, REASON, actor("analyst"))
    assert isinstance(pending, Pending)
    assert len(sandbox.calls) == 1


async def test_unknown_command_needs_approval(gateway: Gateway, sandbox: FakeSandbox) -> None:
    """Fail closed: a command no policy glob recognises is destructive."""
    result = await gateway.invoke("fake", "fake-frobnicate", {}, REASON, actor("analyst"))
    assert isinstance(result, Pending)
    assert sandbox.calls == []


async def test_secret_args_masked_in_audit_and_approval_but_bound_by_hash(
    gateway: Gateway, sandbox: FakeSandbox, audit_dir: Path
) -> None:
    password = "new-Passw0rd-e41b9"
    args = {"user": "carol", "password": password}
    pending = await gateway.invoke("fake", "fake-set-password", args, REASON, actor("analyst"))
    assert isinstance(pending, Pending)
    approval = gateway.get_approval(pending.approval_id)
    assert approval is not None
    assert approval.args == {"user": "carol", "password": REDACTED}
    assert not isinstance(gateway.decide_approval(pending.approval_id, actor("approver", id="bob"), True), Denied)
    # The masked args do not match the approval; only the real ones do.
    masked = await gateway.invoke("fake", "fake-set-password", approval.args, REASON, actor("analyst"), approval.id)
    assert isinstance(masked, Denied)
    result = await gateway.invoke("fake", "fake-set-password", args, REASON, actor("analyst"), approval.id)
    assert isinstance(result, CommandResult)
    assert sandbox.calls[-1][1].args == args  # the sandbox gets the real value

    read = await gateway.invoke("fake", "fake-get-token", {"token": password}, REASON, actor("analyst"))
    assert isinstance(read, CommandResult)
    assert password not in audit_text(audit_dir)
    invokes = [r for r in audit_records(audit_dir) if r["type"] == "invoke"]
    assert invokes[0]["args"] == {"user": "carol", "password": REDACTED}
    assert invokes[-1]["args"] == {"token": REDACTED}


async def test_literal_credentials_are_registered_and_redacted(
    gateway: Gateway, sandbox: FakeSandbox, audit_dir: Path
) -> None:
    ident, password, apikey = "svc-ident-5d1c", "literal-Pw/+=-77aa", "literal-apikey-0c3e"
    gateway.config.instances.append(
        InstanceConfig(
            name="fake-literal",
            integration="Fake",
            params={"credentials": {"identifier": ident, "password": password}, "apikey": apikey},
        )
    )
    basic = base64.b64encode(f"{ident}:{password}".encode()).decode()
    leaks = [password, quote(password, safe=""), apikey, basic, ident]
    sandbox.result = CommandResult(
        status="error",
        error_kind="integration",
        error=f"401 for url https://x/?key={quote(password, safe='')}&k={apikey} pw={password} Basic {basic} u={ident}",
    )
    result = await gateway.invoke("fake-literal", "ip", {}, REASON, actor("analyst"))
    assert isinstance(result, CommandResult)
    assert result.error is not None
    assert REDACTED in result.error
    text = audit_text(audit_dir)
    for leak in leaks:
        assert leak not in result.error, leak
        assert leak not in text, leak


async def test_other_actor_cannot_consume_approval(gateway: Gateway, sandbox: FakeSandbox) -> None:
    args = {"ip": "6.6.6.6"}
    approval_id = await _approved(gateway, args)  # requested by alice
    for other in (
        actor("analyst", id="agent:mcp", kind="agent", on_behalf_of="mallory"),
        actor("analyst", id="mallory"),
        actor("analyst", id="alice", kind="agent", on_behalf_of="alert:9"),
    ):
        stolen = await gateway.invoke("fake", "fake-block-ip", args, REASON, other, approval_id)
        assert isinstance(stolen, Denied)
        assert "someone else" in stolen.reason
    assert sandbox.calls == []
    result = await gateway.invoke("fake", "fake-block-ip", args, REASON, actor("analyst"), approval_id)
    assert isinstance(result, CommandResult)
    assert len(sandbox.calls) == 1


def test_resolve_uses_the_instance_integration_not_a_dotted_split(gateway: Gateway) -> None:
    # "X" + "y.z" and "X.y" + "z" share the catalog key "X.y.z"; the instance's integration decides.
    plain = IntegrationDef(pack="P", name="X", py_path="/dev/null", commands=[CommandDef(name="y.z")])
    dotted = IntegrationDef(pack="P", name="X.y", py_path="/dev/null", commands=[CommandDef(name="z")])
    gateway.catalog = Catalog([plain, dotted])
    assert gateway.resolve("X", "y.z")[0].name == "X"
    assert gateway.resolve("X.y", "z")[0].name == "X.y"
