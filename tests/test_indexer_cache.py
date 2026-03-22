"""Tests for the index caching layer — hash, load, save, and full cache flow."""

import json
from pathlib import Path
from unittest.mock import patch

from dbot.registry.indexer import (
    _get_content_hash,
    _load_cache,
    _save_cache,
    _walk_and_parse,
    index_content,
)
from dbot.registry.models import CommandDef, IntegrationDef


def _make_integration(pack: str = "TestPack", name: str = "TestInt") -> IntegrationDef:
    return IntegrationDef(
        pack=pack,
        name=name,
        category="Utilities",
        py_path="/tmp/fake.py",
        commands=[CommandDef(name="test-cmd", description="test")],
    )


class TestGetContentHash:
    def test_returns_hash_for_git_repo(self, tmp_path: Path) -> None:
        """If the directory is a git repo, return the HEAD hash."""
        # Use the real dbot repo root (which is a git repo)
        repo_root = Path(__file__).parent.parent
        result = _get_content_hash(repo_root)
        assert result is not None
        # Git SHA-1 hashes are 40 hex chars
        assert len(result) == 40
        assert all(c in "0123456789abcdef" for c in result)

    def test_returns_none_for_non_git_dir(self, tmp_path: Path) -> None:
        """A directory that is not itself a git repo (mock to isolate from parent)."""
        with patch("dbot.registry.indexer.subprocess.run", side_effect=FileNotFoundError):
            result = _get_content_hash(tmp_path)
            assert result is None

    def test_returns_none_for_nonexistent_dir(self) -> None:
        result = _get_content_hash(Path("/nonexistent/path"))
        assert result is None


class TestLoadCache:
    def test_returns_none_when_no_file(self, tmp_path: Path) -> None:
        result = _load_cache(tmp_path / "nope.json", "abc123")
        assert result is None

    def test_returns_none_on_hash_mismatch(self, tmp_path: Path) -> None:
        cache_path = tmp_path / "cache.json"
        integration = _make_integration()
        data = {
            "content_hash": "old-hash",
            "integrations": [integration.model_dump()],
        }
        cache_path.write_text(json.dumps(data))

        result = _load_cache(cache_path, "new-hash")
        assert result is None

    def test_returns_integrations_on_hash_match(self, tmp_path: Path) -> None:
        cache_path = tmp_path / "cache.json"
        integration = _make_integration()
        data = {
            "content_hash": "abc123",
            "integrations": [integration.model_dump()],
        }
        cache_path.write_text(json.dumps(data))

        result = _load_cache(cache_path, "abc123")
        assert result is not None
        assert len(result) == 1
        assert result[0].pack == "TestPack"
        assert result[0].commands[0].name == "test-cmd"

    def test_returns_none_on_corrupt_json(self, tmp_path: Path) -> None:
        cache_path = tmp_path / "cache.json"
        cache_path.write_text("not valid json{{{")

        result = _load_cache(cache_path, "abc123")
        assert result is None

    def test_returns_none_on_invalid_schema(self, tmp_path: Path) -> None:
        cache_path = tmp_path / "cache.json"
        cache_path.write_text(json.dumps({"content_hash": "abc123", "integrations": [{"bad": "data"}]}))

        result = _load_cache(cache_path, "abc123")
        assert result is None


class TestSaveCache:
    def test_creates_file(self, tmp_path: Path) -> None:
        cache_path = tmp_path / "config" / "cache.json"
        integration = _make_integration()

        _save_cache(cache_path, "abc123", [integration])

        assert cache_path.exists()
        data = json.loads(cache_path.read_text())
        assert data["content_hash"] == "abc123"
        assert len(data["integrations"]) == 1

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        cache_path = tmp_path / "deep" / "nested" / "cache.json"
        _save_cache(cache_path, "abc123", [_make_integration()])
        assert cache_path.exists()

    def test_roundtrip_preserves_data(self, tmp_path: Path) -> None:
        cache_path = tmp_path / "cache.json"
        original = _make_integration(pack="RoundTrip", name="RTInt")

        _save_cache(cache_path, "hash1", [original])
        loaded = _load_cache(cache_path, "hash1")

        assert loaded is not None
        assert loaded[0].pack == "RoundTrip"
        assert loaded[0].name == "RTInt"
        assert loaded[0].commands[0].name == "test-cmd"


class TestWalkAndParse:
    def test_empty_packs_dir(self, tmp_path: Path) -> None:
        packs = tmp_path / "Packs"
        packs.mkdir()
        result = _walk_and_parse(packs)
        assert result == []


class TestIndexContentCaching:
    def test_missing_packs_returns_empty(self, tmp_path: Path) -> None:
        result = index_content(tmp_path)
        assert result == []

    def test_cache_written_on_first_index(self, tmp_path: Path) -> None:
        """With a git hash available, first index should write cache."""
        content_root = tmp_path / "content"
        packs = content_root / "Packs"
        packs.mkdir(parents=True)
        config_dir = tmp_path / "config"
        config_dir.mkdir()

        with patch("dbot.registry.indexer._get_content_hash", return_value="fakehash"):
            index_content(content_root)

        cache_path = config_dir / ".index_cache.json"
        assert cache_path.exists()
        data = json.loads(cache_path.read_text())
        assert data["content_hash"] == "fakehash"

    def test_cache_hit_skips_walk(self, tmp_path: Path) -> None:
        """Second call with same hash should use cache, not re-walk."""
        content_root = tmp_path / "content"
        packs = content_root / "Packs"
        packs.mkdir(parents=True)
        config_dir = tmp_path / "config"
        config_dir.mkdir()

        # Pre-populate cache
        integration = _make_integration()
        cache_data = {
            "content_hash": "fakehash",
            "integrations": [integration.model_dump()],
        }
        cache_path = config_dir / ".index_cache.json"
        cache_path.write_text(json.dumps(cache_data))

        with (
            patch("dbot.registry.indexer._get_content_hash", return_value="fakehash"),
            patch("dbot.registry.indexer._walk_and_parse") as mock_walk,
        ):
            result = index_content(content_root)

        mock_walk.assert_not_called()
        assert len(result) == 1
        assert result[0].pack == "TestPack"

    def test_stale_cache_triggers_reindex(self, tmp_path: Path) -> None:
        """When hash changes, cache should be invalidated."""
        content_root = tmp_path / "content"
        packs = content_root / "Packs"
        packs.mkdir(parents=True)
        config_dir = tmp_path / "config"
        config_dir.mkdir()

        # Pre-populate cache with old hash
        cache_data = {
            "content_hash": "old-hash",
            "integrations": [_make_integration().model_dump()],
        }
        cache_path = config_dir / ".index_cache.json"
        cache_path.write_text(json.dumps(cache_data))

        with (
            patch("dbot.registry.indexer._get_content_hash", return_value="new-hash"),
            patch("dbot.registry.indexer._walk_and_parse", return_value=[]) as mock_walk,
        ):
            result = index_content(content_root)

        mock_walk.assert_called_once()
        assert result == []

    def test_enabled_packs_filter(self, tmp_path: Path) -> None:
        """enabled_packs should filter cached results."""
        content_root = tmp_path / "content"
        packs = content_root / "Packs"
        packs.mkdir(parents=True)
        config_dir = tmp_path / "config"
        config_dir.mkdir()

        integrations = [_make_integration(pack="Keep"), _make_integration(pack="Drop", name="DropInt")]
        cache_data = {
            "content_hash": "hash1",
            "integrations": [i.model_dump() for i in integrations],
        }
        cache_path = config_dir / ".index_cache.json"
        cache_path.write_text(json.dumps(cache_data))

        with patch("dbot.registry.indexer._get_content_hash", return_value="hash1"):
            result = index_content(content_root, enabled_packs=["Keep"])

        assert len(result) == 1
        assert result[0].pack == "Keep"

    def test_no_git_hash_skips_cache(self, tmp_path: Path) -> None:
        """Without a git hash, should walk every time (no caching)."""
        content_root = tmp_path / "content"
        packs = content_root / "Packs"
        packs.mkdir(parents=True)

        with (
            patch("dbot.registry.indexer._get_content_hash", return_value=None),
            patch("dbot.registry.indexer._walk_and_parse", return_value=[]) as mock_walk,
        ):
            index_content(content_root)

        mock_walk.assert_called_once()
        # No cache file should be written
        config_dir = tmp_path / "config"
        cache_path = config_dir / ".index_cache.json"
        assert not cache_path.exists()
