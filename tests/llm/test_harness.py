"""The eval must skip only when the LLM provider is unavailable, never on unrelated failures."""

import json

import pytest

from tests.llm.test_mcp_agent import provider_unavailable


def _error(type_: str, status: int, message: str = "") -> str:
    return json.dumps({"type": "error", "error": {"type": type_, "status": status, "message": message}})


@pytest.mark.parametrize(
    "stdout",
    [
        _error("provider.rate_limit", 429),
        _error("provider.unknown", 400, "FreeUsageLimitError: Free usage limit reached"),
    ],
)
def test_provider_unavailable_skips(stdout: str) -> None:
    assert provider_unavailable(stdout)


@pytest.mark.parametrize(
    "stdout",
    [
        "",  # e.g. nonzero exit with "Error: local queue overloaded" on stderr only
        _error("provider.auth", 401, "Reconnect OpenCode Console to continue."),
        _error("tool.failed", 503, "overloaded"),
        json.dumps({"type": "text", "part": {"type": "text", "text": "the API is overloaded, rate limit hit"}}),
    ],
)
def test_other_failures_do_not_skip(stdout: str) -> None:
    assert provider_unavailable(stdout) is None
