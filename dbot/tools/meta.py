"""MCP tool: get_tool_schema — get full argument spec for a tool."""

from typing import Any

from dbot.registry.catalog import Catalog


def make_schema_tool(catalog: Catalog):  # type: ignore[no-untyped-def]
    """Create the get_tool_schema MCP tool function."""

    async def get_tool_schema(tool_name: str) -> dict[str, Any]:
        """Get the full argument and output schema for a specific tool.

        Call this before invoke_tool to understand exactly what arguments
        are required and what the output format looks like.
        Secret/credential arguments are not shown — they are handled
        automatically by the credential store.

        Args:
            tool_name: Fully qualified tool name (e.g., "VirusTotal.vt-get-file")
        """
        return catalog.get_schema(tool_name)

    return get_tool_schema
