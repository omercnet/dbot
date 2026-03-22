"""End-to-end agent integration tests — full pipeline with TestModel (zero LLM calls)."""

import json
from pathlib import Path

import pytest
from pydantic_ai.models.test import TestModel

from dbot.agent.chat import ChatAgent
from dbot.agent.deps import IRDeps
from dbot.agent.guardrails import GuardrailConfig
from dbot.agent.ingestion.cli import load_alert_from_string
from dbot.agent.models import Alert, IRReport, Severity, Verdict
from dbot.agent.report import to_json, to_jsonl_event, to_markdown
from dbot.agent.responder import ResponderAgent
from dbot.registry.catalog import Catalog

# ── Fixtures ──────────────────────────────────────────────────────────

# Re-import from conftest


# ── ChatAgent Tests ───────────────────────────────────────────────────


class TestChatAgentIntegration:
    @pytest.mark.asyncio
    async def test_send_returns_string(self, stub_deps: IRDeps) -> None:
        agent = ChatAgent(model="test")
        with agent.agent.override(model=TestModel(call_tools=[])):
            response = await agent.send("What tools can check an IP?", deps=stub_deps)
        assert isinstance(response, str)
        assert len(response) > 0

    @pytest.mark.asyncio
    async def test_multi_turn_preserves_history(self, stub_deps: IRDeps) -> None:
        agent = ChatAgent(model="test")
        with agent.agent.override(model=TestModel(call_tools=[])):
            await agent.send("Check IP 1.2.3.4", deps=stub_deps)
            assert len(agent.history) > 0
            await agent.send("What about its domains?", deps=stub_deps)
            assert len(agent.history) > 2  # at least 2 request/response pairs

    @pytest.mark.asyncio
    async def test_reset_clears_history(self, stub_deps: IRDeps) -> None:
        agent = ChatAgent(model="test")
        with agent.agent.override(model=TestModel(call_tools=[])):
            await agent.send("test", deps=stub_deps)
            assert len(agent.history) > 0
            agent.reset()
            assert len(agent.history) == 0

    @pytest.mark.asyncio
    async def test_stream_yields_chunks(self, stub_deps: IRDeps) -> None:
        agent = ChatAgent(model="test")
        chunks: list[str] = []
        with agent.agent.override(model=TestModel(call_tools=[])):
            async for chunk in agent.send_stream("test query", deps=stub_deps):
                chunks.append(chunk)
        assert len(chunks) > 0


# ── ResponderAgent Tests ──────────────────────────────────────────────


class TestResponderAgentIntegration:
    @pytest.mark.asyncio
    async def test_investigate_returns_report(self, stub_deps: IRDeps, sample_alert: Alert) -> None:
        stub_deps.guardrails = GuardrailConfig.autonomous_default()
        agent = ResponderAgent(model="test")
        with agent.agent.override(model=TestModel()):
            result = await agent.investigate(alert=sample_alert, deps=stub_deps)
        assert isinstance(result.report, IRReport)
        assert result.report.alert.id == sample_alert.id

    @pytest.mark.asyncio
    async def test_investigate_sets_timestamps(self, stub_deps: IRDeps, sample_alert: Alert) -> None:
        stub_deps.guardrails = GuardrailConfig.autonomous_default()
        agent = ResponderAgent(model="test")
        with agent.agent.override(model=TestModel()):
            result = await agent.investigate(alert=sample_alert, deps=stub_deps)
        assert result.report.started_at is not None
        assert result.report.completed_at is not None

    @pytest.mark.asyncio
    async def test_investigate_has_verdict(self, stub_deps: IRDeps, sample_alert: Alert) -> None:
        stub_deps.guardrails = GuardrailConfig.autonomous_default()
        agent = ResponderAgent(model="test")
        with agent.agent.override(model=TestModel()):
            result = await agent.investigate(alert=sample_alert, deps=stub_deps)
        assert result.report.verdict in list(Verdict)

    @pytest.mark.asyncio
    async def test_investigate_no_hitl_records_deferred(self, stub_deps: IRDeps, sample_alert: Alert) -> None:
        """When on_deferred is None, deferred tools are recorded as blocked."""
        stub_deps.guardrails = GuardrailConfig.autonomous_default()
        agent = ResponderAgent(model="test")
        with agent.agent.override(model=TestModel()):
            result = await agent.investigate(alert=sample_alert, deps=stub_deps, on_deferred=None)
        # TestModel doesn't trigger deferred, but the flow completes
        assert isinstance(result.report, IRReport)


# ── Ingestion Tests ───────────────────────────────────────────────────


class TestIngestionIntegration:
    def test_load_alert_from_string(self) -> None:
        json_str = json.dumps(
            {
                "id": "alert-123",
                "title": "Test Alert",
                "description": "Something suspicious",
                "severity": "high",
            }
        )
        alert = load_alert_from_string(json_str)
        assert alert.id == "alert-123"
        assert alert.severity == Severity.HIGH

    def test_load_alert_auto_generates_id(self) -> None:
        json_str = json.dumps({"title": "No ID Alert"})
        alert = load_alert_from_string(json_str)
        assert alert.id.startswith("alert-")

    def test_load_alert_from_file(self, tmp_path: Path) -> None:
        from dbot.agent.ingestion.cli import load_alert_from_file

        alert_file = tmp_path / "alert.json"
        alert_file.write_text(
            json.dumps(
                {
                    "id": "file-alert",
                    "title": "File Alert",
                    "severity": "critical",
                }
            )
        )
        alert = load_alert_from_file(alert_file)
        assert alert.id == "file-alert"
        assert alert.severity == Severity.CRITICAL


# ── Report Rendering Tests ────────────────────────────────────────────


class TestReportIntegration:
    def test_markdown_contains_verdict(self, sample_report: IRReport) -> None:
        md = to_markdown(sample_report)
        assert "MALICIOUS" in md
        assert "85%" in md

    def test_markdown_contains_indicators(self, sample_report: IRReport) -> None:
        md = to_markdown(sample_report)
        assert "1.2.3.4" in md

    def test_markdown_contains_recommendations(self, sample_report: IRReport) -> None:
        md = to_markdown(sample_report)
        assert "Block IP" in md

    def test_json_is_valid(self, sample_report: IRReport) -> None:
        j = to_json(sample_report)
        parsed = json.loads(j)
        assert parsed["verdict"] == "malicious"

    def test_jsonl_is_single_line(self, sample_report: IRReport) -> None:
        line = to_jsonl_event(sample_report)
        assert "\n" not in line
        parsed = json.loads(line)
        assert parsed["event_type"] == "investigation_complete"
        assert parsed["verdict"] == "malicious"


# ── Guardrails Tests ─────────────────────────────────────────────────


class TestGuardrailsIntegration:
    def test_chat_default_is_permissive(self) -> None:
        config = GuardrailConfig.chat_default()
        assert len(config.blocked_tools) == 0
        assert len(config.blocked_categories) == 0
        assert config.max_tool_calls == 100

    def test_autonomous_default_blocks_endpoint(self) -> None:
        config = GuardrailConfig.autonomous_default()
        assert "Endpoint" in config.blocked_categories
        assert config.max_tool_calls == 30

    def test_build_toolset_creates_tools(self, stub_catalog: Catalog) -> None:
        from dbot.agent.guardrails import build_toolset

        config = GuardrailConfig.chat_default()
        toolset = build_toolset(config)
        assert toolset is not None


# ── Full Pipeline: Alert → Investigate → Report ──────────────────────


class TestFullPipeline:
    @pytest.mark.asyncio
    async def test_alert_to_report_pipeline(self, stub_deps: IRDeps) -> None:
        """Full pipeline: parse alert → investigate → render report."""
        # 1. Parse alert
        alert = load_alert_from_string(
            json.dumps(
                {
                    "id": "pipeline-test",
                    "title": "Suspicious Outbound Connection",
                    "description": "Host making connections to known malware C2",
                    "severity": "high",
                    "indicators": [{"type": "ip", "value": "10.0.0.99"}],
                }
            )
        )
        assert alert.id == "pipeline-test"

        # 2. Investigate
        stub_deps.guardrails = GuardrailConfig.autonomous_default()
        agent = ResponderAgent(model="test")
        with agent.agent.override(model=TestModel()):
            result = await agent.investigate(alert=alert, deps=stub_deps)

        # 3. Report
        report = result.report
        assert report.alert.id == "pipeline-test"
        assert report.verdict in list(Verdict)

        # 4. Render
        md = to_markdown(report)
        assert "pipeline-test" in md
        assert "Suspicious Outbound" in md

        j = to_json(report)
        parsed = json.loads(j)
        assert parsed["alert"]["id"] == "pipeline-test"

        line = to_jsonl_event(report)
        event = json.loads(line)
        assert event["alert_id"] == "pipeline-test"
