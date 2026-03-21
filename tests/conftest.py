"""Shared test fixtures for dbot tests."""

from __future__ import annotations

from pathlib import Path

import pytest

CONTENT_ROOT = Path(__file__).parent.parent / "content"


@pytest.fixture(scope="session")
def content_root() -> Path:
    """Path to the demisto/content submodule root."""
    if not CONTENT_ROOT.exists():
        pytest.skip("content submodule not initialized")
    return CONTENT_ROOT
