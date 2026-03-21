"""Tests for the distutils.version LooseVersion shim."""

import sys

import pytest

from dbot.runtime.common_server import _shim_distutils


@pytest.fixture(autouse=True)
def _ensure_shim() -> None:
    # Force re-apply our shim (other test files may inject inferior versions)
    sys.modules.pop("distutils", None)
    sys.modules.pop("distutils.version", None)
    _shim_distutils()


class TestLooseVersionShim:
    def test_import_succeeds(self) -> None:
        from distutils.version import LooseVersion

        assert LooseVersion is not None

    def test_basic_version(self) -> None:
        from distutils.version import LooseVersion

        v = LooseVersion("1.2.3")
        assert str(v) == "1.2.3"

    def test_equality(self) -> None:
        from distutils.version import LooseVersion

        assert LooseVersion("1.0") == LooseVersion("1.0")
        assert LooseVersion("1.0") != LooseVersion("2.0")

    def test_comparison_lt(self) -> None:
        from distutils.version import LooseVersion

        assert LooseVersion("1.0") < LooseVersion("2.0")
        assert LooseVersion("1.9") < LooseVersion("1.10")
        assert not LooseVersion("2.0") < LooseVersion("1.0")

    def test_comparison_gt(self) -> None:
        from distutils.version import LooseVersion

        assert LooseVersion("2.0") > LooseVersion("1.0")
        assert LooseVersion("1.10") > LooseVersion("1.9")

    def test_comparison_le_ge(self) -> None:
        from distutils.version import LooseVersion

        assert LooseVersion("1.0") <= LooseVersion("1.0")
        assert LooseVersion("1.0") <= LooseVersion("2.0")
        assert LooseVersion("2.0") >= LooseVersion("2.0")
        assert LooseVersion("2.0") >= LooseVersion("1.0")

    def test_comparison_with_string(self) -> None:
        from distutils.version import LooseVersion

        assert LooseVersion("1.0") == "1.0"
        assert LooseVersion("1.0") < "2.0"
        assert LooseVersion("2.0") > "1.0"

    def test_complex_versions(self) -> None:
        from distutils.version import LooseVersion

        assert LooseVersion("1.0.0a1") < LooseVersion("1.0.0b1")
        assert LooseVersion("6.0.2") > LooseVersion("5.9.9")

    def test_repr(self) -> None:
        from distutils.version import LooseVersion

        v = LooseVersion("3.14.1")
        assert "3.14.1" in repr(v)

    def test_none_version(self) -> None:
        from distutils.version import LooseVersion

        v = LooseVersion(None)
        assert str(v) == "0"

    def test_distutils_in_sys_modules(self) -> None:
        assert "distutils" in sys.modules
        assert "distutils.version" in sys.modules
