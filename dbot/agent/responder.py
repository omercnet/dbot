"""ResponderAgent — autonomous alert investigation with HITL loop."""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from pydantic_ai import Agent, DeferredToolRequests
from pydantic_ai.messages import ModelMessage

from dbot.agent.deps import IRDeps
from dbot.agent.guardrails import BudgetExceededError, GuardrailConfig, build_toolset
from dbot.agent.models import (
    Alert,
    IRReport,
    Verdict,
)


@dataclass
class InvestigationResult:
    """Result of an autonomous investigation."""

    report: IRReport
    messages: list[ModelMessage] = field(default_factory=list)
    deferred: list[dict[str, Any]] = field(default_factory=list)


RESPONDER_SYSTEM_PROMPT = """\
You are dbot, an autonomous incident response analyst. You are investigating \
a security alert. Your job is to determine if this alert is malicious, \
suspicious, benign, or inconclusive.

You have access to 500+ security tool integrations via three tools:
1. search_tools — discover available tools
2. get_tool_schema — get argument specs before calling a tool
3. invoke_tool — execute a tool (you MUST provide a reason for audit)

Investigation phases:
1. TRIAGE: Read the alert, identify key indicators (IPs, hashes, domains, URLs).
2. ENRICHMENT: Look up each indicator using relevant tools (VirusTotal, Shodan, AbuseIPDB, etc.).
3. CORRELATION: Cross-reference findings. Look for patterns.
4. VERDICT: Determine if the alert is malicious, suspicious, benign, or inconclusive.

Rules:
- Work methodically through each phase.
- ALWAYS provide a 'reason' when invoking tools.
- If a tool returns blocked_by_policy, note it and move on.
- After enrichment, state your verdict with confidence level (0.0-1.0).
- List specific findings and recommended response actions.
- Be concise but thorough.

Your final response MUST include:
- verdict: malicious | suspicious | benign | inconclusive
- confidence: 0.0 to 1.0
- summary: 2-3 sentence assessment
- findings: list of key findings
- recommendations: list of recommended actions
"""


def _emergency_report(alert: Alert, started_at: datetime, reason: str) -> IRReport:
    """Create an incomplete report when investigation fails."""
    return IRReport(
        alert=alert,
        verdict=Verdict.INCONCLUSIVE,
        confidence=0.0,
        summary=f"Investigation incomplete: {reason}",
        started_at=started_at,
        completed_at=datetime.now(UTC),
        error=reason,
    )


class ResponderAgent:
    """Autonomous alert investigation agent with HITL support."""

    def __init__(
        self,
        config: GuardrailConfig | None = None,
        model: str | None = None,
    ) -> None:
        self._config = config or GuardrailConfig.autonomous_default()
        toolset = build_toolset(self._config)
        model_name = model or os.environ.get("DBOT_LLM_MODEL", "openai:gpt-4o")

        self._agent: Agent[IRDeps, str | DeferredToolRequests] = Agent(
            model_name,
            system_prompt=RESPONDER_SYSTEM_PROMPT,
            toolsets=[toolset],
            output_type=str | DeferredToolRequests,  # type: ignore[arg-type]
            deps_type=IRDeps,
        )

    async def investigate(
        self,
        alert: Alert,
        deps: IRDeps,
        on_deferred: Callable[[DeferredToolRequests], Awaitable[dict[str, bool]]] | None = None,
    ) -> InvestigationResult:
        """Run an autonomous investigation on the given alert.

        Args:
            alert: The alert to investigate.
            deps: IR dependencies (catalog, executor, credentials, audit).
            on_deferred: Optional callback for HITL approval. Receives DeferredToolRequests,
                         returns dict mapping tool_call_id → bool (approve/deny).
                         If None, deferred tools are recorded as blocked.
        """
        deps.alert = alert
        started_at = datetime.now(UTC)
        deferred_list: list[dict[str, Any]] = []

        prompt = (
            f"Investigate this alert:\n\n"
            f"**Title**: {alert.title}\n"
            f"**Severity**: {alert.severity.value}\n"
            f"**Source**: {alert.source or 'unknown'}\n"
            f"**Description**: {alert.description}\n"
        )

        if alert.indicators:
            prompt += "\n**Indicators**:\n"
            for ind in alert.indicators:
                prompt += f"- {ind.type}: {ind.value}\n"

        if alert.raw:
            import json

            prompt += f"\n**Raw alert data**:\n```json\n{json.dumps(alert.raw, indent=2)[:2000]}\n```\n"

        try:
            result = await self._agent.run(prompt, deps=deps)
            messages = result.all_messages()

            # Handle HITL loop
            while isinstance(result.output, DeferredToolRequests):
                if on_deferred:
                    approvals = await on_deferred(result.output)
                    from pydantic_ai import DeferredToolResults

                    result = await self._agent.run(
                        message_history=messages,
                        deps=deps,
                        deferred_tool_results=DeferredToolResults(approvals=approvals),
                    )
                    messages = result.all_messages()
                else:
                    # No HITL handler — record as blocked
                    for call in result.output.approvals:
                        deferred_list.append(
                            {
                                "tool_name": call.tool_name,
                                "args": call.args,
                                "tool_call_id": call.tool_call_id,
                                "status": "blocked_by_policy",
                            }
                        )
                    break

            # Parse the agent's final text output into a report
            report = _build_report(
                alert=alert,
                agent_output=result.output if isinstance(result.output, str) else "",
                deps=deps,
                started_at=started_at,
                deferred_list=deferred_list,
            )
            return InvestigationResult(
                report=report,
                messages=messages,
                deferred=deferred_list,
            )

        except BudgetExceededError as e:
            report = _emergency_report(alert, started_at, str(e))
            return InvestigationResult(report=report, deferred=deferred_list)

        except Exception as e:
            report = _emergency_report(alert, started_at, f"Unexpected error: {e}")
            return InvestigationResult(report=report, deferred=deferred_list)

    @property
    def agent(self) -> Agent[IRDeps, str | DeferredToolRequests]:
        """Access the underlying PydanticAI agent (for testing with .override())."""
        return self._agent


def _build_report(
    alert: Alert,
    agent_output: str,
    deps: IRDeps,
    started_at: datetime,
    deferred_list: list[dict[str, Any]],
) -> IRReport:
    """Parse agent output text into a structured IRReport."""
    completed_at = datetime.now(UTC)
    duration_ms = (completed_at - started_at).total_seconds() * 1000

    # Try to extract verdict from agent output
    verdict = Verdict.INCONCLUSIVE
    confidence = 0.0
    output_lower = agent_output.lower()

    for v in Verdict:
        if v.value in output_lower:
            verdict = v
            break

    # Try to extract confidence
    import re

    conf_match = re.search(r"confidence[:\s]+(\d+\.?\d*)", output_lower)
    if conf_match:
        with suppress(ValueError):
            confidence = min(float(conf_match.group(1)), 1.0)

    from dbot.agent.models import ToolCall

    blocked_actions = [
        ToolCall(
            tool_name=d["tool_name"],
            args=d.get("args", {}),
            status="blocked_by_policy",
        )
        for d in deferred_list
    ]

    return IRReport(
        alert=alert,
        verdict=verdict,
        confidence=confidence,
        summary=agent_output[:500] if agent_output else "No output from agent.",
        phases_completed=list(deps.phase_tracker) if deps.phase_tracker else [],
        blocked_actions=blocked_actions,
        started_at=started_at,
        completed_at=completed_at,
        total_duration_ms=duration_ms,
        llm_turns=deps.tool_call_count,
    )
