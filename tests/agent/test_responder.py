"""Responder tests: a real Gateway (approvals, audit, policy) over a fake sandbox, driven by FunctionModel scripts."""

import json
import re
import time
from pathlib import Path
from typing import Any

import pytest
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from dbot.agent.responder import Budget, respond
from dbot.catalog import Catalog
from dbot.catalog.models import ArgDef, CommandDef, IntegrationDef
from dbot.control.approvals import Approvals
from dbot.control.audit import AuditLog
from dbot.control.config import AgentConfig, DbotConfig, InstanceConfig
from dbot.gateway import Gateway
from dbot.sandbox.types import CommandResult, Entry, ExecRequest

ALERT: dict[str, Any] = {
    "id": "ext-1",
    "source": "generic",
    "severity": "high",
    "title": "Beaconing to 1.2.3.4",
    "observables": [{"type": "ip", "value": "1.2.3.4"}, {"type": "domain", "value": "evil.example"}],
    "raw": {"host": "fin-03"},
}
BLOCK = {"instance": "fw", "command": "fw-block-ip", "args": {"ip": "1.2.3.4"}, "reason": "contain C2"}


class FakeSandbox:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ExecRequest]] = []

    async def run(self, integration: IntegrationDef, req: ExecRequest) -> CommandResult:
        self.calls.append((integration.name, req))
        text = f"{req.command} {json.dumps(req.args, sort_keys=True)}: score 97"
        return CommandResult(status="ok", entries=[Entry(contents={"score": 97}, human_readable=text)])


def make_gateway(tmp_path: Path, max_tool_calls: int = 20) -> tuple[Gateway, FakeSandbox, DbotConfig]:
    def integ(name: str, *cmds: str) -> IntegrationDef:
        commands = [CommandDef(name=c, args=[ArgDef(name="ip"), ArgDef(name="domain")]) for c in cmds]
        return IntegrationDef(pack=name, name=name, py_path=f"/x/{name}.py", commands=commands)

    catalog = Catalog([integ("AbuseIPDB", "ip"), integ("OTX", "domain"), integ("Firewall", "fw-block-ip")])
    config = DbotConfig(
        instances=[
            InstanceConfig(name="abuse", integration="AbuseIPDB", params={"apikey": "literal-key"}),
            InstanceConfig(name="otx", integration="OTX"),
            InstanceConfig(name="fw", integration="Firewall"),
            InstanceConfig(name="old", integration="OTX", enabled=False),
        ],
        agent=AgentConfig(model="test", max_tool_calls=max_tool_calls, wall_clock_s=60),
    )
    sandbox = FakeSandbox()
    gateway = Gateway(config, catalog, sandbox, Approvals(tmp_path / "dbot.db"), AuditLog(tmp_path / "audit"))
    return gateway, sandbox, config


def final(**kw: Any) -> dict[str, Any]:
    return {"verdict": "malicious", "confidence": 0.9, "summary": "C2 confirmed", **kw}


class Script:
    """A FunctionModel driven by a list of steps: a list of (tool, args) calls, a final-output dict, or a callable
    `(script, info) -> step` evaluated lazily. The last step repeats."""

    def __init__(self, *steps: Any) -> None:
        self.steps = steps
        self.returns: list[Any] = []  # every tool return the model saw, in order
        self.instructions: list[str] = []
        self.model = FunctionModel(self._fn)

    def _fn(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        self.instructions.append(info.instructions or "")
        self.returns = [
            p.content
            for m in messages
            if isinstance(m, ModelRequest)
            for p in m.parts
            if isinstance(p, ToolReturnPart) and p.tool_name != info.output_tools[0].name
        ]
        n = sum(isinstance(m, ModelResponse) for m in messages)
        step = self.steps[min(n, len(self.steps) - 1)]
        if callable(step):
            step = step(self, info)
        if isinstance(step, dict):
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, step)])
        return ModelResponse(parts=[ToolCallPart(name, args) for name, args in step])


def enrich_steps() -> list[Any]:
    return [
        [
            ("invoke", {"instance": "abuse", "command": "ip", "args": {"ip": "1.2.3.4"}, "reason": "ip reputation"}),
            (
                "invoke",
                {"instance": "otx", "command": "domain", "args": {"domain": "evil.example"}, "reason": "domain"},
            ),
        ],
    ]


async def test_enriches_observables_and_reports_evidence(tmp_path: Path) -> None:
    gateway, sandbox, config = make_gateway(tmp_path)
    evidence = [
        {"instance": "abuse", "command": "ip", "args": {"ip": "1.2.3.4"}, "finding": "score 97", "status": "ok"},
        {"instance": "otx", "command": "domain", "args": {"domain": "evil.example"}, "finding": "score 97"},
    ]
    script = Script(*enrich_steps(), final(evidence=evidence))

    report = await respond(ALERT, gateway, config, alert_id="a1", model=script.model)

    assert [(name, req.command, req.args) for name, req in sandbox.calls] == [
        ("AbuseIPDB", "ip", {"ip": "1.2.3.4"}),
        ("OTX", "domain", {"domain": "evil.example"}),
    ]
    assert sandbox.calls[0][1].params == {"apikey": "literal-key"}
    # The model saw the real results, compacted.
    assert [r["status"] for r in script.returns] == ["ok", "ok"]
    assert "score 97" in script.returns[0]["human_readable"]
    # The prompt lists enabled instances only.
    assert "- abuse (integration AbuseIPDB)" in script.instructions[0]
    assert "old" not in re.findall(r"^- (\w+) ", script.instructions[0], re.M)
    assert report.alert_id == "a1"
    assert report.verdict == "malicious"
    assert {(e.instance, e.command) for e in report.evidence} == {("abuse", "ip"), ("otx", "domain")}
    assert report.actions_pending == []
    assert report.actions_taken == []
    assert report.errors == []
    assert "## Evidence" in report.to_markdown()


async def test_pending_is_reported_even_if_model_omits_it(tmp_path: Path) -> None:
    gateway, sandbox, config = make_gateway(tmp_path)
    fabricated = {"instance": "fw", "command": "fw-block-ip", "args": {"ip": "1.2.3.4"}, "result": "blocked"}
    script = Script([("invoke", BLOCK)], final(actions_pending=[], actions_taken=[fabricated]))

    report = await respond(ALERT, gateway, config, alert_id="a1", model=script.model)

    assert sandbox.calls == []  # destructive never reaches the sandbox without approval
    assert script.returns[0]["status"] == "approval_required"
    assert len(report.actions_pending) == 1
    pa = report.actions_pending[0]
    assert (pa.instance, pa.command, pa.args, pa.reason) == ("fw", "fw-block-ip", {"ip": "1.2.3.4"}, "contain C2")
    assert pa.approval_id == script.returns[0]["approval_id"]
    approval = gateway.get_approval(pa.approval_id)
    assert approval is not None
    assert (approval.requested_by, approval.on_behalf_of, approval.status) == ("agent:responder", "alert:a1", "pending")
    assert report.actions_taken == []  # the model's claim of a taken action is discarded


async def test_budget_exhausted_tools_error_and_report_still_returned(tmp_path: Path) -> None:
    gateway, sandbox, config = make_gateway(tmp_path, max_tool_calls=2)
    call = ("invoke", {"instance": "abuse", "command": "ip", "args": {"ip": "1.2.3.4"}, "reason": "r"})
    script = Script([call, call, ("search_tools", {"query": "ip"}), call], final(verdict="inconclusive"))

    report = await respond(ALERT, gateway, config, model=script.model)

    assert len(sandbox.calls) == 2
    assert [r["status"] for r in script.returns[:2]] == ["ok", "ok"]
    for over in script.returns[2:]:
        assert "budget exhausted" in over["error"]
        assert "conclude" in over["error"]
    assert report.verdict == "inconclusive"
    assert any("budget exhausted" in e for e in report.errors)


async def test_model_ignoring_budget_still_yields_report(tmp_path: Path) -> None:
    gateway, sandbox, config = make_gateway(tmp_path, max_tool_calls=1)
    call = ("invoke", {"instance": "abuse", "command": "ip", "args": {"ip": "1.2.3.4"}, "reason": "r"})
    script = Script([call])  # never concludes

    report = await respond(ALERT, gateway, config, model=script.model)

    assert len(sandbox.calls) == 1
    assert report.verdict == "inconclusive"
    assert report.confidence == 0.0
    assert [(e.instance, e.status) for e in report.evidence] == [("abuse", "ok")]  # only what really ran
    assert any("UsageLimitExceeded" in e for e in report.errors)


def test_budget_wall_clock() -> None:
    budget = Budget(max_tool_calls=10, wall_clock_s=1, started=time.monotonic() - 2)
    err = budget.spend()
    assert err is not None
    assert "wall_clock_s" in err
    assert budget.exhausted


def reinvoke_executed(script: Script, info: AgentInfo) -> list[tuple[str, dict[str, Any]]]:
    """A model that ignores the instructions and re-invokes the already executed approval."""
    m = re.search(r"approval_id=(\w+) executed, status=ok: .* call=(\{.*\})", info.instructions or "")
    assert m, info.instructions
    call = json.loads(m.group(2))
    return [("invoke", {**call, "reason": "approved containment", "approval_id": m.group(1)})]


@pytest.mark.parametrize("model_steps", [[final()], [reinvoke_executed, final()]], ids=["silent", "reinvokes"])
async def test_approved_action_executes_exactly_once(tmp_path: Path, model_steps: list[Any]) -> None:
    gateway, sandbox, config = make_gateway(tmp_path)
    first = await respond(ALERT, gateway, config, alert_id="a1", model=Script([("invoke", BLOCK)], final()).model)
    approval_id = first.actions_pending[0].approval_id
    gateway.approvals.decide(approval_id, "lead@soc", True)

    script = Script(*model_steps)
    report = await respond(ALERT, gateway, config, alert_id="a1", previous=first, model=script.model)

    assert [(req.command, req.args) for _, req in sandbox.calls] == [("fw-block-ip", {"ip": "1.2.3.4"})]
    assert f"approval_id={approval_id} executed, status=ok" in script.instructions[0]  # the model sees the outcome
    if script.returns:
        assert script.returns[0]["status"] == "already_executed"
    assert report.actions_pending == []
    assert [(a.command, a.approval_id, a.status) for a in report.actions_taken] == [("fw-block-ip", approval_id, "ok")]
    assert report.errors == []
    approval = gateway.get_approval(approval_id)
    assert approval is not None
    assert approval.status == "consumed"

    # A later run (e.g. a requeue for another approval) does not execute it again.
    later = await respond(ALERT, gateway, config, alert_id="a1", previous=report, model=Script(final()).model)
    assert len(sandbox.calls) == 1
    assert [a.approval_id for a in later.actions_taken] == [approval_id]


async def test_rerun_while_pending_keeps_it_without_new_approval(tmp_path: Path) -> None:
    gateway, sandbox, config = make_gateway(tmp_path)
    first = await respond(ALERT, gateway, config, alert_id="a1", model=Script([("invoke", BLOCK)], final()).model)

    # Model asks again without an approval_id, then a second run where the model forgets it entirely.
    again = await respond(
        ALERT, gateway, config, alert_id="a1", previous=first, model=Script([("invoke", BLOCK)], final()).model
    )
    forgot = await respond(ALERT, gateway, config, alert_id="a1", previous=again, model=Script(final()).model)

    assert len(gateway.approvals.pending()) == 1
    for report in (again, forgot):
        assert [p.approval_id for p in report.actions_pending] == [first.actions_pending[0].approval_id]
    assert sandbox.calls == []


async def test_denied_approval_is_not_taken(tmp_path: Path) -> None:
    gateway, sandbox, config = make_gateway(tmp_path)
    first = await respond(ALERT, gateway, config, alert_id="a1", model=Script([("invoke", BLOCK)], final()).model)
    approval_id = first.actions_pending[0].approval_id
    gateway.approvals.decide(approval_id, "lead@soc", False)

    # A model that tries to use the denied approval anyway.
    retry = [("invoke", {**BLOCK, "approval_id": approval_id})]
    script = Script(retry, final())
    report = await respond(ALERT, gateway, config, alert_id="a1", previous=first, model=script.model)

    assert f"approval_id={approval_id} denied, not executed" in script.instructions[0]
    assert report.errors == [f"approval {approval_id} for fw fw-block-ip was denied; not executed"]
    assert script.returns[0]["status"] == "denied"
    assert sandbox.calls == []
    assert report.actions_taken == []
    assert report.actions_pending == []


@pytest.mark.parametrize("bad", [{"instance": "nope"}, {"instance": "old"}])
async def test_unknown_or_disabled_instance_is_denied(tmp_path: Path, bad: dict[str, str]) -> None:
    gateway, sandbox, config = make_gateway(tmp_path)
    call = {"instance": "abuse", "command": "ip", "args": {"ip": "1.2.3.4"}, "reason": "r", **bad}
    script = Script([("invoke", call)], final())

    report = await respond(ALERT, gateway, config, model=script.model)

    assert script.returns[0]["status"] == "denied"
    assert sandbox.calls == []
    assert report.actions_taken == []
