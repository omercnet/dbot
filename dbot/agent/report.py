"""IRReport rendering — markdown, JSON, JSONL formats."""

import json

from dbot.agent.models import IRReport


def to_markdown(report: IRReport) -> str:
    """Render IRReport as Markdown investigation report."""
    verdict_badge = f"**{report.verdict.value.upper()}**"
    confidence_pct = f"{report.confidence * 100:.0f}%"

    lines: list[str] = []

    # Header
    lines.append(f"# Investigation Report: {report.alert.title}")
    lines.append("")
    lines.append(f"**Alert ID**: {report.alert.id}")
    lines.append(f"**Verdict**: {verdict_badge} (confidence: {confidence_pct})")
    lines.append(f"**Severity**: {report.alert.severity.value}")
    lines.append(f"**Source**: {report.alert.source or 'N/A'}")
    lines.append("")

    # Summary
    if report.summary:
        lines.append("## Summary")
        lines.append("")
        lines.append(report.summary)
        lines.append("")

    # Indicators
    if report.indicators_found:
        lines.append("## Indicators")
        lines.append("")
        lines.append("| Type | Value | Source |")
        lines.append("|------|-------|--------|")
        for ind in report.indicators_found:
            lines.append(f"| {ind.type} | `{ind.value}` | {ind.source or 'N/A'} |")
        lines.append("")

    # Findings
    if report.findings:
        lines.append("## Findings")
        lines.append("")
        for i, finding in enumerate(report.findings, 1):
            lines.append(f"{i}. {finding}")
        lines.append("")

    # Tool Calls
    if report.tool_calls:
        lines.append("## Tool Calls")
        lines.append("")
        lines.append(f"Total: {len(report.tool_calls)} calls")
        lines.append("")
        for tc in report.tool_calls:
            status_icon = "OK" if tc.status == "success" else tc.status.upper()
            lines.append(f"- **{tc.tool_name}** [{status_icon}] — {tc.reason}")
        lines.append("")

    # Blocked Actions
    if report.blocked_actions:
        lines.append("## Blocked Actions")
        lines.append("")
        lines.append("The following actions were blocked by policy:")
        lines.append("")
        for ba in report.blocked_actions:
            lines.append(f"- **{ba.tool_name}** — {ba.reason} (status: {ba.status})")
        lines.append("")

    # Recommendations
    if report.recommendations:
        lines.append("## Recommended Actions")
        lines.append("")
        for i, rec in enumerate(report.recommendations, 1):
            lines.append(f"{i}. {rec}")
        lines.append("")

    # Phases
    if report.phases_completed:
        lines.append("## Investigation Phases")
        lines.append("")
        for phase in report.phases_completed:
            lines.append(f"- [x] {phase.value}")
        lines.append("")

    # Footer
    lines.append("---")
    lines.append(f"Started: {report.started_at.isoformat()}")
    if report.completed_at:
        lines.append(f"Completed: {report.completed_at.isoformat()}")
    lines.append(f"Duration: {report.total_duration_ms:.0f}ms")
    lines.append(f"LLM turns: {report.llm_turns}")
    if report.error:
        lines.append(f"Error: {report.error}")
    lines.append("")

    return "\n".join(lines)


def to_json(report: IRReport, indent: int = 2) -> str:
    """Render IRReport as pretty-printed JSON."""
    return report.model_dump_json(indent=indent)


def to_jsonl_event(report: IRReport) -> str:
    """Single JSON line for SIEM/SOAR streaming ingestion."""
    data = {
        "event_type": "investigation_complete",
        "alert_id": report.alert.id,
        "alert_title": report.alert.title,
        "verdict": report.verdict.value,
        "confidence": report.confidence,
        "severity": report.alert.severity.value,
        "summary": report.summary,
        "started_at": report.started_at.isoformat(),
        "completed_at": report.completed_at.isoformat() if report.completed_at else None,
        "duration_ms": report.total_duration_ms,
        "indicator_count": len(report.indicators_found),
        "tool_call_count": len(report.tool_calls),
        "blocked_count": len(report.blocked_actions),
        "llm_turns": report.llm_turns,
        "error": report.error,
    }
    return json.dumps(data, default=str)
