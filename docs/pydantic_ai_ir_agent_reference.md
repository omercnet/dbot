# PydanticAI IR Agent — Patterns Reference

SHA: `1344df70b2503f47d5887c45c335a79aa6dabd3a`  
Repo: https://github.com/pydantic/pydantic-ai/tree/1344df70b2503f47d5887c45c335a79aa6dabd3a  
Docs: https://ai.pydantic.dev (v1.x, stable since Sep 2025)

---

## 1. Agent Creation — Signature & Deps

```python
from dataclasses import dataclass
from pydantic import BaseModel
from pydantic_ai import Agent, RunContext

@dataclass
class IRDeps:
    alert_id: str
    mode: str             # "interactive" | "autonomous"
    allowed_tools: set[str]
    db_conn: object       # injected AsyncConnection or similar

class IROutput(BaseModel):
    summary: str
    severity: int         # 1–10
    iocs: list[str]
    recommended_actions: list[str]
    requires_human: bool

agent = Agent(
    'openai:gpt-4o',
    deps_type=IRDeps,          # type only — NOT an instance; used for type checking
    output_type=IROutput,      # structured validated result; use str for freeform
    instructions="You are an IR analyst. Investigate alerts using available tools.",
    toolsets=[dbot_server],    # pass MCP servers / toolsets here
)

# Dynamic instructions per-run (access deps)
@agent.instructions
async def inject_context(ctx: RunContext[IRDeps]) -> str:
    return f"Mode: {ctx.deps.mode}. Alert ID: {ctx.deps.alert_id}"
```

Key `Agent.__init__` params:

| Param | Type | Notes |
|---|---|---|
| `output_type` | `type \| BaseModel \| list[type]` | Validated output; list = union |
| `deps_type` | `type` | DI container type (dataclass recommended) |
| `instructions` | `str \| callable` | System prompt; callable gets `RunContext` |
| `toolsets` | `Sequence[AbstractToolset]` | MCP servers + FunctionToolsets |
| `max_retries` | `int` | Default 1; increase for flaky tools |
| `model_settings` | `ModelSettings` | Temperature, max_tokens, etc. |

---

## 2. MCP Server Connection

### 2a. MCPServerStdio — subprocess transport

```python
from pydantic_ai.mcp import MCPServerStdio

dbot_stdio = MCPServerStdio(
    command='python',
    args=['-m', 'dbot.server'],
    env={'DBOT_API_KEY': 'secret'},  # subprocess env (does NOT inherit parent)
    tool_prefix='dbot',              # prefixes all tools → dbot_tool_name
    timeout=10,                      # connection timeout seconds
    max_retries=2,
)

agent = Agent('openai:gpt-4o', toolsets=[dbot_stdio])

# Server lifecycle: pass to agent (auto-managed) OR explicit context manager
async with agent:
    result = await agent.run("Investigate alert")
```

### 2b. MCPServerStreamableHTTP — remote HTTP transport

```python
from pydantic_ai.mcp import MCPServerStreamableHTTP

dbot_http = MCPServerStreamableHTTP(
    'http://localhost:8000/mcp',
    headers={'Authorization': 'Bearer token'},
    tool_prefix='dbot',
    read_timeout=300,   # 5 min — critical for long-running IR tools
)

agent = Agent('openai:gpt-4o', toolsets=[dbot_http])
async with dbot_http:
    result = await agent.run("Investigate alert", deps=deps)
```

### 2c. FastMCPToolset — in-process (same codebase, zero network overhead)

**Source**: https://ai.pydantic.dev/mcp/fastmcp-client/

This is the **preferred pattern** for dbot: the FastMCP server lives in the same
process, so there's no subprocess or HTTP round-trip.

```python
from fastmcp import FastMCP
from pydantic_ai import Agent
from pydantic_ai.toolsets.fastmcp import FastMCPToolset

# Existing FastMCP server instance
fastmcp_server = FastMCP('dbot')

@fastmcp_server.tool()
async def search_tools(query: str) -> list[dict]:
    """Search available security integrations."""
    return catalog.search(query)

@fastmcp_server.tool()
async def invoke_tool(tool_name: str, args: dict, reason: str) -> dict:
    """Execute an integration tool."""
    return await executor.run(tool_name, args)

# Wrap as toolset — direct in-process call, no serialization overhead
toolset = FastMCPToolset(fastmcp_server)
# Also accepts: URL string, FastMCP Client, FastMCP Transport, path to Python script
# toolset = FastMCPToolset('http://localhost:8000/mcp')  # remote
# toolset = FastMCPToolset('path/to/server.py')          # script

agent = Agent('openai:gpt-4o', toolsets=[toolset])
result = await agent.run("Find VirusTotal tools")
```

**`FastMCPToolset` vs `MCPServerStdio`**:

| | `FastMCPToolset` (in-process) | `MCPServerStdio` (subprocess) |
|---|---|---|
| Overhead | Zero (direct call) | Subprocess spawn + stdio |
| Isolation | None (shared memory) | Full process isolation |
| Use when | Same codebase, dev/testing | External tool server, prod isolation |
| Credential access | Shared with parent | Separate env injection |

### 2d. process_tool_call — intercept every tool call (audit hook)

```python
from pydantic_ai.mcp import ProcessToolCallback, CallToolFunc
from pydantic_ai.tools import RunContext

async def audit_tool_call(
    ctx: RunContext[IRDeps],
    call_func: CallToolFunc,
    name: str,
    args: dict,
) -> Any:
    audit_logger.log(alert_id=ctx.deps.alert_id, tool=name, args=args)
    result = await call_func(name, args, None)
    audit_logger.log(alert_id=ctx.deps.alert_id, tool=name, result=result)
    return result

dbot = MCPServerStreamableHTTP(
    'http://localhost:8000/mcp',
    process_tool_call=audit_tool_call,
)
```

---

## 3. Message History — Multi-Turn Conversation

```python
from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter

history: list[ModelMessage] = []

# Turn 1
result = await agent.run(
    "What ports are open on 10.0.0.1?",
    deps=deps,
    message_history=history,   # empty first turn
)
history = result.all_messages()    # includes user + assistant + tool messages

# Turn 2 — agent remembers context
result = await agent.run(
    "Is port 22 expected for that host?",
    deps=deps,
    message_history=history,   # accumulated history
)
history = result.all_messages()

# Persist to DB / file
json_bytes = result.all_messages_json()

# Reload
history = ModelMessagesTypeAdapter.validate_json(json_bytes)
```

- `result.all_messages()` — full history (user + assistant + tool calls + results)
- `result.new_messages()` — only messages from this run (use for append-only stores)
- `result.output` — final validated `IROutput`

---

## 4. Streaming Responses

**Source**: https://ai.pydantic.dev/agent

```python
# Text streaming — for interactive CLI/chat
async with agent.run_stream(
    "Investigate 10.0.0.1",
    deps=deps,
    message_history=history,
) as stream:
    async for chunk in stream.stream_text(delta=True):   # delta=True = incremental
        print(chunk, end='', flush=True)

    # After stream completes — access full result
    history = stream.all_messages()
    output = await stream.get_output()   # validated IROutput

# Structured output streaming (partial Pydantic model)
async with agent.run_stream("Investigate", deps=deps) as s:
    async for partial in s.stream_output(debounce_by=0.1):
        # partial is IROutput with fields populated as they arrive
        print(partial)
```

### FastAPI streaming endpoint pattern

```python
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
import json

app = FastAPI()

@app.post('/chat/')
async def chat(prompt: str) -> StreamingResponse:
    async def stream_messages():
        messages = await db.get_messages()
        async with agent.run_stream(prompt, message_history=messages, deps=deps) as result:
            async for text in result.stream_text(debounce_by=0.01):
                yield json.dumps({'text': text}).encode() + b'\n'
        await db.add_messages(result.new_messages_json())

    return StreamingResponse(stream_messages(), media_type='text/plain')
```

---

## 5. HITL — DeferredToolRequests / ApprovalRequired

**Source**: https://ai.pydantic.dev/deferred-tools/  
**PR merged**: https://github.com/pydantic/pydantic-ai/pull/2581

### 5a. Static approval on a tool

```python
from pydantic_ai import (
    Agent,
    ApprovalRequired,
    DeferredToolRequests,
    DeferredToolResults,
    RunContext,
    ToolDenied,
)

# output_type MUST include DeferredToolRequests for HITL to work
agent = Agent('openai:gpt-4o', output_type=[IROutput, DeferredToolRequests])

@agent.tool_plain(requires_approval=True)    # always needs approval
def isolate_host(hostname: str) -> str:
    return f"Host {hostname!r} isolated"
```

### 5b. Dynamic approval (based on args or deps)

```python
PROTECTED_TOOLS = {'delete_file', 'block_ip', 'isolate_host'}

@agent.tool
def invoke_action(ctx: RunContext[IRDeps], tool_name: str, args: dict) -> str:
    # Require approval if dangerous tool AND not yet approved
    if tool_name in PROTECTED_TOOLS and not ctx.tool_call_approved:
        raise ApprovalRequired(metadata={
            'tool': tool_name,
            'args': args,
            'reason': 'destructive action requires human sign-off',
        })
    return executor.run(tool_name, args)
```

### 5c. ApprovalRequiredToolset — wrap entire MCP server

```python
from pydantic_ai import DeferredToolRequests, DeferredToolResults
from pydantic_ai.toolsets.fastmcp import FastMCPToolset

toolset = FastMCPToolset(dbot_server)

# Require approval for all tools whose names start with a destructive verb
def needs_approval(ctx, tool_def, tool_args) -> bool:
    destructive_prefixes = ('isolate', 'block', 'delete', 'run', 'execute', 'kill')
    return any(tool_def.name.startswith(p) for p in destructive_prefixes)

approval_toolset = toolset.approval_required(needs_approval)
# approval_toolset = toolset.approval_required()  # all tools require approval

agent = Agent(
    'openai:gpt-4o',
    toolsets=[approval_toolset],
    output_type=[IROutput, DeferredToolRequests],
)
```

### 5d. Full HITL request → approve → resume loop

```python
async def handle_alert_with_hitl(alert: dict):
    deps = IRDeps(alert_id=alert['id'], mode='interactive', ...)
    history: list[ModelMessage] = []

    while True:
        result = await agent.run(
            f"Investigate: {alert['description']}" if not history else None,
            deps=deps,
            message_history=history,
            deferred_tool_results=pending_results if history else None,
            output_type=[IROutput, DeferredToolRequests],
        )

        if isinstance(result.output, IROutput):
            return result.output  # done

        # Agent paused — needs approval
        requests: DeferredToolRequests = result.output
        history = result.all_messages()
        pending_results = DeferredToolResults()

        for call in requests.approvals:
            # Present to operator (Slack, UI, CLI, etc.)
            approved = await ask_operator(
                f"Approve {call.tool_name}({call.args})?\n"
                f"Metadata: {requests.metadata.get(call.tool_call_id, {})}"
            )
            if approved:
                pending_results.approvals[call.tool_call_id] = True
                # Or: ToolApproved(override_args={'hostname': 'safe-host'})
            else:
                pending_results.approvals[call.tool_call_id] = ToolDenied(
                    'Operator denied this action'
                )
```

**Key types**:

| Type | Purpose |
|---|---|
| `DeferredToolRequests` | Returned when agent pauses; has `.approvals` and `.calls` lists |
| `DeferredToolResults` | Container to pass back decisions; `.approvals[tool_call_id] = True/False/ToolDenied()` |
| `ApprovalRequired` | Exception raised inside tool to request approval dynamically |
| `ToolDenied(msg)` | Denial with reason message sent back to model |
| `CallDeferred` | Exception to externalize tool execution (different from approval) |

> **Note**: Builtin tools (`WebSearchTool`, `WebFetchTool`) are executed provider-side
> and **cannot** use `requires_approval`. Use custom tool wrappers instead.  
> Source: https://github.com/pydantic/pydantic-ai/issues/4376

---

## 6. Dependency Injection — RunContext

```python
from pydantic_ai import RunContext
from dataclasses import dataclass, field

@dataclass
class IRDeps:
    alert_id: str
    mode: str                          # "interactive" | "autonomous"
    allowed_tools: set[str] = field(default_factory=set)
    db_conn: object = None
    http_client: object = None         # e.g., httpx.AsyncClient
    audit_log: object = None           # AuditLogger instance

@agent.tool
async def enrich_ioc(ctx: RunContext[IRDeps], ioc: str, ioc_type: str) -> str:
    """Enrich an IOC using threat intelligence."""
    # ctx.deps → IRDeps instance
    # ctx.model → current Model being used
    # ctx.usage → RequestUsage (tokens so far)
    # ctx.prompt → original user prompt string
    # ctx.tool_name → name of this tool call
    # ctx.retry → current retry count (0-based)
    # ctx.tool_call_approved → True if HITL approved this call
    return await ctx.deps.db_conn.query(ioc)

# Pass deps at run time
async with httpx.AsyncClient() as client:
    deps = IRDeps(
        alert_id="INC-001",
        mode="autonomous",
        http_client=client,
    )
    result = await agent.run("Investigate alert", deps=deps)
```

---

## 7. Tool Allow/Deny Guardrails — FilteredToolset

```python
from pydantic_ai.toolsets.filtered import FilteredToolset
from pydantic_ai.tools import RunContext, ToolDefinition

# Blocklist for autonomous mode
AUTONOMOUS_BLOCKED = {
    'dbot_isolate_host',
    'dbot_block_ip',
    'dbot_delete_file',
    'dbot_run_script',
    'dbot_execute_command',
}

def tool_filter(ctx: RunContext[IRDeps], tool_def: ToolDefinition) -> bool:
    """Return False to hide the tool from the model entirely."""
    if ctx.deps.mode == "autonomous":
        # Blocklist approach
        return tool_def.name not in AUTONOMOUS_BLOCKED
    # Allowlist approach
    if ctx.deps.allowed_tools:
        return tool_def.name in ctx.deps.allowed_tools
    return True

filtered_dbot = FilteredToolset(
    toolset=dbot_toolset,
    filter_func=tool_filter,
)

agent = Agent('openai:gpt-4o', deps_type=IRDeps, toolsets=[filtered_dbot])
```

**ToolPrepareFunc** (per-tool, can mutate ToolDefinition):

```python
from pydantic_ai.tools import ToolDefinition

async def restrict_in_autonomous(
    ctx: RunContext[IRDeps], tool_def: ToolDefinition
) -> ToolDefinition | None:
    """Return None = hide tool. Return modified def to rename/re-describe."""
    if ctx.deps.mode == "autonomous" and "destruct" in tool_def.name:
        return None
    return tool_def

@agent.tool(prepare=restrict_in_autonomous)
async def my_destructive_tool(ctx: RunContext[IRDeps], target: str) -> str: ...
```

**Layered guardrail pattern** (filter + HITL):

```python
# Layer 1: Filter — remove tools the model should never see
filtered = FilteredToolset(toolset=dbot, filter_func=autonomous_filter)

# Layer 2: ApprovalRequired — require HITL for remaining dangerous tools
approval_wrapped = filtered.approval_required(
    lambda ctx, td, args: td.name in APPROVAL_REQUIRED_TOOLS
)

agent = Agent('openai:gpt-4o', toolsets=[approval_wrapped],
              output_type=[IROutput, DeferredToolRequests])
```

---

## 8. Structured Output Types

```python
from pydantic import BaseModel
from pydantic_ai import Agent

# Simple Pydantic model
class IROutput(BaseModel):
    summary: str
    severity: int
    iocs: list[str]
    requires_human: bool

agent = Agent('openai:gpt-4o', output_type=IROutput)

# Union output (agent OR deferred)
agent = Agent('openai:gpt-4o', output_type=[IROutput, DeferredToolRequests])

# Check at runtime
result = await agent.run(...)
if isinstance(result.output, IROutput):
    process_finding(result.output)
elif isinstance(result.output, DeferredToolRequests):
    await request_approvals(result.output)

# Freeform string (no validation)
agent = Agent('openai:gpt-4o', output_type=str)

# List union (TypeAdapter union)
agent = Agent('openai:gpt-4o', output_type=[IROutput, str, DeferredToolRequests])
```

---

## 9. Autonomous Agent Loop with Guardrails

```python
import asyncio
from pydantic_ai import Agent
from pydantic_ai.toolsets.fastmcp import FastMCPToolset

MAX_TURNS = 20  # PydanticAI default is 100; tighten for IR

async def autonomous_responder(alert: dict) -> IROutput | None:
    """Full autonomous IR loop with HITL gate for destructive actions."""
    deps = IRDeps(alert_id=alert['id'], mode='autonomous')
    history: list[ModelMessage] = []
    deferred: DeferredToolResults | None = None
    turns = 0

    while turns < MAX_TURNS:
        turns += 1
        prompt = alert['description'] if not history else None

        result = await agent.run(
            prompt,
            deps=deps,
            message_history=history,
            deferred_tool_results=deferred,
            output_type=[IROutput, DeferredToolRequests],
        )

        if isinstance(result.output, IROutput):
            audit_logger.log(
                event='investigation_complete',
                alert_id=deps.alert_id,
                turns=turns,
                output=result.output.model_dump(),
            )
            return result.output

        if isinstance(result.output, DeferredToolRequests):
            history = result.all_messages()
            deferred = await collect_approvals(result.output, deps)
            if deferred is None:
                # All actions denied — surface to human
                return None

    audit_logger.log(event='max_turns_exceeded', alert_id=deps.alert_id, turns=turns)
    return None

async def collect_approvals(
    requests: DeferredToolRequests, deps: IRDeps
) -> DeferredToolResults:
    results = DeferredToolResults()
    for call in requests.approvals:
        approved = await notify_operator_for_approval(call, deps.alert_id)
        results.approvals[call.tool_call_id] = (
            True if approved else ToolDenied('Operator denied')
        )
    return results
```

---

## 10. Alert Ingestion — FastAPI Webhook

```python
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks, Header
from pydantic import BaseModel
import hmac, hashlib, asyncio

app = FastAPI()

class AlertPayload(BaseModel):
    id: str
    source: str          # e.g., "splunk", "sentinel", "manual"
    description: str
    severity: int
    raw: dict

@app.post('/ingest/alert')
async def ingest_alert(
    payload: AlertPayload,
    background_tasks: BackgroundTasks,
    x_signature: str = Header(None),
) -> dict:
    # 1. Verify HMAC signature (optional but recommended)
    if WEBHOOK_SECRET and x_signature:
        expected = hmac.new(
            WEBHOOK_SECRET.encode(),
            payload.model_dump_json().encode(),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected, x_signature):
            raise HTTPException(403, 'Invalid signature')

    # 2. Acknowledge immediately — process in background
    background_tasks.add_task(run_ir_agent, payload.model_dump())
    return {'status': 'accepted', 'alert_id': payload.id}

async def run_ir_agent(alert: dict):
    output = await autonomous_responder(alert)
    await db.store_finding(alert['id'], output)
```

---

## 11. Alert Ingestion — File Watcher (watchdog)

```python
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import asyncio, json

class AlertDropHandler(FileSystemEventHandler):
    """Watch a directory for alert JSON files dropped by monitoring tools."""

    def __init__(self, loop: asyncio.AbstractEventLoop):
        self.loop = loop

    def on_created(self, event):
        if not event.is_directory and event.src_path.endswith('.json'):
            asyncio.run_coroutine_threadsafe(
                self._process(event.src_path), self.loop
            )

    async def _process(self, path: str):
        with open(path) as f:
            alert = json.load(f)
        output = await autonomous_responder(alert)
        # Move file to processed/
        os.rename(path, path.replace('incoming', 'processed'))

# Startup
loop = asyncio.get_event_loop()
handler = AlertDropHandler(loop)
observer = Observer()
observer.schedule(handler, path='alerts/incoming/', recursive=False)
observer.start()
```

---

## 12. JSON-Lines Audit Logging

```python
import json, time
from dataclasses import dataclass, asdict
from pathlib import Path

@dataclass
class AuditEvent:
    ts: float
    alert_id: str
    event: str           # "tool_call" | "tool_result" | "hitl_request" | "hitl_decision" | "complete"
    tool_name: str | None = None
    tool_args: dict | None = None
    tool_result: str | None = None
    approved: bool | None = None
    operator: str | None = None
    output: dict | None = None

class AuditLogger:
    def __init__(self, path: str = 'audit.jsonl'):
        self.path = Path(path)

    def log(self, **kwargs):
        event = AuditEvent(ts=time.time(), **kwargs)
        with self.path.open('a') as f:
            f.write(json.dumps(asdict(event)) + '\n')

# Usage in tools
audit = AuditLogger('logs/ir_audit.jsonl')

async def audit_tool_call(ctx, call_func, name, args):
    audit.log(alert_id=ctx.deps.alert_id, event='tool_call', tool_name=name, tool_args=args)
    result = await call_func(name, args, None)
    audit.log(alert_id=ctx.deps.alert_id, event='tool_result', tool_name=name, tool_result=str(result))
    return result
```

---

## 13. Import Map

```python
from pydantic_ai import Agent, RunContext, ApprovalRequired, ToolDenied
from pydantic_ai import DeferredToolRequests, DeferredToolResults
from pydantic_ai.mcp import MCPServerStdio, MCPServerStreamableHTTP
from pydantic_ai.mcp import ProcessToolCallback, CallToolFunc
from pydantic_ai.toolsets.fastmcp import FastMCPToolset
from pydantic_ai.toolsets.filtered import FilteredToolset
from pydantic_ai.toolsets.function import FunctionToolset
from pydantic_ai.toolsets.abstract import AbstractToolset
from pydantic_ai.tools import RunContext, ToolDefinition, ToolPrepareFunc
from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter
from pydantic_ai.result import StreamedRunResult
from pydantic_ai.exceptions import ModelRetry, UserError
from fastmcp import FastMCP
```

## Install

```bash
pip install "pydantic-ai-slim[mcp,openai]"
# or full
pip install "pydantic-ai[mcp]"
# FastMCP for in-process toolset
pip install fastmcp
# File watcher
pip install watchdog
```
