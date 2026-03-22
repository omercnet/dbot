# dbot Config System — Implementation Plan

**Goal**: SQLite-backed configuration system with encrypted credentials and a web settings UI.  
**Baseline**: 175 tests passing. Zero regressions permitted.  
**TDD discipline**: Tests written first (or simultaneously), red→green, then refactor.

---

## Dependency Graph

```
T1 (encryption.py)
T2 (models.py)       ─┐
T3 (defaults.py)     ─┤─→ T4 (db.py) ─→ T5 (api.py) ─→ T6 (web.py) ─→ T7 (settings.html)
                       │                              └──────────────────→ T8 (migration)
T1 ────────────────────┘
T9 (pyproject + gitignore) ← independent, do first
```

**Parallel execution possible**: T1, T2, T3, T9 are fully independent.  
T4 requires T1+T2+T3. T5 requires T4. T6 requires T5. T7 and T8 require T6.

---

## T0 — Pre-flight (no code changes)

**Verify green baseline:**
```bash
hatch run test-quick
```
Record exact count. Block all other tasks until this passes.

---

## T1 — `dbot/config/encryption.py`

**What**: Fernet key management — generate, load from disk, encrypt, decrypt.

**Key design decisions** (from research):
- Key file: `config/.dbot-key` relative to project root (resolved via `Path(__file__).parent.parent.parent / "config" / ".dbot-key"`)  
- If file absent → auto-generate with `Fernet.generate_key()`, write with `chmod 0o600`
- Key is raw bytes stored as URL-safe base64 (what `Fernet.generate_key()` returns directly)
- No password-based derivation needed for auto-generated key; PBKDF2HMAC only used if user explicitly sets a master password (out of scope v1 — leave hook but don't implement)
- Encrypt: `Fernet(key).encrypt(plaintext.encode())` → bytes token stored as base64 str in DB
- Decrypt: `Fernet(key).decrypt(token_bytes)` → plaintext str; raises `InvalidToken` on wrong key/tamper

**File**: `dbot/config/encryption.py`

```python
# Public API
def load_or_create_key(key_path: Path) -> bytes: ...
    # Reads key_path; if missing, generates + writes (chmod 600); returns key bytes

def encrypt_value(value: str, key: bytes) -> str: ...
    # Returns URL-safe base64 Fernet token as str

def decrypt_value(token: str, key: bytes) -> str: ...
    # Raises InvalidToken on failure

class EncryptionError(Exception): ...
```

**Tests**: `tests/config/test_encryption.py`
```
- test_generate_key_creates_file
- test_load_existing_key_returns_same_bytes
- test_key_file_permissions_are_600
- test_encrypt_decrypt_roundtrip
- test_decrypt_wrong_key_raises
- test_decrypt_tampered_token_raises
- test_encrypt_produces_different_ciphertext_each_call  (Fernet uses random IV)
```

**Commit**: `feat(config): add Fernet key management (encryption.py)`

---

## T2 — `dbot/config/models.py`

**What**: Pydantic BaseModel config section definitions.

**Models** (map directly to DB table rows — one JSON blob per section):

```python
from pydantic import BaseModel, Field

class GeneralConfig(BaseModel):
    execution_mode: Literal["inprocess", "subprocess"] = "inprocess"
    audit_log_path: str = "dbot-agent-audit.log"
    content_root: str = ""   # empty = auto-detect from package root

class LLMConfig(BaseModel):
    default_model: str = "openai:gpt-4o"
    available_models: dict[str, str] = Field(default_factory=dict)
    temperature: float = 0.0
    max_tokens: int = 4096

class GuardrailsConfig(BaseModel):
    chat_max_tool_calls: int = 100
    chat_timeout_seconds: float = 600.0
    autonomous_max_tool_calls: int = 30
    autonomous_timeout_seconds: float = 300.0
    autonomous_blocked_categories: list[str] = Field(default_factory=lambda: ["Endpoint"])
    autonomous_blocked_tools: list[str] = Field(default_factory=list)

class PacksConfig(BaseModel):
    enabled_packs: list[str] = Field(default_factory=list)  # empty = all

# Section registry — maps section name to model class
SECTION_MODELS: dict[str, type[BaseModel]] = {
    "general": GeneralConfig,
    "llm": LLMConfig,
    "guardrails": GuardrailsConfig,
    "packs": PacksConfig,
}
```

**Note**: `CredentialConfig` is NOT a section model — credentials live in their own DB table with per-param encrypted values.

**Tests**: `tests/config/test_models.py`
```
- test_general_config_defaults
- test_llm_config_defaults  
- test_guardrails_config_defaults
- test_packs_config_defaults
- test_all_sections_in_registry
- test_models_round_trip_json
- test_guardrails_blocked_categories_default_is_endpoint
```

**Commit**: `feat(config): add Pydantic config section models`

---

## T3 — `dbot/config/defaults.py`

**What**: Default instances for all sections. Single source of truth for "factory reset".

```python
from dbot.config.models import GeneralConfig, GuardrailsConfig, LLMConfig, PacksConfig

DEFAULT_GENERAL = GeneralConfig()
DEFAULT_LLM = LLMConfig(
    available_models={
        "GPT-4o": "openai:gpt-4o",
        "GPT-4o mini": "openai:gpt-4o-mini",
        "Claude Sonnet": "anthropic:claude-sonnet-4-5",
        "Claude Haiku": "anthropic:claude-haiku-3-5",
        "Gemini Flash": "google-gla:gemini-1.5-flash",
    }
)
DEFAULT_GUARDRAILS = GuardrailsConfig()
DEFAULT_PACKS = PacksConfig()

SECTION_DEFAULTS: dict[str, BaseModel] = {
    "general": DEFAULT_GENERAL,
    "llm": DEFAULT_LLM,
    "guardrails": DEFAULT_GUARDRAILS,
    "packs": DEFAULT_PACKS,
}
```

**Tests**: `tests/config/test_defaults.py`
```
- test_defaults_are_valid_models
- test_all_sections_have_defaults
- test_defaults_match_section_registry_keys
```

**Commit**: `feat(config): add config defaults`

---

## T4 — `dbot/config/db.py`

**What**: `ConfigDB` — SQLite CRUD, schema init, Fernet encryption for credentials, and auto-migration from `credentials.yaml`.

**Schema** (two tables):

```sql
CREATE TABLE IF NOT EXISTS config_sections (
    section  TEXT PRIMARY KEY,
    data     TEXT NOT NULL,        -- JSON blob of the Pydantic model
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS credentials (
    pack        TEXT NOT NULL,
    param_name  TEXT NOT NULL,
    value_enc   TEXT NOT NULL,     -- Fernet token (base64 str)
    updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (pack, param_name)
);
```

**Class API**:

```python
class ConfigDB:
    def __init__(self, db_path: Path, key_path: Path) -> None:
        # Opens/creates SQLite DB, runs schema init, loads Fernet key
        # Calls _migrate_credentials_yaml() if credentials.yaml exists and migration not done

    # ── Config sections ──────────────────────────────────────────────

    def get_section(self, section: str) -> dict[str, Any]:
        # Returns parsed JSON from DB, or default if not set

    def set_section(self, section: str, data: dict[str, Any]) -> None:
        # Validates against SECTION_MODELS[section], upserts JSON

    def get_all_sections(self) -> dict[str, dict[str, Any]]:
        # Returns all 4 sections (with defaults for missing ones)

    # ── Credentials ──────────────────────────────────────────────────

    def set_credential(self, pack: str, param_name: str, value: str) -> None:
        # Encrypts value, upserts into credentials table

    def get_credential(self, pack: str, param_name: str) -> str:
        # Decrypts and returns plaintext — INTERNAL USE ONLY, never exposed via API

    def get_credential_params(self, pack: str) -> list[str]:
        # Returns param names only (no values) — safe for API GET

    def get_all_credential_packs(self) -> list[str]:
        # Returns list of pack names that have any credentials stored

    def delete_pack_credentials(self, pack: str) -> None:
        # Removes all credentials for a pack

    def get_decrypted_pack(self, pack: str) -> dict[str, str]:
        # Returns full decrypted dict — used by CredentialStore bridge

    # ── Migration ────────────────────────────────────────────────────

    def _migrate_credentials_yaml(self, yaml_path: Path) -> None:
        # Reads credentials.yaml, encrypts each value, inserts into DB
        # Writes a sentinel row: config_sections('_migration_done', '{}')
        # Skips if sentinel exists

    # ── Internal ─────────────────────────────────────────────────────

    def _init_schema(self) -> None: ...
    def _conn(self) -> sqlite3.Connection: ...  # returns connection with row_factory=sqlite3.Row
```

**Implementation notes**:
- Thread safety: use `check_same_thread=False` + a threading.Lock for writes (Starlette runs sync handlers in a threadpool)
- `_migrate_credentials_yaml` resolves `${ENV_VAR}` references before encrypting (reuse `CredentialStore._resolve_value` logic or duplicate it)
- Validation: `set_section` must call `SECTION_MODELS[section](**data)` to validate before writing — raises `ValueError` on bad input

**Tests**: `tests/config/test_db.py`
```
- test_db_creates_file_on_init(tmp_path)
- test_schema_tables_exist(tmp_path)
- test_get_section_returns_default_when_missing(tmp_path)
- test_set_and_get_section_roundtrip(tmp_path)
- test_set_section_validates_model(tmp_path)  -- bad data raises ValueError
- test_credential_encrypt_decrypt_roundtrip(tmp_path)
- test_get_credential_params_returns_no_values(tmp_path)
- test_delete_pack_removes_all_params(tmp_path)
- test_migration_from_yaml(tmp_path)
- test_migration_skipped_if_sentinel_exists(tmp_path)
- test_get_all_sections_fills_defaults(tmp_path)
- test_get_all_credential_packs(tmp_path)
```

**Commit**: `feat(config): add ConfigDB with SQLite CRUD and credential encryption`

---

## T5 — `dbot/config/api.py`

**What**: Starlette route handlers for all `/api/settings/*` endpoints. Returns a `Router` that gets mounted onto the main app.

**Route table**:

```
GET  /api/settings                    → get_all_settings
GET  /api/settings/{section}          → get_section
PUT  /api/settings/{section}          → put_section
GET  /api/settings/credentials        → list_credentials
PUT  /api/settings/credentials/{pack} → put_credentials
DEL  /api/settings/credentials/{pack} → delete_credentials
POST /api/settings/credentials/{pack}/test → test_connection
GET  /api/packs                        → list_packs
GET  /api/health                       → health_check
GET  /settings                         → serve settings HTML page
```

**Handler signatures** (all `async def handler(request: Request) -> Response`):

```python
async def get_all_settings(request: Request) -> JSONResponse:
    db: ConfigDB = request.app.state.config_db
    return JSONResponse(db.get_all_sections())

async def get_section(request: Request) -> JSONResponse:
    section = request.path_params["section"]
    if section not in SECTION_MODELS:
        return JSONResponse({"error": "unknown section"}, status_code=404)
    db: ConfigDB = request.app.state.config_db
    return JSONResponse(db.get_section(section))

async def put_section(request: Request) -> JSONResponse:
    section = request.path_params["section"]
    if section not in SECTION_MODELS:
        return JSONResponse({"error": "unknown section"}, status_code=404)
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    try:
        db: ConfigDB = request.app.state.config_db
        db.set_section(section, data)
        return JSONResponse(db.get_section(section))
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=422)

async def list_credentials(request: Request) -> JSONResponse:
    db: ConfigDB = request.app.state.config_db
    packs = db.get_all_credential_packs()
    result = {pack: db.get_credential_params(pack) for pack in packs}
    return JSONResponse(result)  # {"VirusTotal": ["apikey", "base_url"], ...}

async def put_credentials(request: Request) -> JSONResponse:
    pack = request.path_params["pack"]
    try:
        data: dict[str, str] = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    if not isinstance(data, dict):
        return JSONResponse({"error": "body must be object"}, status_code=400)
    db: ConfigDB = request.app.state.config_db
    for param_name, value in data.items():
        if isinstance(value, str) and value:
            db.set_credential(pack, param_name, value)
    return JSONResponse({"pack": pack, "params": db.get_credential_params(pack)})

async def delete_credentials(request: Request) -> JSONResponse:
    pack = request.path_params["pack"]
    db: ConfigDB = request.app.state.config_db
    db.delete_pack_credentials(pack)
    return JSONResponse({"deleted": pack})

async def test_connection(request: Request) -> JSONResponse:
    pack = request.path_params["pack"]
    db: ConfigDB = request.app.state.config_db
    catalog: Catalog = request.app.state.catalog
    executor = request.app.state.executor

    # Find an integration for this pack
    integrations = [i for i in catalog.integrations() if i.pack == pack]
    if not integrations:
        return JSONResponse({"error": f"No integration found for pack {pack!r}"}, status_code=404)

    integration = integrations[0]
    params = db.get_decrypted_pack(pack)
    py_path = Path(integration.py_path)

    result = await executor(py_path, "test-module", {}, params, timeout=15.0)
    return JSONResponse({
        "pack": pack,
        "success": result.get("success", False),
        "output": result.get("results", []),
        "error": result.get("error"),
    })

async def list_packs(request: Request) -> JSONResponse:
    catalog: Catalog = request.app.state.catalog
    # Group integrations by pack, count commands
    packs: dict[str, dict] = {}
    for integration in catalog.integrations():
        p = integration.pack
        if p not in packs:
            packs[p] = {"pack": p, "integrations": 0, "commands": 0}
        packs[p]["integrations"] += 1
        packs[p]["commands"] += len(integration.commands)
    return JSONResponse(sorted(packs.values(), key=lambda x: x["pack"]))

async def health_check(request: Request) -> JSONResponse:
    db: ConfigDB = request.app.state.config_db
    general = db.get_section("general")
    content_root = Path(general.get("content_root") or "content")
    return JSONResponse({
        "status": "ok",
        "config_db": "ok",
        "content_root_exists": content_root.exists(),
        "key_file_exists": request.app.state.key_path.exists(),
    })

async def settings_page(request: Request) -> HTMLResponse:
    html_path = Path(__file__).parent / "settings.html"
    return HTMLResponse(html_path.read_text())
```

**Router factory**:

```python
def make_settings_router() -> Router:
    return Router(routes=[
        Route("/api/settings", get_all_settings, methods=["GET"]),
        Route("/api/settings/credentials", list_credentials, methods=["GET"]),
        Route("/api/settings/credentials/{pack}", put_credentials, methods=["PUT"]),
        Route("/api/settings/credentials/{pack}", delete_credentials, methods=["DELETE"]),
        Route("/api/settings/credentials/{pack}/test", test_connection, methods=["POST"]),
        Route("/api/settings/{section}", get_section, methods=["GET"]),
        Route("/api/settings/{section}", put_section, methods=["PUT"]),
        Route("/api/packs", list_packs, methods=["GET"]),
        Route("/api/health", health_check, methods=["GET"]),
        Route("/settings", settings_page, methods=["GET"]),
    ])
```

**Important**: Route ordering matters in Starlette. `/api/settings/credentials` MUST be registered before `/api/settings/{section}` to avoid `credentials` being captured as a section name.

**`Catalog` needs a new method**: `catalog.integrations() -> list[IntegrationDef]` — currently `Catalog` exposes `search()` and `get()` but no "list all". We must add `def integrations(self) -> list[IntegrationDef]: return list(self._integrations.values())` to `dbot/registry/catalog.py`.

**Tests**: `tests/config/test_api.py` — use `starlette.testclient.TestClient`

```
- test_get_all_settings_returns_four_sections
- test_get_section_general
- test_get_section_unknown_returns_404
- test_put_section_valid_data
- test_put_section_invalid_data_returns_422
- test_put_section_bad_json_returns_400
- test_list_credentials_empty
- test_put_credentials_stores_params
- test_put_credentials_get_shows_param_names_only
- test_delete_credentials_removes_pack
- test_test_connection_no_integration_returns_404
- test_test_connection_calls_executor
- test_list_packs_empty_catalog
- test_list_packs_with_integrations
- test_health_check_ok
- test_settings_page_returns_html
- test_credentials_route_not_captured_as_section  ← route ordering regression guard
```

**Fixtures for api tests** (in `tests/config/conftest.py`):
```python
@pytest.fixture
def config_app(tmp_path):
    """Minimal Starlette app wired with ConfigDB for API tests."""
    from starlette.applications import Starlette
    from dbot.config.db import ConfigDB
    from dbot.config.api import make_settings_router

    db = ConfigDB(tmp_path / "test.db", tmp_path / ".dbot-key")
    app = Starlette(routes=make_settings_router().routes)
    app.state.config_db = db
    app.state.catalog = stub_catalog  # use existing stub
    app.state.executor = stub_executor
    app.state.key_path = tmp_path / ".dbot-key"
    return app

@pytest.fixture
def client(config_app):
    from starlette.testclient import TestClient
    return TestClient(config_app)
```

**Commit**: `feat(config): add settings API routes`

---

## T6 — `dbot/agent/web.py` (modify)

**What**: Wire ConfigDB into the Starlette app returned by `to_web()`, attach config routes, and expose `catalog` + `executor` on `app.state`.

**Changes to `_bootstrap_deps`**: resolve `db_path` and `key_path`, create `ConfigDB`, use DB values to override defaults.

**Changes to `create_app`**:

```python
def create_app(
    model: str | None = None,
    models: dict[str, str] | None = None,
    audit_log: Path | None = None,
    db_path: Path | None = None,
) -> Starlette:
    project_root = Path(__file__).parent.parent.parent
    _db_path = db_path or project_root / "config" / "dbot.db"
    _key_path = project_root / "config" / ".dbot-key"

    # Init config DB (auto-migrates credentials.yaml if present)
    db = ConfigDB(_db_path, _key_path)

    # Read config from DB, fall back to env/args
    general = GeneralConfig(**db.get_section("general"))
    llm_cfg = LLMConfig(**db.get_section("llm"))
    guardrails_cfg = GuardrailsConfig(**db.get_section("guardrails"))

    content_root = Path(general.content_root) if general.content_root else project_root / "content"

    # Build deps (same as before, but sourced from DB config)
    deps, model_name = _bootstrap_deps(
        model=model or llm_cfg.default_model,
        audit_log=audit_log,
        content_root=content_root,
        guardrails_cfg=guardrails_cfg,
        db=db,
    )

    # ... build agent, call to_web() ...
    app = agent.to_web(deps=deps, models=available_models, instructions=CHAT_SYSTEM_PROMPT)

    # Mount settings routes onto the existing app
    from dbot.config.api import make_settings_router
    for route in make_settings_router().routes:
        app.router.routes.append(route)

    # Attach shared state
    app.state.config_db = db
    app.state.catalog = deps.catalog
    app.state.executor = execute_inprocess
    app.state.key_path = _key_path

    return app
```

**`_bootstrap_deps` changes**: accept `content_root`, `guardrails_cfg`, `db` params; build `CredentialStore` from DB instead of YAML:

```python
# New CredentialStore bridge — reads decrypted creds from ConfigDB
class DBCredentialStore(CredentialStore):
    """CredentialStore backed by ConfigDB instead of YAML."""
    def __init__(self, db: ConfigDB) -> None:
        self._credentials = {
            pack: db.get_decrypted_pack(pack)
            for pack in db.get_all_credential_packs()
        }
```

Or simpler: just build a plain `CredentialStore` with `config_path=None` and populate `_credentials` directly from `db`. Since `_credentials` is a plain `dict`, this is clean.

**Tests**: `tests/config/test_web_integration.py`
```
- test_create_app_returns_starlette(tmp_path)   -- smoke test, no content submodule needed
- test_settings_routes_mounted(tmp_path)         -- client.get("/api/settings") == 200
- test_app_state_has_config_db(tmp_path)
- test_app_uses_db_default_model(tmp_path)
```

Use `pytest.mark.skipif` on tests that require `content/` submodule.

**Commit**: `feat(config): wire ConfigDB into web app, mount settings routes`

---

## T7 — `dbot/config/settings.html`

**What**: Self-contained single-file settings page (HTML + inline CSS + inline JS). No build step.

**Page sections**:
1. **Header**: "dbot Settings" title + link back to `/` (chat)
2. **Nav tabs**: General | LLM | Guardrails | Packs | Credentials | Pack Inventory
3. **General tab**: Form fields for `execution_mode` (select), `audit_log_path` (text), `content_root` (text)
4. **LLM tab**: `default_model` (text), `available_models` (key-value editor: add/remove rows), `temperature` (number 0-2), `max_tokens` (number)
5. **Guardrails tab**: All 6 fields; `blocked_categories`/`blocked_tools` as tag-input (comma-separated with add/remove chips)
6. **Packs tab**: `enabled_packs` as tag-input (empty = all packs enabled, shown as hint)
7. **Credentials tab**: Per-pack accordion; shows param names + masked `●●●●●` placeholders; "Edit" opens inline form to set/update values; "Delete Pack" button; "Test Connection" button per pack with spinner + result display
8. **Pack Inventory tab**: Table of all indexed packs (from `GET /api/packs`) showing pack name, integration count, command count

**JS architecture**:
- On load: `fetch('/api/settings')` populates all tabs; `fetch('/api/settings/credentials')` populates credentials tab; `fetch('/api/packs')` populates inventory
- "Save" button per section calls `PUT /api/settings/{section}` with JSON body
- Auto-save on blur for simple fields (optional, reduces friction)
- Toast notification for save success/error
- Credentials: "Set Credentials" form with text inputs for each new param name + value (values are password type); submit calls `PUT /api/settings/credentials/{pack}`
- "Test Connection": calls `POST /api/settings/credentials/{pack}/test`, shows spinner, renders result in a `<pre>` block

**Styling**: Minimal, clean. Dark sidebar nav, white content area. System font stack. No external CDN deps — truly self-contained.

**Tests**: No unit tests for HTML. Verified manually + covered indirectly by `test_settings_page_returns_html` in T5 tests.

**Commit**: `feat(config): add settings HTML page`

---

## T8 — `dbot/config/__init__.py` + `dbot/credentials/models.py` check

**What**: 
1. Write `dbot/config/__init__.py` to export the public surface: `ConfigDB`, all config models, `SECTION_MODELS`, `SECTION_DEFAULTS`.
2. Check `dbot/credentials/models.py` (exists but unknown content) — if it conflicts with new models, reconcile.
3. Update `.gitignore` to add `config/.dbot-key` and `config/dbot.db`.
4. Add `cryptography>=42.0` to `pyproject.toml` dependencies.

**Tests**: None specific — covered by other suites.

**Commit**: `chore(config): add __init__.py, update gitignore and pyproject.toml deps`

---

## T9 — `tests/config/__init__.py` + `tests/config/conftest.py`

**What**: Test package init + shared fixtures for the config test suite.

```python
# tests/config/conftest.py
import pytest
from pathlib import Path
from dbot.config.db import ConfigDB

@pytest.fixture
def key_path(tmp_path: Path) -> Path:
    return tmp_path / ".dbot-key"

@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test.db"

@pytest.fixture
def config_db(db_path: Path, key_path: Path) -> ConfigDB:
    return ConfigDB(db_path, key_path)
```

**Commit**: bundled with T1 or T4 commit (whichever comes first).

---

## T10 — `dbot/registry/catalog.py` patch

**What**: Add `integrations()` method needed by the `/api/packs` and test-connection handlers.

```python
def integrations(self) -> list[IntegrationDef]:
    """Return all loaded integrations."""
    return list(self._integrations.values())
```

**Tests**: Add one test to `tests/test_catalog.py`:
```
- test_integrations_returns_all
```

**Commit**: `feat(registry): add Catalog.integrations() method`

---

## Atomic Commit Strategy

Order of commits (each independently passing `hatch run test-quick`):

```
1. chore(config): add cryptography dep, update gitignore       [T8 partial, T9]
2. feat(registry): add Catalog.integrations() method           [T10]
3. feat(config): add Pydantic config section models            [T2]
4. feat(config): add config defaults                           [T3]
5. feat(config): add Fernet key management (encryption.py)     [T1]
6. feat(config): add ConfigDB with SQLite CRUD and migration   [T4]
7. feat(config): add settings API routes                       [T5]
8. feat(config): wire ConfigDB into web app, mount routes      [T6]
9. feat(config): add settings HTML page                        [T7]
10. feat(config): add config package __init__.py               [T8 remainder]
```

Each commit = tests written + passing for that layer. No commit leaves tests red.

---

## Key Implementation Constraints

### Route ordering (critical)
In `make_settings_router()`, literal routes must precede parameterized ones:
```python
Route("/api/settings/credentials", ...)        # BEFORE
Route("/api/settings/{section}", ...)          # AFTER
```
Starlette matches routes in registration order. Getting this wrong silently breaks credential endpoints.

### Thread safety for ConfigDB
```python
import threading
class ConfigDB:
    def __init__(...):
        self._lock = threading.Lock()
    
    def set_section(self, ...):
        with self._lock:
            ...
```
Starlette runs sync route handlers in a thread pool executor. The lock prevents concurrent writes corrupting the DB.

### CredentialStore backward compat
`DBCredentialStore` must satisfy the existing `CredentialStore` interface (get, has, configured_packs). The cleanest approach: populate `self._credentials` from ConfigDB in `__init__`, then the inherited methods work without override.

### `execute_inprocess` timeout for test-module
Pass `timeout=15.0` explicitly — test-module can hang on bad network config. The API should return the error, not timeout the HTTP request.

### `cryptography` package: add to `[project]` dependencies in `pyproject.toml`
```toml
"cryptography>=42.0",
```
Also add to `[tool.hatch.envs.default]` if it doesn't inherit project deps automatically (hatch default envs do inherit, so this is likely not needed separately).

---

## Files Created / Modified

### New files
```
dbot/config/__init__.py
dbot/config/encryption.py
dbot/config/models.py
dbot/config/defaults.py
dbot/config/db.py
dbot/config/api.py
dbot/config/settings.html
tests/config/__init__.py
tests/config/conftest.py
tests/config/test_encryption.py
tests/config/test_models.py
tests/config/test_defaults.py
tests/config/test_db.py
tests/config/test_api.py
tests/config/test_web_integration.py
```

### Modified files
```
dbot/agent/web.py           — wire ConfigDB, mount routes
dbot/registry/catalog.py    — add integrations() method
pyproject.toml              — add cryptography>=42.0
.gitignore                  — add config/.dbot-key, config/dbot.db
tests/test_catalog.py       — add test_integrations_returns_all
```

### Runtime-generated (gitignored)
```
config/dbot.db
config/.dbot-key
```

---

## Verification Steps (final)

```bash
# 1. All existing tests still pass
hatch run test-quick

# 2. New config tests pass
hatch run pytest tests/config/ -v

# 3. Full suite (skips content-submodule tests if not present)
hatch run test

# 4. Lint clean
hatch run lint

# 5. Type check (mypy)
hatch run typecheck

# 6. Smoke test web UI
hatch run dbot-web --port 7932
# Navigate to http://localhost:7932/settings — page loads
# Navigate to http://localhost:7932/api/settings — JSON response
# Navigate to http://localhost:7932/api/health — {"status": "ok", ...}
```

---

## Open Questions / Risks

1. **`dbot/credentials/models.py`** — file exists but contents unknown. If it defines credential-related Pydantic models, T2/T4 must reconcile to avoid name conflicts. Check before implementing T4.

2. **`Catalog.integrations()` in test-connection handler** — the API uses `catalog.integrations()` to look up a pack's integration. If a pack has multiple integrations, we take `integrations[0]`. This is a reasonable heuristic for test-module (most packs have one main integration), but document this assumption.

3. **`to_web()` route conflict risk** — pydantic-ai's `to_web()` registers routes at `/`, `/{id}`, `/api/*`. Our settings routes at `/settings` and `/api/settings/*` should not conflict, but this must be verified at runtime. The `/{id}` catch-all is last in pydantic-ai's router, so our `/settings` route appended before it should work.

4. **Auto-migration idempotency** — if `credentials.yaml` values use `${ENV_VAR}` references, migration must resolve them at migration time (like `CredentialStore` does). If the env var is not set at migration time, skip that pack and log a warning (don't abort).
