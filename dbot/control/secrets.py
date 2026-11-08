"""Secret references (env:// and file://), resolution, and redaction.

A param value is a secret reference when it is a string starting with ``env://`` or ``file://``.
Every other string is a literal. Resolved values are remembered so logs and the audit chain can redact them.
"""

import os
import threading
from pathlib import Path
from typing import Any

SUPPORTED_SCHEMES = ("env", "file")
# Schemes that look like secret-backend references. They are rejected rather than passed through as literals,
# so a typo or an unsupported backend never ends up sent to a vendor as the credential itself.
REJECTED_SCHEMES = frozenset(
    {"vault", "aws-sm", "awssm", "aws-ssm", "ssm", "secretsmanager", "gcp-sm", "gcpsm", "azure-kv", "keyvault"}
    | {"op", "secret", "secrets", "k8s", "kubernetes", "sops", "doppler", "infisical", "bitwarden", "bw"}
)
REDACTED = "***REDACTED***"

_known: set[str] = set()
_lock = threading.Lock()


class SecretError(Exception):
    """A secret reference could not be resolved. The message names the reference, never a value."""


def scheme_of(value: str) -> str | None:
    """Return the lowercased ``<scheme>`` of ``<scheme>://...``, or None."""
    head, sep, _ = value.partition("://")
    if not sep or not head or not all(c.isalnum() or c in "-_+." for c in head):
        return None
    return head.lower()


def is_secret_ref(value: object) -> bool:
    """True if ``value`` is an env:// or file:// reference."""
    return isinstance(value, str) and scheme_of(value) in SUPPORTED_SCHEMES


def check_ref(value: str) -> str | None:
    """Return a problem description for a malformed or unsupported secret reference, else None."""
    scheme = scheme_of(value)
    if scheme in REJECTED_SCHEMES:
        return f"unsupported secret scheme '{scheme}://'; only env:// and file:// are supported"
    if scheme == "env":
        name = value[len("env://") :]
        if not name or not (name.replace("_", "a").isalnum() and not name[0].isdigit()):
            return f"invalid env reference {value!r}; expected env://NAME"
    if scheme == "file":
        path = value[len("file://") :]
        if not path.startswith("/"):
            return f"invalid file reference {value!r}; expected an absolute path like file:///run/secrets/name"
    return None


def resolve_value(value: str) -> str:
    """Resolve one string: a secret reference becomes its value, a literal is returned unchanged."""
    if not is_secret_ref(value):
        return value
    problem = check_ref(value)
    if problem:
        raise SecretError(problem)
    if scheme_of(value) == "env":
        name = value[len("env://") :]
        resolved = os.environ.get(name)
        if resolved is None:
            raise SecretError(f"secret reference {value!r}: environment variable {name} is not set")
    else:
        path = Path(value[len("file://") :])
        try:
            resolved = path.read_text(encoding="utf-8").rstrip("\r\n")
        except (OSError, UnicodeDecodeError):
            raise SecretError(f"secret reference {value!r}: file {path} is missing or unreadable") from None
    if not resolved:
        raise SecretError(f"secret reference {value!r} resolved to an empty value")
    with _lock:
        _known.add(resolved)
    return resolved


def _resolve(obj: Any) -> Any:
    if isinstance(obj, str):
        return resolve_value(obj)
    if isinstance(obj, dict):
        return {k: _resolve(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [_resolve(v) for v in obj]
    return obj


def resolve(params: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``params`` with every secret reference resolved, recursing into dicts and lists."""
    resolved: dict[str, Any] = _resolve(params)
    return resolved


def known_values() -> set[str]:
    """Every secret value resolved so far in this process."""
    with _lock:
        return set(_known)


def _redact_str(text: str, values: list[str]) -> str:
    for v in values:
        if v in text:
            text = text.replace(v, REDACTED)
    return text


def _redact(obj: Any, values: list[str]) -> Any:
    if isinstance(obj, str):
        return _redact_str(obj, values)
    if isinstance(obj, dict):
        return {(_redact_str(k, values) if isinstance(k, str) else k): _redact(v, values) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [_redact(v, values) for v in obj]
    return obj


def redact(obj: Any) -> Any:
    """Replace every known secret value in a string, or in the strings of a nested dict/list, with a marker."""
    # Longest first so a secret that contains another secret is fully masked.
    values = sorted(known_values(), key=len, reverse=True)
    return _redact(obj, values) if values else obj
