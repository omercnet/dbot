# dbot

dbot is an XSOAR content runtime for SOCs. It runs integrations from
[demisto/content](https://github.com/demisto/content) without XSOAR. Each command runs in a throwaway container built
from the integration's own docker image, behind a gateway that checks roles, holds destructive commands for human
approval, and writes a hash-chained audit log.

You can reach the gateway in two ways:

- **MCP**: `dbot mcp` serves the integrations as MCP tools over stdio, for Claude or any other MCP client.
- **Responder**: signed webhook alerts go into a queue. A pydantic-ai agent enriches each one through the gateway and
  writes an IR report with a verdict, evidence, the actions it took, and the actions waiting for approval.

## Architecture

```
 SIEM / EDR --HMAC--> POST /ingest/{source} --> alert queue (SQLite)
                                                   |
                                                   v
                                     dbot worker (responder agent)
                                                   |
 MCP client --stdio--> dbot mcp ---+               |
 dbot invoke (CLI) ----------------+---> Gateway <-+
                                         | instance + command check, role check,
                                         | risk policy -> approval, secrets, audit
                                         v
                                 docker run (one container per command,
                                 integration's own image, read-only, no caps)
                                         |
                                         v
                                 squid egress proxy (allowlist) --> vendor APIs

 Browser --> oauth2-proxy (OIDC) --> dbot serve: /approvals, /alerts, /metrics
```

The gateway is the only code that starts a sandbox. More detail: [docs/architecture.md](docs/architecture.md).

## Quickstart (local)

You need Python 3.13, [uv](https://docs.astral.sh/uv/), and a local docker daemon.

```bash
git clone https://github.com/omercnet/dbot.git && cd dbot
git submodule update --init   # demisto/content
uv sync

cat > dbot.yaml <<'EOF'
instances:
  - name: abuseipdb
    integration: AbuseIPDB
    params:
      server: https://api.abuseipdb.com/api/v2/
      credentials: {password: env://ABUSEIPDB_API_KEY}
EOF
export DBOT_CONFIG=$PWD/dbot.yaml DBOT_DATA_DIR=$PWD/data ABUSEIPDB_API_KEY=...

uv run dbot config check                       # resolves every secret; prints OK or an error, never a value
uv run dbot invoke abuseipdb ip -a ip=8.8.8.8 -r "triage" --user alice   # one command through the gateway, as alice
uv run dbot audit verify data/audit            # OK, or the first broken seq
DBOT_MCP_USER=alice uv run dbot mcp           # MCP over stdio, acting for alice; point your MCP client at this command
```

The first `invoke` pulls the integration's image (for example `demisto/python3`). Without `DBOT_SANDBOX_NETWORK`,
dbot warns that sandboxes have unrestricted egress. That is acceptable on a laptop, but not in production.

An MCP client config entry looks like this:

```json
{"mcpServers": {"dbot": {"command": "uv", "args": ["run", "--directory", "/path/to/dbot", "dbot", "mcp"],
  "env": {"DBOT_CONFIG": "/path/to/dbot.yaml", "DBOT_DATA_DIR": "/path/to/data", "DBOT_MCP_USER": "alice",
          "ABUSEIPDB_API_KEY": "..."}}}}
```

`DBOT_MCP_USER` is required: it names the person the MCP server acts for. Their requests are filed as `agent:mcp` on
their behalf, so they cannot approve them.

The MCP tools are `search_tools`, `get_tool_schema`, `list_instances`, `invoke_tool`, and `get_approval`.

## Deployment (compose)

`deploy/compose.yml` runs `dbot serve`, `dbot worker`, oauth2-proxy and squid:

```bash
cd deploy
cp dbot.yaml.example dbot.yaml && cp oauth2-proxy.cfg.example oauth2-proxy.cfg
# create .env with the API keys, DBOT_INGEST_SECRET, DBOT_CSRF_SECRET, OAUTH2_PROXY_* and the LLM provider key
docker compose run --rm dbot egress write /dev/stdout > allowlist.txt
sudo install -d -o 10001 /var/cache/dbot
CONTENT_COMMIT=$(git rev-parse HEAD:content) docker compose up -d --build
```

See [docs/deployment.md](docs/deployment.md) for the networks, every `DBOT_*` variable, and the trust boundary.

## Security model

- **Approvals.** The risk policy fails closed: a command is `read` only if it matches a read glob (lookup verbs such
  as `-get`, `-list`, `-search`, plus the generic `ip`/`domain`/`url`/`file` reputation commands) and no
  destructive glob; everything else returns `approval_required`. An approver must approve the request on
  `/approvals`, and nobody can approve their own request or one made on their behalf. An approval is single use,
  expires after 1 hour, is bound to `sha256(instance, command, args)`, and only its requester can use it.
- **Audit chain.** Every invocation, denial, approval decision and accepted ingest is appended to
  `DBOT_DATA_DIR/audit/audit-YYYY-MM-DD.jsonl`. Each record carries `seq`, `prev_hash` and `hash`.
  `dbot audit verify` detects edited, dropped or reordered lines; ship the chain head off-host for tamper evidence.
- **Secrets.** Params use `env://NAME` or `file:///run/secrets/name`. There is no secret store and no HTTP API for
  credentials. Resolved values, and any credential param (type 4/9) even when written as a literal, are passed only
  to the sandbox and are redacted from results, logs and the audit log. Arguments marked `secret: true` are masked
  in the audit log, approvals and reports.
- **Docker socket.** `dbot` and `worker` mount `/var/run/docker.sock`. Whoever controls those containers controls
  the host. Run the stack on a dedicated VM, or point `DOCKER_HOST` at a remote rootless daemon.
- **Sandboxes.** Each command runs in its own container as `nobody`, with a read-only root, all capabilities
  dropped, `no-new-privileges`, and pid, memory and cpu limits. The container is killed on timeout.
- **Egress allowlist.** Sandboxes join an internal network whose only exit is squid. Squid allows only the
  `allow_hosts` of enabled instances and blocks private and link-local ranges.
- **Identity.** The web UI trusts `X-Forwarded-User`/`X-Forwarded-Groups` only from `DBOT_TRUSTED_PROXIES`
  (oauth2-proxy). OIDC groups map to the `viewer`, `analyst` and `approver` roles. `/ingest/*` is authenticated by
  HMAC instead.

## Certified integrations

The following integrations have contract tests against replayed vendor fixtures in CI (`tests/certified`), and
ready-made instances in `deploy/dbot.yaml.example`:

| Instance | Integration |
|---|---|
| abuseipdb | AbuseIPDB |
| virustotal | VirusTotal (API v3) |
| greynoise | GreyNoise |
| urlscan | urlscan.io |
| otx | AlienVault OTX v2 |

Any other python3 integration in the catalog runs too (1,032 non-deprecated ones at the current content commit),
but has not been certified. `dbot probe` reports which ones reach `main()` in their own image.

## Limits

- Only python3 integrations are supported. JavaScript, PowerShell and python2 integrations are not indexed.
- There is no pull-mode `fetch-incidents`. Alerts arrive by webhook.
- There is one worker and no chat UI. Analysts chat through MCP clients.

## Development

```bash
uv sync
uv run pytest                      # unit tests; docker-marked tests skip without a daemon
uv run pytest -m docker            # only the tests that need a docker daemon
uv run pytest -m llm               # real-LLM eval on a local ollama model (see below)
uv run ruff check . && uv run ruff format --check .
uv run mypy dbot
uv run dbot probe --limit 20       # run a nonexistent command in each integration's image
```

The real-LLM eval drives the MCP server (S1 discovery, S2 enrichment, S3 approval enforcement) and the alert
responder (S4) with a pydantic-ai agent on a local ollama model, through the real gateway and sandboxes against a
replay proxy. It needs docker and ollama with the model pulled, and skips only if ollama is unreachable or the model
is missing. CI runs it on every PR and push.

```bash
docker run -d -p 127.0.0.1:11434:11434 -e OLLAMA_CONTEXT_LENGTH=8192 -v ollama:/root/.ollama ollama/ollama:0.40.15
docker exec <container> ollama pull granite4:3b
uv run pytest -m llm
```

`DBOT_EVAL_MODEL` overrides the model (default `ollama:granite4:3b`, any pydantic-ai model string) and
`OLLAMA_BASE_URL` the server (default `http://127.0.0.1:11434/v1`). ollama's OpenAI-compatible API ignores `num_ctx`,
so set the context size on the server with `OLLAMA_CONTEXT_LENGTH`.

Docs: [architecture](docs/architecture.md), [configuration](docs/configuration.md),
[deployment](docs/deployment.md), [operations](docs/operations.md).

## License

MIT. Integration source comes from [demisto/content](https://github.com/demisto/content) (MIT).
