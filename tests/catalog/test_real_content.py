"""Index and search the real demisto/content checkout. Skipped when the submodule is absent."""

from pathlib import Path

import pytest

from dbot.catalog import Catalog, IntegrationDef, index_content

CONTENT = Path(__file__).resolve().parents[2] / "content"


@pytest.fixture(scope="module")
def integrations(tmp_path_factory: pytest.TempPathFactory) -> list[IntegrationDef]:
    if not (CONTENT / "Packs").is_dir():
        pytest.skip("content submodule not checked out")
    return index_content(CONTENT, cache_dir=tmp_path_factory.mktemp("catalog-cache"))


@pytest.fixture(scope="module")
def catalog(integrations: list[IntegrationDef]) -> Catalog:
    return Catalog(integrations)


def test_index_invariants(integrations: list[IntegrationDef]) -> None:
    assert len(integrations) > 900
    assert all(i.script_type == "python" and i.subtype == "python3" for i in integrations)
    assert all(i.dockerimage and Path(i.py_path).is_file() and Path(i.yml_path).is_file() for i in integrations)
    by_name = {i.name: i for i in integrations}
    assert by_name["Azure"].apimodules == ["MicrosoftApiModule", "COOCApiModule"]
    assert by_name["AbuseIPDB"].pack == "AbuseDB"


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("abuseipdb ip", "AbuseIPDB.ip"),
        ("AbuseIPDB IP reputation", "AbuseIPDB.ip"),
        ("virustotal file", "VirusTotal (API v3).file"),
        ("greynoise ip", "GreyNoise.ip"),
        ("alienvault otx domain", "AlienVault OTX v2.domain"),
        ("okta suspend user", "Okta v2.okta-suspend-user"),
    ],
)
def test_top1(catalog: Catalog, query: str, expected: str) -> None:
    assert catalog.search(query, limit=1)[0]["tool_name"] == expected


def test_urlscan_top1_is_urlscan_integration(catalog: Catalog) -> None:
    [hit] = catalog.search("urlscan", limit=1)
    assert hit["integration"] == "urlscan.io"
