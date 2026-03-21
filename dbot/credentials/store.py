"""Credential store — resolves integration secrets from env vars / config."""

import logging
import os
import re
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

logger = logging.getLogger("dbot.credentials")


class CredentialStore:
    """Resolves integration credentials from env vars or config file."""

    ENV_PATTERN = re.compile(r"\$\{([^}]+)\}")

    def __init__(self, config_path: Path | None = None) -> None:
        self._credentials: dict[str, dict[str, str]] = {}

        if config_path and config_path.exists():
            with open(config_path, encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}

            for pack_name, params in raw.items():
                if not isinstance(params, dict):
                    logger.warning("Skipping invalid credential config for %s", pack_name)
                    continue
                try:
                    self._credentials[pack_name] = {k: self._resolve_value(v) for k, v in params.items()}
                except ValueError as e:
                    logger.warning("Failed to resolve credentials for %s: %s", pack_name, e)

    def _resolve_value(self, value: Any) -> str:
        """Resolve ${ENV_VAR} references in a value."""
        if not isinstance(value, str):
            return str(value)

        def replace_env(match: re.Match[str]) -> str:
            env_var = match.group(1)
            env_value = os.environ.get(env_var)
            if env_value is None:
                raise ValueError(f"Environment variable '{env_var}' not set")
            return env_value

        return self.ENV_PATTERN.sub(replace_env, value)

    def get(self, pack_name: str) -> dict[str, str]:
        """Get credentials for a pack. Returns empty dict if not configured."""
        return dict(self._credentials.get(pack_name, {}))

    def has(self, pack_name: str) -> bool:
        """Check if credentials are configured for a pack."""
        return pack_name in self._credentials

    def configured_packs(self) -> list[str]:
        """List all packs with configured credentials."""
        return list(self._credentials.keys())
