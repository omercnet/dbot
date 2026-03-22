"""Tests for SystemExit handling in the execution engine.

Demisto integrations commonly call sys.exit() via return_error() / return_results().
The executor must catch SystemExit and still return captured results.
"""

import textwrap
from pathlib import Path

import pytest

from dbot.runtime.common_server import bootstrap_common_modules
from dbot.runtime.executor import _run_integration, execute_inprocess

CONTENT_ROOT = Path(__file__).parent.parent / "content"


@pytest.fixture(scope="module", autouse=True)
def _bootstrap() -> None:
    if not CONTENT_ROOT.exists():
        pytest.skip("content submodule not initialized")
    bootstrap_common_modules(CONTENT_ROOT)


def _write_integration(tmp_path: Path, code: str) -> Path:
    py = tmp_path / "test_int.py"
    py.write_text(textwrap.dedent(code))
    return py


class TestRunIntegrationSystemExit:
    def test_exit_zero_with_results_is_success(self, tmp_path: Path) -> None:
        """sys.exit(0) after results should be treated as success."""
        py = _write_integration(
            tmp_path,
            """
            import sys
            import demistomock as demisto
            def main():
                demisto.results({"Contents": "done"})
                sys.exit(0)
            main()
        """,
        )
        from dbot.runtime.demistomock import DemistoMock

        mock = DemistoMock(command="test", args={}, params={})
        result = _run_integration(py, mock)
        assert result["success"] is True
        assert len(result["results"]) > 0

    def test_exit_with_error_results_is_failure(self, tmp_path: Path) -> None:
        """sys.exit() after an error result should be treated as failure."""
        py = _write_integration(
            tmp_path,
            """
            import sys
            import demistomock as demisto
            def main():
                demisto.results({"Type": 4, "Contents": "Error: something failed"})
                sys.exit(1)
            main()
        """,
        )
        from dbot.runtime.demistomock import DemistoMock

        mock = DemistoMock(command="test", args={}, params={})
        result = _run_integration(py, mock)
        assert result["success"] is False

    def test_exit_no_results_is_success(self, tmp_path: Path) -> None:
        """sys.exit(0) with no results — no error text, treat as success."""
        py = _write_integration(
            tmp_path,
            """
            import sys
            def main():
                sys.exit(0)
            main()
        """,
        )
        from dbot.runtime.demistomock import DemistoMock

        mock = DemistoMock(command="test", args={}, params={})
        result = _run_integration(py, mock)
        # No error text in results → success
        assert result["success"] is True


class TestExecuteInprocessSystemExit:
    @pytest.mark.asyncio
    async def test_systemexit_caught_not_crash(self, tmp_path: Path) -> None:
        """execute_inprocess must never propagate SystemExit to caller."""
        py = _write_integration(
            tmp_path,
            """
            import sys
            import demistomock as demisto
            def main():
                demisto.results({"Contents": "before exit"})
                sys.exit(1)
            main()
        """,
        )
        result = await execute_inprocess(py, command="test", args={}, params={})
        # Should return a result dict, NOT raise SystemExit
        assert isinstance(result, dict)
        assert "success" in result

    @pytest.mark.asyncio
    async def test_systemexit_returns_captured_results(self, tmp_path: Path) -> None:
        """Results captured before SystemExit should be in the response."""
        py = _write_integration(
            tmp_path,
            """
            import sys
            import demistomock as demisto
            def main():
                demisto.results({"Contents": "captured before exit"})
                sys.exit(0)
            main()
        """,
        )
        result = await execute_inprocess(py, command="test", args={}, params={})
        assert "captured before exit" in str(result["results"])

    @pytest.mark.asyncio
    async def test_systemexit_at_module_level_caught(self, tmp_path: Path) -> None:
        """sys.exit() at module level (no main) should still return a dict, not crash."""
        py = _write_integration(
            tmp_path,
            """
            import sys
            sys.exit(42)
        """,
        )
        result = await execute_inprocess(py, command="test", args={}, params={})
        # Must return a dict (not propagate SystemExit)
        assert isinstance(result, dict)
        assert "success" in result
