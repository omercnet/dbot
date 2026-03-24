"""Guardrail system — tool filtering, approval gating, budget enforcement."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic_ai import FunctionToolset, RunContext
from pydantic_ai.toolsets import AbstractToolset

from dbot.agent.deps import IRDeps
from dbot.agent.models import ToolCall

logger = logging.getLogger("dbot.guardrails")


class BudgetExceededError(Exception):
    """Raised when the tool call budget is exhausted."""


@dataclass
class GuardrailConfig:
    """Configuration for agent tool restrictions."""

    # Hard deny — tool never reaches LLM
    blocked_tools: set[str] = field(default_factory=set)
    blocked_categories: set[str] = field(default_factory=set)

    # Soft gate — tool visible but requires HITL approval
    require_approval_tools: set[str] = field(default_factory=set)
    require_approval_categories: set[str] = field(default_factory=set)

    # Budget limits
    max_tool_calls: int = 50
    timeout_seconds: float = 300.0

    @classmethod
    def chat_default(cls) -> GuardrailConfig:
        """Permissive config for interactive chat mode.

        No approval_required fields — chat uses output_type=str only,
        which is incompatible with DeferredToolRequests.
        """
        return cls(max_tool_calls=100, timeout_seconds=600.0)

    @classmethod
    def autonomous_default(cls) -> GuardrailConfig:
        """Restrictive config for autonomous responder mode."""
        return cls(
            blocked_categories={"Endpoint"},
            require_approval_tools=set(),
            max_tool_calls=30,
            timeout_seconds=300.0,
        )


def build_toolset(config: GuardrailConfig) -> AbstractToolset:
    """Build a FunctionToolset with 3 native IR tools, layered with guardrails.

    Returns a toolset chain:
        FunctionToolset (3 tools)
        → FilteredToolset (hard deny blocked tools/categories)
        → ApprovalRequiredToolset (soft gate, only if require_approval_* set)
    """
    toolset = FunctionToolset()

    @toolset.tool  # type: ignore[arg-type]
    async def search_tools(ctx: RunContext[IRDeps], query: str, category: str | None = None) -> list[dict[str, Any]]:
        """Search for security tools matching a query. Only call when the user asks to investigate something specific.

        Args:
            query: Search keywords (e.g., "file hash reputation", "isolate host")
            category: Optional category filter (Data Enrichment, Endpoint, SIEM, etc.)
        """
        return ctx.deps.catalog.search(query, category, top_k=10)

    @toolset.tool  # type: ignore[arg-type]
    async def get_tool_schema(ctx: RunContext[IRDeps], tool_name: str) -> dict[str, Any]:
        """Get argument schema for a tool. Call after search_tools to check required args before invoking.

        Args:
            tool_name: Fully qualified tool name from search_tools results
        """
        return ctx.deps.catalog.get_schema(tool_name)

    @toolset.tool  # type: ignore[arg-type]
    async def invoke_tool(
        ctx: RunContext[IRDeps],
        tool_name: str,
        args: dict[str, Any],
        reason: str,
    ) -> dict[str, Any]:
        """Run a security tool. Requires get_tool_schema first to know the arguments.

        Args:
            tool_name: Fully qualified tool name from search_tools results
            args: Command arguments matching the schema
            reason: Why you are calling this tool (required for audit)
        """
        deps = ctx.deps
        guardrails = deps.guardrails

        # Budget check
        if deps.tool_call_count >= guardrails.max_tool_calls:
            raise BudgetExceededError(f"Tool call budget exhausted ({guardrails.max_tool_calls} calls)")
        deps.tool_call_count += 1

        # Resolve tool
        try:
            integration, command = deps.catalog.resolve(tool_name)
        except KeyError:
            return {
                "status": "error",
                "tool_name": tool_name,
                "error": f"Tool '{tool_name}' not found",
            }

        # Check guardrails — blocked categories
        if integration.category.lower() in {c.lower() for c in guardrails.blocked_categories}:
            blocked = ToolCall(
                tool_name=tool_name,
                args=args,
                reason=reason,
                status="blocked_by_policy",
            )
            logger.info("Tool blocked by category policy: %s (%s)", tool_name, integration.category)
            return blocked.model_dump()

        # Check guardrails — blocked tools
        if tool_name in guardrails.blocked_tools:
            blocked = ToolCall(
                tool_name=tool_name,
                args=args,
                reason=reason,
                status="blocked_by_policy",
            )
            logger.info("Tool blocked by tool policy: %s", tool_name)
            return blocked.model_dump()

        # Check dangerous flag
        if command.dangerous:
            logger.warning("Dangerous tool invocation: %s (reason: %s)", tool_name, reason)
            return {
                "status": "approval_required",
                "tool_name": tool_name,
                "args": args,
                "reason": reason,
                "description": f"{command.name} is a dangerous operation. Human approval required.",
            }

        # Live-reload credentials from DB (picks up saves from settings UI)
        if deps.config_db is not None:
            for pack_name in deps.config_db.get_all_credential_packs_filtered():
                deps.credential_store._credentials[pack_name] = deps.config_db.get_decrypted_pack(pack_name)

        # Check credentials — if pack needs creds but none configured, return credentials_required
        if integration.credential_params and not deps.credential_store.has(integration.pack):
            logger.info("Credentials required for %s (pack: %s)", tool_name, integration.pack)
            config_params = []
            for p in integration.params:
                if p.hidden or p.type in (8, 15, 17):
                    continue
                if p.is_credential or p.required:
                    param_info: dict[str, object] = {
                        "name": p.name,
                        "display": p.display or p.name,
                        "type": p.type,
                        "required": p.required,
                    }
                    if p.default:
                        param_info["default"] = p.default
                    if p.display_password:
                        param_info["display_password"] = p.display_password
                    config_params.append(param_info)
            return {
                "status": "credentials_required",
                "tool_name": tool_name,
                "pack": integration.pack,
                "error": f"Pack '{integration.pack}' requires credentials. Configure via /settings.",
                "required_credentials": [p.name for p in integration.params if p.is_credential],
                "config_params": config_params,
            }

        # Execute — reconstruct nested param structure expected by integrations
        raw_creds = deps.credential_store.get(integration.pack)
        params: dict[str, object] = {}
        for p in integration.params:
            if p.type == 9:
                params[p.name] = {
                    "identifier": raw_creds.get(f"{p.name}_id", raw_creds.get(p.name, "")),
                    "password": raw_creds.get(f"{p.name}_password", ""),
                }
            elif p.name in raw_creds:
                params[p.name] = raw_creds[p.name]
            elif p.default is not None:
                params[p.name] = p.default
        start = time.monotonic()

        result = await deps.executor(
            Path(integration.py_path),
            command.name,
            args,
            params,
        )

        duration_ms = (time.monotonic() - start) * 1000

        # Audit
        deps.audit.log_invocation(
            tool_name=tool_name,
            args=args,
            reason=reason,
            dangerous=command.dangerous,
            result=result,
            duration_ms=duration_ms,
        )

        error_str = str(result.get("error", "")).lower()
        results_str = str(result.get("results", "")).lower()
        auth_keywords = (
            "401",
            "unauthorized",
            "authentication failed",
            "invalid credentials",
            "access denied",
            "forbidden",
            "invalid api key",
            "token expired",
        )
        is_auth_error = not result.get("success", False) and any(
            kw in error_str or kw in results_str for kw in auth_keywords
        )

        if is_auth_error and integration.credential_params:
            config_params = []
            for p in integration.params:
                if p.hidden or p.type in (8, 15, 17):
                    continue
                if p.is_credential or p.required:
                    param_info: dict[str, object] = {
                        "name": p.name,
                        "display": p.display or p.name,
                        "type": p.type,
                        "required": p.required,
                    }
                    if p.default:
                        param_info["default"] = p.default
                    if p.display_password:
                        param_info["display_password"] = p.display_password
                    config_params.append(param_info)
            return {
                "status": "credentials_invalid",
                "tool_name": tool_name,
                "pack": integration.pack,
                "error": result.get("error", "Authentication failed"),
                "config_params": config_params,
            }

        return {
            "tool_name": tool_name,
            "reason": reason,
            "success": result.get("success", False),
            "results": result.get("results", []),
            "error": result.get("error"),
            "duration_ms": round(duration_ms, 2),
        }

    # Layer 1: Filter out blocked tools/categories from LLM visibility
    has_blocks = config.blocked_tools or config.blocked_categories

    if has_blocks:
        blocked_lower = {t.lower() for t in config.blocked_tools}

        def _filter_fn(ctx: RunContext[IRDeps], tool_def: Any) -> bool:
            name = tool_def.name.lower()
            # Only filter invoke_tool — search and schema are always visible
            if name in ("search_tools", "get_tool_schema"):
                return True
            # invoke_tool: always visible (guardrail logic is inside the function)
            return name not in blocked_lower

        toolset = toolset.filtered(_filter_fn)  # type: ignore[assignment,arg-type]

    # Layer 2: Require approval for specific tools (only in responder mode)
    has_approvals = config.require_approval_tools or config.require_approval_categories
    if has_approvals:
        toolset = toolset.approval_required(  # type: ignore[assignment]
            lambda ctx, tool_def, tool_args: tool_def.name in config.require_approval_tools
        )

    return toolset
