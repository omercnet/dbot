"""FastMCP server over the gateway. Every tool call goes through ``Gateway.invoke`` as actor ``agent:mcp``, on behalf
of the human named in ``DBOT_MCP_USER``."""

import json
import os
from typing import Any

from fastmcp import FastMCP

from dbot.gateway import TEST_MODULE, Actor, Denied, Gateway, Pending

MAX_FIELD_BYTES = 20 * 1024
TRUNCATED = "...[truncated]"


def mcp_actor() -> Actor:
    """``agent:mcp`` acting for ``DBOT_MCP_USER``, so that person can never approve their own MCP requests."""
    user = (os.environ.get("DBOT_MCP_USER") or "").strip()
    if not user:
        raise ValueError("DBOT_MCP_USER is not set; set it to the user id of the person this MCP server acts for")
    roles = {r.strip() for r in (os.environ.get("DBOT_MCP_ROLES") or "analyst").split(",") if r.strip()}
    return Actor(id="agent:mcp", kind="agent", roles=roles, on_behalf_of=user)


def _cap(text: str, limit: int = MAX_FIELD_BYTES) -> str:
    data = text.encode("utf-8")
    if len(data) <= limit:
        return text
    keep = limit - len(TRUNCATED.encode("utf-8"))
    return data[:keep].decode("utf-8", errors="ignore") + TRUNCATED


def _text(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, default=str)


def build_mcp(gateway: Gateway) -> FastMCP:
    actor = mcp_actor()
    mcp = FastMCP(
        "dbot",
        instructions=(
            "Run XSOAR integration commands on configured instances. Find a tool with search_tools, read its "
            "arguments with get_tool_schema, then call invoke_tool with an instance name. Destructive commands "
            "return approval_required; retry with the approval_id once a human approves it."
        ),
    )

    @mcp.tool
    def search_tools(query: str, limit: int = 10) -> list[dict[str, Any]]:
        """Search integration commands by keyword. Each hit lists the enabled instances that can run it."""
        hits = gateway.catalog.search(query, limit=max(1, min(limit, 50)))
        return [{**h, "instances": gateway.enabled_instances(h["integration"])} for h in hits]

    @mcp.tool
    def get_tool_schema(tool_name: str) -> dict[str, Any]:
        """Arguments and outputs of one '<Integration>.<command>' tool."""
        try:
            schema = gateway.catalog.get_schema(tool_name)
        except KeyError as e:
            name, sep, command = tool_name.rpartition(".")
            if not sep or command != TEST_MODULE.name:
                return {"error": e.args[0]}
            try:
                integration, _ = gateway.resolve(name, command)
            except KeyError as e2:
                return {"error": e2.args[0]}
            schema = {
                "tool_name": tool_name,
                "integration": integration.name,
                "display": integration.display,
                "pack": integration.pack,
                "description": TEST_MODULE.description,
                "dangerous": False,
                "arguments": [],
                "outputs": [],
            }
        return {**schema, "instances": gateway.enabled_instances(schema["integration"])}

    @mcp.tool
    def list_instances() -> list[dict[str, Any]]:
        """Configured integration instances and whether they are ready to use."""
        return gateway.list_instances()

    @mcp.tool
    async def invoke_tool(
        instance: str, command: str, args: dict[str, Any], reason: str, approval_id: str | None = None
    ) -> dict[str, Any]:
        """Run a command on an instance. `reason` is required and audited."""
        result = await gateway.invoke(instance, command, args, reason, actor, approval_id)
        if isinstance(result, Pending | Denied):
            return result.model_dump()
        human = "\n\n".join(
            _text(e.human_readable if e.human_readable is not None else e.contents)
            for e in result.entries
            if e.human_readable is not None or e.contents is not None
        )
        context: Any = result.context
        context_json = json.dumps(context, default=str)
        if len(context_json.encode("utf-8")) > MAX_FIELD_BYTES:
            context = _cap(context_json)
        return {
            "status": result.status,
            "error": _cap(result.error) if result.error else None,
            "error_kind": result.error_kind,
            "human_readable": _cap(human),
            "context": context,
        }

    @mcp.tool
    def get_approval(approval_id: str) -> dict[str, Any]:
        """Status of an approval request (pending, approved, denied, consumed, expired)."""
        approval = gateway.get_approval(approval_id)
        if approval is None:
            return {"error": f"approval {approval_id} does not exist"}
        return approval.model_dump()

    return mcp


def main() -> None:
    try:
        mcp_actor()
    except ValueError as e:
        raise SystemExit(f"dbot mcp: {e}") from None
    build_mcp(Gateway.from_env()).run(show_banner=False)


if __name__ == "__main__":
    main()
