import shutil
import subprocess
from pathlib import Path

import pytest

from dbot.catalog.index import parse_integration
from dbot.catalog.models import IntegrationDef

REPO = Path(__file__).resolve().parents[2]
CONTENT = REPO / "content"
REAL_CSP = CONTENT / "Packs" / "Base" / "Scripts" / "CommonServerPython" / "CommonServerPython.py"
PY312_IMAGE = "demisto/python3:3.12.12.7090913"


@pytest.fixture(autouse=True)
def cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cache = tmp_path / "assembled"
    monkeypatch.setenv("DBOT_CACHE_DIR", str(cache))
    monkeypatch.delenv("DBOT_CONTENT_COMMIT", raising=False)
    return cache


def make_content(
    root: Path, integration_src: str, *, csp: str | None = None, apimodules: dict[str, str] | None = None
) -> IntegrationDef:
    """Create a minimal content tree; `csp=None` links the real CommonServerPython."""
    csp_path = root / "Packs" / "Base" / "Scripts" / "CommonServerPython" / "CommonServerPython.py"
    csp_path.parent.mkdir(parents=True)
    if csp is None:
        csp_path.symlink_to(REAL_CSP)
        (root / "Packs" / "ApiModules").symlink_to(CONTENT / "Packs" / "ApiModules")
    else:
        csp_path.write_text(csp)
    for name, src in (apimodules or {}).items():
        p = root / "Packs" / "ApiModules" / "Scripts" / name / f"{name}.py"
        p.parent.mkdir(parents=True)
        p.write_text(src)
    py = root / "Packs" / "Test" / "Integrations" / "Test Int" / "TestInt.py"
    py.parent.mkdir(parents=True)
    py.write_text(integration_src)
    return IntegrationDef(pack="Test", name="Test Int", py_path=str(py), dockerimage=PY312_IMAGE)


def real_integration(rel_yml: str) -> IntegrationDef:
    parsed = parse_integration(CONTENT / rel_yml)
    assert parsed is not None
    return parsed[0]


def docker_available() -> bool:
    if not shutil.which("docker"):
        return False
    return subprocess.run(["docker", "info"], capture_output=True, check=False).returncode == 0  # noqa: S607
