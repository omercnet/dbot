# IR Agent Harness — Implementation Plan

> Status: READY TO EXECUTE  
> Stack: Python 3.14, pydantic-ai 0.2+, hatch/uv  
> Approach: TDD (write failing tests first, then implementation)  
> Verified against: installed pydantic-ai in `.venv/lib/python3.14/site-packages/pydantic_ai/`  
> Architecture: **Hybrid native FunctionToolset** (updated 2026-03-21)

---

## Architecture Decision: Native FunctionToolset

The agent harness uses a **native `FunctionToolset`** — three Python functions that call
`catalog` and `executor` directly — instead of wrapping the MCP server via `FastMCPToolset`.

**Benefits**:
- `RunContext[IRDeps]` gives tools direct access to `catalog`, `credential_store`, `executor`, `audit`, `guardrails`
- Guardrail logic lives *inside* `invoke_tool`, not in a wrapper predicate — more transparent
- Tests use `TestModel` + the real `FunctionToolset` — no MCP server, no mocking
- Budget counting, audit logging, and blocked-tool logic are first-class, not layered
- The MCP server (`dbot/server.py`) is **unchanged** — still serves external clients (Claude Desktop, etc.)

**Key implication**: `ChatAgent` and `ResponderAgent` constructors take `IRDeps` (which carries
`catalog`, `credential_store`, `executor`) instead of `mcp: FastMCP` + `catalog: Catalog`.

---

## Verified API Surface (pydantic-ai installed version)

```python
# All confirmed importable:
from pydantic_ai import Agent, FunctionToolset, DeferredToolRequests, DeferredToolResults
from pydantic_ai import ToolApproved, ToolDenied, RunContext
from pydantic_ai.toolsets.filtered import FilteredToolset       # filter_func(ctx, tool_def) -> bool ✅
from pydantic_ai.toolsets.approval_required import ApprovalRequiredToolset
# approval_required_func(ctx, tool_def, tool_args: dict) -> bool ✅
from pydantic_ai.models.test import TestModel                    # call_tools='all'|list[str] ✅

# FunctionToolset(tools=[...]) OR decorator style ✅
# FilteredToolset wraps FunctionToolset — confirmed with live test ✅
# ApprovalRequiredToolset wraps FilteredToolset — confirmed with live test ✅
# DeferredToolRequests: import from pydantic_ai (re-exported from pydantic_ai.tools)
```

### FunctionToolset decorator pattern (verified from source)
```python
from pydantic_ai import FunctionToolset, RunContext

toolset = FunctionToolset()

@toolset.tool   # takes RunContext as first arg
async def my_tool(ctx: RunContext[MyDeps], param: str) -> dict:
    """Tool description from docstring."""
    return {"result": ctx.deps.do_something(param)}

# Layering (verified live):
filtered = FilteredToolset(wrapped=toolset, filter_func=lambda ctx, td: True)
gated = ApprovalRequiredToolset(wrapped=filtered, approval_required_func=lambda ctx, td, ta: False)
```

### HITL data structures (verified from tools.py source)
```python
@dataclass
class DeferredToolRequests:
    calls: list[ToolCallPart]         # deferred for external execution
    approvals: list[ToolCallPart]     # deferred for human approval (ApprovalRequired raised)
    metadata: dict[str, dict]

@dataclass
class ToolApproved:
    override_args: dict | None = None
    kind: Literal['tool-approved'] = 'tool-approved'

@dataclass
class ToolDenied:
    message: str = 'The tool call was denied.'
    kind: Literal['tool-denied'] = 'tool-denied'

@dataclass
class DeferredToolResults:
    calls: dict[str, DeferredToolCallResult]
    approvals: dict[str, ToolApproved | ToolDenied]  # keyed by tool_call_id
```

### Resume pattern (verified from result.py)
```python
result1 = await agent.run(prompt, deps=deps, output_type=[IRReport, DeferredToolRequests])
# assert isinstance(result1.output, DeferredToolRequests)

approvals = {call.tool_call_id: ToolApproved() for call in result1.output.approvals}

result2 = await agent.run(
    None,                                      # no new user prompt
    message_history=result1.all_messages(),
    deferred_tool_results=DeferredToolResults(approvals=approvals),
    deps=deps,
    output_type=[IRReport, DeferredToolRequests],
)
```

---

## Repository Layout (new files only)

```
dbot/
├── dbot/agent/
│   ├── __init__.py            # re-exports: ChatAgent, ResponderAgent, IRDeps, GuardrailConfig
│   ├── deps.py                # IRDeps @dataclass  ← carries catalog, credential_store, executor
│   ├── guardrails.py          # GuardrailConfig, build_toolset (FunctionToolset), BudgetExceededError
│   ├── models.py              # Alert, IRReport, InvestigationPhase, Indicator, Verdict, Severity
│   ├── chat.py                # ChatAgent (multi-turn, streaming)
│   ├── responder.py           # ResponderAgent (autonomous loop + HITL)
│   ├── report.py              # to_markdown, to_json, to_jsonl_event
│   ├── cli.py                 # Typer apps: dbot-chat, dbot-respond, dbot-watch
│   └── ingestion/
│       ├── __init__.py
│       ├── cli.py             # load_alert_from_file/stdin/string
│       └── watcher.py         # AlertWatcher (watchdog-based file-drop)
└── tests/agent/
    ├── __init__.py
    ├── conftest.py            # mock_audit, sample_alert, sample_report, make_deps fixtures
    ├── test_models.py
    ├── test_guardrails.py
    ├── test_deps.py
    ├── test_chat.py
    ├── test_responder.py
    ├── test_report.py
    ├── test_ingestion_cli.py
    ├── test_ingestion_watcher.py
    └── test_integration.py

# Modified:
pyproject.toml                 # +watchdog>=4.0, +typer>=0.12, +rich>=13.0, +CLI scripts
dbot/server.py                 # +module-level `catalog` export (for CLI use)
dbot/registry/catalog.py       # +get_category(pack: str) -> str
tests/test_catalog.py          # +test for get_category
```

---

## Task Breakdown (TDD: write failing tests first, then implement)

### ROUND 1 — No dependencies (run in parallel)

---

#### T-01 · `pyproject.toml` — Add agent dependencies and scripts

**Files modified**: `pyproject.toml`

```toml
[project]
dependencies = [
    # ... existing ...
    "watchdog>=4.0",
    "typer>=0.12",
    "rich>=13.0",
]

[project.scripts]
dbot-chat    = "dbot.agent.cli:chat_app"
dbot-respond = "dbot.agent.cli:respond_app"
dbot-watch   = "dbot.agent.cli:watch_app"

[tool.hatch.envs.default.scripts]
test-agent = "pytest {args:tests/agent/} -v"
```

**Tests**: none (build config)

**Commit**: `build: add agent harness deps (watchdog, typer, rich) and CLI scripts`

---

#### T-02 · `dbot/agent/models.py` — Core domain models

**Files**: `dbot/agent/models.py`, `tests/agent/test_models.py`

```python
from __future__ import annotations
import uuid
from datetime import datetime
from enum import Enum
from typing import Any
from pydantic import BaseModel, Field

class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH     = "high"
    MEDIUM   = "medium"
    LOW      = "low"
    INFO     = "info"

class Verdict(str, Enum):
    MALICIOUS    = "malicious"
    SUSPICIOUS   = "suspicious"
    BENIGN       = "benign"
    INCONCLUSIVE = "inconclusive"

class Alert(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str
    description: str
    severity: Severity = Severity.MEDIUM
    source: str = "unknown"
    raw: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)

class Indicator(BaseModel):
    type: str           # "ip" | "domain" | "file_hash" | "user" | "url"
    value: str
    malicious: bool | None = None
    source: str = ""

class InvestigationPhase(BaseModel):
    name: str
    started_at: datetime
    completed_at: datetime | None = None
    findings: list[str] = Field(default_factory=list)
    tool_calls: int = 0

class IRReport(BaseModel):
    alert_id: str
    alert_title: str
    verdict: Verdict
    severity: Severity
    confidence: float = Field(ge=0.0, le=1.0)
    summary: str
    indicators: list[Indicator] = Field(default_factory=list)
    phases: list[InvestigationPhase] = Field(default_factory=list)
    recommended_actions: list[str] = Field(default_factory=list)
    blocked_actions: list[str] = Field(default_factory=list)
    tool_calls_total: int = 0
    dangerous_tools_deferred: int = 0
    duration_seconds: float = 0.0
    started_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: datetime | None = None
```

**Tests** (`test_models.py`):
- `Alert` auto-generates `id` (UUID) and `created_at` when omitted
- `Alert` round-trips `model_dump_json()` / `model_validate_json()`
- `Alert` rejects unknown `severity` string
- `IRReport` rejects `confidence` outside [0.0, 1.0]
- `IRReport` rejects unknown `verdict` string
- `IRReport.blocked_actions` defaults to `[]`
- `Indicator` with `malicious=None` serializes to `null`

**Commit**: `feat(agent): add IR domain models (Alert, IRReport, Verdict, Severity)`

---

### ROUND 2 — Depends on T-02 only (run in parallel)

---

#### T-03 · `dbot/agent/deps.py` — IRDeps dataclass

**Files**: `dbot/agent/deps.py`, `tests/agent/test_deps.py`

```python
from __future__ import annotations
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any
from dbot.audit import AuditLogger
from dbot.credentials.store import CredentialStore
from dbot.registry.catalog import Catalog
from dbot.agent.models import Alert, InvestigationPhase

if TYPE_CHECKING:
    from dbot.agent.guardrails import GuardrailConfig

# Type alias for executor function (matches execute_inprocess / execute_subprocess signature)
ExecutorFn = Callable[[Path, str, dict[str, Any], dict[str, Any]], Awaitable[dict[str, Any]]]

@dataclass
class IRDeps:
    # Core infrastructure (always required)
    catalog: Catalog
    credential_store: CredentialStore
    executor: ExecutorFn
    audit: AuditLogger
    guardrails: "GuardrailConfig"

    # Per-run state
    model_name: str = "openai:gpt-4o"
    alert: Alert | None = None            # None in Chat mode
    phase_tracker: list[InvestigationPhase] = field(default_factory=list)
    tool_call_count: int = 0              # incremented by invoke_tool
```

**Tests** (`test_deps.py`):
- Construct with `alert=None` (chat mode)
- Construct with a full `Alert`
- Two instances do NOT share `phase_tracker` list
- `tool_call_count` starts at 0; mutation is independent per instance
- `executor` field accepts any async callable matching the signature

**Commit**: `feat(agent): add IRDeps dataclass with catalog, credential_store, executor`

---

#### T-07 · `dbot/agent/report.py` — IRReport rendering

**Files**: `dbot/agent/report.py`, `tests/agent/test_report.py`

```python
import json
from dbot.agent.models import IRReport

def to_markdown(report: IRReport) -> str:
    """Render IRReport as Markdown.
    Sections:
    - H1: alert title + verdict badge
    - Metadata table: severity | confidence | duration | tool_calls_total
    - Summary paragraph
    - ## Indicators: table (type | value | malicious | source)
    - ## Timeline: phases with findings
    - ## Recommended Actions: numbered list
    - ## Blocked Actions: list labelled "blocked_by_policy" (only if non-empty)
    - Footer: started_at / completed_at
    """

def to_json(report: IRReport, indent: int = 2) -> str:
    """Render IRReport as pretty-printed JSON string."""
    return report.model_dump_json(indent=indent)

def to_jsonl_event(report: IRReport) -> str:
    """Single JSON line for SIEM/SOAR streaming ingestion.
    Flat dict: all scalar fields + indicator_count, action_count, blocked_count.
    Guaranteed no embedded newlines.
    """
    data = {
        "alert_id": report.alert_id,
        "alert_title": report.alert_title,
        "verdict": report.verdict.value,
        "severity": report.severity.value,
        "confidence": report.confidence,
        "summary": report.summary,
        "tool_calls_total": report.tool_calls_total,
        "dangerous_tools_deferred": report.dangerous_tools_deferred,
        "duration_seconds": report.duration_seconds,
        "started_at": report.started_at.isoformat(),
        "completed_at": report.completed_at.isoformat() if report.completed_at else None,
        "indicator_count": len(report.indicators),
        "action_count": len(report.recommended_actions),
        "blocked_count": len(report.blocked_actions),
    }
    return json.dumps(data)
```

**Tests** (`test_report.py`):
- `to_markdown` contains verdict string (case-insensitive)
- `to_markdown` contains all indicator values
- `to_markdown` contains all recommended action strings
- `to_markdown` contains "blocked_by_policy" section when `blocked_actions` non-empty
- `to_markdown` omits blocked section when `blocked_actions` is empty
- `to_json` round-trips: `IRReport.model_validate_json(to_json(r)) == r`
- `to_jsonl_event` is valid JSON (no parse error)
- `to_jsonl_event` contains no `\n` characters
- `to_jsonl_event` has `indicator_count`, `action_count`, `blocked_count` keys

**Commit**: `feat(agent): add IRReport renderer (markdown, JSON, JSONL)`

---

#### T-08 · `dbot/agent/ingestion/cli.py` — Alert loader

**Files**: `dbot/agent/ingestion/__init__.py`, `dbot/agent/ingestion/cli.py`, `tests/agent/test_ingestion_cli.py`

```python
from __future__ import annotations
import sys
from pathlib import Path
from dbot.agent.models import Alert

def load_alert_from_file(path: Path) -> Alert:
    """Load Alert from a JSON file. Raises FileNotFoundError or ValidationError."""
    text = path.read_text(encoding="utf-8")
    return Alert.model_validate_json(text)

def load_alert_from_stdin() -> Alert:
    """Read JSON from stdin and parse as Alert."""
    return Alert.model_validate_json(sys.stdin.read())

def load_alert_from_string(json_str: str) -> Alert:
    """Parse Alert from JSON string. Auto-generates id/created_at if absent."""
    return Alert.model_validate_json(json_str)
```

**Tests** (`test_ingestion_cli.py`):
- `load_alert_from_file` with valid JSON → correct `Alert`
- `load_alert_from_file` missing `title` → raises `ValidationError`
- `load_alert_from_file` nonexistent path → raises `FileNotFoundError`
- `load_alert_from_stdin` with monkeypatched `sys.stdin`
- `load_alert_from_string` minimal JSON (`title` + `description` only)
- Auto-generates `id` when absent; auto-sets `created_at` when absent

**Commit**: `feat(agent/ingestion): add CLI/file alert loader`

---

### ROUND 3 — Depends on T-02+T-03 or T-02+T-08 (run in parallel)

---

#### T-04 · `dbot/agent/guardrails.py` — Guardrail system + native FunctionToolset
#### Also: `dbot/registry/catalog.py` — Add `get_category()` + `dbot/server.py` — Export `catalog`

**Files**:
- `dbot/agent/guardrails.py`, `tests/agent/test_guardrails.py`
- `dbot/registry/catalog.py` (add `get_category`)
- `tests/test_catalog.py` (add `test_get_category`)
- `dbot/server.py` (add module-level `catalog` export)

**`dbot/registry/catalog.py` addition**:
```python
def get_category(self, pack_or_integration: str) -> str:
    """Return category for a pack or integration name. Returns '' if unknown."""
    integration = self._integrations.get(pack_or_integration)
    if integration:
        return integration.category
    for integ in self._integrations.values():
        if integ.pack == pack_or_integration:
            return integ.category
    return ""
```

**`dbot/server.py` addition** (after `mcp = create_server()`):
```python
# Module-level exports for CLI / agent harness
catalog: Catalog = mcp._catalog          # populated during create_server()
credential_store: CredentialStore = mcp._credential_store
executor: ExecutorFn = mcp._executor_fn
```
> Note: expose these properly from `create_server()` return value rather than private attrs;
> exact approach depends on server.py internals. Option B: return a named tuple/dataclass from `create_server()`.

**`dbot/agent/guardrails.py`** — builds the native FunctionToolset:

```python
from __future__ import annotations
import time
from pathlib import Path
from typing import Any
from pydantic import BaseModel, Field
from pydantic_ai import FunctionToolset, RunContext
from pydantic_ai.toolsets.filtered import FilteredToolset
from pydantic_ai.toolsets.approval_required import ApprovalRequiredToolset
from pydantic_ai.tools import ToolDefinition
from dbot.agent.deps import IRDeps   # no circular: deps uses TYPE_CHECKING for GuardrailConfig


class BudgetExceededError(Exception):
    """Raised when max_tool_calls budget is exceeded during investigation."""


class GuardrailConfig(BaseModel):
    # Hard deny: tool never offered to LLM (FilteredToolset)
    blocked_tools: set[str] = Field(default_factory=set)
    blocked_categories: set[str] = Field(default_factory=set)

    # Soft gate: requires HITL before execution (ApprovalRequiredToolset)
    require_approval_tools: set[str] = Field(default_factory=set)
    require_approval_categories: set[str] = Field(default_factory=set)

    # Budget
    max_tool_calls: int = Field(default=50, gt=0)
    max_dangerous_approvals: int = 3
    timeout_seconds: float = 300.0

    @classmethod
    def autonomous_default(cls) -> GuardrailConfig:
        return cls(
            blocked_categories={"Endpoint"},
            require_approval_tools={"invoke_tool"},
            max_tool_calls=30,
            max_dangerous_approvals=0,
        )

    @classmethod
    def chat_default(cls) -> GuardrailConfig:
        return cls(max_tool_calls=100)


def build_toolset(config: GuardrailConfig) -> ApprovalRequiredToolset | FilteredToolset | FunctionToolset:
    """
    Build a native FunctionToolset with 3 IR tools, then layer guardrails.

    Layer 1 (innermost): FunctionToolset with search_tools, get_tool_schema, invoke_tool.
    Layer 2: FilteredToolset — hard deny; blocked tools never seen by LLM.
    Layer 3: ApprovalRequiredToolset — soft HITL gate (only when needed).

    All tools receive RunContext[IRDeps] and access catalog/executor/audit directly.
    """
    base = FunctionToolset()

    @base.tool
    async def search_tools(
        ctx: RunContext[IRDeps],
        query: str,
        category: str | None = None,
    ) -> list[dict[str, Any]]:
        """Search available security tools by keyword or category.

        Call this first to discover which tools exist for a given task.
        Returns a list of matching tools with name, description, pack, category,
        args summary, and whether the tool is dangerous.

        Args:
            query: Keyword(s) to search for (e.g. "file reputation", "IP lookup").
            category: Optional category filter (e.g. "Data Enrichment", "Endpoint", "SIEM").
        """
        return ctx.deps.catalog.search(query, category, top_k=10)

    @base.tool
    async def get_tool_schema(
        ctx: RunContext[IRDeps],
        tool_name: str,
    ) -> dict[str, Any]:
        """Get the full argument and output specification for a specific tool.

        Always call this before invoke_tool to understand required arguments,
        their types, and expected output structure. Secret/credential arguments
        are not shown — they are injected automatically.

        Args:
            tool_name: Fully-qualified tool name, e.g. "VirusTotal.vt-get-file".
        """
        return ctx.deps.catalog.get_schema(tool_name)

    @base.tool
    async def invoke_tool(
        ctx: RunContext[IRDeps],
        tool_name: str,
        args: dict[str, Any],
        reason: str,
    ) -> dict[str, Any]:
        """Execute a security tool command.

        Always call search_tools and get_tool_schema before calling this.
        The reason argument is REQUIRED — it becomes the audit trail entry for
        post-incident review.

        Dangerous tools (host isolation, account suspension, firewall changes)
        will return status='approval_required' instead of executing.
        Tools blocked by operator policy return status='blocked_by_policy'.

        Args:
            tool_name: Fully-qualified tool name, e.g. "VirusTotal.vt-get-file".
            args: Tool arguments as a dict matching the schema from get_tool_schema.
            reason: Why this tool is being invoked (required for audit trail).
        """
        deps = ctx.deps

        # Budget enforcement
        deps.tool_call_count += 1
        if deps.tool_call_count > deps.guardrails.max_tool_calls:
            raise BudgetExceededError(
                f"Budget exceeded: {deps.tool_call_count} tool calls > "
                f"max {deps.guardrails.max_tool_calls}"
            )

        # Resolve tool from catalog
        try:
            integration, command = deps.catalog.resolve(tool_name)
        except KeyError:
            return {"status": "error", "tool_name": tool_name, "error": f"Unknown tool: {tool_name}"}

        # Hard block: check category against blocked_categories
        if integration.category in deps.guardrails.blocked_categories:
            return {
                "status": "blocked_by_policy",
                "tool_name": tool_name,
                "reason": reason,
                "policy": f"Category '{integration.category}' is blocked by operator policy",
            }

        # Dangerous gate: YAML execution=true → return approval_required
        if command.dangerous:
            start = time.monotonic()
            result = {
                "status": "approval_required",
                "tool_name": tool_name,
                "args": args,
                "reason": reason,
                "description": f"{command.name} is a dangerous operation. Human approval required.",
            }
            deps.audit.log_invocation(
                tool_name=tool_name,
                args=args,
                reason=reason,
                dangerous=True,
                result=result,
                duration_ms=(time.monotonic() - start) * 1000,
            )
            return result

        # Safe execution
        params = deps.credential_store.get(integration.pack)
        start = time.monotonic()
        raw = await deps.executor(Path(integration.py_path), command.name, args, params)
        duration_ms = (time.monotonic() - start) * 1000

        result = {
            "tool_name": tool_name,
            "reason": reason,
            "success": raw.get("success", False),
            "results": raw.get("results", []),
            "error": raw.get("error"),
        }

        deps.audit.log_invocation(
            tool_name=tool_name,
            args=args,
            reason=reason,
            dangerous=False,
            result=result,
            duration_ms=duration_ms,
        )
        return result

    # Layer 2: hard deny blocked tool names (category blocking is handled inside invoke_tool)
    def _keep(ctx: RunContext[IRDeps], tool_def: ToolDefinition) -> bool:
        return tool_def.name not in ctx.deps.guardrails.blocked_tools

    filtered = FilteredToolset(wrapped=base, filter_func=_keep)

    # Layer 3: soft HITL gate (only when approval rules are configured)
    if not (config.require_approval_tools or config.require_approval_categories):
        return filtered

    def _needs_approval(ctx: RunContext[IRDeps], tool_def: ToolDefinition, tool_args: dict[str, Any]) -> bool:
        g = ctx.deps.guardrails
        if tool_def.name in g.require_approval_tools:
            return True
        if tool_def.name == "invoke_tool":
            target = tool_args.get("tool_name", "")
            pack = target.split(".")[0] if "." in target else target
            category = ctx.deps.catalog.get_category(pack)
            return category in g.require_approval_categories
        return False

    return ApprovalRequiredToolset(wrapped=filtered, approval_required_func=_needs_approval)
```

**Key design points**:
- `blocked_categories` enforcement is **inside `invoke_tool`** (hard stop, returns `blocked_by_policy`)
- `command.dangerous` is **inside `invoke_tool`** (returns `approval_required`)
- `require_approval_tools` / `require_approval_categories` live at the **toolset layer** (raises `ApprovalRequired` → `DeferredToolRequests`)
- Budget is incremented **inside `invoke_tool`** via `ctx.deps.tool_call_count`
- Audit logging happens **inside `invoke_tool`** for every execution

**Tests** (`test_guardrails.py`) — use real `FunctionToolset`, fake `IRDeps` with a stub catalog:
- `GuardrailConfig.autonomous_default()`: `blocked_categories={"Endpoint"}`, `require_approval_tools={"invoke_tool"}`, `max_tool_calls=30`
- `GuardrailConfig.chat_default()`: `max_tool_calls=100`, empty sets
- `GuardrailConfig` rejects `max_tool_calls=0`
- `build_toolset(chat_config)` returns a toolset with `search_tools`, `get_tool_schema`, `invoke_tool`
- `invoke_tool` with unknown tool → `{"status": "error", ...}`
- `invoke_tool` with blocked category → `{"status": "blocked_by_policy", ...}`
- `invoke_tool` with `command.dangerous=True` → `{"status": "approval_required", ...}`
- `invoke_tool` success → `{"success": True, "results": [...], ...}`
- `invoke_tool` increments `deps.tool_call_count`
- `invoke_tool` raises `BudgetExceededError` when budget exceeded
- `invoke_tool` calls `audit.log_invocation` on execution
- `build_toolset` with `blocked_tools={"invoke_tool"}` → tool absent from `get_tools()` result
- `catalog.get_category("VirusTotal")` returns expected string

**Commit**: `feat(agent): add native FunctionToolset with guardrails, audit, and budget control`

---

#### T-09 · `dbot/agent/ingestion/watcher.py` — File-drop watcher

**Files**: `dbot/agent/ingestion/watcher.py`, `tests/agent/test_ingestion_watcher.py`

```python
from __future__ import annotations
import asyncio
import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path
from watchdog.events import FileCreatedEvent, FileSystemEventHandler
from watchdog.observers import Observer
from dbot.agent.ingestion.cli import load_alert_from_file
from dbot.agent.models import Alert


class AlertWatcher:
    """Watches a directory for new *.json files.
    On creation: parse as Alert → call async handler.
    Success: move to done/. Failure: move to failed/ with .error sidecar.
    """
    def __init__(
        self,
        watch_dir: Path,
        handler: Callable[[Alert], Awaitable[None]],
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        self._watch_dir = watch_dir
        self._handler = handler
        self._loop = loop or asyncio.get_event_loop()
        self._observer = Observer()
        self._done_dir = watch_dir / "done"
        self._failed_dir = watch_dir / "failed"

    def start(self) -> None:
        self._done_dir.mkdir(exist_ok=True)
        self._failed_dir.mkdir(exist_ok=True)
        handler = _AlertEventHandler(self._handler, self._done_dir, self._failed_dir, self._loop)
        self._observer.schedule(handler, str(self._watch_dir), recursive=False)
        self._observer.start()

    def stop(self) -> None:
        self._observer.stop()
        self._observer.join()


class _AlertEventHandler(FileSystemEventHandler):
    def __init__(self, handler, done_dir, failed_dir, loop):
        super().__init__()
        self._handler = handler
        self._done_dir = done_dir
        self._failed_dir = failed_dir
        self._loop = loop

    def on_created(self, event: FileCreatedEvent) -> None:
        if not event.is_directory and str(event.src_path).endswith(".json"):
            asyncio.run_coroutine_threadsafe(
                self._process(Path(str(event.src_path))), self._loop
            )

    async def _process(self, path: Path) -> None:
        try:
            alert = load_alert_from_file(path)
            await self._handler(alert)
            shutil.move(str(path), str(self._done_dir / path.name))
        except Exception as exc:
            shutil.move(str(path), str(self._failed_dir / path.name))
            (self._failed_dir / f"{path.stem}.error").write_text(str(exc), encoding="utf-8")
```

**Tests** (`test_ingestion_watcher.py`):
- `start()` creates `done/` and `failed/` subdirs
- Valid JSON file appears → handler called with parsed `Alert`; file moves to `done/`
- Invalid JSON → file moves to `failed/`, handler NOT called, `.error` sidecar exists
- Invalid Alert schema → file moves to `failed/`
- `stop()` does not raise

**Commit**: `feat(agent/ingestion): add watchdog file-drop alert watcher`

---

### ROUND 4 — Depends on T-04 (run in parallel)

---

#### T-05 · `dbot/agent/chat.py` — ChatAgent

**Files**: `dbot/agent/chat.py`, `tests/agent/test_chat.py`

**Key change from old plan**: constructor takes NO `mcp`/`catalog` — those come from `IRDeps`.

```python
from __future__ import annotations
import os
from collections.abc import AsyncIterator
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage
from dbot.agent.deps import IRDeps
from dbot.agent.guardrails import GuardrailConfig, build_toolset

CHAT_SYSTEM_PROMPT = """\
You are an IR analyst assistant with access to 500+ security tools.

WORKFLOW:
1. When the user mentions an indicator (IP, domain, hash, user, URL), call search_tools first.
2. Call get_tool_schema before invoke_tool to understand required arguments.
3. Explain findings — what was found, confidence, and next steps.
4. Ask for clarification if the request is ambiguous.
5. NEVER invoke a tool without a clear reason in the 'reason' argument.
6. Summarise each tool result before deciding next steps.
"""


class ChatAgent:
    def __init__(
        self,
        config: GuardrailConfig | None = None,
        model: str | None = None,
    ) -> None:
        cfg = config or GuardrailConfig.chat_default()
        toolset = build_toolset(cfg)
        # Chat: output_type=str. chat_default has no require_approval rules,
        # so ApprovalRequiredToolset is NOT in the stack — DeferredToolRequests never returned.
        self._agent: Agent[IRDeps, str] = Agent(
            model or os.environ.get("DBOT_LLM_MODEL", "openai:gpt-4o"),
            deps_type=IRDeps,
            output_type=str,
            toolsets=[toolset],
            instructions=CHAT_SYSTEM_PROMPT,
        )
        self._history: list[ModelMessage] = []

    async def send(self, message: str, deps: IRDeps) -> str:
        result = await self._agent.run(message, deps=deps, message_history=self._history)
        self._history = result.all_messages()
        return result.output

    async def send_stream(self, message: str, deps: IRDeps) -> AsyncIterator[str]:
        async with self._agent.run_stream(message, deps=deps, message_history=self._history) as stream:
            async for chunk in stream.stream_text(delta=True):
                yield chunk
            self._history = stream.all_messages()

    def reset(self) -> None:
        self._history = []

    @property
    def history(self) -> list[ModelMessage]:
        return list(self._history)
```

**Tests** (`test_chat.py`) — use `TestModel(call_tools=[])` via `.override()`:
- `send()` returns a string
- Second `send()` → `len(agent.history) > 0`
- `send_stream()` yields at least one string chunk
- `reset()` sets `history` to `[]`
- `GuardrailConfig.chat_default()` used when none provided
- Blocked tool (in `blocked_tools`) absent from active toolset tools

**Commit**: `feat(agent): add ChatAgent with multi-turn history and streaming`

---

#### T-06 · `dbot/agent/responder.py` — ResponderAgent

**Files**: `dbot/agent/responder.py`, `tests/agent/test_responder.py`

**Key change**: constructor takes NO `mcp`/`catalog`; toolset built from `GuardrailConfig` only.

```python
from __future__ import annotations
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from pydantic_ai import Agent, DeferredToolRequests, DeferredToolResults, ToolDenied
from dbot.agent.deps import IRDeps
from dbot.agent.guardrails import GuardrailConfig, BudgetExceededError, build_toolset
from dbot.agent.models import Alert, IRReport, Verdict

RESPONDER_INSTRUCTIONS = """\
You are an autonomous IR analyst. An alert has been assigned to you.

INVESTIGATION PHASES (follow in order):
1. TRIAGE: Extract all indicators (IPs, domains, hashes, users, URLs). Assess severity.
2. ENRICHMENT: For each indicator, call search_tools then invoke the best tool.
3. CORRELATION: Connect findings. Identify patterns and attack chain.
4. VERDICT: Conclude: malicious / suspicious / benign / inconclusive.

RULES:
- Always call search_tools before an unfamiliar tool.
- Always call get_tool_schema before invoke_tool.
- Include a specific reason in every invoke_tool call.
- If a tool returns blocked_by_policy or approval_required, add it to blocked_actions.
- Output ONLY a valid IRReport JSON object when done. No prose outside the JSON.
"""

HITLHandler = Callable[[DeferredToolRequests], Awaitable[DeferredToolResults]]


@dataclass
class InvestigationResult:
    report: IRReport
    deferred: list[dict] = field(default_factory=list)


def _emergency_report(alert: Alert, started_at: datetime, reason: str) -> IRReport:
    return IRReport(
        alert_id=alert.id,
        alert_title=alert.title,
        verdict=Verdict.INCONCLUSIVE,
        severity=alert.severity,
        confidence=0.0,
        summary=f"Investigation incomplete: {reason}",
        started_at=started_at,
    )


class ResponderAgent:
    def __init__(
        self,
        config: GuardrailConfig | None = None,
        model: str | None = None,
    ) -> None:
        self._config = config or GuardrailConfig.autonomous_default()
        toolset = build_toolset(self._config)
        # Responder: output_type includes DeferredToolRequests for HITL loop
        self._agent: Agent[IRDeps, IRReport | DeferredToolRequests] = Agent(
            model or os.environ.get("DBOT_LLM_MODEL", "openai:gpt-4o"),
            deps_type=IRDeps,
            output_type=[IRReport, DeferredToolRequests],
            toolsets=[toolset],
        )

    async def investigate(
        self,
        alert: Alert,
        deps: IRDeps,
        on_deferred: HITLHandler | None = None,
    ) -> InvestigationResult:
        started_at = datetime.utcnow()
        deferred_list: list[dict] = []

        try:
            result = await self._agent.run(
                f"Investigate alert:\n\n{alert.model_dump_json(indent=2)}",
                deps=deps,
                instructions=RESPONDER_INSTRUCTIONS,
                output_type=[IRReport, DeferredToolRequests],
            )
        except BudgetExceededError as exc:
            return InvestigationResult(report=_emergency_report(alert, started_at, str(exc)))
        except Exception as exc:
            return InvestigationResult(report=_emergency_report(alert, started_at, str(exc)))

        # HITL resume loop (max 5 rounds)
        for _ in range(5):
            if not isinstance(result.output, DeferredToolRequests):
                break
            deferred_req = result.output

            if on_deferred:
                approvals = await on_deferred(deferred_req)
            else:
                # --no-hitl: deny all, track as blocked_by_policy
                for call in deferred_req.approvals:
                    deferred_list.append({
                        "tool_call_id": call.tool_call_id,
                        "tool_name": call.tool_name,
                        "args": call.args,
                        "status": "blocked_by_policy",
                    })
                approvals = DeferredToolResults(
                    approvals={
                        call.tool_call_id: ToolDenied(
                            "Blocked by policy: autonomous mode, no human available."
                        )
                        for call in deferred_req.approvals
                    }
                )

            try:
                result = await self._agent.run(
                    None,
                    message_history=result.all_messages(),
                    deferred_tool_results=approvals,
                    deps=deps,
                    output_type=[IRReport, DeferredToolRequests],
                )
            except Exception:
                break

        report = (
            result.output
            if isinstance(result.output, IRReport)
            else _emergency_report(alert, started_at, "Investigation did not converge")
        )

        report.started_at = started_at
        report.completed_at = datetime.utcnow()
        report.duration_seconds = (report.completed_at - report.started_at).total_seconds()
        report.dangerous_tools_deferred = len(deferred_list)
        report.blocked_actions = [d["tool_name"] for d in deferred_list]

        return InvestigationResult(report=report, deferred=deferred_list)
```

**Tests** (`test_responder.py`) — use `TestModel` via `.override()`, fake `IRDeps`:
- `investigate()` returns `InvestigationResult` with valid `IRReport`
- `report.alert_id == alert.id`
- `report.verdict` is a valid `Verdict`
- `report.duration_seconds >= 0`
- `report.started_at` and `report.completed_at` set
- `on_deferred=None` with deferred → `deferred_list` non-empty, `blocked_actions` populated
- `on_deferred` callable is invoked when deferred tools present
- `BudgetExceededError` → emergency report (INCONCLUSIVE, confidence=0.0)
- `GuardrailConfig.autonomous_default()` used when none provided

**Commit**: `feat(agent): add ResponderAgent with autonomous investigation loop and HITL`

---

### ROUND 5 — Depends on T-05+T-06+T-08+T-09 (run in parallel)

---

#### T-10 · `dbot/agent/cli.py` — CLI entrypoints

**Files**: `dbot/agent/cli.py`, `tests/agent/test_cli.py`

**Command signatures**:
```
dbot-chat
  --model TEXT           [env: DBOT_LLM_MODEL, default: openai:gpt-4o]
  --audit-log PATH       [default: dbot-agent-audit.log]
  --no-stream            Disable streaming output

dbot-respond [ALERT_FILE]
  --model TEXT
  --audit-log PATH
  --output [json|markdown|jsonl]  [default: markdown]
  --output-file PATH
  --max-calls INT        [default: 30]
  --block-category TEXT  (repeatable)
  --no-hitl              Deny deferred tools; include as blocked_by_policy in report

dbot-watch WATCH_DIR
  --model TEXT
  --audit-log PATH
  --output-dir PATH      [default: WATCH_DIR/reports/]
  --max-calls INT
  --jsonl                Also append to output-dir/investigations.jsonl
```

**How the CLI builds `IRDeps`** (same pattern for all three commands):
```python
from dbot.server import catalog, credential_store, executor  # module-level exports
from dbot.runtime.executor import execute_inprocess           # default executor

deps = IRDeps(
    catalog=catalog,
    credential_store=credential_store,
    executor=executor,
    audit=AuditLogger(audit_log_path),
    guardrails=config,
    model_name=model,
)
```

**`dbot-chat` flow**:
1. Import `catalog`, `credential_store`, `executor` from `dbot.server`
2. `ChatAgent(config=GuardrailConfig.chat_default(), model=model)`
3. Build `IRDeps`; REPL loop with `send_stream()` + Rich live output
4. Ctrl-C / `exit` → graceful shutdown

**`dbot-respond` flow**:
1. Load alert from file/stdin
2. Build `GuardrailConfig` from flags
3. `on_deferred = None if --no-hitl else _cli_hitl_handler`
4. `_cli_hitl_handler`: Rich table per deferred call, `Confirm.ask("Approve?")`, build `DeferredToolResults`
5. `await agent.investigate(alert, deps, on_deferred=on_deferred)`
6. Render + output

**`dbot-watch` flow**:
1. `output_dir.mkdir(parents=True, exist_ok=True)`
2. `process_alert(alert)` handler: investigate → write `{alert_id}_{ts}.md` + `.json`; if `--jsonl` append JSONL line
3. `AlertWatcher(watch_dir, process_alert).start()`
4. Block on `asyncio.Event().wait()` until Ctrl-C; `watcher.stop()` on exit

**Tests** (`test_cli.py`) — use `typer.testing.CliRunner`:
- `dbot-chat --help` exits 0
- `dbot-respond --help` exits 0
- `dbot-watch --help` exits 0
- `dbot-respond` with valid JSON file + `TestModel` patched → exits 0, stdout contains report
- `dbot-respond` with nonexistent file → exits non-zero with error
- `dbot-respond --output json` → valid JSON on stdout
- `dbot-respond --no-hitl` → exits 0

**Commit**: `feat(agent): add CLI entrypoints (dbot-chat, dbot-respond, dbot-watch)`

---

#### T-11 · `tests/agent/conftest.py` — Shared test fixtures

**Files**: `tests/agent/__init__.py`, `tests/agent/conftest.py`

**Key change**: no `fake_mcp` fixture — tests use real `FunctionToolset` built by `build_toolset()`.
`IRDeps` is built with a stub `Catalog`, stub `CredentialStore`, and a fake async executor.

```python
import pytest
from pathlib import Path
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
from pydantic_ai.models.test import TestModel
from dbot.audit import AuditLogger
from dbot.credentials.store import CredentialStore
from dbot.registry.catalog import Catalog
from dbot.agent.models import Alert, Severity, IRReport, Verdict, Indicator
from dbot.agent.deps import IRDeps
from dbot.agent.guardrails import GuardrailConfig


@pytest.fixture
def sample_alert() -> Alert:
    return Alert(
        id="test-alert-001",
        title="Suspicious IP Detected",
        description="Outbound connection to known C2 IP 1.2.3.4",
        severity=Severity.HIGH,
        source="test",
    )


@pytest.fixture
def sample_report(sample_alert: Alert) -> IRReport:
    return IRReport(
        alert_id=sample_alert.id,
        alert_title=sample_alert.title,
        verdict=Verdict.MALICIOUS,
        severity=Severity.HIGH,
        confidence=0.85,
        summary="C2 communication confirmed.",
        indicators=[Indicator(type="ip", value="1.2.3.4", malicious=True, source="VirusTotal")],
        recommended_actions=["Block IP 1.2.3.4", "Isolate affected host"],
        tool_calls_total=3,
        started_at=datetime(2026, 1, 1, 12, 0, 0),
        completed_at=datetime(2026, 1, 1, 12, 0, 30),
        duration_seconds=30.0,
    )


@pytest.fixture
def stub_catalog() -> Catalog:
    """Minimal Catalog stub for tests (no real YAML indexing)."""
    catalog = MagicMock(spec=Catalog)
    catalog.search.return_value = [
        {"tool_name": "FakePack.fake-check", "pack": "FakePack",
         "description": "Fake check", "category": "Data Enrichment", "dangerous": False}
    ]
    catalog.get_schema.return_value = {
        "tool_name": "FakePack.fake-check", "arguments": [], "outputs": [], "dangerous": False
    }
    catalog.get_category.return_value = "Data Enrichment"

    from dbot.registry.models import IntegrationDef, CommandDef
    fake_cmd = CommandDef(name="fake-check", dangerous=False)
    fake_int = IntegrationDef(
        pack="FakePack", name="FakePack", category="Data Enrichment",
        py_path="/fake/FakePack.py", commands=[fake_cmd]
    )
    catalog.resolve.return_value = (fake_int, fake_cmd)
    return catalog


@pytest.fixture
def stub_executor() -> "ExecutorFn":
    """Async executor that always returns a successful result."""
    async def _exec(py_path, command, args, params):
        return {"success": True, "results": [{"data": "benign"}], "error": None}
    return _exec


@pytest.fixture
def mock_audit(tmp_path: Path) -> AuditLogger:
    return AuditLogger(tmp_path / "test-audit.log")


@pytest.fixture
def chat_deps(stub_catalog, stub_executor, mock_audit) -> IRDeps:
    return IRDeps(
        catalog=stub_catalog,
        credential_store=CredentialStore(),
        executor=stub_executor,
        audit=mock_audit,
        guardrails=GuardrailConfig.chat_default(),
        model_name="test",
    )


@pytest.fixture
def responder_deps(sample_alert, stub_catalog, stub_executor, mock_audit) -> IRDeps:
    return IRDeps(
        catalog=stub_catalog,
        credential_store=CredentialStore(),
        executor=stub_executor,
        audit=mock_audit,
        guardrails=GuardrailConfig.autonomous_default(),
        model_name="test",
        alert=sample_alert,
    )


@pytest.fixture
def test_model_no_tools() -> TestModel:
    return TestModel(call_tools=[])


@pytest.fixture
def test_model_all_tools() -> TestModel:
    return TestModel(call_tools="all")
```

**Commit**: `test(agent): add shared fixtures (stub_catalog, stub_executor, make_deps)`

---

#### T-12 · `tests/agent/test_integration.py` — End-to-end smoke tests

**Files**: `tests/agent/test_integration.py`

```python
"""End-to-end smoke tests. TestModel only — no real LLM, no network, no MCP server."""
import pytest
from pydantic_ai.models.test import TestModel
from dbot.agent.models import Alert, IRReport, Verdict
from dbot.agent.chat import ChatAgent
from dbot.agent.responder import ResponderAgent, InvestigationResult
from dbot.agent.report import to_markdown, to_json, to_jsonl_event
from dbot.agent.ingestion.cli import load_alert_from_file


async def test_chat_two_turns(chat_deps):
    with TestModel(call_tools=[]).override():
        agent = ChatAgent()
        r1 = await agent.send("Check IP 1.2.3.4", chat_deps)
        assert isinstance(r1, str)
        assert len(agent.history) > 0
        r2 = await agent.send("Any other indicators?", chat_deps)
        assert isinstance(r2, str)


async def test_responder_returns_report(sample_alert, responder_deps):
    with TestModel(call_tools=[]).override():
        agent = ResponderAgent()
        result = await agent.investigate(sample_alert, responder_deps)
        assert isinstance(result, InvestigationResult)
        assert isinstance(result.report, IRReport)
        assert result.report.alert_id == sample_alert.id
        assert result.report.verdict in list(Verdict)
        assert result.report.duration_seconds >= 0
        assert result.report.started_at is not None


async def test_file_to_report_pipeline(tmp_path, sample_alert, sample_report):
    alert_file = tmp_path / "alert.json"
    alert_file.write_text(sample_alert.model_dump_json())
    loaded = load_alert_from_file(alert_file)
    assert loaded.id == sample_alert.id

    md = to_markdown(sample_report)
    assert "malicious" in md.lower()
    assert "1.2.3.4" in md

    assert to_json(sample_report)
    jsonl = to_jsonl_event(sample_report)
    assert "\n" not in jsonl
    assert "indicator_count" in jsonl
```

**Commit**: `test(agent): add end-to-end integration smoke tests`

---

## Dependency Graph (parallel execution)

```
Round 1 (||):  T-01            T-02
Round 2 (||):  T-03            T-07            T-08
Round 3 (||):  T-04                            T-09
Round 4 (||):  T-05            T-06
Round 5 (||):  T-10                            T-11
Round 6:       T-12
```

---

## Atomic Commit Order (15 commits, CI green at every step)

```
 1. build: add agent harness deps (watchdog, typer, rich) and CLI scripts      [T-01]
 2. feat(agent): add IR domain models (Alert, IRReport, Verdict, Severity)     [T-02 impl]
 3. test(agent): add model unit tests                                           [T-02 tests]
 4. feat(agent): add IRDeps with catalog, credential_store, executor            [T-03 impl+tests]
 5. feat(agent): add IRReport renderer (markdown, JSON, JSONL)                  [T-07 impl+tests]
 6. feat(agent/ingestion): add CLI/file alert loader                            [T-08 impl+tests]
 7. feat(agent): add native FunctionToolset guardrails + catalog.get_category() [T-04 impl+tests]
 8. feat(agent/ingestion): add watchdog file-drop alert watcher                 [T-09 impl+tests]
 9. feat(agent): add ChatAgent with streaming and multi-turn history            [T-05 impl+tests]
10. feat(agent): add ResponderAgent with autonomous loop and HITL               [T-06 impl+tests]
11. feat(agent): add CLI entrypoints (dbot-chat, dbot-respond, dbot-watch)     [T-10 impl+tests]
12. test(agent): add shared fixtures (stub_catalog, stub_executor)             [T-11]
13. test(agent): add end-to-end integration smoke tests                         [T-12]
14. docs: update README with agent harness usage
15. chore: bump version to 0.2.0
```

---

## Implementation Notes

### Circular import: deps.py ↔ guardrails.py
```python
# deps.py — use TYPE_CHECKING to avoid runtime circular import
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from dbot.agent.guardrails import GuardrailConfig
```
`guardrails.py` imports `IRDeps` for `RunContext[IRDeps]` type annotation only —
use `from __future__ import annotations` to defer resolution.

### `dbot.server` catalog export
Current `server.py` creates `catalog` inside `create_server()`. Two clean options:

**Option A** (preferred): `create_server()` returns a named dataclass:
```python
@dataclass
class ServerComponents:
    mcp: FastMCP
    catalog: Catalog
    credential_store: CredentialStore
    executor_fn: ExecutorFn

def create_server() -> ServerComponents: ...
components = create_server()
mcp = components.mcp          # for MCP protocol
catalog = components.catalog  # for agent harness CLI
```

**Option B**: Add module-level assignments after `create_server()` returns.

The CLI does `from dbot.server import catalog, credential_store, executor` —
either option works; Option A is cleaner for typing.

### `TestModel.override()` context manager
```python
with TestModel(call_tools=[]).override():
    # All Agent.run() calls inside this block use TestModel
    result = await agent.send("hello", deps)
```
This patches the model globally for the duration of the `with` block. No need to pass `model=` to `ChatAgent`/`ResponderAgent` constructors in tests.

### `autonomous_default` + `output_type` constraint
`autonomous_default()` sets `require_approval_tools={"invoke_tool"}`, which causes
`build_toolset()` to wrap with `ApprovalRequiredToolset`. This means `ResponderAgent`
MUST use `output_type=[IRReport, DeferredToolRequests]` (it does). `ChatAgent` uses
`chat_default()` which has no approval rules → no `ApprovalRequiredToolset` →
`output_type=str` is safe (no `DeferredToolRequests` will be emitted).

### `invoke_tool` dual gate (inside tool vs toolset layer)
Two independent gates for dangerous tools:
1. **Inside `invoke_tool`**: `command.dangerous` (from YAML `execution: true`) →
   returns `{"status": "approval_required", ...}` as a normal tool result (not HITL)
2. **Toolset layer**: `require_approval_tools={"invoke_tool"}` →
   `ApprovalRequiredToolset` raises `ApprovalRequired` → agent returns `DeferredToolRequests`

For Responder with `autonomous_default()`, gate 2 fires for EVERY `invoke_tool` call.
This is intentional: operators review all tool invocations. If you want only
dangerous tools to require HITL, use `require_approval_tools=set()` and rely on gate 1 alone.

### Agent-level audit log (`Q3`)
The existing `AuditLogger` logs tool invocations at MCP layer. In the native
FunctionToolset approach, `invoke_tool` calls `deps.audit.log_invocation(...)` directly.
For higher-level agent events (investigation start, phase transitions, verdict):
add `log_event(event_type, **kwargs)` to `AuditLogger` in a separate commit, or
use a second `AuditLogger` instance pointing to `dbot-agent-audit.log`.

### `dbot-watch` output paths (`Q4`)
```
output_dir/{alert_id}_{timestamp:%Y%m%dT%H%M%S}.md   ← markdown report
output_dir/{alert_id}_{timestamp:%Y%m%dT%H%M%S}.json  ← JSON report
output_dir/investigations.jsonl                         ← JSONL stream (--jsonl flag only)
```

### `blocked_by_policy` vs `approval_required` distinction
- `blocked_by_policy`: category is in `blocked_categories` → tool cannot run even with approval
- `approval_required`: tool is dangerous OR in `require_approval_tools` → human can approve
- Both appear in `report.blocked_actions` when `--no-hitl` / `on_deferred=None`
