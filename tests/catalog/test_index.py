"""Indexer: field extraction, default filters, ApiModule parsing and cache invalidation."""

from pathlib import Path
from typing import Any

import pytest
import yaml  # type: ignore[import-untyped]

from dbot.catalog import index as index_mod
from dbot.catalog.index import index_content, parse_apimodules


def _write_integration(
    root: Path,
    name: str,
    *,
    pack: str = "Pack",
    script_type: str = "python",
    subtype: str | None = "python3",
    deprecated: bool = False,
    display: str | None = None,
    source: str = "def main():\n    pass\n",
    commands: list[dict[str, Any]] | None = None,
) -> Path:
    int_dir = root / "Packs" / pack / "Integrations" / name
    int_dir.mkdir(parents=True, exist_ok=True)
    script: dict[str, Any] = {
        "type": script_type,
        "dockerimage": "demisto/python3:3.12.12.7090913",
        "commands": [{"name": f"{name.lower()}-get"}] if commands is None else commands,
        "script": "-",
    }
    if subtype is not None:
        script["subtype"] = subtype
    data: dict[str, Any] = {"name": name, "display": display or name, "script": script}
    if deprecated:
        data["deprecated"] = True
    yml = int_dir / f"{name}.yml"
    yml.write_text(yaml.safe_dump(data), encoding="utf-8")
    (int_dir / f"{name}.py").write_text(source, encoding="utf-8")
    return yml


class TestParseApimodules:
    def test_order_and_dedupe(self) -> None:
        src = (
            "import demistomock as demisto\n"
            "from MicrosoftApiModule import *  # noqa: E402\n"
            "from COOCApiModule import *\n"
            "from MicrosoftApiModule import *\n"
        )
        assert parse_apimodules(src) == ["MicrosoftApiModule", "COOCApiModule"]

    def test_ignores_non_star_indented_and_commented_imports(self) -> None:
        src = (
            "from MicrosoftApiModule import MicrosoftClient\n"
            "    from IAMApiModule import *\n"
            "# from AWSApiModule import *\n"
            "from CommonServerPython import *\n"
            "from SomethingElse import *\n"
        )
        assert parse_apimodules(src) == []


class TestIndexContent:
    def test_populates_new_fields(self, tmp_path: Path) -> None:
        yml = _write_integration(tmp_path, "Foo", source="from IAMApiModule import *\nfrom AWSApiModule import *\n")
        [foo] = index_content(tmp_path, cache_dir=tmp_path / "cache")
        assert foo.pack == "Pack"
        assert foo.yml_path == str(yml.resolve())
        assert Path(foo.py_path).is_absolute()
        assert foo.py_path == str(yml.with_suffix(".py").resolve())
        assert foo.dockerimage == "demisto/python3:3.12.12.7090913"
        assert (foo.script_type, foo.subtype) == ("python", "python3")
        assert foo.apimodules == ["IAMApiModule", "AWSApiModule"]

    def test_default_filters(self, tmp_path: Path) -> None:
        _write_integration(tmp_path, "Live")
        _write_integration(tmp_path, "Old", deprecated=True)
        _write_integration(tmp_path, "Py2", subtype="python2")
        _write_integration(tmp_path, "Js", script_type="javascript", subtype=None)
        _write_integration(tmp_path, "Empty", commands=[])

        names = {i.name for i in index_content(tmp_path, cache_dir=tmp_path / "cache")}
        assert names == {"Live"}

        with_deprecated = index_content(tmp_path, cache_dir=tmp_path / "cache", include_deprecated=True)
        assert {i.name for i in with_deprecated} == {"Live", "Old"}

        everything = index_content(tmp_path, cache_dir=tmp_path / "cache", include_deprecated=True, python3_only=False)
        # Integrations without commands are never indexed.
        assert {i.name for i in everything} == {"Live", "Old", "Py2", "Js"}

    def test_skips_invalid_yaml(self, tmp_path: Path) -> None:
        _write_integration(tmp_path, "Good")
        bad = tmp_path / "Packs" / "Pack" / "Integrations" / "Bad" / "Bad.yml"
        bad.parent.mkdir(parents=True)
        bad.write_text("name: [unclosed\n", encoding="utf-8")
        assert [i.name for i in index_content(tmp_path, cache_dir=tmp_path / "cache")] == ["Good"]

    def test_missing_packs_dir(self, tmp_path: Path) -> None:
        assert index_content(tmp_path, cache_dir=tmp_path / "cache") == []


class TestCache:
    @pytest.fixture
    def tree(self, tmp_path: Path) -> Path:
        _write_integration(tmp_path, "Foo", display="Before")
        return tmp_path

    def _display(self, root: Path) -> str:
        [foo] = index_content(root, cache_dir=root / "cache")
        return foo.display

    def _edit_display(self, root: Path) -> None:
        _write_integration(root, "Foo", display="After")

    def test_cache_hit_for_same_commit(self, tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DBOT_CONTENT_COMMIT", "aaa")
        assert self._display(tree) == "Before"
        self._edit_display(tree)
        # Same commit and parser version: served from cache, edit not seen.
        assert self._display(tree) == "Before"

    def test_commit_change_invalidates(self, tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DBOT_CONTENT_COMMIT", "aaa")
        assert self._display(tree) == "Before"
        self._edit_display(tree)
        monkeypatch.setenv("DBOT_CONTENT_COMMIT", "bbb")
        assert self._display(tree) == "After"

    def test_parser_version_change_invalidates(self, tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DBOT_CONTENT_COMMIT", "aaa")
        assert self._display(tree) == "Before"
        self._edit_display(tree)
        monkeypatch.setattr(index_mod, "PARSER_VERSION", index_mod.PARSER_VERSION + 1)
        assert self._display(tree) == "After"

    def test_cache_keeps_deprecated_so_filters_apply_after_load(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DBOT_CONTENT_COMMIT", "aaa")
        _write_integration(tmp_path, "Live")
        _write_integration(tmp_path, "Old", deprecated=True)
        assert [i.name for i in index_content(tmp_path, cache_dir=tmp_path / "cache")] == ["Live"]
        cached = index_content(tmp_path, cache_dir=tmp_path / "cache", include_deprecated=True)
        assert {i.name for i in cached} == {"Live", "Old"}

    def test_corrupt_cache_reparses(self, tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DBOT_CONTENT_COMMIT", "aaa")
        cache_dir = tree / "cache"
        cache_dir.mkdir()
        (cache_dir / index_mod.CACHE_FILENAME).write_text("{not json", encoding="utf-8")
        assert self._display(tree) == "Before"

    def test_no_commit_means_no_cache(self, tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DBOT_CONTENT_COMMIT", raising=False)
        assert self._display(tree) == "Before"
        assert not (tree / "cache" / index_mod.CACHE_FILENAME).exists()
        self._edit_display(tree)
        assert self._display(tree) == "After"
