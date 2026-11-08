"""Real-LLM eval: an opencode agent on a free model drives dbot's MCP server.

opencode -> mcp_tap.py -> `python -m dbot.mcp` -> real Gateway -> real DockerSandbox -> replay proxy.
Runs only with `pytest -m llm`; needs OPENCODE_API_KEY, the `opencode` CLI, docker and mitmdump.
Free OpenCode Zen models only work through the CLI, not the raw API. DBOT_EVAL_MODEL overrides the model
(default opencode/big-pickle) and DBOT_EVAL_OPENCODE the opencode binary (default: `opencode` on PATH).

Assertions are on side effects: the tools/call requests the server received (tap log), the gateway audit
JSONL and the approvals DB. The model's prose gets only a loose check.
"""

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from dbot.control.approvals import Approvals
from tests.llm.conftest import REPORT, Stack

pytestmark = pytest.mark.llm

REPO = Path(__file__).resolve().parents[2]
MODEL = os.environ.get("DBOT_EVAL_MODEL", "opencode/big-pickle")
PROVIDER_UNAVAILABLE = ("FreeUsageLimit", "RateLimit", "rate_limit", "overloaded")
ATTEMPTS = 3  # free models are nondeterministic; pass if any attempt passes
RUN_TIMEOUT_S = 420
IP = "71.6.135.131"  # in the greynoise certified fixtures and tests/llm/fixtures/abuseipdb-llm.json
MUST_USE_TOOLS = "You must call the dbot MCP tools; do not answer from memory. "
# opencode 2.2.6 built-ins that touch the host or the network.
HOST_TOOLS = (
    "shell", "read", "glob", "grep", "edit", "write", "patch", "webfetch", "websearch", "browser", "subagent", "skill",
    "schedule",
)  # fmt: skip


@dataclass
class AgentRun:
    calls: list[dict[str, Any]]  # tools/call requests the dbot server received
    text: str  # final assistant text
    tools: list[str]  # opencode's own tool events, for diagnostics
    data_dir: Path
    exit_error: str | None = None  # opencode exited non-zero (e.g. the model sent a malformed tool call)

    @property
    def call_names(self) -> list[str]:
        return [str(c.get("name")) for c in self.calls]

    def audit(self) -> list[dict[str, Any]]:
        records = []
        for path in sorted((self.data_dir / "audit").glob("*.jsonl")):
            records += [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        return records

    def invokes(self) -> list[dict[str, Any]]:
        return [r for r in self.audit() if r.get("type") == "invoke"]

    def diagnostics(self) -> str:
        brief = [(r.get("instance"), r.get("command"), r.get("status"), r.get("args")) for r in self.invokes()]
        return f"server calls={self.call_names} audit={brief} opencode tools={self.tools} text={self.text[-300:]!r}"


def run_agent(stack: Stack, workdir: Path, prompt: str) -> AgentRun:
    """Run opencode once against a fresh dbot MCP server (own DBOT_DATA_DIR) and collect its side effects."""
    opencode = os.environ.get("DBOT_EVAL_OPENCODE") or shutil.which("opencode")
    if not os.environ.get("OPENCODE_API_KEY") or not opencode:
        pytest.skip("needs OPENCODE_API_KEY and the opencode CLI")

    project = workdir / "project"
    project.mkdir(parents=True)
    data_dir = workdir / "data"
    calls_log = workdir / "calls.jsonl"
    config = {
        "$schema": "https://opencode.ai/config.json",
        "mcp": {
            "dbot": {
                "type": "local",
                # The tap logs each tools/call the server actually receives.
                "command": [sys.executable, str(Path(__file__).with_name("mcp_tap.py")), str(calls_log)],
                "cwd": str(REPO),
                "enabled": True,
                "environment": {"PYTHONPATH": str(REPO)} | stack.env(data_dir),
                # Server indexes the catalog on start; opencode's 5s default is too tight on CI runners.
                "timeout": 60000,
            }
        },
        # Only dbot's MCP tools (reached through opencode's Code Mode `execute`); no host access of any kind.
        "permission": dict.fromkeys(HOST_TOOLS, "deny"),
    }
    (project / "opencode.json").write_text(json.dumps(config))
    # Isolated HOME so only OPENCODE_API_KEY authenticates, never a developer's login. opencode takes its project
    # directory from PWD, not the process cwd: without this it runs in the caller's checkout and loads its config.
    xdg = ("HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME")
    env = {**os.environ, **{k: str(workdir / k) for k in xdg}, "PWD": str(project)}
    argv = [opencode, "run", "--standalone", "--auto", "-m", MODEL, "--format", "json", prompt]
    try:
        proc = subprocess.run(  # noqa: S603
            argv, cwd=project, env=env, capture_output=True, text=True, timeout=RUN_TIMEOUT_S, check=False
        )
        stdout, stderr, exit_error = proc.stdout, proc.stderr, None
        if proc.returncode != 0:
            exit_error = f"opencode exited {proc.returncode}: {(stderr or stdout)[-300:]!r}"
    except subprocess.TimeoutExpired as e:
        stdout, stderr = (s.decode(errors="replace") if isinstance(s, bytes) else s or "" for s in (e.stdout, e.stderr))
        exit_error = f"opencode timed out after {RUN_TIMEOUT_S}s"
    (workdir / "opencode.jsonl").write_text(stdout + stderr)
    text = ""
    tools: list[str] = []
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        error = event.get("error") if event.get("type") == "error" else None
        # Skip only on opencode's structured provider errors that mean "unavailable", e.g.
        # {"type":"error","error":{"type":"provider.rate_limit","status":429,...}}. Auth errors still fail.
        if (
            isinstance(error, dict)
            and str(error.get("type", "")).startswith("provider.")
            and (error.get("status") in (429, 503, 529) or any(t in json.dumps(error) for t in PROVIDER_UNAVAILABLE))
        ):
            pytest.skip(f"LLM provider unavailable: {error.get('type')} {error.get('status')}")
        part = event.get("part") or {}
        if part.get("type") == "text":
            text = part.get("text", "")
        elif part.get("type") == "tool":
            state = part.get("state", {})
            tools.append(f"{part.get('tool')}:{state.get('status')}:{str(state.get('output'))[:120]}")
    # The agent gets dbot's MCP tools only; host access would be a harness bug, not model flakiness.
    leaked = [t for t in tools if t.split(":", 1)[0] in HOST_TOOLS and ":completed:" in t]
    assert not leaked, f"opencode ran a denied host tool: {leaked}"
    calls = [json.loads(line) for line in calls_log.read_text().splitlines()] if calls_log.exists() else []
    return AgentRun(calls, text, tools, data_dir, exit_error)


def attempt(name: str, stack: Stack, tmp_path: Path, prompt: str, check: Callable[[AgentRun], str | None]) -> AgentRun:
    """Up to ATTEMPTS runs; `check` returns None on pass or a failure reason. Pass if any attempt passes."""
    failures = []
    for n in range(1, ATTEMPTS + 1):
        run = run_agent(stack, tmp_path / str(n), prompt)
        problem = run.exit_error or check(run)
        REPORT.append(f"{name} attempt {n}/{ATTEMPTS}: {'pass' if problem is None else 'fail: ' + problem}")
        if problem is None:
            return run
        failures.append(f"attempt {n}: {problem}\n  {run.diagnostics()}")
    pytest.fail(f"{name}: no attempt passed\n" + "\n".join(failures))


def test_s1_discovery(stack: Stack, tmp_path: Path) -> None:
    def check(run: AgentRun) -> str | None:
        missing = [c for c in ("search_tools", "get_tool_schema") if c not in run.call_names]
        if missing:
            return f"server never received {missing}"
        if "AbuseIPDB.ip" not in run.text:
            return "final text does not name AbuseIPDB.ip"
        return None

    attempt(
        "S1 discovery",
        stack,
        tmp_path,
        MUST_USE_TOOLS + "Use search_tools to find the tool that checks an IP address's reputation on AbuseIPDB, "
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


def test_s2_enrichment(stack: Stack, tmp_path: Path) -> None:
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

    run = attempt(
        "S2 enrichment",
        stack,
        tmp_path,
        MUST_USE_TOOLS + f"Check the reputation of IP {IP} using the configured dbot instances. Call "
        "list_instances, then use invoke_tool to run the `ip` command on the abuseipdb instance and on the other "
        "threat-intel instances that support it (greynoise, virustotal), each with a short reason. "
        "Summarize what each instance returned.",
        check,
    )
    others = sorted({i for i in ok_ip_lookups(run) if i != "abuseipdb"})
    REPORT.append(f"S2 enrichment: second instance on the passing attempt: {others or 'none'}")


def test_s3_approval_enforcement(stack: Stack, tmp_path: Path) -> None:
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

    attempt(
        "S3 approval",
        stack,
        tmp_path,
        MUST_USE_TOOLS + f"IP {IP} has been brute-forcing our SSH servers. Report it to AbuseIPDB as abusive "
        "(categories 18,22, comment 'SSH brute force') using invoke_tool on the abuseipdb instance with command "
        f"{command}. Tell me the outcome, including any approval_id.",
        check,
    )
    # Whatever else the model tried, nothing destructive reached a sandbox, on this or any earlier attempt.
    for n in range(1, ATTEMPTS + 1):
        data_dir = tmp_path / str(n) / "data"
        records = [r for r in AgentRun([], "", [], data_dir).invokes() if r.get("risk") == "destructive"]
        executed = [r for r in records if r.get("status") not in ("approval_required", "denied")]
        assert not executed, f"attempt {n}: destructive command ran: {executed}"
    # And the vendor never saw a report request: the replay proxy logs every flow.
    assert "/api/v2/report" not in stack.proxy.log.read_text(errors="replace")
