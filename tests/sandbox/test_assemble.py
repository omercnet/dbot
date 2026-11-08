import os
from pathlib import Path

import pytest

from dbot.sandbox import assemble as asm
from tests.sandbox.conftest import make_content

FAKE_CSP = """\"\"\"CSP docstring.\"\"\"
from __future__ import print_function
import demistomock as demisto
from DemistoClassApiModule import *  # type: ignore
CSP_MARK = 1
"""

APIMODULES = {
    "DemistoClassApiModule": "DEMISTO_CLASS = 1\n",
    "BaseApiModule": "from CommonServerPython import *\nBASE_MARK = 1\n",
    "MidApiModule": "from __future__ import annotations\nfrom BaseApiModule import *  # noqa\nMID_MARK = BASE_MARK + 1\n",  # noqa: E501
}

INTEGRATION = """import demistomock as demisto
from CommonServerPython import *
from CommonServerUserPython import *
from MidApiModule import *
from BaseApiModule import *
from MissingApiModule import *
try:
    from CommonServerPython import *
except ImportError:
    pass
X: Tuple[int, int] = (MID_MARK, BASE_MARK)
PAIR = Tuple[int, int]
"""


def test_apimodules_inlined_transitively_once_in_order(tmp_path: Path) -> None:
    integ = make_content(tmp_path, INTEGRATION, csp=FAKE_CSP, apimodules=APIMODULES)
    src = asm.assemble_source(integ, tmp_path)

    # Transitive: BaseApiModule is pulled in by MidApiModule, before Mid's own code, and only once.
    assert src.count("BASE_MARK = 1") == 1
    assert src.index("BASE_MARK = 1") < src.index("MID_MARK = BASE_MARK + 1")
    # CSP's own ApiModule import is inlined too, and CSP precedes the integration.
    assert src.count("DEMISTO_CLASS = 1") == 1
    assert src.index("CSP_MARK") < src.index("X: Tuple")
    # Star imports of the shared namespace and already-inlined modules are gone; unknown ones stay.
    assert "from CommonServerPython import *" not in src
    assert "from CommonServerUserPython import *" not in src
    assert "from BaseApiModule import" not in src
    assert "from MissingApiModule import *" in src
    # __future__ imports from every part are hoisted to the top, so the file compiles.
    lines = src.splitlines()
    assert lines[:2] == ["from __future__ import annotations", "from __future__ import print_function"]
    assert lines[2].startswith("from typing import *")
    assert src.count("from __future__") == 2
    compile(src, "assembled.py", "exec")


def test_assembled_namespace_runs(tmp_path: Path) -> None:
    """Names from typing and inlined modules resolve without the integration importing them."""
    integ = make_content(
        tmp_path, INTEGRATION.replace("from MissingApiModule import *\n", ""), csp=FAKE_CSP, apimodules=APIMODULES
    )
    src = asm.assemble_source(integ, tmp_path).replace("import demistomock as demisto", "demisto = None")
    ns: dict[str, object] = {}
    exec(compile(src, "assembled.py", "exec"), ns)  # noqa: S102
    assert ns["X"] == (2, 1)


def _git_dir(root: Path, head: str) -> Path:
    git = root / ".git"
    git.mkdir(parents=True)
    (git / "HEAD").write_text(head + "\n")
    return git


def test_commit_read_from_git_dir_ref_and_packed_refs(tmp_path: Path) -> None:
    git = _git_dir(tmp_path / "a", "ref: refs/heads/master")
    (git / "refs" / "heads").mkdir(parents=True)
    (git / "refs" / "heads" / "master").write_text("a" * 40 + "\n")
    assert asm._read_git_commit(tmp_path / "a") == "a" * 40

    git = _git_dir(tmp_path / "b", "ref: refs/heads/main")
    (git / "packed-refs").write_text(f"# pack-refs\n{'b' * 40} refs/heads/main\n")
    assert asm._read_git_commit(tmp_path / "b") == "b" * 40


def test_commit_read_from_submodule_gitdir_file(tmp_path: Path) -> None:
    gitdir = tmp_path / "modules" / "content"
    gitdir.mkdir(parents=True)
    (gitdir / "HEAD").write_text("c" * 40 + "\n")
    (tmp_path / "content").mkdir()
    (tmp_path / "content" / ".git").write_text("gitdir: ../modules/content\n")
    assert asm._read_git_commit(tmp_path / "content") == "c" * 40


def test_cache_reused_and_keyed_by_commit(tmp_path: Path, cache_dir: Path) -> None:
    root = tmp_path / "content"
    integ = make_content(root, "X = 1\n", csp="CSP = 1\n")
    git = _git_dir(root, "d" * 40)

    first = asm.assemble(integ, root)
    assert first.is_relative_to(cache_dir)
    assert {p.name for p in first.parent.iterdir()} == {"integration.py", "runner.py", "demistomock.py"}
    os.utime(first, (1, 1))
    assert asm.assemble(integ, root) == first
    assert first.stat().st_mtime == 1  # cache hit: not rewritten

    (git / "HEAD").write_text("e" * 40)
    second = asm.assemble(integ, root)
    assert second != first
    assert "eeeeeeeeeeee" in str(second)


def test_cache_key_falls_back_to_env_then_mtimes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    integ = make_content(tmp_path, "X = 1\n", csp="CSP = 1\n")
    monkeypatch.setenv("DBOT_CONTENT_COMMIT", "f" * 40)
    assert asm.content_version(tmp_path, integ) == "f" * 12

    monkeypatch.delenv("DBOT_CONTENT_COMMIT")
    before = asm.content_version(tmp_path, integ)
    assert before.startswith("mtime-")
    st = Path(integ.py_path).stat()
    os.utime(integ.py_path, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))
    assert asm.content_version(tmp_path, integ) != before


def test_integration_names_with_spaces_get_distinct_safe_dirs() -> None:
    a, b = asm._safe_dirname("Active Directory: v2"), asm._safe_dirname("Active Directory_ v2")
    assert a != b
    assert " " not in a
    assert ":" not in a
