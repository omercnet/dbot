"""Audit logger for dbot tool invocations."""

import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("dbot.audit")

DEFAULT_AUDIT_PATH = Path("dbot-audit.log")


class AuditLogger:
    """JSON-lines audit logger for tool invocations.

    Each invocation is logged as a single JSON object per line.
    Credentials are NEVER included in audit entries.
    """

    def __init__(self, audit_path: Path | None = None) -> None:
        self._path = audit_path or DEFAULT_AUDIT_PATH

    def log_invocation(
        self,
        tool_name: str,
        args: dict[str, Any],
        reason: str,
        dangerous: bool,
        result: dict[str, Any],
        duration_ms: float,
        approved_by: str | None = None,
    ) -> None:
        """Write a single audit entry as JSON line."""
        entry = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "tool_name": tool_name,
            "args": args,
            "reason": reason,
            "dangerous": dangerous,
            "approved_by": approved_by,
            "result_success": result.get("success"),
            "result_status": result.get("status"),
            "duration_ms": round(duration_ms, 2),
        }

        try:
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, default=str) + "\n")
        except OSError as e:
            logger.error("Failed to write audit log: %s", e)
