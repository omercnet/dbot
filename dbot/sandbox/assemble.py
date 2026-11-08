"""Assemble an integration into one script, the way XSOAR runs it (C3).

XSOAR executes CommonServerPython, CommonServerUserPython, the inlined ApiModules and the integration
in a single namespace, so integrations rely on names they never import (e.g. `Tuple`). The result is
cached per content commit and integration, next to the in-container shim files.
"""

import hashlib
import os
import re
from pathlib import Path

from dbot.catalog.models import IntegrationDef

SHIM_DIR = Path(__file__).parent / "shim"
SHIM_FILES = ("runner.py", "demistomock.py")
SCRIPT_NAME = "integration.py"

TYPING_HEADER = (
    "from typing import *  # noqa: F403\n"
    "from typing import Any, Callable, Dict, Generator, Iterable, Iterator, List, Optional, Set, Tuple, Union, cast\n"
)
# CommonServerUserPython is XSOAR's hook for overriding CSP functions; dbot's only override keeps the
# sandbox egress proxy in effect even when an instance has its proxy checkbox off.
CSUP_STUB = """import os as _dbot_os

if _dbot_os.environ.get("HTTPS_PROXY"):

    def skip_proxy():
        return None
"""

_APIMODULE_IMPORT = re.compile(r"^from (\w+ApiModule) import \*")
_COMMON_IMPORT = re.compile(r"^(\s*)from (?:CommonServerPython|CommonServerUserPython) import \*")
_FUTURE_IMPORT = re.compile(r"^from __future__ import ([\w, ]+?)\s*(?:#.*)?$")


def cache_root() -> Path:
    return Path(os.environ.get("DBOT_CACHE_DIR") or Path.home() / ".cache" / "dbot" / "assembled")


def _read_git_commit(content_root: Path) -> str | None:
    """Read HEAD from content/.git (a dir, or a `gitdir:` file for submodules) without running git."""
    dot_git = content_root / ".git"
    try:
        if dot_git.is_file():
            target = dot_git.read_text().strip().removeprefix("gitdir:").strip()
            dot_git = (content_root / target).resolve()
        head = (dot_git / "HEAD").read_text().strip()
    except OSError:
        return None
    if not head.startswith("ref:"):
        return head or None
    ref = head.removeprefix("ref:").strip()
    try:
        return (dot_git / ref).read_text().strip()
    except OSError:
        pass
    try:
        for line in (dot_git / "packed-refs").read_text().splitlines():
            sha, _, name = line.partition(" ")
            if name == ref:
                return sha
    except OSError:
        pass
    return None


def content_version(content_root: Path, integration: IntegrationDef) -> str:
    """Content commit; else DBOT_CONTENT_COMMIT; else a hash of the relevant files' mtimes."""
    commit = _read_git_commit(content_root) or os.environ.get("DBOT_CONTENT_COMMIT")
    if commit:
        return commit[:12]
    paths = [_csp_path(content_root), Path(integration.py_path)]
    paths += sorted((content_root / "Packs" / "ApiModules" / "Scripts").glob("*/*ApiModule.py"))
    h = hashlib.sha256()
    for p in paths:
        try:
            h.update(f"{p}:{p.stat().st_mtime_ns}\n".encode())
        except OSError:
            h.update(f"{p}:missing\n".encode())
    return "mtime-" + h.hexdigest()[:12]


def _shim_version() -> str:
    h = hashlib.sha256(Path(__file__).read_bytes())
    for name in SHIM_FILES:
        h.update((SHIM_DIR / name).read_bytes())
    return h.hexdigest()[:8]


def _csp_path(content_root: Path) -> Path:
    return content_root / "Packs" / "Base" / "Scripts" / "CommonServerPython" / "CommonServerPython.py"


def _apimodule_path(content_root: Path, name: str) -> Path:
    return content_root / "Packs" / "ApiModules" / "Scripts" / name / f"{name}.py"


def _inline(source: str, content_root: Path, seen: set[str], futures: set[str]) -> str:
    """Replace ApiModule star imports with the module source (once each, transitively).

    Also drops CommonServerPython/CommonServerUserPython star imports (they are the same namespace)
    and hoists `from __future__` imports, which must come first in the assembled file.
    """
    out: list[str] = []
    for line in source.splitlines():
        if m := _FUTURE_IMPORT.match(line):
            futures.update(n.strip() for n in m.group(1).split(",") if n.strip())
            continue
        if m := _COMMON_IMPORT.match(line):
            if m.group(1):
                out.append(m.group(1) + "pass")
            continue
        if m := _APIMODULE_IMPORT.match(line):
            name = m.group(1)
            path = _apimodule_path(content_root, name)
            if name in seen:
                continue
            if path.is_file():
                seen.add(name)
                out.append(f"# --- begin {name} (inlined by dbot) ---")
                out.append(_inline(path.read_text(encoding="utf-8"), content_root, seen, futures))
                out.append(f"# --- end {name} ---")
                continue
            # Unknown module: leave the import so it fails as an import error inside the sandbox.
        out.append(line)
    return "\n".join(out)


def assemble_source(integration: IntegrationDef, content_root: Path) -> str:
    """Build the single-namespace script text for `integration`."""
    seen: set[str] = set()
    futures: set[str] = set()
    csp = _inline(_csp_path(content_root).read_text(encoding="utf-8"), content_root, seen, futures)
    body = _inline(Path(integration.py_path).read_text(encoding="utf-8"), content_root, seen, futures)
    head = "".join(f"from __future__ import {name}\n" for name in sorted(futures))
    return "\n".join(
        [
            head + TYPING_HEADER,
            "# --- CommonServerPython ---",
            csp,
            "# --- CommonServerUserPython ---",
            CSUP_STUB,
            f"# --- {integration.name} ---",
            body,
            "",
        ]
    )


def _safe_dirname(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]", "_", name)[:80]
    return f"{slug}-{hashlib.sha256(name.encode()).hexdigest()[:8]}"


def _write_atomic(path: Path, data: str) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(data, encoding="utf-8")
    tmp.chmod(0o644)
    tmp.replace(path)


def assemble(integration: IntegrationDef, content_root: Path) -> Path:
    """Write (or reuse) the assembled script plus shim files; return the script path.

    The script's directory is what the sandbox mounts at /dbot.
    """
    key = f"{content_version(content_root, integration)}-{_shim_version()}"
    target = cache_root() / key / _safe_dirname(integration.name)
    script = target / SCRIPT_NAME
    if script.is_file() and all((target / n).is_file() for n in SHIM_FILES):
        return script
    target.mkdir(parents=True, exist_ok=True)
    target.chmod(0o755)
    for name in SHIM_FILES:
        _write_atomic(target / name, (SHIM_DIR / name).read_text(encoding="utf-8"))
    _write_atomic(script, assemble_source(integration, content_root))
    return script
