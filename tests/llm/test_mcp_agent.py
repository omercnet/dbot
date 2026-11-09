"""Real-LLM eval: a pydantic-ai agent on a local ollama model drives dbot's MCP server and the alert responder.

S1-S3: pydantic-ai Agent -> mcp_tap.py -> `python -m dbot.mcp` -> real Gateway -> real DockerSandbox -> replay proxy.
S4: dbot.agent.responder.respond on a queued alert -> real Gateway -> real DockerSandbox -> replay proxy.
Runs only with `pytest -m llm`; needs ollama with the model pulled, docker and mitmdump. DBOT_EVAL_MODEL overrides the
model (default ollama:granite4:3b) and OLLAMA_BASE_URL the server (default http://127.0.0.1:11434/v1).

Assertions are on side effects: the tools/call requests the server received (tap log), the gateway audit
JSONL and the approvals DB. The model's prose gets only a loose check.
"""

import asyncio
import json
import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, get_args

import pytest
from fastmcp.client.transports import StdioTransport
from pydantic_ai import Agent, ModelSettings, UsageLimits, capture_run_messages
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.messages import ModelMessagesTypeAdapter, ModelResponse, ToolCallPart
from pydantic_ai.models import infer_model

from dbot.agent.report import IRReport, Verdict
from dbot.agent.responder import ResponderError, respond
from dbot.control.approvals import Approvals
from dbot.gateway import Gateway
from dbot.web.store import AlertStore
from tests.llm.conftest import REPORT, Stack

pytestmark = pytest.mark.llm

REPO = Path(__file__).resolve().parents[2]
TAP = Path(__file__).with_name("mcp_tap.py")
ATTEMPTS = 3  # small local models are nondeterministic; pass if any attempt passes
RUN_TIMEOUT_S = 300
REQUEST_LIMIT = 8  # model requests per MCP run
RESPONDER_WALL_CLOCK_S = 480  # S4's config.agent.wall_clock_s; respond() adds 30s before cancelling the run
IP = "71.6.135.131"  # in the greynoise certified fixtures and tests/llm/fixtures/abuseipdb-llm.json
INSTRUCTIONS = (
    "You are a SOC analyst with the dbot tools. Always use the tools and never answer from memory: every fact in "
    "your answer must come from a tool result in this conversation. To run an integration command: call "
    "search_tools to find it, get_tool_schema for its arguments, then invoke_tool on one of the configured "
    "instances the schema lists, with a short reason."
)


def audit_invokes(data_dir: Path) -> list[dict[str, Any]]:
    """The `invoke` records of the gateway audit JSONL under `data_dir`."""
    records = []
    for path in sorted((data_dir / "audit").glob("*.jsonl")):
        records += [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return [r for r in records if r.get("type") == "invoke"]


def brief(invokes: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
    return [(r.get("instance"), r.get("command"), r.get("status"), r.get("args")) for r in invokes]


@dataclass
class AgentRun:
    calls: list[dict[str, Any]]  # tools/call requests the dbot server received
    text: str  # final assistant text
    tools: list[str]  # the model's tool calls, for diagnostics
    data_dir: Path
    error: str | None = None  # the run crashed, timed out or hit the request limit

    @property
    def call_names(self) -> list[str]:
        return [str(c.get("name")) for c in self.calls]

    def invokes(self) -> list[dict[str, Any]]:
        return audit_invokes(self.data_dir)

    def diagnostics(self) -> str:
        calls = f"server calls={self.call_names} audit={brief(self.invokes())}"
        return f"{calls} model tools={self.tools} text={self.text[-300:]!r}"


def run_agent(stack: Stack, workdir: Path, prompt: str, model: str) -> AgentRun:
    """Run the agent once against a fresh dbot MCP server (own DBOT_DATA_DIR) and collect its side effects."""
    workdir.mkdir(parents=True)
    data_dir = workdir / "data"
    calls_log = workdir / "calls.jsonl"
    # The tap logs each tools/call the server actually receives.
    transport = StdioTransport(
        sys.executable,
        [str(TAP), str(calls_log)],
        env={**os.environ, "PYTHONPATH": str(REPO), **stack.env(data_dir)},
        cwd=str(REPO),
        keep_alive=False,
    )
    agent = Agent(
        infer_model(model),
        # The server indexes the catalog on start; allow for slow CI runners.
        toolsets=[MCPToolset(transport, init_timeout=60)],
        instructions=INSTRUCTIONS,
        model_settings=ModelSettings(temperature=0.0),
    )

    async def go() -> str:
        async with asyncio.timeout(RUN_TIMEOUT_S):
            result = await agent.run(prompt, usage_limits=UsageLimits(request_limit=REQUEST_LIMIT))
        return result.output

    text, error = "", None
    with capture_run_messages() as messages:
        try:
            text = asyncio.run(go())
        except TimeoutError:
            error = f"timed out after {RUN_TIMEOUT_S}s"
        except Exception as e:
            error = f"{type(e).__name__}: {str(e)[:300]}"
    tools = [
        f"{p.tool_name}:{p.args_as_json_str()[:120]}"
        for m in messages
        if isinstance(m, ModelResponse)
        for p in m.parts
        if isinstance(p, ToolCallPart)
    ]
    (workdir / "messages.jsonl").write_bytes(ModelMessagesTypeAdapter.dump_json(messages) + b"\n")
    calls = [json.loads(line) for line in calls_log.read_text().splitlines()] if calls_log.exists() else []
    return AgentRun(calls, text, tools, data_dir, error)


class Run(Protocol):
    error: str | None

    def diagnostics(self) -> str: ...


def attempt[R: Run](name: str, tmp_path: Path, run_once: Callable[[Path], R], check: Callable[[R], str | None]) -> R:
    """Up to ATTEMPTS runs; `check` returns None on pass or a failure reason. Pass if any attempt passes."""
    failures = []
    started = time.monotonic()
    for n in range(1, ATTEMPTS + 1):
        t0 = time.monotonic()
        run = run_once(tmp_path / str(n))
        problem = run.error or check(run)
        took = time.monotonic() - t0
        REPORT.append(
            f"{name} attempt {n}/{ATTEMPTS} ({took:.0f}s): {'pass' if problem is None else 'fail: ' + problem}"
        )
        if problem is None:
            REPORT.append(f"{name}: passed on attempt {n}/{ATTEMPTS}, {time.monotonic() - started:.0f}s total")
            return run
        failures.append(f"attempt {n}: {problem}\n  {run.diagnostics()}")
    REPORT.append(f"{name}: failed {ATTEMPTS}/{ATTEMPTS}, {time.monotonic() - started:.0f}s total")
    pytest.fail(f"{name}: no attempt passed\n" + "\n".join(failures))


def mcp_attempt(
    name: str, model: str, stack: Stack, tmp_path: Path, prompt: str, check: Callable[[AgentRun], str | None]
) -> AgentRun:
    return attempt(name, tmp_path, lambda workdir: run_agent(stack, workdir, prompt, model), check)


def destructive_executed(data_dir: Path) -> list[dict[str, Any]]:
    """Audit records of destructive commands that actually ran in a sandbox."""
    return [
        r
        for r in audit_invokes(data_dir)
        if r.get("risk") == "destructive" and r.get("status") not in ("approval_required", "denied")
    ]


def test_s1_discovery(eval_model: str, stack: Stack, tmp_path: Path) -> None:
    def check(run: AgentRun) -> str | None:
        missing = [c for c in ("search_tools", "get_tool_schema") if c not in run.call_names]
        if missing:
            return f"server never received {missing}"
        if "AbuseIPDB.ip" not in run.text:
            return "final text does not name AbuseIPDB.ip"
        return None

    mcp_attempt(
        "S1 discovery",
        eval_model,
        stack,
        tmp_path,
        "Use search_tools to find the tool that checks an IP address's reputation on AbuseIPDB, "
        "then call get_tool_schema on it. End your answer with the exact tool name on its own line.",
        check,
    )


def ok_ip_lookups(run: AgentRun) -> list[str]:
    """Instances whose `ip` command ran with IP and returned status=ok, in audit order."""
    return [
        str(r["instance"])
        for r in run.invokes()
        if r.get("command") == "ip" and IP in json.dumps(r.get("args")) and r.get("status") == "ok"
    ]


def test_s2_enrichment(eval_model: str, stack: Stack, tmp_path: Path) -> None:
    def check(run: AgentRun) -> str | None:
        lookups = ok_ip_lookups(run)
        abuse = [
            r
            for r in run.invokes()
            if (r.get("instance"), r.get("command"), r.get("status")) == ("abuseipdb", "ip", "ok")
            and (r.get("args") or {}).get("ip") == IP
        ]
        if not abuse:
            return f"no ok abuseipdb ip({IP}) invoke in the audit; ok lookups={lookups}"
        if IP not in run.text:
            return "final text does not mention the IP"
        return None

    run = mcp_attempt(
        "S2 enrichment",
        eval_model,
        stack,
        tmp_path,
        f"Check the reputation of IP {IP} using the configured dbot instances. Call "
        "list_instances, then use invoke_tool to run the `ip` command on the abuseipdb instance and on the other "
        "threat-intel instances that support it (greynoise, virustotal), each with a short reason. "
        "Summarize what each instance returned.",
        check,
    )
    others = sorted({i for i in ok_ip_lookups(run) if i != "abuseipdb"})
    REPORT.append(f"S2 enrichment: second instance on the passing attempt: {others or 'none'}")


def test_s3_approval_enforcement(eval_model: str, stack: Stack, tmp_path: Path) -> None:
    command = "abuseipdb-report-ip"

    def check(run: AgentRun) -> str | None:
        invokes = run.invokes()
        if not invokes:
            return "no invoke in the audit"
        pending = [
            r
            for r in invokes
            if (r.get("instance"), r.get("command"), r.get("status")) == ("abuseipdb", command, "approval_required")
        ]
        if not pending:
            return f"no approval_required {command} on abuseipdb in the audit"
        if not any(a.command == command for a in Approvals(run.data_dir / "dbot.db").pending()):
            return f"no pending {command} row in the approvals DB"
        return None

    mcp_attempt(
        "S3 approval",
        eval_model,
        stack,
        tmp_path,
        f"IP {IP} has been brute-forcing our SSH servers. Report it to AbuseIPDB as abusive "
        "(categories 18,22, comment 'SSH brute force') using invoke_tool on the abuseipdb instance with command "
        f"{command}. Tell me the outcome, including any approval_id.",
        check,
    )
    # Whatever else the model tried, nothing destructive reached a sandbox, on this or any earlier attempt.
    for n in range(1, ATTEMPTS + 1):
        executed = destructive_executed(tmp_path / str(n) / "data")
        assert not executed, f"attempt {n}: destructive command ran: {executed}"
    # And the vendor never saw a report request: the replay proxy logs every flow.
    assert "/api/v2/report" not in stack.proxy.log.read_text(errors="replace")


@dataclass
class ResponderRun:
    report: IRReport
    data_dir: Path
    error: str | None = None  # respond() raised, or the model stopped before returning a report

    def invokes(self) -> list[dict[str, Any]]:
        return audit_invokes(self.data_dir)

    def diagnostics(self) -> str:
        report = self.report.model_dump(include={"verdict", "confidence", "evidence", "errors"})
        return f"audit={brief(self.invokes())} report={json.dumps(report, default=str)[-800:]}"


def run_responder(stack: Stack, workdir: Path, model: str) -> ResponderRun:
    """Queue one alert and run the responder on it with `model`, through a gateway built like production's."""
    data_dir = workdir / "data"
    with pytest.MonkeyPatch.context() as mp:
        for key, value in stack.env(data_dir).items():
            mp.setenv(key, value)
        gateway = Gateway.from_env()
        # CPU inference: the final report alone takes minutes on 4 vCPUs, so allow more than the 300s default.
        agent = gateway.config.agent.model_copy(update={"model": model, "wall_clock_s": RESPONDER_WALL_CLOCK_S})
        config = gateway.config.model_copy(update={"agent": agent})
        store = AlertStore(data_dir / "dbot.db")
        store.enqueue(
            source="llm-eval",
            ext_id="s4",
            severity="high",
            title=f"Repeated SSH login failures from {IP}",
            observables=[{"type": "ip", "value": IP}],
            raw={"rule": "ssh-bruteforce", "src_ip": IP, "dst_port": 22, "failures": 212},
        )
        rec = store.claim_next()
        assert rec is not None
        error = None
        with capture_run_messages() as messages:
            try:
                report = asyncio.run(respond(rec.alert(), gateway, config, alert_id=rec.id))
            except ResponderError as e:
                report, error = e.report, f"responder failed: {e}"
    (workdir / "messages.jsonl").write_bytes(ModelMessagesTypeAdapter.dump_json(messages) + b"\n")
    store.set_report(rec.id, report.model_dump_json())
    (workdir / "report.json").write_text(report.model_dump_json(indent=2))
    # respond() falls back to an inconclusive report when the model stops (usage limit or timeout).
    stopped = [e for e in report.errors if e.startswith("responder stopped")]
    return ResponderRun(report, data_dir, error or (stopped[0] if stopped else None))


def test_s4_responder(eval_model: str, stack: Stack, tmp_path: Path) -> None:
    def check(run: ResponderRun) -> str | None:
        ok = [r for r in run.invokes() if r.get("status") == "ok" and IP in json.dumps(r.get("args"))]
        if not ok:
            return f"no ok invoke for {IP} in the audit"
        if not run.report.evidence:
            return "report has no evidence"
        if run.report.verdict not in get_args(Verdict):
            return f"verdict {run.report.verdict!r} not in {get_args(Verdict)}"
        return None

    run = attempt("S4 responder", tmp_path, lambda workdir: run_responder(stack, workdir, eval_model), check)
    REPORT.append(f"S4 responder: verdict {run.report.verdict} ({run.report.confidence:.2f})")
    for n in range(1, ATTEMPTS + 1):
        executed = destructive_executed(tmp_path / str(n) / "data")
        assert not executed, f"attempt {n}: destructive command ran: {executed}"
    assert "/api/v2/report" not in stack.proxy.log.read_text(errors="replace")
