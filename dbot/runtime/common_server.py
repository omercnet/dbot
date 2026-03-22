"""CommonServerPython bootstrap loader."""

import importlib.util
import logging
import sys
import types
from pathlib import Path

logger = logging.getLogger("dbot.common_server")

_bootstrapped = False


def bootstrap_common_modules(content_root: Path) -> None:
    """Load CommonServerPython and CommonServerUserPython into sys.modules.

    Must be called ONCE at startup, AFTER demistomock is set up in sys.modules.
    Idempotent — skips if already bootstrapped.

    The order matters:
    1. dbot's demistomock goes into sys.modules['demistomock']
    2. CommonServerUserPython (empty stub) goes into sys.modules
    3. CommonServerPython is loaded (it imports demistomock and CommonServerUserPython)
    """
    global _bootstrapped
    if _bootstrapped:
        logger.debug("CommonServerPython already bootstrapped, skipping")
        return

    # Step 1: Inject our demistomock as THE demistomock
    from dbot.runtime import demistomock

    sys.modules["demistomock"] = demistomock

    # Step 2: Create empty CommonServerUserPython stub
    if "CommonServerUserPython" not in sys.modules:
        user_python = types.ModuleType("CommonServerUserPython")
        user_python.__doc__ = "Empty stub — dbot does not support org-level overrides"
        sys.modules["CommonServerUserPython"] = user_python

    # Step 2.1: Stub DemistoClassApiModule (XSOAR internal, not needed standalone)
    if "DemistoClassApiModule" not in sys.modules:
        api_mod = types.ModuleType("DemistoClassApiModule")
        sys.modules["DemistoClassApiModule"] = api_mod
    # Step 2.5: Shim distutils.version (removed in Python 3.12+)
    # CommonServerPython imports `from distutils.version import LooseVersion`
    if "distutils" not in sys.modules:
        _shim_distutils()

    # Step 3: Load the real CommonServerPython
    if "CommonServerPython" not in sys.modules:
        csp_path = content_root / "Packs" / "Base" / "Scripts" / "CommonServerPython" / "CommonServerPython.py"
        if not csp_path.exists():
            raise FileNotFoundError(
                f"CommonServerPython.py not found at {csp_path}. "
                f"Did you initialize the content submodule? "
                f"Run: git submodule update --init"
            )

        spec = importlib.util.spec_from_file_location("CommonServerPython", str(csp_path))
        if spec is None or spec.loader is None:
            raise ImportError(f"Could not create module spec for {csp_path}")

        module = importlib.util.module_from_spec(spec)
        sys.modules["CommonServerPython"] = module
        spec.loader.exec_module(module)
        logger.info("CommonServerPython loaded from %s", csp_path)

    _bootstrapped = True


def _shim_distutils() -> None:
    """Create a minimal distutils.version shim for Python 3.12+."""
    try:
        from distutils.version import LooseVersion as _LV  # type: ignore[import-not-found]  # noqa: F401, N814

        return  # distutils exists, no shim needed
    except ImportError:
        pass

    # Create distutils and distutils.version as fake modules
    distutils_mod = types.ModuleType("distutils")
    distutils_mod.__path__ = []
    sys.modules["distutils"] = distutils_mod

    version_mod = types.ModuleType("distutils.version")

    class LooseVersion:
        """Minimal LooseVersion shim."""

        def __init__(self, vstring: str | None = None) -> None:
            self.vstring = vstring or "0"
            self._parts = self._parse(self.vstring)

        @staticmethod
        def _parse(s: str) -> list[str | int]:
            import re

            parts: list[str | int] = []
            for chunk in re.split(r"(\d+)", s):
                if chunk.isdigit():
                    parts.append(int(chunk))
                elif chunk:
                    parts.append(chunk)
            return parts

        def __str__(self) -> str:
            return self.vstring

        def __repr__(self) -> str:
            return f"LooseVersion('{self.vstring}')"

        def __eq__(self, other: object) -> bool:
            if isinstance(other, str):
                other = LooseVersion(other)
            if not isinstance(other, LooseVersion):
                return NotImplemented
            return self._parts == other._parts

        def __lt__(self, other: object) -> bool:
            if isinstance(other, str):
                other = LooseVersion(other)
            if not isinstance(other, LooseVersion):
                return NotImplemented
            return self._cmp(other) < 0

        def __le__(self, other: object) -> bool:
            if isinstance(other, str):
                other = LooseVersion(other)
            if not isinstance(other, LooseVersion):
                return NotImplemented
            return self._cmp(other) <= 0

        def __gt__(self, other: object) -> bool:
            if isinstance(other, str):
                other = LooseVersion(other)
            if not isinstance(other, LooseVersion):
                return NotImplemented
            return self._cmp(other) > 0

        def __ge__(self, other: object) -> bool:
            if isinstance(other, str):
                other = LooseVersion(other)
            if not isinstance(other, LooseVersion):
                return NotImplemented
            return self._cmp(other) >= 0

        def _cmp(self, other: "LooseVersion") -> int:
            for a, b in zip(self._parts, other._parts, strict=False):
                if type(a) is type(b):
                    if a < b:  # type: ignore[operator]
                        return -1
                    if a > b:  # type: ignore[operator]
                        return 1
                else:
                    a_str, b_str = str(a), str(b)
                    if a_str < b_str:
                        return -1
                    if a_str > b_str:
                        return 1
            len_diff = len(self._parts) - len(other._parts)
            if len_diff:
                return 1 if len_diff > 0 else -1
            return 0

    version_mod.LooseVersion = LooseVersion  # type: ignore[attr-defined]
    sys.modules["distutils.version"] = version_mod
    distutils_mod.version = version_mod  # type: ignore[attr-defined]
