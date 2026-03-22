"""ConfigDB — SQLite-backed configuration store with encrypted credentials."""

import json
import logging
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from dbot.config.defaults import SECTION_DEFAULTS
from dbot.config.encryption import decrypt_value, encrypt_value, load_or_create_key
from dbot.config.models import SECTION_MODELS

logger = logging.getLogger("dbot.config.db")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS config_sections (
    section    TEXT PRIMARY KEY,
    data       TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS credentials (
    pack       TEXT NOT NULL,
    param_name TEXT NOT NULL,
    value_enc  TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (pack, param_name)
);

CREATE TABLE IF NOT EXISTS migrations (
    name       TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


class ConfigDB:
    """SQLite configuration store with Fernet-encrypted credentials.

    Thread-safe — all DB access is protected by a lock.
    """

    def __init__(self, db_path: Path, key_path: Path) -> None:
        self._db_path = db_path
        self._key = load_or_create_key(key_path)
        self._lock = threading.Lock()

        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()
        self._migrate_credentials_yaml(db_path.parent)
        self._migrate_llm_yaml(db_path.parent)

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(SCHEMA_SQL)

    def get_section(self, section: str) -> dict[str, Any]:
        """Get a config section. Returns defaults if not set."""
        with self._lock:
            row = self._conn.execute("SELECT data FROM config_sections WHERE section = ?", (section,)).fetchone()

        if row:
            return json.loads(row["data"])  # type: ignore[no-any-return]

        if section in SECTION_DEFAULTS:
            return SECTION_DEFAULTS[section].model_dump()
        return {}

    def set_section(self, section: str, data: dict[str, Any]) -> None:
        """Set a config section. Validates against the section's Pydantic model."""
        if section in SECTION_MODELS:
            SECTION_MODELS[section].model_validate(data)

        with self._lock:
            self._conn.execute(
                """INSERT INTO config_sections (section, data, updated_at)
                   VALUES (?, ?, datetime('now'))
                   ON CONFLICT(section) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at""",
                (section, json.dumps(data)),
            )
            self._conn.commit()

    def get_all_sections(self) -> dict[str, dict[str, Any]]:
        """Get all config sections, filling in defaults for missing ones."""
        result: dict[str, dict[str, Any]] = {}
        for section_name in SECTION_MODELS:
            result[section_name] = self.get_section(section_name)
        return result

    def set_credential(self, pack: str, param_name: str, value: str) -> None:
        """Store an encrypted credential."""
        encrypted = encrypt_value(value, self._key)
        with self._lock:
            self._conn.execute(
                """INSERT INTO credentials (pack, param_name, value_enc, updated_at)
                   VALUES (?, ?, ?, datetime('now'))
                   ON CONFLICT(pack, param_name) DO UPDATE SET
                   value_enc = excluded.value_enc,
                   updated_at = excluded.updated_at""",
                (pack, param_name, encrypted),
            )
            self._conn.commit()

    def set_pack_credentials(self, pack: str, params: dict[str, str]) -> None:
        """Set all credentials for a pack (replaces existing)."""
        self.delete_pack_credentials(pack)
        for param_name, value in params.items():
            self.set_credential(pack, param_name, value)

    def get_credential_params(self, pack: str) -> list[str]:
        """Get param names for a pack (NO values — security)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT param_name FROM credentials WHERE pack = ? ORDER BY param_name", (pack,)
            ).fetchall()
        return [row["param_name"] for row in rows]

    def get_all_credential_packs(self) -> list[str]:
        """List all packs that have credentials stored."""
        with self._lock:
            rows = self._conn.execute("SELECT DISTINCT pack FROM credentials ORDER BY pack").fetchall()
        return [row["pack"] for row in rows]

    def get_decrypted_pack(self, pack: str) -> dict[str, str]:
        """Get decrypted credentials for a pack. Used by CredentialStore bridge."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT param_name, value_enc FROM credentials WHERE pack = ?", (pack,)
            ).fetchall()
        return {row["param_name"]: decrypt_value(row["value_enc"], self._key) for row in rows}

    def delete_pack_credentials(self, pack: str) -> None:
        """Remove all credentials for a pack."""
        with self._lock:
            self._conn.execute("DELETE FROM credentials WHERE pack = ?", (pack,))
            self._conn.commit()

    # ── LLM Provider Keys ────────────────────────────────────────────

    PROVIDER_PREFIX = "__provider__"

    def set_provider_key(self, provider: str, api_key: str) -> None:
        """Store an encrypted LLM provider API key."""
        self.set_credential(f"{self.PROVIDER_PREFIX}{provider}", "api_key", api_key)

    def get_provider_key(self, provider: str) -> str | None:
        """Get decrypted API key for a provider. Returns None if not set."""
        creds = self.get_decrypted_pack(f"{self.PROVIDER_PREFIX}{provider}")
        return creds.get("api_key")

    def get_all_provider_keys(self) -> dict[str, str]:
        """Get all provider keys (decrypted). Used at startup to inject into env."""
        result: dict[str, str] = {}
        for pack in self.get_all_credential_packs():
            if pack.startswith(self.PROVIDER_PREFIX):
                provider = pack[len(self.PROVIDER_PREFIX) :]
                key = self.get_provider_key(provider)
                if key:
                    result[provider] = key
        return result

    def delete_provider_key(self, provider: str) -> None:
        """Remove a provider's API key."""
        self.delete_pack_credentials(f"{self.PROVIDER_PREFIX}{provider}")

    def get_all_credential_packs_filtered(self) -> list[str]:
        """List credential packs excluding internal provider keys."""
        return [p for p in self.get_all_credential_packs() if not p.startswith(self.PROVIDER_PREFIX)]

    def _migrate_credentials_yaml(self, config_dir: Path) -> None:
        """Auto-migrate credentials from credentials.yaml on first run."""
        migration_name = "credentials_yaml_import"

        with self._lock:
            done = self._conn.execute("SELECT 1 FROM migrations WHERE name = ?", (migration_name,)).fetchone()

        if done:
            return

        yaml_path = config_dir / "credentials.yaml"
        if not yaml_path.exists():
            self._mark_migration(migration_name)
            return

        try:
            with open(yaml_path, encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}

            migrated = 0
            for pack_name, params in raw.items():
                if not isinstance(params, dict):
                    continue
                for param_name, value in params.items():
                    resolved = self._resolve_env(str(value))
                    if resolved is not None:
                        self.set_credential(pack_name, param_name, resolved)
                        migrated += 1
                    else:
                        logger.warning("Skipping %s.%s — env var not set", pack_name, param_name)

            logger.info("Migrated %d credentials from credentials.yaml", migrated)
        except Exception:
            logger.exception("Failed to migrate credentials.yaml")

        self._mark_migration(migration_name)

    def _migrate_llm_yaml(self, config_dir: Path) -> None:
        """Auto-migrate LLM config from llm.yaml on first run.

        Reads default_model, temperature, max_tokens, available_models into
        the 'llm' config section. Provider API keys are encrypted and stored
        as provider credentials. Only runs once."""
        migration_name = "llm_yaml_import"

        with self._lock:
            done = self._conn.execute("SELECT 1 FROM migrations WHERE name = ?", (migration_name,)).fetchone()

        if done:
            return

        yaml_path = config_dir / "llm.yaml"
        if not yaml_path.exists():
            self._mark_migration(migration_name)
            return

        try:
            with open(yaml_path, encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}

            # Build LLM config section (non-secret fields)
            llm_data: dict[str, Any] = {}
            for key in ("default_model", "temperature", "max_tokens", "available_models"):
                if key in raw:
                    llm_data[key] = raw[key]

            # Process providers
            providers_config: dict[str, dict[str, str]] = {}
            migrated_keys = 0
            for provider_name, prov_data in (raw.get("providers") or {}).items():
                if not isinstance(prov_data, dict):
                    continue

                # Extract and encrypt API key
                raw_key = prov_data.get("api_key", "")
                if raw_key:
                    resolved_key = self._resolve_env(str(raw_key))
                    if resolved_key:
                        self.set_provider_key(provider_name, resolved_key)
                        migrated_keys += 1
                    else:
                        logger.warning("Skipping %s API key — env var not set", provider_name)

                # Store non-secret provider config
                providers_config[provider_name] = {
                    "base_url": str(prov_data.get("base_url", "")),
                    "env_var": str(prov_data.get("env_var", "")),
                }

            if providers_config:
                llm_data["providers"] = providers_config

            # Save LLM config section
            if llm_data:
                from dbot.config.models import LLMConfig

                # Merge with defaults to ensure all fields present
                current = self.get_section("llm")
                current.update(llm_data)
                LLMConfig.model_validate(current)  # validate before saving
                self.set_section("llm", current)

            logger.info(
                "Migrated LLM config from llm.yaml (%d provider keys)",
                migrated_keys,
            )
        except Exception:
            logger.exception("Failed to migrate llm.yaml")

        self._mark_migration(migration_name)

    def _mark_migration(self, name: str) -> None:
        with self._lock:
            self._conn.execute("INSERT OR IGNORE INTO migrations (name) VALUES (?)", (name,))
            self._conn.commit()

    @staticmethod
    def _resolve_env(value: str) -> str | None:
        """Resolve ${ENV_VAR} in a value. Returns None if env var missing."""
        import re

        pattern = re.compile(r"\$\{([^}]+)\}")
        match = pattern.search(value)
        if not match:
            return value

        def replace(m: re.Match[str]) -> str:
            env_val = os.environ.get(m.group(1))
            if env_val is None:
                raise ValueError(f"Env var {m.group(1)} not set")
            return env_val

        try:
            return pattern.sub(replace, value)
        except ValueError:
            return None

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()
