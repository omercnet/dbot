"""The in-container runner, exercised with the host interpreter (no docker needed)."""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from dbot.sandbox.assemble import assemble
from tests.sandbox.conftest import make_content

FAKE_CSP = """import sys
import demistomock as demisto

def return_error(message):
    demisto.results({"Type": 4, "Contents": message, "ContentsFormat": "text"})
    sys.exit(0)
"""


def run_runner(tmp_path: Path, source: str, request: dict[str, Any] | None = None) -> tuple[list[str], dict[str, Any]]:
    integ = make_content(tmp_path, source, csp=FAKE_CSP)
    script = assemble(integ, tmp_path)
    req = request or {"command": "cmd", "args": {"a": "1"}, "params": {}}
    proc = subprocess.run(  # noqa: S603
        [sys.executable, str(script.parent / "runner.py"), str(script), integ.name],
        input=json.dumps(req),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    lines = proc.stdout.splitlines()
    return lines, json.loads(lines[-1])


def test_stray_prints_go_to_stderr_and_stdout_has_one_line(tmp_path: Path) -> None:
    src = """import os, subprocess
def main():
    print("stray print")
    os.write(1, b"raw fd write\\n")
    subprocess.run(["echo", "child process"])
    demisto.results("done")
"""
    lines, out = run_runner(tmp_path, src)
    assert len(lines) == 1
    assert out == {"results": ["done"], "logs": [], "exit": "ok", "error": None}


def test_return_error_sysexit_keeps_results(tmp_path: Path) -> None:
    src = """def main():
    demisto.results("partial")
    return_error("boom")
"""
    _, out = run_runner(tmp_path, src)
    assert out["exit"] == "sysexit"
    assert out["error"] is None
    assert out["results"] == ["partial", {"Type": 4, "Contents": "boom", "ContentsFormat": "text"}]


def test_nonzero_sysexit_is_reported(tmp_path: Path) -> None:
    _, out = run_runner(tmp_path, "import sys\ndef main():\n    sys.exit(3)\n")
    assert out["exit"] == "sysexit"
    assert out["error"] == "SystemExit: 3"


def test_import_error_vs_runtime_exception(tmp_path: Path) -> None:
    _, out = run_runner(tmp_path / "a", "import no_such_module_xyz\ndef main():\n    pass\n")
    assert out["exit"] == "import"
    assert out["error"].startswith("ModuleNotFoundError")

    _, out = run_runner(tmp_path / "b", "def main():\n    demisto.results(1)\n    raise ValueError('bad')\n")
    assert out["exit"] == "exception"
    assert out["error"] == "ValueError: bad"
    assert out["results"] == [1]
    assert any("ValueError: bad" in line for line in out["logs"])

    # Module-level code that reads params fails at runtime, not import; unresolved names are import errors.
    _, out = run_runner(tmp_path / "c", "REGION = demisto.params()['defaultRegion']\n")
    assert (out["exit"], out["error"]) == ("exception", "KeyError: 'defaultRegion'")
    _, out = run_runner(tmp_path / "d", "X: Tupple[int] = ()\n")
    assert out["exit"] == "import"


def test_main_runs_exactly_once(tmp_path: Path) -> None:
    guarded = """def main():
    demisto.results("ran")
if __name__ in ("__main__", "__builtin__", "builtins"):
    main()
"""
    _, out = run_runner(tmp_path / "a", guarded)
    assert out["results"] == ["ran"]

    unguarded = 'def main():\n    demisto.results("ran")\nmain()\n'
    _, out = run_runner(tmp_path / "b", unguarded)
    assert out["results"] == ["ran"]


def test_mock_exposes_request_and_xsoar_context(tmp_path: Path) -> None:
    src = """def main():
    p = demisto.params()
    demisto.results({
        "cmd": demisto.command(),
        "args": demisto.args(),
        "url": p.get("url", "default-url"),
        "token": p["token"],
        "brand": demisto.callingContext["context"]["IntegrationBrand"],
        "instance": demisto.integrationInstance(),
        "version": demisto.demistoVersion()["version"],
        "exec_type": demisto.executeCommand("x", {})[0]["Type"],
        "get": demisto.get({"a": {"b": 2}}, "a.b"),
    })
    demisto.setIntegrationContext({"k": 1})
    demisto.results(demisto.getIntegrationContext())
"""
    req = {"command": "c-1", "args": {"x": 1}, "params": {"url": None, "token": "t"}}
    _, out = run_runner(tmp_path, src, req)
    first, ctx = out["results"]
    assert first == {
        "cmd": "c-1",
        "args": {"x": 1},
        "url": "default-url",  # None params fall back to the integration default
        "token": "t",
        "brand": "Test Int",
        "instance": "Test Int_instance_1",
        "version": "8.9.0",
        "exec_type": 4,
        "get": 2,
    }
    assert ctx == {"k": 1}


def test_invalid_request_is_reported_not_crashed(tmp_path: Path) -> None:
    integ = make_content(tmp_path, "def main():\n    pass\n", csp=FAKE_CSP)
    script = assemble(integ, tmp_path)
    proc = subprocess.run(  # noqa: S603
        [sys.executable, str(script.parent / "runner.py"), str(script), "x"],
        input="not json",
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    out = json.loads(proc.stdout)
    assert out["exit"] == "exception"
    assert out["error"].startswith("invalid request")
