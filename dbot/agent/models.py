"""IR domain models — alerts, investigations, reports, verdicts."""

import enum
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class Severity(enum.StrEnum):
    """Alert severity levels."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class Verdict(enum.StrEnum):
    """Investigation verdict."""

    MALICIOUS = "malicious"
    SUSPICIOUS = "suspicious"
    BENIGN = "benign"
    INCONCLUSIVE = "inconclusive"


class InvestigationPhase(enum.StrEnum):
    """Phases of an autonomous investigation."""

    TRIAGE = "triage"
    ENRICHMENT = "enrichment"
    CORRELATION = "correlation"
    VERDICT = "verdict"
    RESPONSE = "response"
    COMPLETE = "complete"


class Indicator(BaseModel):
    """An observable indicator found during investigation."""

    type: str  # ip, domain, file_hash, url, email, hostname
    value: str
    source: str = ""  # which tool provided this
    context: dict[str, Any] = Field(default_factory=dict)


class Alert(BaseModel):
    """Incoming alert to investigate."""

    id: str
    title: str
    description: str = ""
    severity: Severity = Severity.MEDIUM
    source: str = ""  # e.g., "Splunk", "CrowdStrike", "manual"
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    indicators: list[Indicator] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)  # original alert payload


class ToolCall(BaseModel):
    """Record of a tool invocation during investigation."""

    tool_name: str
    args: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""
    result: dict[str, Any] = Field(default_factory=dict)
    status: str = "success"  # success, error, approval_required, blocked_by_policy
    duration_ms: float = 0.0


class IRReport(BaseModel):
    """Complete investigation report."""

    alert: Alert
    verdict: Verdict = Verdict.INCONCLUSIVE
    confidence: float = 0.0  # 0.0 to 1.0
    summary: str = ""
    findings: list[str] = Field(default_factory=list)
    indicators_found: list[Indicator] = Field(default_factory=list)
    tool_calls: list[ToolCall] = Field(default_factory=list)
    blocked_actions: list[ToolCall] = Field(default_factory=list)  # tools that were blocked by policy
    recommendations: list[str] = Field(default_factory=list)
    phases_completed: list[InvestigationPhase] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    total_duration_ms: float = 0.0
    llm_turns: int = 0
    error: str | None = None
