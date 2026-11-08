# Operations

## Approvals

1. A caller invokes a destructive command, as decided by `policy` (see [configuration](configuration.md#policy)).
   The gateway stores a pending approval and returns:

   ```json
   {"status": "approval_required", "approval_id": "0083...", "risk": "destructive",
    "message": "'abuseipdb-report-ip' on 'abuseipdb' is destructive and needs approval 0083.... ..."}
   ```

2. An approver opens `/approvals` (through oauth2-proxy) and clicks Approve or Deny. The page shows the instance,
   command, args (with `secret: true` args masked), reason, requester, the alert or person it is for, and the
   expiry. Nobody can decide a request they made or that was made on their behalf (an MCP request is made on behalf
   of `DBOT_MCP_USER`). Every form carries a CSRF token.
3. The requester repeats the same call with `approval_id`. For example, from the CLI:

   ```bash
   dbot invoke abuseipdb abuseipdb-report-ip -a ip=203.0.113.7 -a categories=18 -r "confirmed abuse" \
     --user alice --approval-id 0083...
   ```

   Over MCP, the caller passes `approval_id` to `invoke_tool`, and can poll `get_approval(approval_id)` first. For the
   responder, approving or denying requeues the alert, and the next run executes the approved action once.
4. An approval runs only an identical call (same instance, command and args) by the same requester (actor id and
   on-behalf-of), and only once. Anyone else gets `denied: approval ... was requested by someone else`. A second use
   returns `denied: approval ... is consumed, not approved`. Approvals expire after 1 hour. Their statuses are
   `pending`, `approved`, `denied`, `consumed` and `expired`.

## Audit log

Records are appended to `$DBOT_DATA_DIR/audit/audit-YYYY-MM-DD.jsonl`, one file per UTC day. The record types are:

- `invoke`: instance, command, args (known secret values and `secret: true` args redacted), reason, risk, status,
  `error_kind`, duration, actor, roles and `on_behalf_of`;
- `approval_decision`;
- `ingest`: `accepted`, `duplicate`, or `rejected` for a bad signature.

Each record has `ts`, `seq`, `prev_hash` and `hash = sha256(prev_hash + canonical_json(record_without_hash))`, and
the chain continues across days. Known secret values are redacted before hashing.

```bash
dbot audit verify /var/lib/dbot/audit                 # whole directory, in order
dbot audit verify /var/lib/dbot/audit/audit-2026-11-08.jsonl
```

The command prints `OK` and exits 0, or prints `FAIL: chain broken at seq N` and exits 1. Any edited, removed or
reordered line breaks the chain. In compose, run `docker compose exec dbot dbot audit verify /var/lib/dbot/audit`.
The chain has no key, so someone who can write the data volume can rewrite it from the edited line on, or truncate
it, and `verify` still passes. For tamper evidence, ship the files, or at least each day's last `hash`, to an
external append-only sink (SIEM, syslog, WORM storage); see [deployment](deployment.md#security-notes).

## Alerts

`/alerts` lists the queue (newest first) with status: `queued`, `running`, `waiting_approval`, `done` or `failed`.
`/alerts/{id}` shows the alert, its observables, and the IR report (verdict, confidence, summary, evidence, actions
taken, pending approvals, errors). The viewer role is required. A `failed` alert keeps its report, including any
approved actions that already ran.

## Metrics and health

- `GET /healthz` returns `ok` with no authentication. Use it for container and load-balancer checks.
- `GET /metrics` returns Prometheus text format and needs the viewer role, so scrape it through oauth2-proxy or with
  trusted headers. It exposes:
  - `dbot_alerts{status=...}` (gauge): alerts by status;
  - `dbot_approvals_pending` (gauge): unexpired pending approvals;
  - `dbot_ingest_total{result="accepted|rejected|duplicate"}` (counter since process start).

## Probe

`dbot probe` runs a nonexistent command in every catalog integration, each in its own image, and counts how many
reach `main()`:

```bash
dbot probe --limit 20 --concurrency 4
dbot probe --json probe.json            # every python3 integration; pulls many images, takes hours
```

Each integration ends up in one of `reached_main`, `import_error`, `timeout` or `sandbox_error`. The command
prints the most common error messages for each bucket. CI runs the full probe nightly and uploads `probe.json`.

## Troubleshooting

- `credentials for instance 'x' could not be resolved`: run `dbot config check`. It names the missing env var or
  file.
- `sandboxes have unrestricted egress; set DBOT_SANDBOX_NETWORK`: you are running without the sandbox network.
  This is fine locally, but must not happen in compose.
- `error_kind: sandbox` with `docker pull ... failed`: the image could not be pulled (registry rate limit or
  network). The pull is retried on the next call.
- `error_kind: integration` with HTTP 403 or a connection error through squid: the host is not in `allowlist.txt`.
  Add it to the instance's `allow_hosts`, regenerate the list, and HUP squid.
- Approval forms fail after a restart: set `DBOT_CSRF_SECRET`.
