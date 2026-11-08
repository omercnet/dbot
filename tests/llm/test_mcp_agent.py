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
        "permission": {"bash": "deny", "edit": "deny", "webfetch": "deny"},
    }
    (project / "opencode.json").write_text(json.dumps(config))
    # Isolated HOME so only OPENCODE_API_KEY authenticates, never a developer's login.
    xdg = ("HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME")
    home = {k: str(tmp_path / k) for k in xdg}
    proc = subprocess.run(  # noqa: S603
        [opencode, "run", "--standalone", "--auto", "-m", MODEL, "--format", "json", prompt],
        cwd=project,
        env={**os.environ, **home},
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    (tmp_path / "opencode.jsonl").write_text(proc.stdout + proc.stderr)
    text = ""
    tools: list[str] = []
    for line in proc.stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        error = event.get("error") if event.get("type") == "error" else None
        # Skip only on opencode's structured provider errors that mean "unavailable", e.g.
        # {"type":"error","error":{"type":"provider.rate_limit","status":429,...}}. Auth errors still fail.
        if isinstance(error, dict) and str(error.get("type", "")).startswith("provider."):
            if error.get("status") in (429, 503, 529) or any(t in json.dumps(error) for t in PROVIDER_UNAVAILABLE):
                pytest.skip(f"LLM provider unavailable: {error.get('type')} {error.get('status')}")
        elif event.get("part", {}).get("type") == "text":
            text = event["part"].get("text", "")
        elif event.get("part", {}).get("type") == "tool":
            state = event["part"].get("state", {})
            tools.append(f"{event['part'].get('tool')}:{state.get('status')}:{str(state.get('output'))[:120]}")
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]
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
