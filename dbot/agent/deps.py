"""IRDeps — dependencies injected into agent tools via RunContext."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from dbot.agent.models import Alert, InvestigationPhase
from dbot.audit import AuditLogger
from dbot.credentials.store import CredentialStore
from dbot.registry.catalog import Catalog

if TYPE_CHECKING:
    from dbot.agent.guardrails import GuardrailConfig

# Matches execute_inprocess / execute_subprocess signature
type ExecutorFn = Callable[[Path, str, dict[str, Any], dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass
class IRDeps:
    """Dependencies carried by RunContext[IRDeps] into every tool call."""

    # Core infrastructure (always required)
    catalog: Catalog
    credential_store: CredentialStore
    executor: ExecutorFn
    audit: AuditLogger
    guardrails: GuardrailConfig

    # Per-run configuration
    model_name: str = "openai:gpt-4o"

    # Per-run state
    alert: Alert | None = None  # None in Chat mode
    phase_tracker: list[InvestigationPhase] = field(default_factory=list)
    tool_call_count: int = 0  # incremented by invoke_tool
