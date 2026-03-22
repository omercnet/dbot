"""End-to-end tests — boot the full server, exercise the complete MCP tool flow.

These tests verify the actual user journey:
  1. Server boots without error
  2. search_tools discovers integrations
  3. get_tool_schema returns argument specs
  4. invoke_tool executes safe commands and blocks dangerous ones
  5. Full flow: search → schema → invoke on real content
"""

import json
from pathlib import Path
from typing import Any

import pytest
from fastmcp import FastMCP

from dbot.audit import AuditLogger
from dbot.credentials.store import CredentialStore
from dbot.registry.catalog import Catalog
from dbot.registry.indexer import index_content
from dbot.runtime.common_server import bootstrap_common_modules
from dbot.runtime.executor import execute_inprocess
from dbot.tools.invoke import make_invoke_tool
from dbot.tools.meta import make_schema_tool
from dbot.tools.search import make_search_tool

CONTENT_ROOT = Path(__file__).parent.parent / "content"


@pytest.fixture(scope="module")
def _bootstrap() -> None:
    if not CONTENT_ROOT.exists():
        pytest.skip("content submodule not initialized")
    bootstrap_common_modules(CONTENT_ROOT)


@pytest.fixture(scope="module")
def catalog(_bootstrap: None) -> Catalog:
    integrations = index_content(CONTENT_ROOT)
    return Catalog(integrations)


@pytest.fixture(scope="module")
def mcp_server(_bootstrap: None, catalog: Catalog) -> FastMCP:
    """Build a real FastMCP server with all 3 tools wired up."""
    store = CredentialStore()  # no credentials — safe for testing
    mcp = FastMCP("dbot-test")
    mcp.tool()(make_search_tool(catalog))
    mcp.tool()(make_schema_tool(catalog))
    mcp.tool()(make_invoke_tool(catalog, store, execute_inprocess))
    return mcp


# ── Server Bootstrap ─────────────────────────────────────────────────


class TestServerBootstrap:
    def test_server_creates(self, mcp_server: FastMCP) -> None:
        assert mcp_server.name == "dbot-test"

    @pytest.mark.asyncio
    async def test_lists_three_tools(self, mcp_server: FastMCP) -> None:
        tools = await mcp_server.list_tools()
        tool_names = {t.name for t in tools}
        assert "search_tools" in tool_names
        assert "get_tool_schema" in tool_names
        assert "invoke_tool" in tool_names

    @pytest.mark.asyncio
    async def test_tool_count_is_three(self, mcp_server: FastMCP) -> None:
        tools = await mcp_server.list_tools()
        assert len(tools) == 3

    def test_catalog_has_integrations(self, catalog: Catalog) -> None:
        stats = catalog.stats
        assert stats["total_integrations"] > 0
        assert stats["total_commands"] > 0


# ── search_tools via MCP ─────────────────────────────────────────────


class TestSearchToolsMCP:
    @pytest.mark.asyncio
    async def test_search_returns_results(self, mcp_server: FastMCP) -> None:
        result = await mcp_server.call_tool("search_tools", {"query": "file hash reputation"})
        data = _parse_tool_result(result)
        assert len(data) > 0

    @pytest.mark.asyncio
    async def test_search_result_has_expected_fields(self, mcp_server: FastMCP) -> None:
        result = await mcp_server.call_tool("search_tools", {"query": "file hash"})
        data = _parse_tool_result(result)
        first = data[0]
        assert "tool_name" in first
        assert "pack" in first
        assert "description" in first
        assert "dangerous" in first

    @pytest.mark.asyncio
    async def test_search_finds_virustotal(self, mcp_server: FastMCP) -> None:
        result = await mcp_server.call_tool("search_tools", {"query": "virustotal file"})
        data = _parse_tool_result(result)
        packs = {r["pack"] for r in data}
        assert "VirusTotal" in packs

    @pytest.mark.asyncio
    async def test_search_with_category_filter(self, mcp_server: FastMCP) -> None:
        result = await mcp_server.call_tool("search_tools", {"query": "detections", "category": "Endpoint"})
        data = _parse_tool_result(result)
        # All results should be from endpoint category
        for r in data:
            assert "endpoint" in r["category"].lower()

    @pytest.mark.asyncio
    async def test_search_no_results(self, mcp_server: FastMCP) -> None:
        result = await mcp_server.call_tool("search_tools", {"query": "xyzzy_nonexistent_gibberish_12345"})
        data = _parse_tool_result(result)
        assert data == []


# ── get_tool_schema via MCP ──────────────────────────────────────────


class TestGetToolSchemaMCP:
    @pytest.mark.asyncio
    async def test_schema_returns_tool_info(self, mcp_server: FastMCP) -> None:
        # First find a real tool name
        search_result = await mcp_server.call_tool("search_tools", {"query": "hello"})
        tools = _parse_tool_result(search_result)
        if not tools:
            pytest.skip("No HelloWorld tools found")

        tool_name = tools[0]["tool_name"]
        schema_result = await mcp_server.call_tool("get_tool_schema", {"tool_name": tool_name})
        schema = _parse_tool_result(schema_result)
        assert schema["tool_name"] == tool_name
        assert "arguments" in schema
        assert "outputs" in schema

    @pytest.mark.asyncio
    async def test_schema_hides_secret_args(self, mcp_server: FastMCP, catalog: Catalog) -> None:
        # Find a tool that has secret args
        for key, (_integration, cmd) in catalog._commands.items():
            secret_args = [a for a in cmd.args if a.secret]
            if secret_args:
                schema_result = await mcp_server.call_tool("get_tool_schema", {"tool_name": key})
                schema = _parse_tool_result(schema_result)
                schema_arg_names = {a["name"] for a in schema["arguments"]}
                for secret in secret_args:
                    assert secret.name not in schema_arg_names, (
                        f"Secret arg '{secret.name}' exposed in schema for {key}"
                    )
                return
        pytest.skip("No tools with secret args found in catalog")

    @pytest.mark.asyncio
    async def test_schema_unknown_tool_errors(self, mcp_server: FastMCP) -> None:
        with pytest.raises((KeyError, Exception)):
            await mcp_server.call_tool("get_tool_schema", {"tool_name": "Fake.nonexistent"})


# ── invoke_tool via MCP ──────────────────────────────────────────────


class TestInvokeToolMCP:
    @pytest.mark.asyncio
    async def test_invoke_helloworld(self, mcp_server: FastMCP) -> None:
        result = await mcp_server.call_tool(
            "invoke_tool",
            {
                "tool_name": "HelloWorld.helloworld-say-hello",
                "args": {"name": "E2ETest"},
                "reason": "end-to-end test",
            },
        )
        data = _parse_tool_result(result)
        assert data["reason"] == "end-to-end test"
        assert data["tool_name"] == "HelloWorld.helloworld-say-hello"
        # May or may not succeed depending on HelloWorld's HTTP call,
        # but the flow should complete without crashing
        assert "success" in data

    @pytest.mark.asyncio
    async def test_invoke_dangerous_blocked(self, mcp_server: FastMCP, catalog: Catalog) -> None:
        # Find a dangerous command
        dangerous_tool = None
        for key, (_integration, cmd) in catalog._commands.items():
            if cmd.dangerous:
                dangerous_tool = key
                break

        if not dangerous_tool:
            pytest.skip("No dangerous commands in catalog")

        result = await mcp_server.call_tool(
            "invoke_tool",
            {
                "tool_name": dangerous_tool,
                "args": {"device_id": "test-123"},
                "reason": "e2e test — should be blocked",
            },
        )
        data = _parse_tool_result(result)
        assert data["status"] == "approval_required"
        assert data["reason"] == "e2e test — should be blocked"

    @pytest.mark.asyncio
    async def test_invoke_unknown_tool_errors(self, mcp_server: FastMCP) -> None:
        with pytest.raises((KeyError, Exception)):
            await mcp_server.call_tool(
                "invoke_tool",
                {
                    "tool_name": "Nonexistent.fake-cmd",
                    "args": {},
                    "reason": "should fail",
                },
            )

    @pytest.mark.asyncio
    async def test_invoke_reason_propagated(self, mcp_server: FastMCP) -> None:
        result = await mcp_server.call_tool(
            "invoke_tool",
            {
                "tool_name": "HelloWorld.helloworld-say-hello",
                "args": {"name": "ReasonTest"},
                "reason": "checking reason propagation",
            },
        )
        data = _parse_tool_result(result)
        assert data["reason"] == "checking reason propagation"


# ── Full Agent Flow ──────────────────────────────────────────────────


class TestFullAgentFlow:
    """Simulates what an actual agent would do: search → schema → invoke."""

    @pytest.mark.asyncio
    async def test_agent_discovers_and_invokes(self, mcp_server: FastMCP) -> None:
        # Step 1: Agent searches for hello/greeting tools
        search_result = await mcp_server.call_tool("search_tools", {"query": "hello say"})
        tools = _parse_tool_result(search_result)
        assert len(tools) > 0, "Agent should find at least one tool"

        # Step 2: Agent picks first result and gets schema
        tool_name = tools[0]["tool_name"]
        schema_result = await mcp_server.call_tool("get_tool_schema", {"tool_name": tool_name})
        schema = _parse_tool_result(schema_result)
        assert "arguments" in schema

        # Step 3: Agent builds args from schema and invokes
        args = {}
        for arg in schema["arguments"]:
            if arg["required"]:
                args[arg["name"]] = "test-value"

        invoke_result = await mcp_server.call_tool(
            "invoke_tool",
            {"tool_name": tool_name, "args": args, "reason": "agent flow test"},
        )
        data = _parse_tool_result(invoke_result)
        assert data["tool_name"] == tool_name
        assert "success" in data

    @pytest.mark.asyncio
    async def test_agent_hits_dangerous_and_backs_off(self, mcp_server: FastMCP, catalog: Catalog) -> None:
        # Find a dangerous command via search
        dangerous_tool = None
        for key, (_integration, cmd) in catalog._commands.items():
            if cmd.dangerous:
                dangerous_tool = key
                break
        if not dangerous_tool:
            pytest.skip("No dangerous commands available")

        # Agent tries to invoke it
        result = await mcp_server.call_tool(
            "invoke_tool",
            {
                "tool_name": dangerous_tool,
                "args": {},
                "reason": "agent trying dangerous action",
            },
        )
        data = _parse_tool_result(result)

        # Agent sees approval_required and backs off
        assert data["status"] == "approval_required"
        assert "approval" in data["description"].lower() or "dangerous" in data["description"].lower()


# ── Audit Integration ────────────────────────────────────────────────


class TestAuditIntegration:
    def test_audit_captures_invocation(self, tmp_path: Path) -> None:
        audit = AuditLogger(audit_path=tmp_path / "audit.log")
        audit.log_invocation(
            tool_name="VirusTotal.vt-get-file",
            args={"file": "abc123"},
            reason="checking hash from alert",
            dangerous=False,
            result={"success": True, "results": [{"data": "clean"}]},
            duration_ms=456.78,
        )
        log_line = (tmp_path / "audit.log").read_text().strip()
        entry = json.loads(log_line)
        assert entry["tool_name"] == "VirusTotal.vt-get-file"
        assert entry["reason"] == "checking hash from alert"
        assert entry["result_success"] is True
        assert entry["duration_ms"] == 456.78

    def test_audit_dangerous_tool_logged(self, tmp_path: Path) -> None:
        audit = AuditLogger(audit_path=tmp_path / "audit.log")
        audit.log_invocation(
            tool_name="CrowdStrike.endpoint-isolation",
            args={"device_id": "host-42"},
            reason="C2 beacon detected",
            dangerous=True,
            result={"status": "approval_required"},
            duration_ms=12.0,
            approved_by=None,
        )
        entry = json.loads((tmp_path / "audit.log").read_text().strip())
        assert entry["dangerous"] is True
        assert entry["approved_by"] is None
        assert entry["result_status"] == "approval_required"


# ── Helper ───────────────────────────────────────────────────────────


def _parse_tool_result(result: Any) -> Any:
    """Extract the actual data from a FastMCP ToolResult."""
    # FastMCP call_tool returns a ToolResult with content
    if hasattr(result, "structured_content") and result.structured_content is not None:
        return result.structured_content.get("result", result.structured_content)
    if hasattr(result, "content"):
        for item in result.content:
            if hasattr(item, "text"):
                return json.loads(item.text)
        # Empty content list = empty result (e.g., search with no matches)
        return []
    # Fallback: might be raw data
    if isinstance(result, dict | list):
        return result
    raise ValueError(f"Could not parse tool result: {result!r}")
