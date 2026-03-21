"""Tests for the execution engine — in-process and subprocess modes."""

import asyncio
import textwrap
from pathlib import Path

import pytest

from dbot.runtime.common_server import bootstrap_common_modules
from dbot.runtime.executor import execute_inprocess, execute_subprocess

CONTENT_ROOT = Path(__file__).parent.parent / "content"


@pytest.fixture(scope="module", autouse=True)
def _bootstrap() -> None:
    if not CONTENT_ROOT.exists():
        pytest.skip("content submodule not initialized")
    bootstrap_common_modules(CONTENT_ROOT)


def _write_integration(tmp_path: Path, code: str, name: str = "test_int") -> Path:
    py = tmp_path / f"{name}.py"
    py.write_text(textwrap.dedent(code))
    return py


class TestExecuteInprocess:
    @pytest.mark.asyncio
    async def test_simple_integration(self, tmp_path: Path) -> None:
        py = _write_integration(
            tmp_path,
            """
            import demistomock as demisto
            def main():
                demisto.results({"Type": 1, "Contents": f"Hello {demisto.args().get('name', 'World')}"})
            if __name__ in ('__main__', '__builtin__', 'builtins'):
                main()
        """,
        )
        result = await execute_inprocess(py, command="test", args={"name": "dbot"}, params={})
        assert result["success"] is True
        assert len(result["results"]) > 0
        assert "dbot" in str(result["results"])

    @pytest.mark.asyncio
    async def test_captures_multiple_results(self, tmp_path: Path) -> None:
        py = _write_integration(
            tmp_path,
            """
            import demistomock as demisto
            def main():
                demisto.results({"Contents": "first"})
                demisto.results({"Contents": "second"})
        """,
        )
        result = await execute_inprocess(py, command="test", args={}, params={})
        assert result["success"] is True
        assert len(result["results"]) == 2

    @pytest.mark.asyncio
    async def test_captures_logs(self, tmp_path: Path) -> None:
        py = _write_integration(
            tmp_path,
            """
            import demistomock as demisto
            def main():
                demisto.info("info message")
                demisto.error("error message")
                demisto.results({"Contents": "ok"})
        """,
        )
        result = await execute_inprocess(py, command="test", args={}, params={})
        assert result["success"] is True
        assert len(result["logs"]) >= 2

    @pytest.mark.asyncio
    async def test_params_accessible(self, tmp_path: Path) -> None:
        py = _write_integration(
            tmp_path,
            """
            import demistomock as demisto
            def main():
                key = demisto.params().get("apikey", "none")
                demisto.results({"Contents": f"key={key}"})
        """,
        )
        result = await execute_inprocess(py, command="test", args={}, params={"apikey": "s3cret"})
        assert result["success"] is True
        assert "s3cret" in str(result["results"])

    @pytest.mark.asyncio
    async def test_command_routing(self, tmp_path: Path) -> None:
        py = _write_integration(
            tmp_path,
            """
            import demistomock as demisto
            def main():
                cmd = demisto.command()
                demisto.results({"Contents": f"ran:{cmd}"})
        """,
        )
        result = await execute_inprocess(py, command="my-command", args={}, params={})
        assert "ran:my-command" in str(result["results"])

    @pytest.mark.asyncio
    async def test_missing_main_still_succeeds(self, tmp_path: Path) -> None:
        py = _write_integration(
            tmp_path,
            """
            x = 42  # no main function
        """,
        )
        result = await execute_inprocess(py, command="test", args={}, params={})
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_integration_exception_captured(self, tmp_path: Path) -> None:
        py = _write_integration(
            tmp_path,
            """
            def main():
                raise ValueError("intentional error")
        """,
        )
        result = await execute_inprocess(py, command="test", args={}, params={})
        assert result["success"] is False
        assert "intentional error" in result["error"]
        assert result["error_type"] == "ValueError"

    @pytest.mark.asyncio
    async def test_import_error_captured(self, tmp_path: Path) -> None:
        py = _write_integration(
            tmp_path,
            """
            import nonexistent_module_xyz123
            def main(): pass
        """,
        )
        result = await execute_inprocess(py, command="test", args={}, params={})
        assert result["success"] is False
        assert "nonexistent_module_xyz123" in result["error"]

    @pytest.mark.asyncio
    async def test_timeout_returns_error(self, tmp_path: Path) -> None:
        py = _write_integration(
            tmp_path,
            """
            import time
            def main():
                time.sleep(10)
        """,
        )
        result = await execute_inprocess(py, command="test", args={}, params={}, timeout=0.5)
        assert result["success"] is False
        assert "Timeout" in result["error"]

    @pytest.mark.asyncio
    async def test_nonexistent_file_fails(self) -> None:
        result = await execute_inprocess(Path("/nonexistent/integration.py"), command="test", args={}, params={})
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_concurrent_executions_isolated(self, tmp_path: Path) -> None:
        py = _write_integration(
            tmp_path,
            """
            import demistomock as demisto
            def main():
                name = demisto.args().get("name")
                demisto.results({"Contents": name})
        """,
        )
        results = await asyncio.gather(
            execute_inprocess(py, "test", {"name": "alice"}, {}),
            execute_inprocess(py, "test", {"name": "bob"}, {}),
            execute_inprocess(py, "test", {"name": "charlie"}, {}),
        )
        names = {str(r["results"]) for r in results}
        assert any("alice" in n for n in names)
        assert any("bob" in n for n in names)
        assert any("charlie" in n for n in names)


class TestExecuteSubprocess:
    @pytest.mark.asyncio
    async def test_simple_subprocess(self, tmp_path: Path) -> None:
        py = _write_integration(
            tmp_path,
            """
            import demistomock as demisto
            def main():
                demisto.results({"Contents": f"hello {demisto.args().get('name')}"})
            if __name__ in ('__main__', '__builtin__', 'builtins'):
                main()
        """,
        )
        result = await execute_subprocess(
            py, command="test", args={"name": "subprocess"}, params={}, content_root=CONTENT_ROOT
        )
        assert result["success"] is True
        assert "subprocess" in str(result["results"])

    @pytest.mark.asyncio
    async def test_subprocess_timeout(self, tmp_path: Path) -> None:
        py = _write_integration(
            tmp_path,
            """
            import time
            def main():
                time.sleep(30)
        """,
        )
        result = await execute_subprocess(
            py, command="test", args={}, params={}, timeout=1.0, content_root=CONTENT_ROOT
        )
        assert result["success"] is False
        assert "Timeout" in result["error"]

    @pytest.mark.asyncio
    async def test_subprocess_crash_isolated(self, tmp_path: Path) -> None:
        py = _write_integration(
            tmp_path,
            """
            def main():
                raise RuntimeError("crash!")
        """,
        )
        result = await execute_subprocess(py, command="test", args={}, params={}, content_root=CONTENT_ROOT)
        assert result["success"] is False
        assert "crash!" in str(result.get("error", ""))

    @pytest.mark.asyncio
    async def test_subprocess_nonexistent_file(self) -> None:
        result = await execute_subprocess(
            Path("/nonexistent.py"), command="test", args={}, params={}, content_root=CONTENT_ROOT
        )
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_subprocess_params_injected(self, tmp_path: Path) -> None:
        py = _write_integration(
            tmp_path,
            """
            import demistomock as demisto
            def main():
                demisto.results({"Contents": demisto.params().get("secret")})
            if __name__ in ('__main__', '__builtin__', 'builtins'):
                main()
        """,
        )
        result = await execute_subprocess(
            py, command="test", args={}, params={"secret": "injected"}, content_root=CONTENT_ROOT
        )
        assert result["success"] is True
        assert "injected" in str(result["results"])
