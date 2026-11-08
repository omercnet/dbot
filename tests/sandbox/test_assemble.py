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


def test_cache_reused_and_keyed_by_source(tmp_path: Path, cache_dir: Path) -> None:
    root = tmp_path / "content"
    integ = make_content(root, "X = 1\n", csp="CSP = 1\n")

    first = asm.assemble(integ, root)
    assert first.is_relative_to(cache_dir)
    assert {p.name for p in first.parent.iterdir()} == {"integration.py", "runner.py", "demistomock.py"}
    os.utime(first, (1, 1))
    assert asm.assemble(integ, root) == first
    assert first.stat().st_mtime == 1  # cache hit: not rewritten

    # Same mtime, same size, new bytes: still a new key, and the new code is what runs.
    st = Path(integ.py_path).stat()
    Path(integ.py_path).write_text("X = 2\n")
    os.utime(integ.py_path, ns=(st.st_atime_ns, st.st_mtime_ns))
    second = asm.assemble(integ, root)
    assert second != first
    assert "X = 2" in second.read_text()


@pytest.mark.parametrize("part", ["integration", "csp", "apimodule", "nested apimodule"])
def test_source_digest_covers_every_input(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, part: str) -> None:
    monkeypatch.setenv("DBOT_CONTENT_COMMIT", "f" * 40)  # ignored: the key is the bytes, not a commit label
    integ = make_content(tmp_path, INTEGRATION, csp=FAKE_CSP, apimodules=APIMODULES)
    before = asm.source_digest(integ, tmp_path)
    target = {
        "integration": Path(integ.py_path),
        "csp": tmp_path / "Packs" / "Base" / "Scripts" / "CommonServerPython" / "CommonServerPython.py",
        "apimodule": tmp_path / "Packs" / "ApiModules" / "Scripts" / "MidApiModule" / "MidApiModule.py",
        "nested apimodule": tmp_path / "Packs" / "ApiModules" / "Scripts" / "BaseApiModule" / "BaseApiModule.py",
    }[part]
    target.write_text(target.read_text() + "\n# patched\n")
    assert asm.source_digest(integ, tmp_path) != before


def test_source_digest_ignores_unrelated_apimodules(tmp_path: Path) -> None:
    integ = make_content(tmp_path, INTEGRATION, csp=FAKE_CSP, apimodules={**APIMODULES, "OtherApiModule": "O = 1\n"})
    before = asm.source_digest(integ, tmp_path)
    other = tmp_path / "Packs" / "ApiModules" / "Scripts" / "OtherApiModule" / "OtherApiModule.py"
    other.write_text("O = 2\n")
    assert asm.source_digest(integ, tmp_path) == before


def test_integration_names_with_spaces_get_distinct_safe_dirs() -> None:
    a, b = asm._safe_dirname("Active Directory: v2"), asm._safe_dirname("Active Directory_ v2")
    assert a != b
    assert " " not in a
    assert ":" not in a
