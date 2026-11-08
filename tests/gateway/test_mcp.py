"""MCP tools through FastMCP's in-memory client."""

from typing import Any

import pytest
from fastmcp import Client

from dbot.gateway import Gateway
from dbot.mcp import MAX_FIELD_BYTES, TRUNCATED, build_mcp
from dbot.sandbox.types import CommandResult, Entry
from tests.gateway.conftest import CANARY, FakeSandbox


async def call(gateway: Gateway, tool: str, args: dict[str, Any]) -> Any:
    async with Client(build_mcp(gateway)) as client:
        result = await client.call_tool(tool, args)
    return result.structured_content.get("result", result.structured_content) if result.structured_content else None


async def test_tools_are_listed(gateway: Gateway) -> None:
    async with Client(build_mcp(gateway)) as client:
        names = {t.name for t in await client.list_tools()}
    assert names == {"search_tools", "get_tool_schema", "list_instances", "invoke_tool", "get_approval"}


async def test_search_includes_enabled_instances(gateway: Gateway) -> None:
    hits = await call(gateway, "search_tools", {"query": "ip reputation"})
    by_tool = {h["tool_name"]: h for h in hits}
    assert by_tool["Fake.ip"]["instances"] == ["fake", "fake-nokey"]  # fake-off is disabled
    assert by_tool["Other.ip"]["instances"] == []


async def test_schema_and_unknown_tool(gateway: Gateway) -> None:
    schema = await call(gateway, "get_tool_schema", {"tool_name": "Fake.fake-block-ip"})
    assert schema["tool_name"] == "Fake.fake-block-ip"
    assert schema["instances"] == ["fake", "fake-nokey"]
    missing = await call(gateway, "get_tool_schema", {"tool_name": "Fake.nope"})
    assert "not found" in missing["error"]


async def test_invoke_truncates_and_redacts(gateway: Gateway, sandbox: FakeSandbox) -> None:
    big = "A" * (MAX_FIELD_BYTES * 2)
    sandbox.result = CommandResult(
        status="ok",
        entries=[Entry(human_readable=big), Entry(contents=f"raw {CANARY}")],
        context={"Blob": big},
    )
    out = await call(gateway, "invoke_tool", {"instance": "fake", "command": "ip", "args": {}, "reason": "triage"})
    assert out["status"] == "ok"
    assert out["error"] is None
    assert out["human_readable"].endswith(TRUNCATED)
    assert len(out["human_readable"].encode()) <= MAX_FIELD_BYTES
    assert isinstance(out["context"], str)
    assert out["context"].endswith(TRUNCATED)
    assert len(out["context"].encode()) <= MAX_FIELD_BYTES


async def test_invoke_small_result_joins_entries(gateway: Gateway, sandbox: FakeSandbox) -> None:
    sandbox.result = CommandResult(
        status="ok", entries=[Entry(human_readable="## Hi"), Entry(contents="plain")], context={"IP": {"A": 1}}
    )
    out = await call(gateway, "invoke_tool", {"instance": "fake", "command": "ip", "args": {}, "reason": "triage"})
    assert out["human_readable"] == "## Hi\n\nplain"
    assert out["context"] == {"IP": {"A": 1}}


async def test_invoke_destructive_returns_pending_then_approval_visible(gateway: Gateway, sandbox: FakeSandbox) -> None:
    args = {"instance": "fake", "command": "fake-block-ip", "args": {"ip": "6.6.6.6"}, "reason": "contain"}
    out = await call(gateway, "invoke_tool", args)
    assert out["status"] == "approval_required"
    assert sandbox.calls == []
    approval = await call(gateway, "get_approval", {"approval_id": out["approval_id"]})
    assert approval["status"] == "pending"
    assert approval["requested_by"] == "agent:mcp"


async def test_mcp_roles_from_env(gateway: Gateway, sandbox: FakeSandbox, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DBOT_MCP_ROLES", "viewer")
    out = await call(gateway, "invoke_tool", {"instance": "fake", "command": "ip", "args": {}, "reason": "x"})
    assert out["status"] == "denied"
    assert sandbox.calls == []


async def test_list_instances_tool(gateway: Gateway) -> None:
    rows = await call(gateway, "list_instances", {})
    assert {r["name"] for r in rows} == {"fake", "fake-off", "fake-nokey"}
    assert all("params" not in r for r in rows)


async def test_schema_for_implicit_test_module(gateway: Gateway) -> None:
    schema = await call(gateway, "get_tool_schema", {"tool_name": "Other.test-module"})
    assert schema["integration"] == "Other"
    assert schema["arguments"] == []
    missing = await call(gateway, "get_tool_schema", {"tool_name": "Nope.test-module"})
    assert "not found" in missing["error"]
