"""MCP tool: invoke_tool — execute security integration commands."""

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from dbot.credentials.store import CredentialStore
from dbot.registry.catalog import Catalog

logger = logging.getLogger("dbot.invoke")

type ExecutorFn = Callable[
    [Path, str, dict[str, Any], dict[str, Any]],
    Awaitable[dict[str, Any]],
]


def make_invoke_tool(
    catalog: Catalog,
    credential_store: CredentialStore,
    executor_fn: ExecutorFn,
):  # type: ignore[no-untyped-def]
    """Create the invoke_tool MCP tool function."""

    async def invoke_tool(tool_name: str, args: dict[str, Any], reason: str) -> dict[str, Any]:
        """Execute a security tool command.

        IMPORTANT: Call get_tool_schema first to understand required arguments.

        Dangerous tools (host isolation, account suspension, firewall changes)
        will return an approval_required response instead of executing.

        The reason field is REQUIRED — state why you are calling this tool.
        This becomes the audit trail for post-incident review.

        Args:
            tool_name: Fully qualified tool name (e.g., "VirusTotal.vt-get-file")
            args: Command arguments (non-secret only)
            reason: Why you are calling this tool (audit trail)
        """
        integration, command = catalog.resolve(tool_name)

        if command.dangerous:
            logger.warning(
                "Dangerous tool invocation blocked: %s (reason: %s)",
                tool_name,
                reason,
            )
            return {
                "status": "approval_required",
                "tool_name": tool_name,
                "args": args,
                "reason": reason,
                "description": (f"{command.name} is a dangerous operation. Human approval required."),
            }

        params = credential_store.get(integration.pack)

        result = await executor_fn(
            Path(integration.py_path),
            command.name,
            args,
            params,
        )

        return {
            "tool_name": tool_name,
            "reason": reason,
            "success": result.get("success", False),
            "results": result.get("results", []),
            "error": result.get("error"),
        }

    return invoke_tool
