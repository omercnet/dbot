"""Real-LLM eval: an opencode agent drives the dbot MCP server.

Runs only with `pytest -m llm` and needs OPENCODE_API_KEY plus the `opencode` CLI.
Free OpenCode Zen models only work through the CLI, not the raw API.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.llm

REPO = Path(__file__).resolve().parents[2]
MODEL = os.environ.get("DBOT_EVAL_MODEL", "opencode/big-pickle")
PROVIDER_UNAVAILABLE = ("FreeUsageLimit", "RateLimit", "rate_limit", "overloaded")
ATTEMPTS = 3  # free models are nondeterministic; pass if any attempt passes
# opencode 2.2.6 built-ins that touch the host or the network.
HOST_TOOLS = (
    "shell", "bash", "read", "glob", "grep", "edit", "write", "patch", "webfetch", "websearch", "browser",
    "subagent", "task", "skill", "schedule",
)  # fmt: skip


def provider_unavailable(stdout: str) -> str | None:
    """Reason to skip, or None. Only opencode's structured provider errors meaning "unavailable" count, e.g.
    {"type":"error","error":{"type":"provider.rate_limit","status":429}}. Auth errors, stderr and model text never do.
    """
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        error = event.get("error") if isinstance(event, dict) and event.get("type") == "error" else None
        if not (isinstance(error, dict) and str(error.get("type", "")).startswith("provider.")):
            continue
        if error.get("status") in (429, 503, 529) or any(t in json.dumps(error) for t in PROVIDER_UNAVAILABLE):
            return f"{error.get('type')} {error.get('status')}"
    return None


def run_agent(tmp_path: Path, prompt: str) -> tuple[list[str], str, list[str]]:
    """Run opencode against dbot's MCP server.

    Returns (tool calls the dbot server received, final text, opencode's own tool events for diagnostics).
    """
    opencode = shutil.which("opencode")
    if not os.environ.get("OPENCODE_API_KEY") or not opencode:
        pytest.skip("needs OPENCODE_API_KEY and the opencode CLI")

    project = tmp_path / "project"
    project.mkdir(parents=True)
    config = {
        "$schema": "https://opencode.ai/config.json",
        "mcp": {
            "dbot": {
                "type": "local",
                # The tap logs each tools/call the server actually receives.
                "command": [sys.executable, str(Path(__file__).with_name("mcp_tap.py")), str(tmp_path / "calls.log")],
                "cwd": str(REPO),
                "enabled": True,
                "environment": {"PYTHONPATH": str(REPO)},
                # Server indexes ~1,300 integrations on start; opencode's 5s default is too tight on CI runners.
                "timeout": 60000,
            }
        },
        # Only dbot's MCP tools; no host or network access of any kind.
        "permission": dict.fromkeys(HOST_TOOLS, "deny"),
    }
    (project / "opencode.json").write_text(json.dumps(config))
    # Isolated HOME so only OPENCODE_API_KEY authenticates, never a developer's login. opencode takes its project
    # directory from PWD, not the process cwd: without this it runs in the caller's checkout with its config.
    xdg = ("HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME")
    home = {k: str(tmp_path / k) for k in xdg} | {"PWD": str(project)}
    try:
        proc = subprocess.run(  # noqa: S603
            [opencode, "run", "--standalone", "--auto", "-m", MODEL, "--format", "json", prompt],
            cwd=project,
            env={**os.environ, **home},
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return [], "opencode timed out after 300s", []  # a failed attempt; later attempts may pass
    (tmp_path / "opencode.jsonl").write_text(proc.stdout + proc.stderr)
    reason = provider_unavailable(proc.stdout)
    if reason:
        pytest.skip(f"LLM provider unavailable: {reason}")
    text = ""
    tools: list[str] = []
    for line in proc.stdout.splitlines():
        try:
            part = json.loads(line).get("part", {})
        except json.JSONDecodeError:
            continue
        if part.get("type") == "text":
            text = part.get("text", "")
        elif part.get("type") == "tool":
            state = part.get("state", {})
            tools.append(f"{part.get('tool')}:{state.get('status')}:{str(state.get('output'))[:120]}")
    # The agent gets dbot's MCP tools only; a completed host tool is a harness bug, not model flakiness.
    leaked = [t for t in tools if t.split(":", 1)[0] in HOST_TOOLS and ":completed:" in t]
    assert not leaked, f"opencode ran a denied host tool: {leaked}"
    if proc.returncode != 0:  # e.g. the model sent a malformed tool call: a failed attempt, not a harness error
        text = f"opencode exited {proc.returncode}: {proc.stderr[-500:]}"
    log = tmp_path / "calls.log"
    return (log.read_text().split() if log.exists() else []), text, tools


def test_agent_discovers_abuseipdb_tool(tmp_path: Path) -> None:
    failures = []
    for attempt in range(ATTEMPTS):
        calls, text, tools = run_agent(
            tmp_path / str(attempt),
            "You must call the dbot MCP tools; do not answer from memory. Use search_tools to find the tool that "
            "checks an IP address's reputation on AbuseIPDB, then call get_tool_schema on it. "
            "End your answer with the exact tool name on its own line.",
        )
        if "search_tools" in calls and "get_tool_schema" in calls and "AbuseDB.ip" in text:
            return
        failures.append(f"attempt {attempt}: server calls={calls} opencode tools={tools} text={text[-300:]!r}")
    pytest.fail("\n".join(failures))
