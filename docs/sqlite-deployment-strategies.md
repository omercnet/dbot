# SQLite Production Deployment Strategies
*For: Starlette + PydanticAI app with config-store / credential-vault (low writes, mostly reads)*

---

## TL;DR Decision Matrix

| Strategy | HA? | Complexity | Cost | Best For |
|---|---|---|---|---|
| **Single VPS + WAL** | ❌ | Low | $5–25/mo | MVP, solo app, <100 users |
| **Litestream + S3** | DR only | Low | $5–25 + cents | Single node + backup |
| **LiteFS on Fly.io** | ✅ | Medium | $10–40/mo | Multi-region read scale |
| **Turso** | ✅ | Low | $0–25/mo | Edge reads, multi-tenant |
| **Cloudflare D1** | ✅ | Medium | usage-based | Worker-native apps only |
| **PostgreSQL** | ✅ | Medium-High | $25–100/mo | >1K concurrent writers |

---

## 1. Plain SQLite on Single VPS (Railway / Render / Fly single machine)

**How it works**: One process, one file, standard SQLite. Enable WAL mode.

```python
# pragma on connect — do this ONCE per connection
conn.execute("PRAGMA journal_mode=WAL")
conn.execute("PRAGMA synchronous=NORMAL")   # safe + fast
conn.execute("PRAGMA busy_timeout=5000")    # retry on lock
conn.execute("PRAGMA cache_size=-64000")    # 64 MB cache
```

**WAL + Connection Pooling for Starlette**:
```python
# databases library or aiosqlite with a single write-serializing lock
# WAL allows N concurrent readers + 1 writer simultaneously
# Use a thread-pool executor for sync SQLite in async Starlette

from sqlalchemy.ext.asyncio import create_async_engine
engine = create_async_engine(
    "sqlite+aiosqlite:///./config.db",
    connect_args={"check_same_thread": False},
    pool_size=5,       # readers
    max_overflow=10,
)
# Writes: serialize via asyncio.Lock() or DB-level WAL write lock
```

**Capacity**: Handles thousands of reads/sec. WAL allows concurrent reads with zero lock contention. Single writer — fine for config store.

**When enough**: Single-region app, <1K concurrent users, config/settings store (not OLTP). Railway/Render/Fly all support persistent volumes.

**Caveat**: Zero HA. If machine dies, you're down until restart. Use with Litestream for backup.

---

## 2. Litestream — Streaming Replication to S3/GCS

**How it works**: Sidecar process shadows the WAL. It intercepts checkpointing, maintains a "shadow WAL" sequence (`00000000.wal`, `00000001.wal`, …), and continuously uploads frames to S3. Restoring replays snapshot + all WAL frames.

```yaml
# litestream.yml
dbs:
  - path: /data/config.db
    replicas:
      - type: s3
        bucket: my-app-db-backup
        path: config
        region: us-east-1
        sync-interval: 1s          # default, near-real-time
        snapshot-interval: 24h
        retention: 72h             # keep 3 days of point-in-time
```

**RPO**: ~1 second (async WAL streaming). **RTO**: Minutes (replay snapshot + WAL).

**Restore**:
```bash
litestream restore -o /data/config.db s3://my-app-db-backup/config
```

**Cost**: S3 storage ~$0.02/GB/mo + minimal PUT costs. Litestream itself is free/OSS.

**Fit**: Perfect pairing with single-VPS. Adds disaster recovery without complexity. Does NOT add HA or read replicas.

**Limitation**: One writer, one node. Not multi-region. Litestream v0.5.x is actively maintained.

---

## 3. LiteFS (Fly.io) — Distributed SQLite FUSE Replication

**How it works**: FUSE filesystem that intercepts every SQLite page write into LTX transaction files. Uses Consul for primary lease. Replicas stream LTX files from primary in real-time.

**Write forwarding (built-in HTTP proxy)**:
```yaml
# litefs.yml
proxy:
  addr: ":8080"
  target: "localhost:8081"   # your Starlette app
  db: "config.db"
```
- `POST/PUT/PATCH` on replicas → `fly-replay` header → auto-forwarded to primary
- `GET` on replicas → waits for replication position cookie to catch up, then serves locally
- Requires cookies enabled; does NOT work with WebSockets

**Replication model**: Asynchronous (no write confirmation from replicas before commit). Split-brain protection via rolling checksum — out-of-sync node auto-snapshots from new primary.

**⚠️ Critical warning from Fly docs**: "Do NOT combine LiteFS with autostop/autostart on Fly Machines." The proxy's autoscaler can shut down machines without lease awareness, risking data rollback. Keep machines always-on.

**Capacity**: Each replica handles read traffic independently. Primary handles all writes (single-writer SQLite constraint). Good for config store (low write volume).

**Cost**: Fly.io VMs ~$5–10/machine/mo. 2 machines + Consul ≈ $15–25/mo.

**Complexity**: Medium. Requires `litefs.yml`, Consul setup, FUSE filesystem in container.

**Status**: Pre-1.0, no official support. Fly discourages use for new projects.

---

## 4. Turso (libSQL) — Managed SQLite with HTTP Edge

**How it works**: Fork of SQLite (libSQL) hosted as managed service. HTTP/WebSocket API. Embedded replicas sync a local SQLite file from the remote primary — reads are local, writes go to Turso's edge.

```python
# Python via HTTP API (no native Python client yet — use httpx)
import httpx

turso_url = "https://[db-name]-[org].turso.io"
turso_token = "eyJ..."

async def query(sql: str, params: list = []):
    async with httpx.AsyncClient() as c:
        r = await c.post(f"{turso_url}/v2/pipeline", 
            headers={"Authorization": f"Bearer {turso_token}"},
            json={"requests": [{"type": "execute", "stmt": {"sql": sql, "args": params}}]})
        return r.json()
```

**Embedded replicas** (best for your use case): Local SQLite file synced from Turso — zero-latency reads, writes forwarded to primary.

**Pricing** (2026):
| Tier | Price | Databases | Storage | Rows Read/mo |
|---|---|---|---|---|
| Free | $0 | 100 | 5 GB | 500M |
| Developer | $4.99/mo | Unlimited | 9 GB + $0.75/GB | 2.5B |
| Scaler | $24.92/mo | Unlimited | 24 GB + $0.50/GB | 100B |

**Credential-vault fit**: ✅ Good. Turso supports at-rest encryption (Pro tier). Low write volume fits free/developer tier easily.

**Limitation**: libSQL is a fork — some SQLite extensions/pragmas may differ. Concurrent writes coming soon (beta as of 2026). No native Python SDK yet (use HTTP API or via Rust bindings).

---

## 5. Cloudflare D1 — Edge SQLite (Workers-native)

**How it works**: SQLite backed by Durable Objects, globally replicated, accessible only from Cloudflare Workers. HTTP API available but Workers binding is primary path.

**Hard limits**:
| Limit | Value |
|---|---|
| Max DB size | 10 GB (hard cap, cannot increase) |
| Max columns/table | 100 |
| Max row/BLOB size | 2 MB |
| Max query duration | 30 seconds |
| Concurrency | Single-threaded per DB instance |
| `LIKE`/`GLOB` pattern | 50 bytes max |

**Concurrency**: Each DB is single-threaded (one Durable Object). At 1ms avg query → ~1000 QPS. Queue overflow → `overloaded` error. Read replication available (each replica = separate DO).

**Point-in-time recovery**: 30 days (paid), 7 days (free).

**Fit for your app**: ❌ Poor. D1 is designed for Workers-native apps. Your Starlette app runs outside Workers — you'd use the HTTP API which adds latency and complexity. Also: 10 GB hard cap, no custom SQLite pragmas, single-threaded bottleneck.

**Only consider if**: You're already fully on Cloudflare Workers/Pages infrastructure.

---

## 6. WAL Mode + Connection Pooling (Deep Dive)

**WAL enables concurrent reads + 1 writer simultaneously**. Critical settings for production:

```python
# Optimal pragmas for a config store
PRAGMAS = [
    "PRAGMA journal_mode=WAL",
    "PRAGMA synchronous=NORMAL",    # safe with WAL (crash-safe via WAL header)
    "PRAGMA wal_autocheckpoint=1000",  # checkpoint after 1000 pages
    "PRAGMA busy_timeout=10000",    # 10s retry window
    "PRAGMA foreign_keys=ON",
    "PRAGMA cache_size=-32000",     # 32 MB in-process cache
    "PRAGMA temp_store=MEMORY",
    "PRAGMA mmap_size=268435456",   # 256 MB memory-mapped I/O
]

# For Starlette async: use separate read pool + write serializer
from contextlib import asynccontextmanager
import aiosqlite, asyncio

_write_lock = asyncio.Lock()

@asynccontextmanager
async def write_conn(path: str):
    async with _write_lock:
        async with aiosqlite.connect(path) as db:
            yield db

async def read_conn(path: str):
    # Multiple concurrent readers — no lock needed with WAL
    return await aiosqlite.connect(path)
```

**Key insight**: WAL checkpointing must not be blocked. Litestream blocks it intentionally. If you use WAL without Litestream, set `wal_autocheckpoint` to prevent unbounded WAL growth.

---

## 7. When to Migrate to PostgreSQL

**Migrate when ANY of these are true**:

| Signal | Threshold |
|---|---|
| Concurrent writers | >10 simultaneous write transactions |
| Write throughput | >100 writes/sec sustained |
| DB size | >10 GB with complex joins |
| Team size | >3 engineers writing schema migrations |
| Compliance | SOC2/HIPAA requiring audit logs (Turso Pro does this) |
| Multi-region writes | Need low-latency writes from multiple regions |
| Full-text search | Need advanced FTS beyond SQLite FTS5 |
| LISTEN/NOTIFY | Real-time event streams |
| Row-level security | Complex auth at DB layer (PostgREST, Supabase) |

**SQLite is fine forever if**: Config store + credential vault with <50 writes/min, single-region, team <3. Your use case fits this profile.

**Migration path**: SQLAlchemy ORM → swap `sqlite+aiosqlite` for `postgresql+asyncpg`. Schema migration via Alembic (same workflow).

---

## Recommendation for Your App (Starlette + PydanticAI + Config Store)

### Stage 1 — MVP/Production (single node)
```
Single Fly.io / Railway machine
+ WAL mode + aiosqlite + asyncio write lock
+ Litestream → S3 (continuous backup, ~$0.02/mo)
```
Cost: ~$7–15/mo. Zero ops complexity. Survives machine restart. DR with 1s RPO.

### Stage 2 — Multi-region read scale
```
Turso Developer ($5/mo)
+ Embedded replica in your Starlette process
+ Writes to Turso edge, reads from local file
```
Fits perfectly: low write volume, config/credential reads are latency-sensitive.

### Stage 3 — HA + failover (if you need it)
```
LiteFS on Fly.io (2+ machines)
OR migrate to PostgreSQL (Supabase / Neon / Railway Postgres)
```

**Never use D1** unless you rewrite the app as a Workers app.

---

*Sources: [LiteFS docs](https://fly.io/docs/litefs/), [LiteFS proxy](https://fly.io/docs/litefs/proxy/), [Litestream how it works](https://litestream.io/how-it-works/), [Litestream S3](https://litestream.io/guides/s3/), [Turso intro](https://docs.turso.tech/introduction), [Turso pricing](https://turso.tech/pricing), [D1 overview](https://developers.cloudflare.com/d1/), [D1 limits](https://developers.cloudflare.com/d1/platform/limits/)*
