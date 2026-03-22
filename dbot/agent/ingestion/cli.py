"""Alert ingestion — load alerts from file, stdin, or string."""

import sys
import uuid
from pathlib import Path

from dbot.agent.models import Alert


def load_alert_from_file(path: Path) -> Alert:
    """Load Alert from a JSON file. Raises FileNotFoundError or ValidationError."""
    text = path.read_text(encoding="utf-8")
    return _parse_alert(text)


def load_alert_from_stdin() -> Alert:
    """Read JSON from stdin and parse as Alert."""
    return _parse_alert(sys.stdin.read())


def load_alert_from_string(json_str: str) -> Alert:
    """Parse Alert from a JSON string."""
    return _parse_alert(json_str)


def _parse_alert(text: str) -> Alert:
    """Parse and validate alert JSON, auto-generating id if missing."""
    import json

    data = json.loads(text)
    if "id" not in data:
        data["id"] = f"alert-{uuid.uuid4().hex[:8]}"
    return Alert.model_validate(data)
