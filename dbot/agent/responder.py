"""The alert responder: a pydantic-ai agent whose only path to execution is the gateway (C9)."""

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any

from pydantic_ai import Agent, RunContext
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.models import Model
from pydantic_ai.usage import UsageLimits

from dbot.agent.report import ActionTaken, Evidence, IRReport, PendingAction
from dbot.control.config import DbotConfig
from dbot.gateway import Actor, Denied, Gateway, Pending
from dbot.sandbox.types import CommandResult

RESULT_CAP = 20_000  # bytes of tool result handed back to the model, same as MCP
RAW_CAP = 8_000  # bytes of the raw alert payload included in the prompt
SEARCH_LIMIT = 10

INSTRUCTIONS = """\
You are a SOC incident responder working one alert. Work in this order:
1. Triage: read the alert and list its observables (ips, domains, urls, hashes, emails, users, hosts).
2. Enrich every observable with read-tier commands on the configured instances below. Use search_tools and
   get_tool_schema to find commands, then invoke(instance, command, args, reason).
3. Decide a verdict (benign, suspicious, malicious, inconclusive) with a confidence between 0 and 1. Cite evidence:
   one entry per invoke you relied on, with its instance, command, args, status and what it showed.
4. Only if the verdict is malicious and confidence >= 0.8, propose destructive containment (block, isolate,
   disable, ...) by invoking it. Destructive commands return approval_required; that is expected, do not retry.
   Actions approved in an earlier run have already been executed for you; their outcomes are listed below. Use them
   to update the verdict and summary, and do not invoke them again.
5. Finish by calling the final_result tool with the report. Do not answer in plain text.
Never invent tool results: every finding must come from an invoke result in this run. If a tool says the budget is
exhausted, stop calling tools and return the report with what you have.
"""


@dataclass
class Budget:
    max_tool_calls: int
    wall_clock_s: float
    started: float = field(default_factory=time.monotonic)
    calls: int = 0
    exhausted: bool = False

    def spend(self) -> str | None:
        """Count one tool call. Returns an error message if the budget is exhausted."""
        self.calls += 1
        if self.calls > self.max_tool_calls:
            self.exhausted = True
            return f"budget exhausted: max_tool_calls={self.max_tool_calls}. Stop calling tools and conclude now."
        if time.monotonic() - self.started > self.wall_clock_s:
            self.exhausted = True
            return f"budget exhausted: wall_clock_s={self.wall_clock_s}. Stop calling tools and conclude now."
        return None


@dataclass
class ResponderDeps:
    gateway: Gateway
    alert: dict[str, Any]
    budget: Budget
    alert_id: str = ""  # the store id; approvals are filed on behalf of f"alert:{alert_id}"
    previous: list[tuple[PendingAction, str]] = field(default_factory=list)  # (prior pending, current status)
    evidence: list[Evidence] = field(default_factory=list)
    taken: list[ActionTaken] = field(default_factory=list)
    pending: list[PendingAction] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    resumed: list[str] = field(default_factory=list)  # prompt lines: outcomes of earlier approvals


responder: Agent[ResponderDeps, IRReport] = Agent(
    deps_type=ResponderDeps,
    output_type=IRReport,
    instructions=INSTRUCTIONS,
    name="dbot-responder",
)


@responder.instructions
def _context(ctx: RunContext[ResponderDeps]) -> str:
    lines = ["Configured instances (pass the instance name to invoke):"]
    for inst in ctx.deps.gateway.list_instances():
        if inst.get("enabled", True) and not inst.get("problem"):
            lines.append(f"- {inst['name']} (integration {inst['integration']})")
    if len(lines) == 1:
        lines.append("- none")
    if ctx.deps.resumed:
        lines.append("Outcomes of actions approved since the earlier run (already executed or refused):")
        lines += [f"- {line}" for line in ctx.deps.resumed]
    still = [(pa, status) for pa, status in ctx.deps.previous if status == "pending"]
    if still:
        lines.append("Approvals from an earlier run still waiting for a decision (do not request them again):")
        for pa, _ in still:
            lines.append(f"- approval_id={pa.approval_id} call={_call(pa)}")
    return "\n".join(lines)


def _call(pa: PendingAction) -> str:
    return json.dumps({"instance": pa.instance, "command": pa.command, "args": pa.args}, sort_keys=True)


def _actor(alert_id: str) -> Actor:
    return Actor(id="agent:responder", kind="agent", roles={"analyst"}, on_behalf_of=f"alert:{alert_id}")


def compact(result: CommandResult) -> dict[str, Any]:
    """The model-facing view of a CommandResult: status, readable text, context; capped like MCP."""
    texts = []
    for entry in result.entries:
        if entry.human_readable:
            texts.append(entry.human_readable)
        elif entry.contents is not None:
            texts.append(entry.contents if isinstance(entry.contents, str) else json.dumps(entry.contents, default=str))
    out: dict[str, Any] = {"status": result.status, "human_readable": "\n\n".join(texts), "context": result.context}
    if result.status == "error":
        out["error"] = result.error
        out["error_kind"] = result.error_kind
    text = json.dumps(out, default=str)
    if len(text) > RESULT_CAP:
        out = {
            "status": result.status,
            "truncated": True,
            "human_readable": out["human_readable"][: RESULT_CAP // 2],
            "context": json.dumps(result.context, default=str)[: RESULT_CAP // 4],
        }
        if result.status == "error":
            out["error"] = (result.error or "")[:1000]
    return out


@responder.tool
def search_tools(ctx: RunContext[ResponderDeps], query: str) -> Any:
    """Search integration commands by free text. Each result lists the configured instances that can run it."""
    if err := ctx.deps.budget.spend():
        return {"error": err}
    by_integration: dict[str, list[str]] = {}
    for inst in ctx.deps.gateway.list_instances():
        if inst.get("enabled", True) and not inst.get("problem"):
            by_integration.setdefault(inst["integration"], []).append(inst["name"])
    results = []
    # Rank the whole catalog, then keep configured integrations: cutting to SEARCH_LIMIT first would let a generic
    # query's top hits on unconfigured integrations hide every configured command.
    for hit in ctx.deps.gateway.catalog.search(query, limit=ctx.deps.gateway.catalog.stats["total_commands"]):
        instances = by_integration.get(hit["integration"], [])
        if instances:
            results.append({**hit, "instances": instances})
            if len(results) == SEARCH_LIMIT:
                break
    return results


@responder.tool
def get_tool_schema(ctx: RunContext[ResponderDeps], tool_name: str) -> Any:
    """Argument schema for a tool name like `AbuseIPDB.ip`."""
    if err := ctx.deps.budget.spend():
        return {"error": err}
    try:
        return ctx.deps.gateway.catalog.get_schema(tool_name)
    except (KeyError, ValueError) as e:
        return {"error": f"unknown tool {tool_name}: {e}"}


@responder.tool
async def invoke(
    ctx: RunContext[ResponderDeps],
    instance: str,
    command: str,
    args: dict[str, Any],
    reason: str,
    approval_id: str | None = None,
) -> Any:
    """Run a command on a configured instance. `command` is the command part of a search_tools tool_name (for
    `AbuseIPDB.ip` it is `ip`). Destructive commands need an approved approval_id."""
    deps = ctx.deps
    if err := deps.budget.spend():
        return {"error": err}
    if approval_id is not None and any(a.approval_id == approval_id for a in deps.taken):
        return {"status": "already_executed", "approval_id": approval_id, "message": "see the outcome listed above"}
    if approval_id is None:
        # Do not file a duplicate approval for a call that is still waiting on one from an earlier run.
        for pa, status in deps.previous:
            if status == "pending" and (pa.instance, pa.command, pa.args) == (instance, command, args):
                _record_pending(deps, pa)
                return {"status": "approval_required", "approval_id": pa.approval_id, "message": "still pending"}
    try:
        out = await deps.gateway.invoke(
            instance, command, args, reason, actor=_actor(deps.alert_id), approval_id=approval_id
        )
    except Exception as e:
        deps.errors.append(f"{instance}.{command}: {type(e).__name__}: {e}")
        return {"status": "error", "error": f"{type(e).__name__}: {e}"}
    # What the report stores and shows: secret args masked. A pending action with a secret arg therefore cannot
    # be replayed by _execute_approved (its args no longer hash to the approval) and is refused, never leaked.
    shown = deps.gateway.mask_args(instance, command, args)
    if isinstance(out, Pending):
        _record_pending(
            deps,
            PendingAction(approval_id=out.approval_id, instance=instance, command=command, args=shown, reason=reason),
        )
        return {"status": out.status, "approval_id": out.approval_id, "risk": out.risk, "message": out.message}
    if isinstance(out, Denied):
        deps.evidence.append(
            Evidence(instance=instance, command=command, args=shown, finding=out.reason, status="denied")
        )
        return {"status": out.status, "reason": out.reason}
    view = compact(out)
    finding = (view.get("error") or view.get("human_readable") or "")[:500]
    deps.evidence.append(Evidence(instance=instance, command=command, args=shown, finding=finding, status=out.status))
    if approval_id is not None:  # the approval was consumed, so the action ran whatever its status
        deps.taken.append(
            ActionTaken(
                instance=instance,
                command=command,
                args=shown,
                result=finding,
                approval_id=approval_id,
                status=out.status,
            )
        )
    return view


def _record_pending(deps: ResponderDeps, pa: PendingAction) -> None:
    if all(p.approval_id != pa.approval_id for p in deps.pending):
        deps.pending.append(pa)


def _previous(gateway: Gateway, previous: IRReport | None) -> list[tuple[PendingAction, str]]:
    out: list[tuple[PendingAction, str]] = []
    for pa in previous.actions_pending if previous else []:
        approval = gateway.get_approval(pa.approval_id)
        out.append((pa, approval.status if approval else "unknown"))
    return out


async def _execute_approved(deps: ResponderDeps) -> None:
    """Run every earlier approval that is now approved, exactly as requested; record refusals as errors.

    This does not depend on the model: a human-approved containment action must not silently stay unexecuted.
    """
    for pa, status in deps.previous:
        if status == "pending":
            continue
        if status != "approved":
            deps.errors.append(f"approval {pa.approval_id} for {pa.instance} {pa.command} was {status}; not executed")
            deps.resumed.append(f"approval_id={pa.approval_id} {status}, not executed: call={_call(pa)}")
            continue
        try:
            out = await deps.gateway.invoke(
                pa.instance, pa.command, pa.args, pa.reason, actor=_actor(deps.alert_id), approval_id=pa.approval_id
            )
        except Exception as e:
            deps.errors.append(f"approved {pa.instance} {pa.command} ({pa.approval_id}) crashed: {type(e).__name__}")
            deps.resumed.append(f"approval_id={pa.approval_id} crashed: call={_call(pa)}")
            continue
        if not isinstance(out, CommandResult):  # Denied: expired or no longer matching; Pending cannot happen here
            why = out.reason if isinstance(out, Denied) else out.message
            deps.errors.append(f"approved {pa.instance} {pa.command} ({pa.approval_id}) refused: {why}")
            deps.resumed.append(f"approval_id={pa.approval_id} refused ({why}): call={_call(pa)}")
            continue
        view = compact(out)
        finding = (view.get("error") or view.get("human_readable") or "")[:500]
        deps.taken.append(
            ActionTaken(
                instance=pa.instance,
                command=pa.command,
                args=pa.args,
                result=finding,
                approval_id=pa.approval_id,
                status=out.status,
            )
        )
        if out.status != "ok":
            deps.errors.append(f"approved {pa.instance} {pa.command} ({pa.approval_id}) failed: {finding}")
        deps.resumed.append(f"approval_id={pa.approval_id} executed, status={out.status}: {finding} call={_call(pa)}")


def _prompt(alert: dict[str, Any]) -> str:
    view = dict(alert)
    raw = json.dumps(view.pop("raw", None), default=str)
    view["raw"] = raw if len(raw) <= RAW_CAP else raw[:RAW_CAP] + "...(truncated)"
    return "Alert:\n" + json.dumps(view, indent=2, default=str)


async def respond(
    alert: dict[str, Any],
    gateway: Gateway,
    config: DbotConfig,
    *,
    alert_id: str | None = None,
    previous: IRReport | None = None,
    model: Model | str | None = None,
) -> IRReport:
    """Run the responder on one alert.

    `alert_id` is the store id (defaults to `alert["id"]`); approvals are filed on behalf of `alert:<alert_id>`.
    `previous` is the report from an earlier run of the same alert (resume after an approval decision).
    """
    alert_id = alert_id or str(alert.get("id", ""))
    budget = Budget(config.agent.max_tool_calls, config.agent.wall_clock_s)
    deps = ResponderDeps(
        gateway=gateway, alert=alert, budget=budget, alert_id=alert_id, previous=_previous(gateway, previous)
    )
    await _execute_approved(deps)
    # Requests are bounded too, so a model that ignores budget errors cannot loop forever.
    limits = UsageLimits(request_limit=config.agent.max_tool_calls + 5)
    report: IRReport | None = None
    failure: Exception | None = None
    try:
        async with asyncio.timeout(config.agent.wall_clock_s + 30):
            result = await responder.run(
                _prompt(alert), deps=deps, model=model or config.agent.model, usage_limits=limits
            )
        report = result.output
    except (UsageLimitExceeded, TimeoutError) as e:
        deps.errors.append(f"responder stopped: {type(e).__name__}: {e}")
    except Exception as e:
        # Approved actions may already have run; the caller must still be able to store what happened.
        failure = e
        deps.errors.append(f"{type(e).__name__}: {e}")
    if report is None:
        report = IRReport(
            verdict="inconclusive",
            confidence=0.0,
            summary=(
                "The responder failed."
                if failure
                else "The responder did not finish; evidence below is what it collected."
            ),
            evidence=deps.evidence,
        )
    # Actions come from what the gateway actually returned, never from the model's claims.
    taken = (previous.actions_taken if previous else []) + deps.taken
    taken_ids = {a.approval_id for a in taken}
    pending = [p for p in deps.pending if p.approval_id not in taken_ids]
    # Approvals still waiting from an earlier run stay pending even if the model did not touch them again.
    for pa, status in deps.previous:
        if (
            status == "pending"
            and pa.approval_id not in taken_ids
            and all(p.approval_id != pa.approval_id for p in pending)
        ):
            pending.append(pa)
    errors = list(report.errors) + deps.errors
    if budget.exhausted:
        errors.append(f"tool budget exhausted after {budget.calls} calls")
    # The model writes its own evidence list; mask its args the same way, since it may copy a secret arg into it.
    evidence = [
        e.model_copy(update={"args": gateway.mask_args(e.instance, e.command, e.args)}) for e in report.evidence
    ]
    final = report.model_copy(
        update={
            "alert_id": alert_id,
            "evidence": evidence,
            "actions_taken": taken,
            "actions_pending": pending,
            "errors": errors,
        }
    )
    if failure is not None:
        raise ResponderError(final) from failure
    return final


class ResponderError(Exception):
    """The model run failed. `report` still records every action the gateway actually executed or queued."""

    def __init__(self, report: IRReport) -> None:
        super().__init__("; ".join(report.errors))
        self.report = report
