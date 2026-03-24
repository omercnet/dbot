# Hosting Platform Comparison: Starlette/Uvicorn + SQLite + SSE

**App profile**: Python Starlette/Uvicorn, React SPA (StaticFiles), PydanticAI streaming SSE,
SQLite config store, ~60s cold start (YAML indexing), single-user/small-team.

**Key constraints**:
- Persistent SQLite (not ephemeral FS)
- SSE streaming (long-lived HTTP connections, no 30s timeout)
- 60s cold start — auto-sleep is painful unless warm-up is acceptable
- Hatch-managed Python project (`hatch run serve`)

---

## ✅ Platforms Compared

### 1. Fly.io ⭐ (Best overall for solo devs)

| | |
|---|---|
| **Monthly cost** | ~$4–6/mo (shared-cpu-1x 512–1GB + 3GB volume) |
| **SQLite** | ✅ Fly Volumes — NVMe local disk, $0.15/GB/mo. Mount at `/data`, SQLite lives there. Charged even when machine is stopped. |
| **LiteFS** | ✅ Available (read replica sync across machines), overkill for single-user. |
| **SSE** | ✅ Full support. No timeout imposed by Fly Proxy on long-lived connections. |
| **Cold start** | ⚠️ `auto_stop_machines = "suspend"` resumes in ~1–2s (memory preserved). `"stop"` = full restart = triggers 60s indexing. Keep `min_machines_running = 1` to avoid cold starts entirely (~$4/mo always-on). |
| **Auto-stop** | ✅ Built-in. Use `"suspend"` not `"stop"` for near-instant resume. |
| **Deploy** | `fly launch` + `fly deploy`. Add `Dockerfile` or buildpack. |

**fly.toml key config**:
```toml
[mounts]
source = "data"
destination = "/data"

[[services]]
internal_port = 8000
auto_stop_machines = "suspend"   # NOT "stop" — avoids 60s cold start
auto_start_machines = true
min_machines_running = 1         # keep 1 warm if 24/7 availability needed
```

**Dockerfile** (hatch):
```dockerfile
FROM python:3.12-slim
RUN pip install hatch
WORKDIR /app
COPY . .
RUN hatch env create
EXPOSE 8000
CMD ["hatch", "run", "serve"]
```

**Verdict**: Best bang for buck. $4–6/mo, persistent volumes, smart suspend, SSE works.

---

### 2. Railway

| | |
|---|---|
| **Monthly cost** | ~$9–12/mo (Hobby: $1 sub + ~$8–11 usage at 512MB/0.5vCPU always-on) |
| **SQLite** | ✅ Volumes available on Hobby+. Up to 5GB. Mount path configurable. Volume pricing: ~$0/mo for small sizes (included in resource billing). |
| **SSE** | ✅ No forced timeouts reported; standard HTTP streaming works. |
| **Cold start** | ✅ **No sleep on Hobby** — services run continuously. Zero cold-start issue. |
| **Idle sleep** | ❌ Not available (by design — Railway doesn't sleep paid services). Pay-per-second means you always pay for uptime. |
| **Deploy** | Git push → auto-deploy. `railway.toml` or Dockerfile. nixpacks auto-detects Python. |

**railway.toml**:
```toml
[deploy]
startCommand = "hatch run serve"

[deploy.volumeMounts]
"/data" = "sqlite-vol"
```

**Verdict**: Simple DX, always-on (no cold start worry), but costs ~2x Fly.io for same resources.

---

### 3. Render

| | |
|---|---|
| **Monthly cost** | ~$7.75/mo (Starter $7 + 3GB disk $0.75) or $25.75/mo (Standard) |
| **SQLite** | ✅ Persistent disks on **paid tiers only** ($0.25/GB/mo, SSD, daily snapshots). Cannot decrease size once set. |
| **SSE** | ✅ Supported. No artificial streaming timeout documented. |
| **Cold start** | ❌ **Free tier spins down after 15min idle → ~60s spin-up** — catastrophic for this app. Starter ($7/mo) **does NOT sleep** — always running. |
| **Free tier** | ❌ Unusable: spins down, no persistent disk, SQLite lost on every restart. |
| **Deploy** | Git push → auto-deploy. `render.yaml` or Dockerfile. |

**render.yaml**:
```yaml
services:
  - type: web
    name: dbot
    runtime: python
    plan: starter       # minimum paid tier for persistent disk
    buildCommand: pip install hatch && hatch env create
    startCommand: hatch run serve
    disk:
      name: data
      mountPath: /data
      sizeGB: 3
```

**Verdict**: Clean UX, predictable pricing. Slightly more expensive than Fly.io ($7.75 vs ~$5). Free tier is a hard no.

---

### 4. Hetzner / DigitalOcean VPS + systemd + nginx

| | |
|---|---|
| **Monthly cost** | Hetzner CX22: €4.51/mo (2vCPU, 4GB RAM, 40GB SSD). DO Basic: $6/mo (1vCPU, 1GB). |
| **SQLite** | ✅ Native filesystem. SQLite at `/opt/dbot/data/config.db`. No abstraction layer. |
| **SSE** | ✅ nginx proxy_pass with `proxy_buffering off; proxy_read_timeout 600s;` |
| **Cold start** | ✅ systemd auto-restarts, process stays up permanently. |
| **Deploy** | Manual or CI/CD (rsync + `systemctl restart dbot`). No git-push magic. |
| **TLS** | certbot + Let's Encrypt, auto-renew via systemd timer. |

**systemd unit** (`/etc/systemd/system/dbot.service`):
```ini
[Unit]
Description=dbot
After=network.target

[Service]
User=dbot
WorkingDirectory=/opt/dbot
ExecStart=/opt/dbot/.venv/bin/uvicorn dbot.server:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5
Environment=DBOT_DATA_DIR=/opt/dbot/data

[Install]
WantedBy=multi-user.target
```

**nginx snippet**:
```nginx
location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_buffering off;              # critical for SSE
    proxy_read_timeout 600s;          # allow long SSE streams
    proxy_set_header Connection '';
    proxy_http_version 1.1;
}
```

**Verdict**: Cheapest + most control. Hetzner is exceptional value. Trade-off: manual ops, no auto-scaling.

---

### 5. Docker Self-Hosted (Compose on VPS)

Same VPS as above, but containerized:

`compose.yml` written to `/home/omer/dev/dbot/docs/compose.yml`

| | |
|---|---|
| **Monthly cost** | Same as VPS above |
| **SQLite** | ✅ Named volume or bind mount (`./data:/data`) |
| **SSE** | ✅ via nginx container or Traefik |
| **Cold start** | ✅ `restart: unless-stopped` |
| **Deploy** | `docker compose pull && docker compose up -d` via CI/CD or Watchtower |

---

### 6. Coolify / Dokku (Self-Hosted PaaS on VPS)

| | |
|---|---|
| **Monthly cost** | VPS cost only (Hetzner CX22 €4.51/mo). Coolify/Dokku free. |
| **SQLite** | ✅ Persistent volumes managed by platform. Dokku: `dokku storage:mount app /var/lib/dokku/data/storage/dbot:/data`. |
| **SSE** | ✅ nginx-based proxy; configure read timeout in Procfile or app config. |
| **Cold start** | ✅ Always running, no sleep. |
| **Deploy** | `git push dokku main` (Dokku) or git webhook (Coolify). |
| **Python/hatch** | Dokku buildpack auto-detects Python. Add `Procfile`: `web: hatch run serve`. Coolify: Dockerfile-based. |

**Coolify advantage**: Docker-native, GUI, built-in TLS via Caddy, env secrets UI.
**Dokku advantage**: Heroku-like simplicity, battle-tested, minimal overhead.

---

### 7. ❌ Vercel / Netlify — SKIP

| Issue | Detail |
|---|---|
| No persistent filesystem | Serverless/edge — ephemeral. SQLite impossible. |
| No SSE on standard plans | Vercel Functions: 60s max execution. Edge: 30s. SSE streams killed. |
| Python support | Limited (Vercel supports Python Functions but serverless only). |
| **Verdict** | **Incompatible** with this app profile. Skip entirely. |

---

## Summary Comparison Table

| Platform | Monthly Cost | SQLite | SSE | Cold Start | Deploy DX |
|---|---|---|---|---|---|
| **Fly.io** | ~$5/mo | ✅ NVMe Volume | ✅ | ⚠️ Use `suspend` | ⭐⭐⭐⭐⭐ |
| **Railway** | ~$10/mo | ✅ Volume | ✅ | ✅ (no sleep) | ⭐⭐⭐⭐⭐ |
| **Render** | ~$8/mo | ✅ Paid disk | ✅ | ✅ (Starter+) | ⭐⭐⭐⭐ |
| **Hetzner VPS** | ~$5/mo | ✅ Native FS | ✅ + nginx config | ✅ | ⭐⭐⭐ |
| **Docker/VPS** | ~$5/mo | ✅ Volume mount | ✅ | ✅ | ⭐⭐⭐ |
| **Coolify/Dokku** | ~$5/mo | ✅ PaaS volumes | ✅ | ✅ | ⭐⭐⭐⭐ |
| **Vercel/Netlify** | — | ❌ | ❌ | — | ❌ SKIP |

## Recommendation for This App

**Solo dev, low-traffic**: **Fly.io** (`suspend` + 1 min machine). ~$5/mo, zero config SQLite,
SSE works, git-push deploys, suspend means near-zero cold starts.

**Zero ops tolerance**: **Railway**. Pay ~$10/mo for "it just works always-on" simplicity.

**Maximum control + value**: **Hetzner CX22** (€4.51) + Coolify. Git-push deploys, GUI secrets,
2 vCPU/4GB for the same price as a 512MB cloud container.

**Avoid**: Render Free, Vercel, Netlify.
