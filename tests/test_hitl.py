from pathlib import Path
from typing import Any

import pytest

from dbot.credentials.store import CredentialStore
from dbot.registry.catalog import Catalog
from dbot.registry.models import CommandDef, IntegrationDef
from dbot.tools.invoke import make_invoke_tool


def _make_test_catalog() -> Catalog:
    safe_cmd = CommandDef(name="safe-cmd", description="safe", dangerous=False)
    dangerous_cmd = CommandDef(name="endpoint-isolation", description="isolate host", dangerous=True)
    integration = IntegrationDef(
        pack="TestPack",
        name="TestIntegration",
        py_path="/tmp/fake.py",
        commands=[safe_cmd, dangerous_cmd],
    )
    return Catalog([integration])


async def _fake_executor(py: Path, cmd: str, args: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    return {"success": True, "results": [{"data": "ok"}], "logs": []}


class TestHITLGate:
    @pytest.mark.asyncio
    async def test_dangerous_tool_returns_approval_required(self) -> None:
        catalog = _make_test_catalog()
        store = CredentialStore()
        invoke = make_invoke_tool(catalog, store, _fake_executor)
        result = await invoke("TestPack.endpoint-isolation", {"device_id": "abc"}, "C2 detected")
        assert result["status"] == "approval_required"
        assert result["tool_name"] == "TestPack.endpoint-isolation"
        assert result["reason"] == "C2 detected"

    @pytest.mark.asyncio
    async def test_safe_tool_executes_normally(self) -> None:
        catalog = _make_test_catalog()
        store = CredentialStore()
        invoke = make_invoke_tool(catalog, store, _fake_executor)
        result = await invoke("TestPack.safe-cmd", {"arg": "val"}, "testing")
        assert result["success"] is True
        assert result.get("status") != "approval_required"
        assert result["reason"] == "testing"
