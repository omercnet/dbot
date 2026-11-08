"""Sandbox entrypoint: run one integration command and print one JSON line.

Usage (inside the container): python /dbot/runner.py <assembled script> <integration name>

Reads {"command", "args", "params"} as JSON on stdin. Writes exactly one line to the original stdout:
{"results": [...], "logs": [...], "exit": "ok|sysexit|exception|import", "error": str|null}.
Everything the integration prints goes to stderr. Stdlib only, Python 3.10 compatible.
"""

import contextlib
import json
import os
import re
import sys
import traceback
import types
from typing import Any

MAX_TRACEBACK_LINES = 40
_TOP_LEVEL_MAIN = re.compile(r"^main\(\)", re.MULTILINE)


def _ensure_distutils() -> None:
    """CommonServerPython imports distutils.version.LooseVersion, removed from the stdlib in 3.12."""
    try:
        import distutils.version  # type: ignore[import-not-found]  # noqa: F401
    except ImportError:
        pass
    else:
        return

    class LooseVersion:
        def __init__(self, vstring: str | None = None) -> None:
            self.vstring = str(vstring or "0")
            self.version = [
                int(p) if p.isdigit() else p for p in re.split(r"(\d+|[a-z]+|\.)", self.vstring) if p and p != "."
            ]

        def _key(self, other: Any) -> tuple[list[str], list[str]]:
            if not isinstance(other, LooseVersion):
                other = LooseVersion(other)
            return [f"{p:>10}" for p in map(str, self.version)], [f"{p:>10}" for p in map(str, other.version)]

        def __eq__(self, other: object) -> bool:
            a, b = self._key(other)
            return a == b

        def __lt__(self, other: Any) -> bool:
            a, b = self._key(other)
            return a < b

        def __le__(self, other: Any) -> bool:
            a, b = self._key(other)
            return a <= b

        def __gt__(self, other: Any) -> bool:
            a, b = self._key(other)
            return a > b

        def __ge__(self, other: Any) -> bool:
            a, b = self._key(other)
            return a >= b

        def __hash__(self) -> int:
            return hash(self.vstring)

        def __str__(self) -> str:
            return self.vstring

    pkg = types.ModuleType("distutils")
    pkg.__path__ = []
    version = types.ModuleType("distutils.version")
    version.LooseVersion = LooseVersion  # type: ignore[attr-defined]
    version.StrictVersion = LooseVersion  # type: ignore[attr-defined]
    pkg.version = version  # type: ignore[attr-defined]
    sys.modules["distutils"] = pkg
    sys.modules["distutils.version"] = version


def _write_line(fd: int, out: dict[str, Any]) -> None:
    try:
        line = json.dumps(out, default=str)
    except (TypeError, ValueError) as e:
        line = json.dumps(
            {
                "results": [],
                "logs": out.get("logs", []),
                "exit": "exception",
                "error": "unserializable result: " + str(e),
            }
        )
    data = (line + "\n").encode("utf-8", "replace")
    while data:
        data = data[os.write(fd, data) :]


def run(script: str, brand: str, raw_request: str) -> dict[str, Any]:
    out: dict[str, Any] = {"results": [], "logs": [], "exit": "ok", "error": None}
    try:
        req = json.loads(raw_request)
        command = str(req["command"])
        args = req.get("args") or {}
        params = req.get("params") or {}
    except (ValueError, KeyError, TypeError) as e:
        out.update(exit="exception", error="invalid request: " + type(e).__name__)
        return out

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    _ensure_distutils()
    import demistomock  # type: ignore[import-not-found]  # copied next to this file in the container

    demistomock._init(command, args, params, brand)

    # XSOAR runs CSP, ApiModules and the integration in one namespace; alias the module names to it.
    module = types.ModuleType("CommonServerPython")
    module.__file__ = script
    sys.modules["CommonServerPython"] = module
    sys.modules["CommonServerUserPython"] = module

    phase = "import"
    try:
        with open(script, encoding="utf-8") as f:
            source = f.read()
        exec(compile(source, script, "exec"), module.__dict__)  # noqa: S102 - running integration code is the job
        if os.environ.get("HTTPS_PROXY"):
            # The sandbox egress proxy must stay in effect even when the instance has proxy disabled.
            module.__dict__["skip_proxy"] = lambda: None
        phase = "main"
        entry = module.__dict__.get("main")
        if callable(entry) and not _TOP_LEVEL_MAIN.search(source):
            entry()
    except SystemExit as e:
        out["exit"] = "sysexit"
        if e.code not in (0, None):
            out["error"] = "SystemExit: " + str(e.code)
    except BaseException as e:
        # Module-level code also reads params; only failures to load code or resolve names are import errors.
        is_import = phase == "import" and isinstance(e, (ImportError, SyntaxError, NameError))
        out["exit"] = "import" if is_import else "exception"
        out["error"] = type(e).__name__ + ": " + str(e)
        tb = traceback.format_exc().splitlines()[-MAX_TRACEBACK_LINES:]
        demistomock._logs.extend("TRACEBACK: " + line for line in tb)

    out["results"] = list(demistomock._results)
    out["logs"] = list(demistomock._logs)
    return out


def main() -> None:
    # Keep the real stdout for the protocol line; route fd 1 and sys.stdout to stderr.
    proto_fd = os.dup(1)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    if len(sys.argv) < 3:
        out: dict[str, Any] = {"results": [], "logs": [], "exit": "exception", "error": "usage: runner.py SCRIPT NAME"}
    else:
        out = run(sys.argv[1], sys.argv[2], sys.stdin.read())
    with contextlib.suppress(Exception):
        sys.stderr.flush()
    _write_line(proto_fd, out)
    # Integration threads must not keep the container alive after the answer is written.
    os._exit(0)


if __name__ == "__main__":
    main()
