"""Tests for the searchable catalog — search, schema, resolve, secret filtering."""

import pytest

from dbot.registry.catalog import Catalog
from dbot.registry.models import ArgDef, CommandDef, IntegrationDef, OutputDef


def _make_integration(
    pack: str = "TestPack",
    name: str = "TestIntegration",
    category: str = "Data Enrichment & Threat Intelligence",
    description: str = "",
    commands: list[CommandDef] | None = None,
) -> IntegrationDef:
    if commands is None:
        commands = [
            CommandDef(name="test-cmd", description="A test command"),
        ]
    return IntegrationDef(
        pack=pack, name=name, category=category, description=description, py_path="/fake.py", commands=commands
    )


def _vt_integration() -> IntegrationDef:
    return _make_integration(
        pack="VirusTotal",
        name="VirusTotalV3",
        category="Data Enrichment & Threat Intelligence",
        commands=[
            CommandDef(
                name="vt-get-file",
                description="Check file hash reputation",
                args=[
                    ArgDef(name="file", description="File hash", required=True),
                    ArgDef(name="apikey", description="API Key", secret=True),
                ],
                outputs=[OutputDef(context_path="VirusTotal.File.MD5", type="String")],
            ),
            CommandDef(name="vt-get-ip", description="Check IP reputation"),
            CommandDef(name="vt-get-domain", description="Check domain reputation"),
        ],
    )


def _cs_integration() -> IntegrationDef:
    return _make_integration(
        pack="CrowdStrikeFalcon",
        name="CrowdStrike",
        category="Endpoint",
        commands=[
            CommandDef(name="cs-get-detections", description="Get detections"),
            CommandDef(name="endpoint-isolation", description="Isolate a host", dangerous=True),
        ],
    )


def _deprecated_integration() -> IntegrationDef:
    return _make_integration(
        pack="OldPack",
        name="OldIntegration",
        commands=[
            CommandDef(name="old-cmd", description="Deprecated command", deprecated=True),
            CommandDef(name="new-cmd", description="Active command", deprecated=False),
        ],
    )


@pytest.fixture
def catalog() -> Catalog:
    return Catalog([_vt_integration(), _cs_integration(), _deprecated_integration()])


class TestCatalogSearch:
    def test_search_by_keyword(self, catalog: Catalog) -> None:
        results = catalog.search("file hash")
        assert len(results) > 0
        assert results[0]["tool_name"] == "VirusTotal.vt-get-file"

    def test_search_returns_multiple(self, catalog: Catalog) -> None:
        results = catalog.search("reputation")
        assert len(results) >= 3  # vt-get-file, vt-get-ip, vt-get-domain all match

    def test_search_by_pack_name(self, catalog: Catalog) -> None:
        results = catalog.search("CrowdStrike")
        tool_names = [r["tool_name"] for r in results]
        assert "CrowdStrikeFalcon.cs-get-detections" in tool_names

    def test_search_no_results(self, catalog: Catalog) -> None:
        results = catalog.search("xyzzy_nonexistent_12345")
        assert results == []

    def test_search_category_filter(self, catalog: Catalog) -> None:
        results = catalog.search("detections", category="Endpoint")
        assert len(results) > 0
        assert all(r["category"] == "Endpoint" for r in results)

    def test_search_category_filter_excludes(self, catalog: Catalog) -> None:
        results = catalog.search("file", category="Endpoint")
        # VirusTotal is "Data Enrichment", should be excluded
        vt_results = [r for r in results if r["pack"] == "VirusTotal"]
        assert len(vt_results) == 0

    def test_search_category_matches_description(self) -> None:
        """Category filter should match against integration.description too."""
        integration = _make_integration(
            pack="MislabeledPack",
            name="MislabeledInt",
            category="Utilities",  # wrong category in YAML
            description="Network security endpoint detection tool",
            commands=[CommandDef(name="ml-detect", description="Detect threats")],
        )
        catalog = Catalog([integration])
        # Searching with category="endpoint" should match the description
        results = catalog.search("detect", category="endpoint")
        assert len(results) == 1
        assert results[0]["tool_name"] == "MislabeledPack.ml-detect"

    def test_search_category_still_matches_category_field(self, catalog: Catalog) -> None:
        """Category filter should still match the actual category field."""
        results = catalog.search("detections", category="Endpoint")
        assert len(results) > 0
        assert results[0]["pack"] == "CrowdStrikeFalcon"

    def test_search_category_no_match_in_either(self) -> None:
        """Category filter returns nothing when neither category nor description match."""
        integration = _make_integration(
            pack="Unrelated",
            name="UnrelatedInt",
            category="Utilities",
            description="A file management tool",
            commands=[CommandDef(name="u-cmd", description="manage files")],
        )
        catalog = Catalog([integration])
        results = catalog.search("manage", category="Endpoint")
        assert results == []

    def test_search_respects_top_k(self, catalog: Catalog) -> None:
        results = catalog.search("reputation", top_k=2)
        assert len(results) <= 2

    def test_search_excludes_deprecated(self, catalog: Catalog) -> None:
        results = catalog.search("old deprecated command")
        tool_names = [r["tool_name"] for r in results]
        assert "OldPack.old-cmd" not in tool_names

    def test_search_includes_non_deprecated(self, catalog: Catalog) -> None:
        results = catalog.search("active command")
        tool_names = [r["tool_name"] for r in results]
        assert "OldPack.new-cmd" in tool_names

    def test_search_marks_dangerous(self, catalog: Catalog) -> None:
        results = catalog.search("isolate host")
        dangerous = [r for r in results if r["dangerous"]]
        assert len(dangerous) > 0
        assert dangerous[0]["tool_name"] == "CrowdStrikeFalcon.endpoint-isolation"

    def test_search_secret_args_hidden(self, catalog: Catalog) -> None:
        results = catalog.search("file hash")
        vt_result = next(r for r in results if r["tool_name"] == "VirusTotal.vt-get-file")
        arg_names = [a["name"] for a in vt_result["args_summary"]]
        assert "file" in arg_names
        assert "apikey" not in arg_names


class TestCatalogGetSchema:
    def test_schema_basic(self, catalog: Catalog) -> None:
        schema = catalog.get_schema("VirusTotal.vt-get-file")
        assert schema["tool_name"] == "VirusTotal.vt-get-file"
        assert schema["pack"] == "VirusTotal"
        assert schema["dangerous"] is False

    def test_schema_args_exclude_secrets(self, catalog: Catalog) -> None:
        schema = catalog.get_schema("VirusTotal.vt-get-file")
        arg_names = [a["name"] for a in schema["arguments"]]
        assert "file" in arg_names
        assert "apikey" not in arg_names

    def test_schema_includes_outputs(self, catalog: Catalog) -> None:
        schema = catalog.get_schema("VirusTotal.vt-get-file")
        assert len(schema["outputs"]) > 0
        assert schema["outputs"][0]["context_path"] == "VirusTotal.File.MD5"

    def test_schema_unknown_tool_raises(self, catalog: Catalog) -> None:
        with pytest.raises(KeyError, match="not found"):
            catalog.get_schema("Nonexistent.fake-tool")

    def test_schema_dangerous_flag(self, catalog: Catalog) -> None:
        schema = catalog.get_schema("CrowdStrikeFalcon.endpoint-isolation")
        assert schema["dangerous"] is True


class TestCatalogResolve:
    def test_resolve_returns_tuple(self, catalog: Catalog) -> None:
        integration, cmd = catalog.resolve("VirusTotal.vt-get-file")
        assert integration.pack == "VirusTotal"
        assert cmd.name == "vt-get-file"

    def test_resolve_unknown_raises(self, catalog: Catalog) -> None:
        with pytest.raises(KeyError, match="not found"):
            catalog.resolve("Fake.not-real")


class TestCatalogStats:
    def test_stats_counts(self, catalog: Catalog) -> None:
        stats = catalog.stats
        assert stats["total_integrations"] == 3
        # VT: 3 + CS: 2 + Old: 2 = 7
        assert stats["total_commands"] == 7

    def test_stats_categories(self, catalog: Catalog) -> None:
        cats = catalog.stats["categories"]
        assert "Endpoint" in cats
        assert "Data Enrichment & Threat Intelligence" in cats


class TestCatalogEmpty:
    def test_empty_catalog(self) -> None:
        catalog = Catalog([])
        assert catalog.stats["total_commands"] == 0
        assert catalog.search("anything") == []

    def test_integration_with_no_commands(self) -> None:
        integration = _make_integration(commands=[])
        catalog = Catalog([integration])
        assert catalog.stats["total_commands"] == 0
