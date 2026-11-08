# SOC rebuild: execution plan

Design rationale: `soc-rebuild.md` (Fable decision doc + spike addendum). Owner decisions (2026-11-08):

- Q1 rebuild.
- Q2 approver page only. The React chat SPA is deleted; analysts chat through MCP clients.
- Q3 v1 certifies threat-intel enrichment plus a generic HMAC webhook.
- Q4 secrets come from env/file only; the dbot container mounts the docker socket.

Branch: `feat/soc-rebuild`. Python 3.13 host, uv-managed. Ship one PR per wave.

## Problem
dbot today runs third-party integration code inside the server process. It also has an unauthenticated REST API that handles secrets, approvals that nothing can grant, and success detection by string match. Roughly a third of the library cannot import. None of this is deployable in a SOC.

## Non-goals (v1)
- JS, PowerShell and python2 integrations.
- Native OIDC (use oauth2-proxy).
- Helm.
- Vault or AWS Secrets Manager backends (the URI scheme leaves room for them).
- Pull-mode fetch-incidents.
- Multi-worker queue.
- Chat UI.
- Settings or credential editing over HTTP.
- Vector search.

## Target layout (new code only; old modules are deleted in wave C)

```
dbot/catalog/      models.py index.py search.py          (port of dbot/registry + new fields)
dbot/sandbox/      types.py (CONTRACT, exists)  assemble.py  docker.py  probe.py
dbot/sandbox/shim/ demistomock.py runner.py               (stdlib-only, Python 3.10 compatible, runs IN the container)
dbot/control/      config.py secrets.py policy.py approvals.py audit.py
dbot/gateway.py    the only path from MCP/agent to a sandbox
dbot/mcp.py        FastMCP stdio server over the gateway
dbot/web/          app.py ingest.py approvals.py auth.py  (Starlette, server-rendered HTML, no JS build)
dbot/agent/        responder.py report.py worker.py       (pydantic-ai)
dbot/cli.py        typer: serve | mcp | worker | audit verify | probe | invoke
deploy/            Dockerfile compose.yml squid.conf oauth2-proxy.cfg.example
tests/<area>/      per-area tests; docker tests marked `docker`, real-LLM tests marked `llm`
```

## Contracts

### C1. Catalog (`dbot/catalog/models.py`)
Port `dbot/registry/models.py`. Field names stay unchanged so callers keep working. Add these fields to `IntegrationDef`:

- `dockerimage: str = ""` (from `script.dockerimage`)
- `script_type: str = "python"` (from `script.type`)
- `subtype: str = "python3"` (from `script.subtype`)
- `apimodules: list[str] = []` (names from `^from (\w+ApiModule) import \*` in the .py, in order)
- `yml_path: str`

`py_path` stays absolute. Catalog API:

- `Catalog(integrations)`
- `.search(query, limit=10) -> list[dict]`
- `.resolve("Integration.command") -> (IntegrationDef, CommandDef)`
- `.get(name) -> IntegrationDef`

Tool names are `<IntegrationDef.name>.<command>`.

The catalog indexes only `script_type == "python"` and `subtype == "python3"`, and nothing deprecated by default.

### C2. Sandbox (`dbot/sandbox/types.py`, exists)
`Sandbox.run(integration, ExecRequest) -> CommandResult`. Rules:

- Any entry with `type == 4` gives `status="error"` and `error_kind="integration"`, with `error` set to that entry's contents.
- `sys.exit()` is not success. Results captured before the exit still count.
- Import failure gives `error_kind="import"`.
- An uncaught exception gives `"runtime"`.
- A deadline gives `"timeout"`, and the container is killed (docker kill), not just abandoned.
- OOM or signal gives `"killed"`.
- A docker or infra failure gives `"sandbox"`.
- `test-module` is `ok` only if a results entry equals `"ok"`.

Wire protocol inside the container: the runner reads one JSON `{command,args,params}` on stdin and writes exactly one JSON line on stdout, `{"results":[...],"logs":[...],"exit":"ok|sysexit|exception|import","error":str|null}`. The host maps this to `CommandResult`.

### C3. Script assembly (`dbot/sandbox/assemble.py`)
`assemble(integration, content_root) -> Path` writes one file to a cache dir, keyed by content commit and integration. The file mirrors XSOAR's single-namespace execution:

- `from typing import *` plus the typing names XSOAR provides
- `CommonServerPython`
- the `CommonServerUserPython` stub
- each ApiModule inlined, transitively, in place of its `from X import *` line
- the integration source

### C4. Instances and secrets (`dbot/control/config.py`, `secrets.py`)
Operators edit one YAML file, `DBOT_CONFIG`, default `/etc/dbot/dbot.yaml`. This format is a public contract:

```yaml
instances:
  - name: abuseipdb                 # unique; what the agent passes
    integration: AbuseIPDB          # IntegrationDef.name
    params: {server: https://api.abuseipdb.com, credentials: {password: env://ABUSEIPDB_API_KEY}}
    allow_hosts: [api.abuseipdb.com]  # egress allowlist; default derived from url-like params
    enabled: true
policy:
  destructive: ["*block*", "*isolate*", "*quarantine*", "*disable*", "*delete*", "*revoke*", "*suspend*", "*unblock*", "*release*", "*-create-*", "*-update-*"]
  read: ["test-module"]            # explicit overrides win over destructive globs
roles:                              # OIDC group -> role
  viewer: [soc-viewers]
  analyst: [soc-analysts]
  approver: [soc-leads]
ingest:
  sources:
    generic: {hmac_secret: env://DBOT_INGEST_SECRET}
agent:
  model: "openai:gpt-5.4-mini"      # any pydantic-ai model string
  max_tool_calls: 20
  wall_clock_s: 300
```

Secret values are `env://NAME` or `file:///run/secrets/x`; any other string is a literal. `secrets.resolve(params) -> dict` returns the resolved params. `secrets.known_values() -> set[str]` returns every resolved secret so the audit log and other logs can redact them.

### C5. Policy, approvals, audit (`dbot/control/`)
- **`policy.risk(command_name, instance) -> "read" | "destructive"`.** `read` overrides win, then `destructive` globs, then the YAML `dangerous: true` flag (counts as destructive), else `read`.
- **`Approvals` (SQLite, `DBOT_DATA_DIR/dbot.db`):**
  - `request(instance, command, args, reason, requested_by) -> Approval`
  - `decide(id, approver, approve: bool) -> Approval`
  - `get(id)`
  - `pending()`
  - `consume(id, instance, command, args) -> bool`. True only if approved, unexpired (TTL 1h), unconsumed, and `sha256(canonical_json(instance, command, args))` matches. Single use.
- **`AuditLog` (JSONL, `DBOT_DATA_DIR/audit/audit-YYYY-MM-DD.jsonl`):**
  - `append(event: dict) -> dict` adds `ts`, `seq`, `prev_hash` and `hash = sha256(prev_hash + canonical_json(record_without_hash))`, and redacts known secret values.
  - `verify(path) -> (ok, first_bad_seq)`.
  - Canonical JSON is `json.dumps(sort_keys=True, separators=(",", ":"), default=str)`.

### C6. Gateway (`dbot/gateway.py`)
```python
class Actor(BaseModel): id: str; kind: Literal["human", "agent"]; roles: set[str]; on_behalf_of: str | None = None
class Pending(BaseModel): status: Literal["approval_required"] = "approval_required"; approval_id: str; risk: str; message: str
class Denied(BaseModel): status: Literal["denied"] = "denied"; reason: str
async def invoke(instance: str, command: str, args: dict, reason: str, actor: Actor, approval_id: str | None = None) -> CommandResult | Pending | Denied
```
Order of checks:

1. Instance exists and is enabled.
2. The command exists for its integration.
3. Role check: `analyst` or `agent` may run `read`; anyone may request `destructive`.
4. If the command is destructive, `approval_id` must `consume()` true, otherwise return `Pending` (creating the approval).
5. Resolve secrets.
6. Run the sandbox with the egress proxy.
7. Audit every outcome, including denials.

Nothing else may call `Sandbox.run`.

### C7. MCP (`dbot/mcp.py`)
These tools sit over the gateway. The actor is `agent:mcp`, and its roles come from env `DBOT_MCP_ROLES` (default `analyst`).

- `search_tools(query)`. Results include the configured instances for each integration.
- `get_tool_schema(tool_name)`
- `list_instances()`
- `invoke_tool(instance, command, args, reason, approval_id=None)`
- `get_approval(approval_id)`

Tool results return `human_readable` and `context`, capped at 20 KB.

### C8. Web (`dbot/web/`)
- **Identity:** taken from `X-Forwarded-User` and `X-Forwarded-Groups`, trusted only when the peer IP is in `DBOT_TRUSTED_PROXIES`; otherwise 401.
- **Routes:**
  - `POST /ingest/{source}`: HMAC `X-Dbot-Signature: sha256=<hex>` over the raw body; no proxy auth; dedupes on `(source, alert.id)`.
  - `GET /approvals` (HTML)
  - `POST /approvals/{id}/approve` and `/deny` (approver role, CSRF token)
  - `GET /alerts`, `GET /alerts/{id}` (report view, viewer role)
  - `GET /healthz`
  - `GET /metrics` (Prometheus)
- **Alert schema:** `{id, source, severity, title, observables: [{type: ip|domain|url|hash|email|user|host, value}], raw}`.

### C9. Agent (`dbot/agent/`)
- The worker polls the alert queue (SQLite).
- The responder is a pydantic-ai agent whose tools call `gateway.invoke` with `Actor(kind="agent", on_behalf_of=f"alert:{id}")`.
- It writes an `IRReport`: verdict (`benign`, `suspicious`, `malicious`, `inconclusive`), summary, evidence with entry references, actions taken, and actions pending approval.
- When an approval is decided, the worker resumes the alert.

## Waves

| Wave | Slice | Owns (files) | Depends on |
|---|---|---|---|
| A | A1 sandbox | dbot/sandbox/{assemble,docker,probe}.py, dbot/sandbox/shim/*, tests/sandbox/* | C1, C2 |
| A | A2 catalog | dbot/catalog/*, tests/catalog/* | C1 |
| A | A3 control | dbot/control/*, tests/control/* | C4, C5 |
| A | A4 build+deploy | pyproject.toml, uv.lock, .github/workflows/*, deploy/*, mise.toml, .pre-commit-config.yaml | none |
| B | B1 gateway+mcp+cli | dbot/gateway.py, dbot/mcp.py, dbot/cli.py, tests/gateway/* | A1-A3 |
| B | B2 web | dbot/web/*, tests/web/* | A3, C6 |
| B | B3 agent | dbot/agent/{responder,report,worker}.py, tests/agent/* | C6, C9 |
| C | C1 certify TI | replay proxy + fixtures from content `test_data/` for VirusTotal v3, AbuseIPDB, AlienVault OTX, GreyNoise, urlscan.io | B1 |
| C | C2 cutover | delete dbot/{registry,runtime,tools,config,credentials,ui}, old agent/web, old tests; rewrite README + docs; LLM eval scenarios | B* |

Rules for every slice:

- Touch only the files you own.
- Need a dependency? Ask the A4 owner, or the orchestrator if A4 is done.
- Run only your area's tests.
- Code that runs inside the container (`dbot/sandbox/shim/*`) is stdlib-only and Python 3.10 compatible.
- Never log or persist resolved params.

## Acceptance (v1)
1. The nightly probe shows at least 1,100 of the python3 integrations reaching `main()` in their own images.
2. AbuseIPDB returning 404 gives `status=error`.
3. A timeout kills the container within 2s of the deadline.
4. A destructive command without approval is rejected at the gateway, in a test that bypasses the agent. A tampered audit line is detected by `dbot audit verify`. A canary secret appears in no log or audit file.
5. The 5 TI integrations pass contract tests (test-module, curated commands, one error fixture) against replay fixtures in CI.
6. `docker compose up` on a clean VM, followed by a signed webhook alert, produces an IRReport with evidence from at least 2 instances. A destructive action executes only after approval, and the whole flow appears in the audit chain.
7. The real-LLM opencode+MCP eval passes nightly. It is advisory on PRs.
