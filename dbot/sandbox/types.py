"""Shared contract between the sandbox, the gateway and the MCP/agent layers."""

from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from dbot.catalog.models import IntegrationDef

ENTRY_ERROR = 4  # XSOAR EntryType.ERROR; any entry of this type makes the result an error.

ErrorKind = Literal["integration", "import", "runtime", "timeout", "killed", "sandbox"]


class Entry(BaseModel):
    """One `demisto.results()` entry, normalized."""

    type: int = 1
    contents: Any = None
    human_readable: str | None = None
    entry_context: dict[str, Any] | None = None


class ExecRequest(BaseModel):
    """One command execution. `params` holds resolved secrets: never log or persist it."""

    command: str
    args: dict[str, Any] = Field(default_factory=dict)
    params: dict[str, Any] = Field(default_factory=dict)
    timeout_s: float = 60.0
    proxy: str | None = None  # HTTPS_PROXY for the sandbox (egress allowlist proxy or replay proxy)


class CommandResult(BaseModel):
    status: Literal["ok", "error"]
    error: str | None = None
    error_kind: ErrorKind | None = None
    entries: list[Entry] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)  # merged entry_context
    logs: list[str] = Field(default_factory=list)
    duration_ms: int = 0
    image: str = ""


class Sandbox(Protocol):
    async def run(self, integration: IntegrationDef, req: ExecRequest) -> CommandResult: ...
