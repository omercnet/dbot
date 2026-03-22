"""Shared fixtures for config tests."""

from pathlib import Path

import pytest

from dbot.config.db import ConfigDB
from dbot.config.encryption import load_or_create_key


@pytest.fixture
def key_path(tmp_path: Path) -> Path:
    return tmp_path / ".dbot-key"


@pytest.fixture
def fernet_key(key_path: Path) -> bytes:
    return load_or_create_key(key_path)


@pytest.fixture
def config_db(tmp_path: Path) -> ConfigDB:
    db_path = tmp_path / "test.db"
    key_path = tmp_path / ".dbot-key"
    return ConfigDB(db_path, key_path)
