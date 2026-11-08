# Deployment

The supported deployment is `deploy/compose.yml` on a dedicated Linux host or VM with docker.

## Services

| Service | Command | Networks | Role |
|---|---|---|---|
| `oauth2-proxy` | OIDC login | `front` (172.30.0.2), `egress` | the only published port (4180); forwards to `dbot:8080` with `X-Forwarded-User/Groups` |
| `dbot` | `dbot serve` | `front` (172.30.0.3), `egress` | ingest, approvals, alerts, metrics; no published port |
| `worker` | `dbot worker` | `egress` | runs the responder on queued alerts |
| `squid` | egress proxy | `sandbox`, `egress` | the only way out of the sandbox network |

Integration containers are started by `dbot` and `worker` through the host docker daemon. They join `dbot-sandbox`,
an `internal: true` network with no route out, so squid (`DBOT_EGRESS_PROXY=http://squid:3128`) is their only exit.

## Steps

```bash
git submodule update --init
cd deploy
cp dbot.yaml.example dbot.yaml            # edit instances, roles, ingest sources
cp oauth2-proxy.cfg.example oauth2-proxy.cfg   # set oidc_issuer_url, redirect_url
cat > .env <<'EOF'
ABUSEIPDB_API_KEY=...
VIRUSTOTAL_API_KEY=...
GREYNOISE_API_KEY=...
URLSCAN_API_KEY=...
OTX_API_KEY=...
DBOT_INGEST_SECRET=...
DBOT_CSRF_SECRET=...
OPENAI_API_KEY=...
OAUTH2_PROXY_CLIENT_ID=...
OAUTH2_PROXY_CLIENT_SECRET=...
OAUTH2_PROXY_COOKIE_SECRET=...
EOF
chmod 600 .env
docker compose run --rm dbot egress write /dev/stdout > allowlist.txt   # squid allowlist from dbot.yaml
sudo install -d -o 10001 /var/cache/dbot
CONTENT_COMMIT=$(git rev-parse HEAD:content) docker compose up -d --build
docker compose run --rm dbot config check
```

`dbot.yaml`, `oauth2-proxy.cfg` and `.env` are gitignored. Instead of putting secrets in `.env`, you can mount files
and reference them as `file:///run/secrets/<name>` in `dbot.yaml`.

`/var/cache/dbot` holds the assembled scripts. Sandboxes bind-mount it through the host daemon, so the path must
be the same on the host and in the container, and the image's `dbot` user (uid 10001) must own it. The build arg
`DOCKER_GID` (default 999) must match the host's docker group, so that the non-root user can use the socket:

```bash
DOCKER_GID=$(getent group docker | cut -d: -f3) CONTENT_COMMIT=$(git rev-parse HEAD:content) docker compose up -d --build
```

After you change `instances` or `allow_hosts`, regenerate `allowlist.txt`, run `docker compose kill -s HUP squid`,
and restart `dbot` and `worker`.

## oauth2-proxy

- Use the generic OIDC provider, with `oidc_groups_claim = "groups"` and `pass_user_headers = true`.
- `skip_auth_routes = ["POST=^/ingest/"]` lets webhooks through. They authenticate with HMAC instead.
- dbot trusts `X-Forwarded-*` only from `DBOT_TRUSTED_PROXIES` (`172.30.0.2` in compose, oauth2-proxy's fixed
  address). Requests from anywhere else get 401. Uvicorn runs with `proxy_headers=False`, so the peer address is the
  real TCP peer.
- Map your IdP groups to `viewer`, `analyst` and `approver` under `roles:` in `dbot.yaml`.
- Terminate TLS in front of oauth2-proxy (a load balancer or a reverse proxy) and keep `cookie_secure = true`.

## squid

`deploy/squid.conf` denies by default. It allows CONNECT only to port 443, allows only the hosts in `allowlist.txt`,
and denies private, loopback and link-local destinations (which includes cloud metadata endpoints). It does not
cache, and it strips `X-Forwarded-For`. The access log goes to stdout (`docker compose logs squid`).

## Trust boundary

`dbot` and `worker` mount `/var/run/docker.sock`. Anything that controls those containers controls the docker
daemon, which means root on the host. Run the stack on a dedicated VM, or set `DOCKER_HOST` to a remote
rootless or sysbox daemon and drop the socket mount. Integration containers never get the socket.

## Environment variables

All variables that dbot reads (from `grep os.environ dbot/`):

| Variable | Default | Used by | Meaning |
|---|---|---|---|
| `DBOT_CONFIG` | `/etc/dbot/dbot.yaml` | all | the config file |
| `DBOT_DATA_DIR` | `/var/lib/dbot` | all | `dbot.db` (approvals, alerts), `audit/`, `cache/` (catalog index) |
| `DBOT_CACHE_DIR` | `~/.cache/dbot/assembled` | sandbox | assembled scripts; must be the same path on host and container (`/var/cache/dbot/assembled` in the image) |
| `DBOT_CONTENT_ROOT` | `<repo>/content` | gateway, probe | the demisto/content checkout (`/app/content` in the image) |
| `DBOT_CONTENT_COMMIT` | from `git` in the content root | catalog, assembly | cache key when the content checkout has no `.git` (set from the `CONTENT_COMMIT` build arg) |
| `DBOT_SANDBOX_NETWORK` | none (warns: unrestricted egress) | sandbox | docker network for integration containers (`dbot-sandbox`) |
| `DBOT_EGRESS_PROXY` | none | gateway | `HTTPS_PROXY`/`HTTP_PROXY` for sandboxes; also forces `params.proxy = true` |
| `DBOT_TRUSTED_PROXIES` | `127.0.0.1` | web | comma-separated IPs/CIDRs allowed to assert `X-Forwarded-User/Groups` |
| `DBOT_CSRF_SECRET` | random per process | web | CSRF token key; set it, or approval forms break on restart |
| `DBOT_MCP_ROLES` | `analyst` | mcp | comma-separated roles of the `agent:mcp` actor |
| `DBOT_HOST`, `DBOT_PORT` | `0.0.0.0`, `8080` | web | bind address for `dbot serve`; `--host`/`--port` override |
| `DBOT_LOG_LEVEL` | `INFO` | worker | Python log level for `dbot worker` |

The following are not `DBOT_*` variables, but are also read:

- secret references, `env://NAME`, read `NAME` from the environment of the process that runs the command;
- `USER` is the actor id for `dbot invoke`;
- the pydantic-ai provider key (for example `OPENAI_API_KEY`) is read by the worker;
- `DOCKER_HOST` is honoured by the docker CLI that dbot shells out to.
