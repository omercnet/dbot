"""What each certified integration must do. Fixtures live in fixtures/<instance>.json."""

from dataclasses import dataclass, field
from typing import Any

GOOD_KEY = "dbot-replay-good-key"
BAD_KEY = "dbot-replay-revoked-key"  # error fixtures match on this key and answer 401


@dataclass(frozen=True)
class Spec:
    integration: str  # IntegrationDef.name
    yml: str  # relative to content/
    instance: str
    key_param: str  # type-9 credentials param holding the api key in `password`
    reads: dict[str, dict[str, Any]]  # curated read command -> args
    error_command: str  # run with BAD_KEY; the fixture answers 401
    error_args: dict[str, Any]
    error_message: str  # must appear in CommandResult.error
    destructive: list[str]  # submit/report commands the shipped policy must gate
    params: dict[str, Any] = field(default_factory=dict)

    @property
    def key_env(self) -> str:
        return f"DBOT_REPLAY_{self.instance.upper()}_KEY"

    def instance_config(self, name: str, key_env: str) -> dict[str, Any]:
        params = self.params | {self.key_param: {"password": f"env://{key_env}"}}
        return {"name": name, "integration": self.integration, "params": params}


SPECS = [
    Spec(
        integration="VirusTotal (API v3)",
        yml="Packs/VirusTotal/Integrations/VirusTotalV3/VirusTotalV3.yml",
        instance="virustotal",
        key_param="credentials",
        reads={
            "ip": {"ip": "8.8.8.8"},
            "domain": {"domain": "google.com"},
            "file": {"file": "699ec052ecc898bdbdafea0027c4ab44c3d01ae011c17745dd2b7fbddaa077f3"},
            "url": {"url": "https://www.example.com"},
        },
        # Reputation commands turn API errors into an ok "could not be processed" DBotScore entry by design,
        # so the 401 is asserted on test-module, which raises.
        error_command="test-module",
        error_args={},
        error_message="Wrong API key",
        destructive=["vt-comments-add", "file-scan", "url-scan"],
    ),
    Spec(
        integration="AbuseIPDB",
        yml="Packs/AbuseDB/Integrations/AbuseDB/AbuseDB.yml",
        instance="abuseipdb",
        key_param="credentials",
        params={"server": "https://api.abuseipdb.com/api/v2/"},
        reads={"ip": {"ip": "1.1.1.1"}},
        error_command="ip",
        error_args={"ip": "1.1.1.1"},
        # The integration drops the vendor body and reports only the status code.
        error_message="Status code: 401",
        destructive=["abuseipdb-report-ip"],
    ),
    Spec(
        integration="AlienVault OTX v2",
        yml="Packs/AlienVault_OTX/Integrations/AlienVault_OTX_v2/AlienVault_OTX_v2.yml",
        instance="otx",
        key_param="credentials",
        params={"url": "https://otx.alienvault.com", "integrationReliability": "C - Fairly reliable"},
        reads={"ip": {"ip": "98.136.103.23"}, "domain": {"domain": "otx.alienvault.com"}},
        error_command="ip",
        error_args={"ip": "8.8.8.8"},
        error_message="Authentication credentials were not provided or are invalid.",
        destructive=[],  # OTX has no submit/report command
    ),
    Spec(
        integration="GreyNoise",
        yml="Packs/GreyNoise/Integrations/GreyNoise/GreyNoise.yml",
        instance="greynoise",
        key_param="credentials",
        reads={"ip": {"ip": "71.6.135.131"}},
        error_command="ip",
        error_args={"ip": "71.6.135.131"},
        error_message="(401, 'forbidden')",  # the SDK's RequestFailure carries the vendor body
        destructive=[],  # GreyNoise has no submit/report command
    ),
    Spec(
        integration="urlscan.io",
        yml="Packs/UrlScan/Integrations/UrlScan/UrlScan.yml",
        instance="urlscan",
        key_param="creds_apikey",
        params={"scan_visibility": "private", "integrationReliability": "C - Fairly reliable"},
        reads={"urlscan-search": {"searchParameter": "domain:example.com", "searchType": "advanced"}},
        # urlscan-search ignores error bodies (it fails with KeyError 'total'), and test-module never checks the
        # response at all, so the vendor message only surfaces on submit. The test approves it like an operator would.
        error_command="urlscan-submit",
        error_args={"url": "https://example.com/"},
        error_message="The API key supplied is not valid.",
        # Its `url` command also submits a scan, but a name glob cannot tell it from the read `url` of VT/OTX.
        destructive=["urlscan-submit", "urlscan-submit-url-command"],
    ),
]
