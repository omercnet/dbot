# Configuration

Operators edit one YAML file. dbot reads it from `DBOT_CONFIG` (default `/etc/dbot/dbot.yaml`) at startup.
Restart `dbot serve`, `dbot worker` and `dbot mcp` after you change it. Unknown keys are errors.
`dbot config check [PATH]` validates the file and resolves every secret. It prints `OK` or the problem for each
instance and ingest source, never a value, and exits 1 on any failure.

`deploy/dbot.yaml.example` is a complete example with the five certified threat-intel instances.

## Full format

```yaml
instances:
  - name: abuseipdb                    # unique; what callers pass as `instance`
    integration: AbuseIPDB             # catalog integration name (the YAML `name:` in content)
    params:                            # integration params, as in the XSOAR instance settings
      server: https://api.abuseipdb.com/api/v2/
      credentials: {password: env://ABUSEIPDB_API_KEY}
    allow_hosts: [api.abuseipdb.com]   # egress allowlist; default: hostnames of url-like params
    enabled: true                      # default true

policy:
  destructive: ["*block*", "*isolate*", "*quarantine*", "*disable*", "*delete*", "*revoke*", "*suspend*",
                "*unblock*", "*release*", "*-create-*", "*-update-*"]   # the default list
  read: ["test-module"]                # default; explicit read overrides win over destructive globs

roles:                                 # dbot role -> OIDC groups
  viewer: [soc-viewers]
  analyst: [soc-analysts]
  approver: [soc-leads]

ingest:
  sources:                             # POST /ingest/<source name>
    generic: {hmac_secret: env://DBOT_INGEST_SECRET}

agent:
  model: "openai:gpt-5.4-mini"         # any pydantic-ai model string (default shown)
  max_tool_calls: 20                   # default 20
  wall_clock_s: 300                    # default 300
```

Every section is optional. An empty file is valid, but has no instances.

## Instances

- `integration` must match a catalog integration. To find the name, use `search_tools` over MCP, or look at the
  `name:` field of the integration's YAML under `content/Packs/<Pack>/Integrations/`. Only non-deprecated python3
  integrations are in the catalog.
- `params` are passed to the integration as `demisto.params()`. Params you leave out take their defaults from the
  integration YAML. Credential params of type 9 are objects: `{identifier: ..., password: ...}`.
- `allow_hosts` feeds the squid allowlist (`dbot egress write`). If you omit it, dbot uses the hostname of every
  `http(s)://`, `ws(s)://`, `ldap(s)://` (and similar) value in `params`. Integrations with built-in URLs (for example
  VirusTotal) need `allow_hosts` set explicitly. A leading `.` also matches subdomains.
- When `DBOT_EGRESS_PROXY` is set, dbot sets `params.proxy = true` on every call, so you do not need to set it.

## Secrets

A param string is a secret reference when it starts with one of these schemes:

| Value | Resolves to |
|---|---|
| `env://NAME` | the environment variable `NAME` (unset or empty is an error) |
| `file:///run/secrets/name` | the file contents, minus trailing newlines (absolute path required) |
| anything else | the literal string |

dbot rejects backend-looking schemes such as `vault://`, `aws-sm://` and `op://` instead of passing them through as
literals. Resolved values exist only in memory and in the sandbox's stdin. They are redacted from command results,
logs and the audit log. Error messages name the reference, never the value.

## Policy

`policy.risk(command)` decides how a command is gated:

1. If it matches `read`, it is `read`.
2. Otherwise, if it matches `destructive`, it is `destructive`.
3. Otherwise, if the integration YAML marks it `dangerous: true`, it is `destructive`.
4. Otherwise, it is `read`.

Globs use `fnmatch` and match the command name (without the integration prefix) case-insensitively. Destructive
commands always need a single-use approval. The example config adds `*-report-*`, `*-submit*`, `*-add` and `*scan`,
because the threat-intel integrations write to the vendor with those commands. It also marks
`abuseipdb-check-cidr-block` as read, because `*block*` would otherwise catch it.

## Roles

The web app maps the `X-Forwarded-Groups` header from oauth2-proxy to roles through `roles`. A user in none of the
mapped groups gets 403.

| Role | Web | Gateway |
|---|---|---|
| `viewer` | `/alerts`, `/approvals` (read only), `/metrics` | nothing |
| `analyst` | as viewer | run `read` commands, request `destructive` |
| `approver` | as viewer, plus approve/deny on `/approvals` | as analyst |

Nobody can approve a request they made or that was made on their behalf. MCP runs as `agent:mcp` with the roles in
`DBOT_MCP_ROLES` (default `analyst`). The responder runs as `agent:responder` with the `analyst` role.
`dbot invoke` runs as `$USER` with the `analyst` role.

## Ingest

`POST /ingest/{source}` is not behind oauth2-proxy (`skip_auth_routes`). The HMAC signature authenticates it:

- header `X-Dbot-Signature: sha256=<hex>`, where `<hex>` is the HMAC-SHA256 of the raw request body, keyed with the
  source's `hmac_secret`;
- body: JSON, at most 1 MiB, with this schema:

```json
{"id": "alert-1", "source": "optional, informational", "severity": "high", "title": "Suspicious login",
 "observables": [{"type": "ip", "value": "203.0.113.7"}], "raw": {"any": "original payload"}}
```

`type` is one of `ip`, `domain`, `url`, `hash`, `email`, `user` or `host`, with at most 100 observables. Only `id`
is required. The responses are:

| Response | Meaning |
|---|---|
| `202 {"alert_id": "..."}` | accepted and queued |
| `200 {"duplicate": true}` | same `(source, id)` already received |
| `401` | bad signature |
| `404` | unknown source |
| `413` | body too large |
| `422` | invalid alert |
| `503` | the secret could not be resolved |

Signing with curl and openssl:

```bash
BODY='{"id":"alert-1","severity":"high","title":"Suspicious login","observables":[{"type":"ip","value":"203.0.113.7"}]}'
SIG=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "$DBOT_INGEST_SECRET" -r | cut -d' ' -f1)
curl -sS -X POST https://dbot.example.com/ingest/generic \
  -H 'Content-Type: application/json' \
  -H "X-Dbot-Signature: sha256=$SIG" \
  --data-binary "$BODY"
```

Use `--data-binary` (not `-d`) so curl sends exactly the bytes you signed.

## Agent

`agent.model` is any [pydantic-ai](https://ai.pydantic.dev/models/) model string. Set the provider's key in the
worker's environment (for example `OPENAI_API_KEY` or `ANTHROPIC_API_KEY`). Every tool call (`search_tools`,
`get_tool_schema`, `invoke`) counts against `max_tool_calls`. When the budget or `wall_clock_s` runs out, the
responder stops calling tools and still writes a report.
