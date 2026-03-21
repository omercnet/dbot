# Architecture

How dbot runs XSOAR integrations without XSOAR.

---

## The Core Problem

Every XSOAR integration starts with:

```python
import demistomock as demisto
from CommonServerPython import *
```

`demistomock` is the XSOAR runtime context. It provides `demisto.params()`
(credentials), `demisto.args()` (inputs), `demisto.command()` (which command
to run), and captures `demisto.results()` (output). Without a running XSOAR
server, this module doesn't exist.

dbot provides a fake one.

---

## 5 Layers

```
                    MCP Client
                        |
                   dbot server (server.py)
                   /          \
             Registry        Executor
          (indexer.py)    (executor.py)
          (catalog.py)    (runner.py)
               |              |
          YAML parsing    demistomock shim
                          CommonServerPython
                               |
                        demisto/content
                        (git submodule)
```

---

## Layer 1: demistomock Shim

**File**: `dbot/runtime/demistomock.py`

The fake XSOAR runtime. Implements ~50 methods that every integration calls.

### Key Design Decisions

**Thread-safe via `contextvars.ContextVar`**: Each concurrent invocation gets
its own isolated `DemistoMock` instance. No global state, no mutex.

```python
_current_mock: contextvars.ContextVar[DemistoMock] = contextvars.ContextVar(
    "demisto_mock", default=_bootstrap_mock
)
```

**Module-level `__getattr__` proxy**: The module itself acts as the `demisto`
object. When an integration does `demistomock.params()`, Python calls
`demistomock.__getattr__("params")`, which returns
`_get_mock().params()`.

```python
def __getattr__(name: str) -> Any:
    return getattr(_get_mock(), name)
```

**Results are captured, not logged**: The real demistomock just logs results.
Our mock captures them in a list for retrieval after execution.

**Default bootstrap mock**: A mock with empty params/args is the default value
of the ContextVar. This exists because `CommonServerPython` calls
`demisto.params()` at import time (module-level code), before any real
invocation has set a mock.

### Method Categories

| Category | Methods | Behavior |
|----------|---------|----------|
| Critical | `command()`, `args()`, `params()`, `results()` | Returns injected values / captures output |
| Logging | `info()`, `debug()`, `error()`, `log()` | Writes to dbot logger + captures in mock |
| State | `getIntegrationContext()`, `setIntegrationContext()`, `getLastRun()`, `setLastRun()` | In-memory per-invocation (not persisted) |
| Stubbed | `incidents()`, `executeCommand()`, `getFilePath()`, etc. | No-ops or sensible defaults |
| Helpers | `get()`, `gets()`, `getParam()`, `getArg()` | Dict access utilities |

---

## Layer 2: CommonServerPython Loader

**File**: `dbot/runtime/common_server.py`

`CommonServerPython.py` is the XSOAR standard library (~10,000 lines). It
lives at `Packs/Base/Scripts/CommonServerPython/CommonServerPython.py` in the
content repo. It's pure Python -- it just imports `demistomock as demisto`.

The loader:

1. Injects dbot's `demistomock` module into `sys.modules["demistomock"]`
2. Creates an empty `CommonServerUserPython` stub module
3. Loads the real `CommonServerPython` via `importlib`
4. All subsequent integration imports resolve correctly

```python
bootstrap_common_modules(content_root)
# Now any integration can do:
#   from CommonServerPython import BaseClient, CommandResults, return_results
```

The loader is **idempotent** -- calling it multiple times is safe.

### Key Exports from CommonServerPython

| Export | Purpose |
|--------|---------|
| `BaseClient` | HTTP client base class with retry, proxy, auth |
| `CommandResults` | Structured command output (readable + context data) |
| `return_results()` | Writes results back through demisto.results() |
| `return_error()` | Error response helper |
| `EntryType` | Output type enum (NOTE, ERROR, FILE, etc.) |
| `EntryFormat` | Output format enum (JSON, TABLE, MARKDOWN, etc.) |
| `DBotScoreType` | Indicator type enum (IP, FILE, URL, DOMAIN, etc.) |

---

## Layer 3: Registry (Indexer + Catalog)

**Files**: `dbot/registry/indexer.py`, `dbot/registry/catalog.py`,
`dbot/registry/models.py`

### Indexer

Walks `content/Packs/*/Integrations/*/*.yml` at startup. For each YAML:

- Extracts integration name, description, category
- Parses every command: name, description, arguments, outputs
- Identifies credential params (`type: 9` in YAML)
- Flags dangerous commands (`execution: true` in YAML)
- Resolves the path to the `.py` file

### Catalog

Holds the parsed integrations in memory. Provides:

- **`search(query, category?, top_k=10)`**: Keyword search across command
  names, descriptions, pack names, and categories. Returns ranked results.
- **`get_schema(tool_name)`**: Full argument and output spec for a command.
  Secret/credential args are stripped -- the agent never sees them.
- **`resolve(tool_name)`**: Returns `(IntegrationDef, CommandDef)` for
  execution.

Tool names follow the format `Pack.command-name`
(e.g., `VirusTotal.vt-get-file`).

### Data Model

```
IntegrationDef
├── pack: str
├── name: str
├── category: str
├── py_path: str
├── commands: list[CommandDef]
│   ├── name: str
│   ├── description: str
│   ├── dangerous: bool
│   ├── args: list[ArgDef]
│   │   ├── name, required, secret, is_array, options
│   └── outputs: list[OutputDef]
│       ├── context_path, description, type
├── params: list[ParamDef]
│   ├── name, type, is_credential, hidden
└── credential_params: list[str]
```

---

## Layer 4: Executor

**Files**: `dbot/runtime/executor.py`, `dbot/runtime/runner.py`

### In-Process Mode (Development)

```python
result = await execute_inprocess(integration_py, command, args, params)
```

1. Creates a fresh `DemistoMock` with the given command/args/params
2. Sets it as the current context via `contextvars`
3. Imports the integration module via `importlib` (in a thread pool)
4. Calls `module.main()`
5. Captures `mock.get_results()` and `mock.get_logs()`
6. Returns structured result dict

Fast, but risks import side effects between calls.

### Subprocess Mode (Production)

```python
result = await execute_subprocess(integration_py, command, args, params)
```

1. Serializes `{command, args, params}` as JSON
2. Spawns `python -m dbot.runtime.runner <integration.py>`
3. Sends JSON via stdin
4. `runner.py` bootstraps demistomock + CSP, imports the integration, calls
   `main()`, captures results, writes JSON to stdout
5. Parent reads stdout, parses JSON
6. Process exits -- no import pollution

Full process isolation. Each tool call is a fresh process.

### What Happens When `invoke_tool` is Called

```
Agent calls invoke_tool("VirusTotal.vt-get-file", {"file": "abc123"}, "checking hash")
    |
    v
catalog.resolve("VirusTotal.vt-get-file")
    -> (IntegrationDef, CommandDef)
    |
    v
command.dangerous?
    -> Yes: return {status: "approval_required", ...}
    -> No: continue
    |
    v
credential_store.get("VirusTotal")
    -> {"apikey": "actual-key-from-env"}
    |
    v
executor_fn(integration.py_path, "vt-get-file", {"file": "abc123"}, {"apikey": "..."})
    |
    v
[subprocess spawns, integration makes HTTP call to VT API]
    |
    v
{success: true, results: [...], logs: [...]}
    |
    v
Return to agent (credentials stripped, reason logged to audit)
```

---

## Layer 5: Credential Store

**File**: `dbot/credentials/store.py`

Maps pack names to their required credentials. Resolved from
`config/credentials.yaml` at startup.

```yaml
VirusTotal:
  apikey: ${VT_API_KEY}
```

`${VT_API_KEY}` is resolved from the environment variable at load time.

### Security Model

- Credentials are **never exposed to the agent**
- Secret args (`type: 9` in YAML) are stripped from tool schemas
- The agent only passes non-secret inputs (hashes, IPs, domains)
- Credentials are injected server-side before execution
- The audit log records args but never credentials

---

## Audit Trail

**File**: `dbot/audit.py`

Every `invoke_tool` call is logged as a JSON-lines entry:

```json
{
  "timestamp": "2025-03-21T17:45:00+0000",
  "tool_name": "VirusTotal.vt-get-file",
  "args": {"file": "abc123"},
  "reason": "Checking hash from alert #1234",
  "dangerous": false,
  "approved_by": null,
  "result_success": true,
  "duration_ms": 1234.56
}
```

This provides a complete audit trail for post-incident review.

---

Next: [Credential Setup](credentials.md) | [Integration Guide](integrations.md)
