"""MCP tool: search_tools — discover available security integrations."""

from typing import Any

from dbot.registry.catalog import Catalog


def make_search_tool(catalog: Catalog):  # type: ignore[no-untyped-def]
    """Create the search_tools MCP tool function."""

    async def search_tools(query: str, category: str | None = None) -> list[dict[str, Any]]:
        """Search available security tools by keyword or category.

        Returns matching tools with name, description, pack, and argument summary.
        Call this first to discover what tools are available.

        Categories include: Data Enrichment, Endpoint, SIEM, Identity,
        Network, Vulnerability, Case Management, Cloud.

        Args:
            query: Search keywords (e.g., "file hash reputation", "isolate host")
            category: Optional category filter
        """
        return catalog.search(query, category, top_k=10)

    return search_tools
