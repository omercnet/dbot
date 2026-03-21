import re
import sys
import types
from pathlib import Path

from dbot.runtime.common_server import bootstrap_common_modules

pytest = __import__("pytest")

CONTENT_ROOT = Path(__file__).parent.parent / "content"


def _ensure_distutils_shim() -> None:
    if "distutils.version" in sys.modules:
        return

    class LooseVersion:
        def __init__(self, version: str) -> None:
            self.version = str(version)

        def _key(self) -> tuple[object, ...]:
            parts = re.split(r"[._-]", self.version)
            return tuple(int(p) if p.isdigit() else p.lower() for p in parts)

        def __lt__(self, other: object) -> bool:
            if not isinstance(other, LooseVersion):
                return NotImplemented
            return self._key() < other._key()

        def __eq__(self, other: object) -> bool:
            if not isinstance(other, LooseVersion):
                return NotImplemented
            return self._key() == other._key()

    distutils_module = types.ModuleType("distutils")
    version_module = types.ModuleType("distutils.version")
    version_module.LooseVersion = LooseVersion
    distutils_module.version = version_module
    sys.modules["distutils"] = distutils_module
    sys.modules["distutils.version"] = version_module


def _ensure_demisto_class_api_module_shim() -> None:
    if "DemistoClassApiModule" not in sys.modules:
        sys.modules["DemistoClassApiModule"] = types.ModuleType("DemistoClassApiModule")


@pytest.fixture(scope="module", autouse=True)
def bootstrap() -> None:
    if not CONTENT_ROOT.exists():
        pytest.skip("content submodule not initialized")
    _ensure_distutils_shim()
    _ensure_demisto_class_api_module_shim()
    bootstrap_common_modules(CONTENT_ROOT)


class TestCommonServerPython:
    def test_module_in_sys_modules(self) -> None:
        assert "CommonServerPython" in sys.modules
        assert "demistomock" in sys.modules

    def test_base_client_accessible(self) -> None:
        common_server_python = sys.modules["CommonServerPython"]
        assert hasattr(common_server_python, "BaseClient")

    def test_command_results_accessible(self) -> None:
        common_server_python = sys.modules["CommonServerPython"]
        assert getattr(common_server_python, "CommandResults", None) is not None

    def test_return_results_callable(self) -> None:
        common_server_python = sys.modules["CommonServerPython"]
        assert callable(getattr(common_server_python, "return_results", None))

    def test_entry_types(self) -> None:
        common_server_python = sys.modules["CommonServerPython"]
        entry_type = getattr(common_server_python, "EntryType", None)
        assert entry_type is not None
        assert hasattr(entry_type, "NOTE")

    def test_user_python_stub(self) -> None:
        assert "CommonServerUserPython" in sys.modules
