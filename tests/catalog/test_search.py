"""Catalog search ranking, tokenization and resolve/get errors over synthetic integrations."""

import pytest

from dbot.catalog.models import ArgDef, CommandDef, IntegrationDef
from dbot.catalog.search import Catalog, tokenize


def _integration(
    name: str, display: str, pack: str, commands: list[CommandDef], description: str = ""
) -> IntegrationDef:
    return IntegrationDef(
        pack=pack, name=name, display=display, description=description, py_path=f"/x/{name}.py", commands=commands
    )


@pytest.fixture
def catalog() -> Catalog:
    return Catalog(
        [
            _integration(
                "ANYRUN",
                "ANY.RUN",
                "ANYRUN",
                [CommandDef(name="ip", description="Get IP reputation, similar to AbuseIPDB and other feeds.")],
                description="Sandbox with reputation lookups for IPs, AbuseIPDB-style.",
            ),
            _integration(
                "AbuseIPDB",
                "AbuseIPDB",
                "AbuseDB",
                [
                    CommandDef(name="ip", description="Checks the specified IP address."),
                    CommandDef(name="abuseipdb-report-ip", description="Reports an IP address.", dangerous=True),
                    CommandDef(name="abuseipdb-old", description="Old IP lookup.", deprecated=True),
                ],
            ),
            _integration("urlscan.io", "urlscan.io", "UrlScan", [CommandDef(name="url", description="Scan a URL.")]),
            _integration(
                "GreyNoise",
                "GreyNoise",
                "GreyNoise",
                [CommandDef(name="ip", description="Get IP context."), CommandDef(name="greynoise-query")],
            ),
        ]
    )


def _top(catalog: Catalog, query: str) -> str:
    results = catalog.search(query, limit=1)
    assert results, query
    return str(results[0]["tool_name"])


def test_tokenize_splits_on_non_alphanumerics_and_casefolds() -> None:
    assert tokenize("AbuseIPDB-report_ip urlscan.io  V2") == ["abuseipdb", "report", "ip", "urlscan", "io", "v2"]


def test_name_match_beats_description_hits(catalog: Catalog) -> None:
    # ANYRUN.ip mentions AbuseIPDB and reputation in descriptions; the integration name must win.
    assert _top(catalog, "AbuseIPDB IP reputation") == "AbuseIPDB.ip"
    assert _top(catalog, "abuseipdb ip") == "AbuseIPDB.ip"


def test_pack_and_display_names_match(catalog: Catalog) -> None:
    # Query names only the pack, so any AbuseIPDB command is a correct top hit.
    assert catalog.search("abusedb", limit=1)[0]["integration"] == "AbuseIPDB"
    assert _top(catalog, "any run") == "ANYRUN.ip"


def test_prefix_and_camel_case_name_match(catalog: Catalog) -> None:
    assert _top(catalog, "grey ip") == "GreyNoise.ip"
    assert catalog.search("greyno", limit=1)[0]["integration"] == "GreyNoise"
    assert _top(catalog, "URLSCAN") == "urlscan.io.url"


def test_command_name_coverage_breaks_ties(catalog: Catalog) -> None:
    tools = [r["tool_name"] for r in catalog.search("abuseipdb ip")]
    assert tools.index("AbuseIPDB.ip") < tools.index("AbuseIPDB.abuseipdb-report-ip")


def test_result_shape(catalog: Catalog) -> None:
    [hit] = catalog.search("abuseipdb report", limit=1)
    assert hit == {
        "tool_name": "AbuseIPDB.abuseipdb-report-ip",
        "integration": "AbuseIPDB",
        "display": "AbuseIPDB",
        "pack": "AbuseDB",
        "description": "Reports an IP address.",
        "dangerous": True,
    }


def test_limit_no_match_and_empty_query(catalog: Catalog) -> None:
    assert len(catalog.search("ip", limit=2)) == 2
    assert catalog.search("zzzz-nothing") == []
    assert catalog.search("  --  ") == []
    assert catalog.search("ip", limit=0) == []


def test_deprecated_commands_hidden_from_search_but_resolvable(catalog: Catalog) -> None:
    assert all(r["tool_name"] != "AbuseIPDB.abuseipdb-old" for r in catalog.search("abuseipdb old", limit=50))
    _, cmd = catalog.resolve("AbuseIPDB.abuseipdb-old")
    assert cmd.deprecated


def test_resolve_dotted_integration_name(catalog: Catalog) -> None:
    integration, cmd = catalog.resolve("urlscan.io.url")
    assert (integration.name, cmd.name) == ("urlscan.io", "url")


def test_resolve_unknown_command_lists_integration_commands(catalog: Catalog) -> None:
    with pytest.raises(KeyError, match=r"'GreyNoise' has commands: ip, greynoise-query"):
        catalog.resolve("GreyNoise.nope")


def test_resolve_unknown_tool_suggests_close_match(catalog: Catalog) -> None:
    with pytest.raises(KeyError, match=r"Did you mean: AbuseIPDB\.ip"):
        catalog.resolve("AbuseIPD.ip")


def test_get(catalog: Catalog) -> None:
    assert catalog.get("GreyNoise").pack == "GreyNoise"
    with pytest.raises(KeyError, match="Did you mean: GreyNoise"):
        catalog.get("Greynoise")


def test_schema_excludes_secret_args() -> None:
    cmd = CommandDef(name="c", args=[ArgDef(name="q", required=True), ArgDef(name="token", secret=True)])
    cat = Catalog([_integration("X", "X", "X", [cmd])])
    assert [a["name"] for a in cat.get_schema("X.c")["arguments"]] == ["q"]


def test_stats(catalog: Catalog) -> None:
    stats = catalog.stats
    assert stats["total_integrations"] == 4
    assert stats["total_commands"] == 7
