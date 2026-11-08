"""Real-LLM eval: an opencode agent drives the dbot MCP server.

Runs only with `pytest -m llm` and needs OPENCODE_API_KEY plus the `opencode` CLI.
Free OpenCode Zen models only work through the CLI, not the raw API.
"""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.llm

REPO = Path(__file__).resolve().parents[2]
MODEL = os.environ.get("DBOT_EVAL_MODEL", "opencode/big-pickle")
PROVIDER_ERRORS = ("FreeUsageLimit", "rate limit", "RateLimit", "overloaded")


def run_agent(tmp_path: Path, prompt: str) -> tuple[list[str], str]:
    """Run opencode against dbot's MCP server; return (dbot tool calls, final text)."""
    opencode = shutil.which("opencode")
    if not os.environ.get("OPENCODE_API_KEY") or not opencode:
        pytest.skip("needs OPENCODE_API_KEY and the opencode CLI")

    project = tmp_path / "project"
    project.mkdir()
    config = {
        "$schema": "https://opencode.ai/config.json",
        "mcp": {
            "dbot": {
                "type": "local",
                "command": [sys.executable, "-m", "dbot.server"],
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
    calls: list[str] = []
    text = ""
    errors = [proc.stderr]
    for line in proc.stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        part = event.get("part", {})
        if event.get("type") == "error":
            errors.append(json.dumps(event))
        elif part.get("type") == "tool" and part.get("state", {}).get("status") == "completed":
            tool = part.get("tool", "")
            if tool.startswith("dbot_"):  # direct MCP tool
                calls.append(tool.removeprefix("dbot_"))
            elif tool == "execute":  # opencode code mode wraps MCP calls in JS
                calls += re.findall(r"tools\.dbot\.(\w+)\(", part["state"].get("input", {}).get("code", ""))
        elif part.get("type") == "text":
            text = part.get("text", "")
    # Only opencode's own error events and stderr count, never model text or tool output.
    if any(e in err for err in errors for e in PROVIDER_ERRORS):
        pytest.skip(f"LLM provider unavailable: {errors[-1][-200:]}")
    assert proc.returncode == 0, proc.stderr[-2000:]
    return calls, text


def test_agent_discovers_abuseipdb_tool(tmp_path: Path) -> None:
    calls, text = run_agent(
        tmp_path,
        "Using only the dbot tools, find the tool that checks an IP address's reputation on AbuseIPDB "
        "and fetch its schema. End your answer with the exact tool name on its own line.",
    )
    assert "search_tools" in calls, text
    assert "get_tool_schema" in calls, text
    assert "AbuseDB.ip" in text, text
