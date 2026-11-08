# dbot rethink: decision document

Repo: omercnet/dbot at worktree worthy-anaconda, content submodule demisto/content @ 0e59e69.
Scope of this document: read-only review plus a target design. No code changed.

## 0. Verification of the brief (what I re-checked)

All claims in the brief that I could check against the tree hold:

- `dbot/runtime/executor.py:104`: `has_error = any("error" in str(r).lower() for r in results)`. Only runs on the `SystemExit` path. The normal-return path (line 94) is unconditionally `success: True`. Neither path reads the XSOAR entry `Type` field (4 = error). So the AbuseIPDB false positive is structural, not a one-off.
- `execute_inprocess` uses `asyncio.wait_for(run_in_executor(...))` (line 33). The thread is not cancellable; a hung integration leaks a worker thread and keeps the imported module's side effects (sys.path mutation, env) in the server process.
- `dbot/config/api.py:497-521`: 24 `Route(...)` entries, no auth middleware, no token check anywhere in the file. Credentials are written over this API in plaintext over HTTP.
- `dbot/agent/guardrails.py:44`: chat agent explicitly has "No approval_required fields" because `output_type=str` is incompatible with `DeferredToolRequests`. The only place approval is resolved is `ResponderAgent.on_deferred` (responder.py:140) and the CLI prompt (cli.py:145). The MCP server path (`dbot/tools/invoke.py:51`) returns `approval_required` and nothing can ever approve it.
- `content/Packs/ApiModules/Scripts/`: 30 modules. dbot only stubs `DemistoClassApiModule` (common_server.py:42). Top importers: MicrosoftApiModule (34 integrations), IAMApiModule (30), AWSApiModule (16), AnsibleApiModule (12), HTTPFeedApiModule (9), JSONFeedApiModule (8), SiemApiModule (6). These are the integrations a SOC actually wants (Defender, Entra, AWS, Okta via IAM), so the 140 import failures are not fringe.
- dockerimage distribution matches: demisto/python3 779, py3-tools 44, crypto 39, auth-utils 32, boto3py3 28, python (py2) 25, ansible-runner 25, googleapi-python3 16, vendors-sdk 15, fastapi 12.
- 92 JavaScript, 12 PowerShell, 33 python2 integrations.
- 340 pytest tests.
- `demisto-sdk` is not installed in /tmp/dbot-venv. `opencode` v2.2.0 is installed, `docker` is present.

### MCPApiModule and demisto-sdk: do they make parts of dbot unnecessary?

- `MCPApiModule` is the opposite direction. It is an MCP *client* (uses `mcp.client.streamable_http`) that lets XSOAR integrations such as GenericMCP, GitHubMCP, CloudflareMCP, AtlassianCloudMCP call remote MCP servers. It does not expose content commands as MCP tools and does not help run integrations outside XSOAR. Irrelevant to dbot's core; mildly relevant later (dbot could expose those four packs as pass-through MCP bridges, low value).
- `demisto-sdk` [INFERENCE from prior knowledge of the SDK, not installed here to confirm]: its `unify` / `prepare-content` step is what inlines `from XApiModule import *` into the integration script at build time, and its `lint` / `pre-commit` flow runs integration unit tests inside the declared `script.dockerimage`. `demisto-sdk run` requires a live XSOAR server, so it does not replace an executor. Two reusable pieces: (a) the unify logic as a reference for ApiModule inlining (or call `demisto-sdk prepare-content` directly at catalog-build time), (b) the docker-image convention. Recommendation: do not depend on demisto-sdk at runtime (it is heavy, pins many deps, and drags in XSOAR-server assumptions); copy the ApiModule resolution rule, which is a ~40 line regex-and-splice.

## 1. Rebuild or not?

Answer: rebuild the runtime and the control plane; keep the catalog, the demistomock core, and the test corpus as an asset. Narrow the product scope hard. "Fix in place" is wrong because the three load-bearing pieces (in-process executor, unauthenticated config API, unenforceable approval) are each a design, not a bug, and replacing all three leaves little of the current wiring.

Survives:
- `dbot/registry` (catalog/indexer). 0.7s over 1,270 integrations, correct YAML parsing, the data model is the right shape. Add fields: `dockerimage`, `apimodules[]` (parsed from imports), `script.type/subtype`, per-command `deprecated`, `arguments[].required`, `outputs[]` context paths. Keyword search is fine for v1; the agent does not need vector search to find "ip reputation".
- `dbot/runtime/demistomock.py` and `common_server.py` bootstrap. They encode real knowledge of the XSOAR contract (`demisto.params/args/results/incidents/getIntegrationContext`, `callingContext`, `integrationInstance`). Move them into the sandbox image as the "runner shim"; add the missing `callingContext` / `integrationInstance` / None-param handling that the probe surfaced (about 190 crashes).
- `dbot/runtime/runner.py` (subprocess JSON protocol). Becomes the in-container entrypoint.
- The tool triad `search_tools / get_tool_schema / invoke_tool` and the "reason is REQUIRED" convention. That is the right MCP surface.
- pytest corpus as a regression floor during the rebuild; expect to delete the half that pins current wiring.
- The React SPA is optional. Keep it only if the chat surface matters for v1 (see scope cut). It is not the SOC's system of record and should not be where credentials are typed.

Dies:
- `execute_inprocess`. Imports untrusted third-party code into the server process with no isolation, no kill, shared sys.modules. Not defensible in a SOC.
- `dbot/config/api.py` as a public REST surface with secrets. Replace with a server-side config file plus a secrets backend; the UI, if kept, only reads non-secret settings behind auth.
- Fernet-in-SQLite credential store with the key next to the DB. Replace (section 2.4).
- The responder's file-drop watcher and the two divergent credential paths (`credentials.yaml` vs DB).
- The `approval_required` dict-return. Replace with a real approval object with state.
- Docs that describe a nonexistent Docker deployment.

Honest framing: the current code is a good proof that the content library can be driven by an LLM. It is not a system. The rebuild is mostly infrastructure around a core that already exists.

## 2. Target architecture for SOC use

Components (each a separately testable process or module):

```
[SIEM/webhook] -> ingest -> alert queue -> agent worker (pydantic-ai) -> MCP tools -> gateway
                                                     |                     |
                                                 approvals DB         sandbox pool (docker, per dockerimage)
                                                     |                     |
                                                 audit log (hash chain)  egress proxy
```

### 2.1 Execution isolation

Run every command in a container built from the integration's declared `script.dockerimage` (demisto/python3:3.x, demisto/boto3py3, demisto/crypto, ...). This solves the 254 missing-lib failures by construction, because these images are exactly what XSOAR runs them in, and gives process isolation, CPU/memory limits, and a killable timeout for free.

Design:
- Image: `FROM <dockerimage>` plus a thin layer copying `demistomock.py`, `CommonServerPython.py`, `CommonServerUserPython.py` (from content), the resolved ApiModules, and `runner.py`. Build lazily on first use, cache by `(dockerimage digest, content commit)`. About 60 distinct base images cover 1,204 integrations; the top 10 cover ~93%.
- Invocation: `docker run --rm --network <egress-net> --read-only --tmpfs /tmp --memory 512m --cpus 1 --pids-limit 256 --cap-drop ALL --security-opt no-new-privileges -i <image> python runner.py` with the JSON request on stdin (integration path mounted read-only, params/args in stdin, never in argv or env). Timeout kills the container, not a thread.
- Latency: cold `docker run` of demisto/python3 is 300-800 ms on a warm host, image pull is a one-time cost (python3 is ~400 MB). Acceptable for an agent loop where each LLM turn is seconds. Pooling: keep N warm containers per hot image (python3 and the top 5) running a tiny loop that reads requests from stdin, one request per container lifetime, then exit. Simpler than a long-lived daemon and still avoids the exec cost. Do pooling in milestone M3, not M1; measure first.
- Alternative considered: gVisor/Firecracker. Not needed for v1; docker with the hardening flags above is the SOC-standard baseline. Make the runtime pluggable (`Sandbox` protocol with `run(req) -> resp`) so a `podman` or `runsc` backend is a config change.
- JavaScript (92) and PowerShell (12) integrations: out of scope for v1. python2 (33): out of scope; most are deprecated duplicates.
- Secret handling in the sandbox: credentials are injected per call as params (what XSOAR does); the container has no secret store access and is destroyed after the call.

### 2.2 ApiModule resolution

At catalog build time, for each integration, scan for `^from (\w+ApiModule) import \*` and record the list. At image build / request time, the runner shim resolves them in order by prepending `Packs/ApiModules/Scripts/<Name>/<Name>.py` onto `sys.path` of the container (simplest) or by textual inlining the way `demisto-sdk unify` does. sys.path is enough since the modules are plain `.py` files that themselves import `demistomock` and `CommonServerPython`; ApiModules can import other ApiModules (MicrosoftApiModule does not, IAMApiModule does not, but check transitively at build and fail loudly). This single change fixes 140 of the 394 import failures.

### 2.3 Result and error semantics

Define a typed `CommandResult`:

```
status: ok | error | partial
entries: list[Entry]           # each Entry: type (1 note, 4 error, 9 warning...), contents, human_readable, entry_context, raw
context: dict                  # merged EntryContext across entries
error: str | None
exit_code, duration_ms, image, stdout_tail, stderr_tail
```

Rules:
- Any entry with `Type == 4` (EntryType.ERROR) -> `status=error`, `error=` its Contents. Never string-match "error".
- `return_error` calls `demisto.results` with Type 4 then `sys.exit(0)`; exit 0 is not success.
- Non-zero exit, timeout, OOM -> `status=error` with a distinct `error_kind` (runtime, timeout, killed, import).
- `test-module` is special-cased: output `ok` string means ok, anything else is the error text.
- The MCP tool returns `human_readable` plus `context` to the model, with `raw` capped and available via a follow-up `get_result(id)` tool, so a 2 MB raw response does not blow the context window.

### 2.4 Credentials and secrets

- Instance model (XSOAR concept, currently missing): an *instance* = (integration, name, params including credentials). The agent calls `invoke_tool(instance="virustotal-prod", command=...)`, never raw credentials. Multiple instances per integration are a day-one SOC need (prod/test tenants).
- Storage: server-side config file for non-secret params; secrets referenced by URI (`env://`, `file://`, `vault://kv/dbot/virustotal`, `aws-sm://...`). v1 backends: env and file (sops-encrypted or plain file with 0600 behind a mounted docker secret). Vault/AWS SM as pluggable backends. Delete Fernet-in-SQLite; a key stored next to the ciphertext is not encryption at rest in any auditor's book.
- Secrets never leave the gateway process except into the sandbox stdin for one call. Never logged; redact by value in the audit sink (keep a set of secret values in memory and scrub).
- No credential CRUD over HTTP in v1. Operators edit config and restart or `SIGHUP`.

### 2.5 AuthN / AuthZ

- All HTTP (UI, webhooks, approvals API) sits behind an OIDC relying party. v1: use `oauth2-proxy` (or equivalent) in front; dbot trusts `X-Forwarded-User` / `X-Forwarded-Groups` only from the proxy's network. This is boring and auditors know it. Native OIDC in-app is a v2 item.
- MCP stdio transport: identity is the OS user; the MCP server runs with a per-deployment role config. If MCP over HTTP is ever exposed, it goes behind the same proxy with bearer tokens.
- RBAC, three roles is enough: `viewer` (read results, audit), `analyst` (run read-only commands, request approvals), `approver` (approve/deny destructive actions). Groups map from OIDC claims. Command risk tiers come from a per-command policy file (default: everything unknown is `read`; a curated list of destructive verbs such as isolate/quarantine/block/disable/delete/revoke is `destructive`; `test-module` and enrichment are `read`).

### 2.6 Enforced approval workflow with tamper-evident audit

- Approval is a server-side object: `Approval(id, instance, command, args, reason, requested_by (agent run id + human on whose behalf), risk_tier, status pending|approved|denied|expired, decided_by, decided_at, ttl)`. Stored in SQLite/Postgres.
- Enforcement point is the gateway, not the agent. `invoke_tool` for a destructive command without a valid approved `approval_id` is rejected at the gateway; the agent cannot talk itself past it because the agent has no other path to the sandbox. The pydantic-ai `DeferredToolRequests` mechanism is fine for the agent-side pause, but the gateway check is the thing that matters.
- Approver UI: a page listing pending approvals with full args, the agent's stated reason, and the alert context; approve/deny buttons; Slack notification optional. Approval binds to the exact args hash; any change requires a new approval.
- Audit: append-only JSONL where each record carries `prev_hash` and `hash = sha256(prev_hash + canonical_json(record))`. Rotate daily, write the chain head to an external sink (S3 object lock, syslog to the SIEM, or just a signed daily digest). Every tool call, approval decision, login, and config reload is a record. Verification CLI `dbot audit verify <file>` walks the chain.

### 2.7 Alert ingestion

- v1: one HTTPS webhook `POST /ingest/{source}` with HMAC-signed body (shared secret per source) and a normalized `Alert` schema (id, source, severity, title, observables[] with type ip/domain/hash/user/host/email/url, raw). Adapters for Splunk alert actions, Elastic/Kibana webhook, Microsoft Sentinel via Logic App, and generic JSON. Dedup on `(source, id)`.
- Queue: SQLite table with a worker poll in v1 (one process). Postgres + `SELECT ... FOR UPDATE SKIP LOCKED` when multi-worker is needed. No Kafka.
- Later: pull mode via the content library's own fetch-incidents commands, which is a nice reuse of content but needs instance scheduling; v2.

### 2.8 Agent loop

- Keep pydantic-ai. One agent type in v1: the alert responder. Input: `Alert`. Loop: triage -> enrich observables (read-tier tools only, bounded by a tool budget and wall clock) -> propose actions -> for each destructive action create an Approval and pause -> on decision, execute -> write `IRReport` (verdict, evidence with entry references, actions taken, actions pending).
- Instructions are built from the catalog, not hand-written per integration: the system prompt lists available *instances* and their risk tiers; `search_tools` returns command schemas on demand.
- Determinism aids: temperature 0, a fixed enrichment playbook skeleton (for each observable type, the preferred instance list), and the model only decides ordering and verdict. This keeps the eval stable and makes the agent explainable to a SOC lead.
- Chat agent stays as a thin wrapper for analysts (same tools, same gateway), but it is not required for the day-one acceptance.

### 2.9 Deployment

- One image for dbot (`ghcr.io/omercnet/dbot`), running the gateway + worker + UI. It needs the docker socket (or a remote `DOCKER_HOST`) to launch sandboxes; document this as the trust boundary and offer the sysbox/podman-remote variant for people who will not mount the socket.
- `docker-compose.yml`: dbot, oauth2-proxy, postgres (optional, SQLite default), an egress proxy (section 2.11). Helm chart in v2 only after compose has a user.
- Content pinned by submodule commit and baked into the image; sandbox base images pinned by digest in a lockfile generated at build.
- Python lockfile (`uv.lock`) is mandatory. The missing lockfile is why pydantic-ai 2.x broke 24 tests.

### 2.10 Observability

- Structured JSON logs with a run id and alert id on every line. OpenTelemetry traces: one span per agent run, child spans per LLM call (pydantic-ai has instrumentation) and per sandbox exec with image, duration, exit code, result status. Prometheus metrics: tool calls by instance and status, sandbox latency histogram, approvals pending gauge, LLM tokens per run.
- The audit log is a separate stream from app logs and is never sampled.

### 2.11 Egress control

- Sandboxes attach only to a docker network whose only route is an egress proxy (squid or a small allowlist proxy). The proxy has a per-instance allowlist derived from the instance's configured `url` param plus a per-pack static list (e.g. VirusTotal -> `www.virustotal.com`). `HTTP_PROXY/HTTPS_PROXY` env set in the sandbox; `requests` and `httpx` honor it; raw sockets are blocked by the network having no other route. Default deny; the proxy logs every CONNECT with instance id, which doubles as an exfil tripwire.
- The dbot host process itself only needs egress to the LLM provider and the alert sources.

## 3. Scope cut for v1

Day one for a SOC means: an alert comes in, gets enriched from the tools the SOC already pays for, a human sees a report with evidence, and destructive actions are impossible without a named approver. Nothing else.

In v1:
- Sandboxed executor with docker images, ApiModule resolution, typed results.
- Instances + secret URIs (env, file).
- Webhook ingest (generic JSON + one real SIEM adapter chosen by the owner).
- Responder agent with approvals gateway and hash-chained audit.
- Approver UI (one page) behind oauth2-proxy. Chat UI optional.
- MCP stdio server (same tools) for the opencode-based eval and for analysts with MCP clients.
- Compose deployment, lockfile, 20 certified integrations.

Not in v1: JS/PowerShell/py2 integrations, native OIDC, Helm, pull-mode fetch, multi-worker queue, vector search, settings REST API, credential editing in the UI, the file-drop watcher, Vault/AWS SM backends (interfaces yes, implementations no).

### Certify-first list (about 20, by SOC value, all python3)

TI enrichment: VirusTotal (v3), AbuseIPDB, AlienVault OTX, GreyNoise, Shodan, urlscan.io, MISP, Recorded Future (if licensed; else skip).
EDR: CrowdStrike Falcon, Microsoft Defender for Endpoint (uses MicrosoftApiModule, proves ApiModule path), SentinelOne, Cortex XDR.
IdP: Okta v2, Azure AD / Entra ID (MicrosoftApiModule), Google Workspace admin (googleapi-python3 image).
Email: Microsoft Graph Mail, Gmail, Proofpoint TAP.
SIEM: Splunk (SplunkPy), Microsoft Sentinel, Elastic/Elasticsearch.
Ticketing/chat: ServiceNow v2, Jira v3, Slack v3.
Cloud: AWS IAM / EC2 (AWSApiModule, boto3py3 image).

That is 25; cut to 20 by owner's actual stack. The list deliberately spans 5 docker images and 3 ApiModules.

### What "certified" means (proven, not asserted)

An integration is certified when all of the following are true in CI:
1. Its image builds and `test-module` runs in the sandbox against a recorded fixture and returns `ok`.
2. Every command on its curated allowlist (not every command in the YAML; the 3-10 the SOC uses) has a contract test with a recorded HTTP fixture that asserts `status`, the `entry_context` key paths declared in the YAML `outputs`, and `human_readable` non-empty.
3. One error fixture per integration (401 and 404 or rate-limit) asserts `status=error` and the error text is surfaced.
4. Its risk-tier policy entry exists (every command classified read or destructive).
5. A quarterly live smoke against a real tenant (manual, outside CI) is recorded in the audit log.

## 4. Testing strategy

Layers, cheapest first:

1. Unit: catalog parsing, ApiModule resolution, result typing (Type 4 -> error), approval state machine, audit hash chain, policy classification. Pure Python, fast. Keep the TestModel-based agent tests only for loop mechanics (budget exhaustion, deferred handling).
2. Sandbox contract tests per certified integration: run the real integration in its real docker image, with HTTP recorded by a proxy-level recorder. Because the integration runs in a container, in-process `vcr.py` does not apply; instead point the sandbox `HTTPS_PROXY` at a replay proxy (mitmproxy in replay mode, or a tiny `httpx`-based record/replay server that keys on method+URL+body hash) that serves fixtures committed to `tests/fixtures/<integration>/<command>.json`. Recording mode uses real credentials from the developer's env and scrubs secrets and tenant identifiers before commit. This is also the egress proxy from 2.11 running in a different mode, which is a nice reuse. These tests run in CI with docker available (GitHub Actions ubuntu runners have it) and pull base images from a cache.
3. Probe test (already exists as a script): re-run the 1,270-integration import probe nightly inside sandboxes and publish the counts; regressions in "reaches main()" fail the job. This is the health metric for the content submodule bump.
4. Real-LLM e2e via opencode CLI + MCP:
   - CI job starts `dbot mcp` (stdio), writes an `opencode.json` with the MCP server entry, and runs `opencode run --standalone --auto -m opencode/big-pickle --format json "<task>"`. Three to five scenario prompts, each with a sandbox backed by replay fixtures so the integration side is deterministic.
   - Assertions are on side effects, not on prose: the audit log must contain a `search_tools` call, an `invoke_tool` for the expected instance/command with an argument equal to the observable, and for the destructive scenario, an `Approval` in `pending` state and no sandbox exec for that command. Verdict text is checked loosely (contains the expected observable and one of a small set of verdict words).
   - Flake control: run each scenario up to 3 times and pass if any run passes (nondeterminism); serialize scenarios and sleep between them (free-tier rate limits); mark the job `continue-on-error: true` on PRs and required only on nightly, with a trend badge; cache nothing from the model; fail fast on `FreeUsageLimitError` as "skipped" not "failed". Rotate across two or three free models so one model's outage does not block.
   - The key stays in the `OPENCODE_API_KEY` secret; the job writes it to the opencode auth location at runtime and never echoes it.
5. UI: keep vitest for the approver page; one Playwright smoke against compose.

## 5. Phased plan

Each milestone is split into slices that different agents can own concurrently; shared contracts are named.

M0 (foundation, 1 week): `uv.lock`; CI with docker; `Sandbox` protocol and `CommandResult` types defined in `dbot/runtime/types.py`; content pinned. Accept: lockfile committed, CI green on a clean runner, types reviewed.
Parallel slices: lockfile+CI; types+policy file format; catalog field additions (dockerimage, apimodules, outputs).

M1 (sandbox executor): docker backend, image build with shim layer, ApiModule resolution, typed results, kill-on-timeout, nightly probe. Accept: probe shows >= 1,100 of 1,270 reach `main()` (from ~830), AbuseIPDB 404 reports `status=error`, timeout test kills the container within 2 s of deadline. Delete `execute_inprocess`.
Parallel slices: image builder; runner shim + mock fixes (callingContext etc); result typing; probe script.

M2 (instances, secrets, gateway): instance config, secret URIs (env, file), gateway process with policy and approval objects, hash-chained audit, MCP server rewired to the gateway. Accept: destructive command without approval is rejected at gateway in a test that bypasses the agent entirely; `dbot audit verify` detects a tampered line; secrets absent from all logs (grep test on a run with a known canary secret).
Parallel slices: instance/secret loader; approvals + policy; audit chain; MCP rewire.

M3 (certified integrations): record/replay proxy, fixtures and contract tests for the first 20, risk policy entries, warm pool if latency measured > 1 s p50. Accept: 20 integrations pass the 4 CI criteria in section 3. Parallel by integration group (TI, EDR, IdP, email, SIEM, ticketing) with the replay proxy as a shared prerequisite built first.

M4 (ingest + responder + approver UI): webhook ingest with HMAC, one SIEM adapter, responder agent on the gateway, approver page behind oauth2-proxy, compose file. Accept: compose up on a clean VM; a signed webhook alert produces an IRReport with evidence from >= 2 instances; a destructive action appears in the approver page and executes only after approval; the whole flow is in the audit chain.
Parallel slices: ingest; responder loop; approver UI; compose + oauth2-proxy.

M5 (e2e eval + observability): opencode CLI harness in CI, OTel + Prometheus, docs rewrite to match reality. Accept: nightly e2e green 5 nights running with the retry policy; dashboard shows tool latency and approvals.

Later: Helm, native OIDC, Vault/AWS SM, pull-mode fetch-incidents, multi-worker, JS integrations via a node sandbox.

## Open questions for the owner

Big (sets a contract others depend on):
1. Secret backend for v1: file-based only, or must Vault/AWS SM ship in v1? Affects M2 interface and the compose file.
2. Which SIEM is the first ingest adapter, and which 20 integrations are actually licensed in the target SOC? Fixes M3 and M4 scope.
3. Docker socket in the dbot container vs remote docker/podman host: the deployment trust model. Affects the compose file and docs.
4. Is the chat UI in v1 at all, or is the approver page the only UI? Decides whether the React SPA survives.

Small (reversible):
5. SQLite vs Postgres default for approvals/queue (recommend SQLite default, Postgres optional).
6. oauth2-proxy vs native OIDC in v1 (recommend proxy).
7. Warm pool in M1 or deferred to M3 after measuring (recommend defer).
8. Keep pydantic-ai `to_web()` Vercel protocol for the chat UI or drop it (follows from 4).
9. Retry policy and required-vs-advisory status for the free-model e2e job (recommend advisory on PR, required nightly).

## Addendum: spike results (sandbox premise, 2026-11-08)

Ran 4 integrations that failed the host-venv probe inside their declared images (`docker run --rm -i --read-only --tmpfs /tmp --cap-drop ALL`, content and dbot mounted read-only, existing `dbot.runtime.runner`):

| Integration | Image | Host probe | In own image |
|---|---|---|---|
| AbuseDB | demisto/python3:3.12.12.7090913 | ran | ran, but runner printed nothing (see 2) |
| AzureADIdentityProtection | demisto/crypto:1.0.0.51288 (py3.10) | missing MicrosoftApiModule | imports ok; `NameError: Tuple` until assembled (see 1) |
| AWS-CloudTrail | demisto/boto3py3:1.0.0.3575453 | missing boto3 | imports ok, reaches main (`KeyError: defaultRegion`, param issue) |
| CiscoWebExFeed | demisto/btfl-soup:1.0.1.115405 | missing bs4 | imports ok, fails on mock `callingContext` |

Cold `docker run` per call: 0.8-1.1 s with images already pulled.

Corrections to the plan above:
1. ApiModules via `sys.path` (2.2 "simplest") is not enough. Integrations depend on names that XSOAR's script assembly puts in one shared namespace (e.g. `Tuple` from `typing`). Inlining the ApiModule *and* prepending `from typing import *` made AzureADIdentityProtection load cleanly. M1 must replicate XSOAR script assembly (CSP + CSUP + inlined ApiModules + integration, one namespace), not path tricks.
2. `runner.py` catches `Exception` but not `SystemExit`; `return_error()` calls `sys.exit`, so the container exits 0 with no JSON. Must be fixed in the M1 runner shim.
3. Sandbox images span Python 3.10 to 3.12, so the in-container shim must stay stdlib-only and 3.10-compatible (today it is: stdlib imports only).

Real-LLM harness: built ahead of M5 as PR #13 (opencode CLI + MCP, `OPENCODE_API_KEY` secret). The plan's M5 retry/advisory policy still applies once scenarios grow beyond one.
