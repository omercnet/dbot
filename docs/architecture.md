# Architecture

## Problem

XSOAR integrations expect to run inside XSOAR. They import `demistomock` and `CommonServerPython`, read
credentials from `demisto.params()`, and report through `demisto.results()`. Running that third-party code
inside a server process gives it the server's secrets, network and filesystem. dbot runs each command in its own
container instead, and puts a gateway in front of it that decides who may run what.

## Components

```
dbot/catalog/   index content/Packs -> IntegrationDef/CommandDef; keyword search; resolve "Integration.command"
dbot/sandbox/   assemble.py (one script per integration), docker.py (DockerSandbox), probe.py
dbot/sandbox/shim/  demistomock.py + runner.py: stdlib only, run INSIDE the container
dbot/control/   config.py (dbot.yaml), secrets.py, policy.py, approvals.py (SQLite), audit.py (JSONL chain)
dbot/gateway.py the only caller of Sandbox.run
dbot/mcp.py     FastMCP stdio server over the gateway
dbot/web/       Starlette: /ingest, /approvals, /alerts, /metrics, /healthz; server-rendered HTML
dbot/agent/     responder.py (pydantic-ai), report.py (IRReport), worker.py (queue loop)
dbot/cli.py     dbot serve | worker | mcp | invoke | audit verify | config check | egress write | probe
```

## Request path

1. A caller (MCP client, responder agent, or `dbot invoke`) calls
   `Gateway.invoke(instance, command, args, reason, actor, approval_id=None)`.
2. The gateway checks, in order:
   - a non-empty reason, and args that are an object of at most 64 KB;
   - that the instance exists and is enabled;
   - that the command exists for the instance's integration (the catalog);
   - the risk (`policy.risk`): `read` overrides win, then `destructive` globs, then the integration's
     `dangerous: true` flag, else `read`;
   - the role: `viewer` alone may invoke nothing. A `read` command needs `analyst`, `approver` or `agent`.
     Anyone with a non-viewer role may request a destructive command;
   - for a destructive command, the approval. Without `approval_id`, the gateway creates an approval and returns
     `approval_required`. With one, `Approvals.consume()` must succeed: approved, unexpired, unused, and matching
     `sha256(canonical_json(instance, command, args))`.
3. It resolves the instance's `env://` and `file://` params. When `DBOT_EGRESS_PROXY` is set, it also forces
   `params.proxy = true`, so `CommonServerPython` keeps the proxy env.
4. `DockerSandbox.run` assembles the script, pulls the integration's `dockerimage` if it is missing, and starts
   `docker run --rm -i --read-only --user 65534:65534 --cap-drop ALL --security-opt no-new-privileges --pids-limit
   256 --memory 512m --cpus 1` with the script directory mounted read-only at `/dbot`. Params travel only on stdin,
   never in argv or the environment.
5. The results are redacted (every known secret value is masked). Every outcome is audited, including denials and
   crashes.

## Script assembly

XSOAR runs an integration in a single namespace. `assemble()` writes one file per (content commit, integration)
under `DBOT_CACHE_DIR`. The file contains:

1. the typing names XSOAR provides;
2. `CommonServerPython`;
3. a `CommonServerUserPython` stub;
4. each `*ApiModule`, inlined transitively in place of its `from X import *` line;
5. the integration source.

## Wire protocol (inside the container)

`/dbot/runner.py` reads one JSON object, `{"command", "args", "params"}`, from stdin. It installs the shim
`demistomock`, runs the integration's `main()`, and writes exactly one JSON line to stdout:

```json
{"results": [...], "logs": [...], "exit": "ok|sysexit|exception|import", "error": null}
```

The host maps that line to a `CommandResult`:

| Condition | status | error_kind |
|---|---|---|
| any entry with `Type == 4` | error | integration |
| `sys.exit()` with no results, or with a nonzero/error exit | error | runtime |
| `sys.exit()` after results were returned | ok (the results count) | |
| import failure | error | import |
| uncaught exception | error | runtime |
| deadline (default 60s), container killed with `docker kill` | error | timeout |
| OOM or signal (exit > 128) | error | killed |
| docker or infrastructure failure | error | sandbox |
| `test-module` without an `"ok"` result | error | integration |

## Alert flow

1. `POST /ingest/{source}` checks `X-Dbot-Signature: sha256=<hex>` (HMAC-SHA256 over the raw body). It validates the
   alert schema, dedupes on `(source, alert.id)`, and enqueues the alert in `DBOT_DATA_DIR/dbot.db`.
2. `dbot worker` claims the next queued alert and runs the responder. The responder is a pydantic-ai agent whose
   tools are `search_tools`, `get_tool_schema` and `invoke`. Each `invoke` call goes to the gateway as
   `Actor(id="agent:responder", kind="agent", roles={"analyst"}, on_behalf_of="alert:<id>")`. It is limited by
   `agent.max_tool_calls` and `agent.wall_clock_s`.
3. The worker stores the resulting `IRReport` (verdict, confidence, summary, evidence, actions taken, actions
   pending, errors). The model writes only the verdict, confidence and summary. dbot fills in the rest from what the
   gateway returned during the run: one evidence entry per executed or denied `invoke` (instance, command, masked
   args, status, and an excerpt of the result or error), plus the actions taken and pending. If the model fails or
   runs out of budget, the report is `inconclusive` with the evidence collected so far. The alert becomes `done`,
   or `waiting_approval` if any action is pending, or `failed`.
4. When an approver decides one of the alert's approvals on `/approvals`, the web app requeues the alert. On the
   next run, the responder executes the approved action exactly once and updates the report.

## State

Everything lives under `DBOT_DATA_DIR` (default `/var/lib/dbot`):

| Path | Contents |
|---|---|
| `dbot.db` | SQLite: `approvals` and `alerts` tables |
| `audit/audit-YYYY-MM-DD.jsonl` | the hash-chained audit log (UTC days; the chain continues across files) |
| `cache/` | the catalog index cache, keyed by content commit |

Assembled scripts go to `DBOT_CACHE_DIR` (default `~/.cache/dbot/assembled`). When `DBOT_DATA_DIR` is unset, the
catalog cache goes to `<repo>/config/`, which is gitignored.
