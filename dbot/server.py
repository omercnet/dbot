"""FastMCP server entrypoint for dbot."""

import logging
import os
from pathlib import Path

import yaml  # type: ignore[import-untyped]
from fastmcp import FastMCP

from dbot.credentials.store import CredentialStore
from dbot.registry.catalog import Catalog
from dbot.registry.indexer import index_content
from dbot.runtime.common_server import bootstrap_common_modules
from dbot.runtime.executor import execute_inprocess, execute_subprocess
from dbot.tools.invoke import make_invoke_tool
from dbot.tools.meta import make_schema_tool
from dbot.tools.search import make_search_tool

logger = logging.getLogger("dbot")

CONTENT_ROOT = Path(__file__).parent.parent / "content"
CREDENTIALS_PATH = Path(__file__).parent.parent / "config" / "credentials.yaml"
ENABLED_PACKS_PATH = Path(__file__).parent.parent / "config" / "enabled_packs.yaml"


def create_server() -> FastMCP:
    """Initialize and return the dbot MCP server."""
    # 1. Bootstrap the XSOAR runtime shim
    logger.info("Bootstrapping CommonServerPython...")
    bootstrap_common_modules(CONTENT_ROOT)

    # 2. Load enabled packs config
    enabled_packs = None
    if ENABLED_PACKS_PATH.exists():
        with open(ENABLED_PACKS_PATH, encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
        enabled_packs = config.get("enabled_packs")

    # 3. Index integrations
    logger.info("Indexing integrations...")
    integrations = index_content(CONTENT_ROOT, enabled_packs)
    catalog = Catalog(integrations)
    logger.info(
        "Indexed %d commands from %d integrations",
        catalog.stats["total_commands"],
        catalog.stats["total_integrations"],
    )

    # 4. Load credentials
    credential_store = CredentialStore(CREDENTIALS_PATH if CREDENTIALS_PATH.exists() else None)
    logger.info("Credentials configured for: %s", credential_store.configured_packs())

    # 5. Select executor based on DBOT_EXECUTION_MODE
    execution_mode = os.environ.get("DBOT_EXECUTION_MODE", "inprocess")
    if execution_mode == "subprocess":
        executor_fn = lambda py, cmd, args, params: execute_subprocess(  # noqa: E731
            py, cmd, args, params, content_root=CONTENT_ROOT
        )
    else:
        executor_fn = execute_inprocess

    # 6. Create MCP server
    mcp = FastMCP("dbot")

    # 7. Register tools
    mcp.tool()(make_search_tool(catalog))
    mcp.tool()(make_schema_tool(catalog))
    mcp.tool()(make_invoke_tool(catalog, credential_store, executor_fn))

    return mcp


mcp = create_server()

if __name__ == "__main__":
    mcp.run()
