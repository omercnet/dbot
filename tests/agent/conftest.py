"""Shared test fixtures for agent tests."""

from pathlib import Path
from typing import Any

import pytest

from dbot.agent.deps import IRDeps
from dbot.agent.guardrails import GuardrailConfig
from dbot.agent.models import Alert, Indicator, IRReport, Severity, ToolCall, Verdict
from dbot.audit import AuditLogger
from dbot.credentials.store import CredentialStore
from dbot.registry.catalog import Catalog
from dbot.registry.models import ArgDef, CommandDef, IntegrationDef


def _make_stub_catalog() -> Catalog:
    """Create a Catalog with test integrations."""
    safe_cmd = CommandDef(
        name="test-lookup",
        description="Look up an indicator",
        args=[ArgDef(name="indicator", required=True)],
    )
    dangerous_cmd = CommandDef(
        name="endpoint-isolation",
        description="Isolate a host from the network",
        dangerous=True,
    )
    vt_cmd = CommandDef(
        name="vt-get-file",
        description="Check file hash reputation on VirusTotal",
        args=[
            ArgDef(name="file", description="File hash", required=True),
            ArgDef(name="apikey", description="API Key", secret=True),
        ],
    )

    integrations = [
        IntegrationDef(
            pack="TestPack",
            name="TestIntegration",
            category="Data Enrichment & Threat Intelligence",
            py_path="/tmp/fake.py",
            commands=[safe_cmd, dangerous_cmd],
        ),
        IntegrationDef(
            pack="VirusTotal",
            name="VirusTotalV3",
            category="Data Enrichment & Threat Intelligence",
            py_path="/tmp/fake_vt.py",
            commands=[vt_cmd],
        ),
    ]
    return Catalog(integrations)


async def _stub_executor(py: Path, cmd: str, args: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """Fake executor that returns a canned success response."""
    return {
        "success": True,
        "results": [{"Contents": f"Mock result for {cmd}", "args": args}],
        "logs": [("INFO", f"Executed {cmd}")],
    }


@pytest.fixture
def stub_catalog() -> Catalog:
    return _make_stub_catalog()


@pytest.fixture
def stub_executor():
    return _stub_executor


@pytest.fixture
def mock_audit(tmp_path: Path) -> AuditLogger:
    return AuditLogger(audit_path=tmp_path / "test-audit.log")


@pytest.fixture
def sample_alert() -> Alert:
    return Alert(
        id="test-alert-001",
        title="Suspicious IP Detected",
        description="Outbound connection to known C2 IP 1.2.3.4 from host DESKTOP-FINANCE-03",
        severity=Severity.HIGH,
        source="Splunk",
        indicators=[
            Indicator(type="ip", value="1.2.3.4", source="Splunk"),
            Indicator(type="hostname", value="DESKTOP-FINANCE-03", source="Splunk"),
        ],
    )


@pytest.fixture
def sample_report(sample_alert: Alert) -> IRReport:
    return IRReport(
        alert=sample_alert,
        verdict=Verdict.MALICIOUS,
        confidence=0.85,
        summary="C2 communication confirmed. IP 1.2.3.4 is a known C2 server.",
        findings=[
            "IP 1.2.3.4 flagged by VirusTotal (15/90 detections)",
            "AbuseIPDB confidence score: 95%",
            "Repeated beaconing pattern at 60s intervals",
        ],
        indicators_found=[
            Indicator(type="ip", value="1.2.3.4", source="VirusTotal"),
        ],
        tool_calls=[
            ToolCall(tool_name="VirusTotal.vt-get-ip", args={"ip": "1.2.3.4"}, reason="Check IP reputation"),
            ToolCall(tool_name="AbuseIPDB.check-ip", args={"ip": "1.2.3.4"}, reason="Cross-reference IP"),
        ],
        recommendations=[
            "Block IP 1.2.3.4 at firewall",
            "Isolate DESKTOP-FINANCE-03 for forensic analysis",
        ],
        llm_turns=5,
    )


@pytest.fixture
def stub_deps(
    stub_catalog: Catalog,
    mock_audit: AuditLogger,
) -> IRDeps:
    """Build IRDeps with stubs — no real integrations needed."""
    return IRDeps(
        catalog=stub_catalog,
        credential_store=CredentialStore(),
        executor=_stub_executor,
        audit=mock_audit,
        guardrails=GuardrailConfig.chat_default(),
    )
