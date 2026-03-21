import re
import sys
import types
import typing
from pathlib import Path

from dbot.runtime.common_server import bootstrap_common_modules
from dbot.runtime.demistomock import DemistoMock
from dbot.runtime.executor import execute_inprocess

pytest = __import__("pytest")


class _DemistoMockWithContext(DemistoMock):
    callingContext: typing.ClassVar[dict[str, typing.Any]] = {"context": {"IntegrationBrand": "HelloWorld"}}  # noqa: N815


CONTENT_ROOT = Path(__file__).parent.parent.parent / "content"
HELLOWORLD_PY = CONTENT_ROOT / "Packs" / "HelloWorld" / "Integrations" / "HelloWorld" / "HelloWorld.py"


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
    import dbot.runtime.demistomock as demistomock_module
    import dbot.runtime.executor as executor_module

    demistomock_module.DemistoMock = _DemistoMockWithContext
    executor_module.DemistoMock = _DemistoMockWithContext
    bootstrap_common_modules(CONTENT_ROOT)


class TestHelloWorld:
    @pytest.mark.asyncio
    async def test_say_hello(self) -> None:
        if not HELLOWORLD_PY.exists():
            pytest.skip("HelloWorld integration not found")
        result = await execute_inprocess(
            integration_py=HELLOWORLD_PY,
            command="helloworld-say-hello",
            args={"name": "World"},
            params={
                "url": "https://api.xsoar-example.com",
                "credentials": {"password": "dummy-key"},
                "insecure": True,
            },
        )
        assert result["success"] is True
        assert len(result["results"]) > 0

    @pytest.mark.asyncio
    async def test_executor_returns_structure(self) -> None:
        if not HELLOWORLD_PY.exists():
            pytest.skip("HelloWorld integration not found")
        result = await execute_inprocess(
            integration_py=HELLOWORLD_PY,
            command="helloworld-say-hello",
            args={"name": "Test"},
            params={
                "url": "https://api.xsoar-example.com",
                "credentials": {"password": "dummy-key"},
                "insecure": True,
            },
        )
        assert "success" in result
        assert "results" in result
        assert "logs" in result
