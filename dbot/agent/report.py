"""IRReport: the responder's report (C9). The model writes only the Assessment; dbot fills in the rest."""

import json
from typing import Any, Literal

from pydantic import BaseModel, Field

Verdict = Literal["benign", "suspicious", "malicious", "inconclusive"]


class Assessment(BaseModel):
    """The model's final answer. Evidence and actions come from the gateway, never from the model."""

    verdict: Verdict = Field(description="One of: benign, suspicious, malicious, inconclusive.")
    confidence: float = Field(ge=0.0, le=1.0, description="How sure you are of the verdict, from 0 to 1.")
    summary: str = Field(description="Two or three sentences: what the tool results showed and why the verdict.")


class Evidence(BaseModel):
    instance: str
    command: str
    args: dict[str, Any] = Field(default_factory=dict)
    finding: str
    status: str = "ok"


class ActionTaken(BaseModel):
    instance: str
    command: str
    args: dict[str, Any] = Field(default_factory=dict)
    result: str = ""
    approval_id: str | None = None
    status: str = "ok"


class PendingAction(BaseModel):
    approval_id: str
    instance: str
    command: str
    args: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""


class IRReport(BaseModel):
    alert_id: str = ""
    verdict: Verdict
    confidence: float = Field(ge=0.0, le=1.0)
    summary: str
    evidence: list[Evidence] = Field(default_factory=list)
    actions_taken: list[ActionTaken] = Field(default_factory=list)
    actions_pending: list[PendingAction] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

    def to_markdown(self) -> str:
        lines = [
            f"# IR report: {self.alert_id}",
            "",
            f"**Verdict:** {self.verdict} (confidence {self.confidence:.2f})",
            "",
            "## Summary",
            "",
            self.summary,
            "",
            "## Evidence",
            "",
        ]
        if not self.evidence:
            lines.append("None.")
        for ev in self.evidence:
            lines.append(f"- [{ev.status}] `{ev.instance}` `{ev.command}` {_args(ev.args)}: {ev.finding}")
        lines += ["", "## Actions taken", ""]
        if not self.actions_taken:
            lines.append("None.")
        for act in self.actions_taken:
            ref = f" (approval {act.approval_id})" if act.approval_id else ""
            lines.append(f"- [{act.status}] `{act.instance}` `{act.command}` {_args(act.args)}{ref}: {act.result}")
        lines += ["", "## Actions pending approval", ""]
        if not self.actions_pending:
            lines.append("None.")
        for pa in self.actions_pending:
            lines.append(f"- {pa.approval_id}: `{pa.instance}` `{pa.command}` {_args(pa.args)}: {pa.reason}")
        if self.errors:
            lines += ["", "## Errors", ""]
            lines += [f"- {err}" for err in self.errors]
        return "\n".join(lines) + "\n"


def _args(args: dict[str, Any]) -> str:
    return json.dumps(args, sort_keys=True, default=str)
