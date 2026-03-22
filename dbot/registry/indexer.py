"""YAML indexer — walks Packs/ and parses integration definitions."""

import json
import logging
import subprocess
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from dbot.registry.models import (
    ArgDef,
    CommandDef,
    IntegrationDef,
    OutputDef,
    ParamDef,
)

logger = logging.getLogger("dbot.indexer")

_CACHE_FILENAME = ".index_cache.json"


def _coerce_options(raw: Any) -> list[str] | None:
    """Coerce YAML predefined/options to list of strings.

    Some YAMLs have `predefined: [true, false]` (booleans) instead of strings.
    """
    if raw is None:
        return None
    if not isinstance(raw, list):
        return [str(raw)]
    return [str(v) for v in raw]


def _parse_arg(arg_data: dict[str, Any]) -> ArgDef:
    """Parse a single argument from YAML."""
    default_val = arg_data.get("defaultValue")
    return ArgDef(
        name=arg_data.get("name", ""),
        description=arg_data.get("description", ""),
        required=bool(arg_data.get("required", False)),
        default=str(default_val) if default_val is not None else None,
        is_array=bool(arg_data.get("isArray", False)),
        secret=bool(arg_data.get("secret", False)),
        options=_coerce_options(arg_data.get("predefined") or arg_data.get("options")),
    )


def _parse_output(output_data: dict[str, Any]) -> OutputDef:
    """Parse a single output from YAML."""
    return OutputDef(
        context_path=output_data.get("contextPath", ""),
        description=output_data.get("description", ""),
        type=str(output_data.get("type", "Unknown")),
    )


def _parse_command(cmd_data: dict[str, Any]) -> CommandDef:
    """Parse a single command from YAML."""
    raw_args = cmd_data.get("arguments") or []
    raw_outputs = cmd_data.get("outputs") or []

    return CommandDef(
        name=cmd_data.get("name", ""),
        description=cmd_data.get("description", ""),
        args=[_parse_arg(a) for a in raw_args if isinstance(a, dict)],
        outputs=[_parse_output(o) for o in raw_outputs if isinstance(o, dict)],
        dangerous=bool(cmd_data.get("execution", False)),
        deprecated=bool(cmd_data.get("deprecated", False)),
    )


def _resolve_py_path(yml_path: Path) -> Path:
    """Find the Python file for an integration."""
    # Most integrations: same name as yml, same directory
    py_path = yml_path.with_suffix(".py")
    if py_path.exists():
        return py_path

    # Fallback: find any non-test .py file in the directory
    candidates = [p for p in yml_path.parent.glob("*.py") if "_test" not in p.name and "test_" not in p.name]
    if candidates:
        return candidates[0]

    return yml_path.with_suffix(".py")


def parse_integration_yaml(yml_path: Path) -> IntegrationDef | None:
    """Parse a single integration YAML into IntegrationDef.

    Returns None if the YAML is not a valid integration definition.
    """
    try:
        with open(yml_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except (yaml.YAMLError, OSError) as e:
        logger.warning("Failed to parse %s: %s", yml_path, e)
        return None

    if not isinstance(data, dict) or "script" not in data:
        return None

    # Extract pack name from path: Packs/{PackName}/Integrations/{IntName}/{IntName}.yml
    parts = yml_path.parts
    pack_name = ""
    for i, part in enumerate(parts):
        if part == "Packs" and i + 1 < len(parts):
            pack_name = parts[i + 1]
            break

    if not pack_name:
        logger.warning("Could not determine pack name from %s", yml_path)
        return None

    # Parse configuration params
    params: list[ParamDef] = []
    credential_params: list[str] = []
    for cfg in data.get("configuration", []) or []:
        if not isinstance(cfg, dict):
            continue
        param = ParamDef(
            name=cfg.get("name", ""),
            display=str(cfg.get("display", cfg.get("displaypassword", ""))),
            type=cfg.get("type", 0),
            required=bool(cfg.get("required", False)),
            default=(str(cfg["defaultvalue"]) if cfg.get("defaultvalue") is not None else None),
            is_credential=cfg.get("type") == 9,
            hidden=bool(cfg.get("hidden", False) or cfg.get("hiddenusername", False)),
            options=_coerce_options(cfg.get("options")),
        )
        params.append(param)
        if param.is_credential:
            credential_params.append(param.name)

    # Parse commands
    script_data = data.get("script", {})
    if not isinstance(script_data, dict):
        return None

    commands: list[CommandDef] = []
    for cmd_data in script_data.get("commands", []) or []:
        if not isinstance(cmd_data, dict):
            continue
        commands.append(_parse_command(cmd_data))

    py_path = _resolve_py_path(yml_path)

    return IntegrationDef(
        pack=pack_name,
        name=data.get("name", yml_path.stem),
        display=data.get("display", ""),
        description=data.get("description", ""),
        category=data.get("category", ""),
        py_path=str(py_path),
        commands=commands,
        params=params,
        credential_params=credential_params,
    )


def _get_content_hash(content_root: Path) -> str | None:
    """Get git commit hash of the content directory for cache invalidation."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=content_root,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return None


def _load_cache(cache_path: Path, content_hash: str) -> list[IntegrationDef] | None:
    """Load cached integrations if the cache exists and matches the content hash."""
    if not cache_path.exists():
        return None
    try:
        with open(cache_path, encoding="utf-8") as f:
            data = json.load(f)
        if data.get("content_hash") != content_hash:
            logger.info("Index cache stale (hash mismatch), re-indexing")
            return None
        integrations = [IntegrationDef.model_validate(entry) for entry in data["integrations"]]
        logger.info("Loaded %d integrations from cache", len(integrations))
        return integrations
    except Exception:
        logger.warning("Failed to load index cache, re-indexing", exc_info=True)
        return None


def _save_cache(cache_path: Path, content_hash: str, integrations: list[IntegrationDef]) -> None:
    """Persist the full integration index to a JSON cache file."""
    try:
        data = {
            "content_hash": content_hash,
            "integrations": [i.model_dump() for i in integrations],
        }
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(data, f, separators=(",", ":"))
        logger.info("Saved index cache (%d integrations)", len(integrations))
    except OSError:
        logger.warning("Failed to save index cache", exc_info=True)


def _walk_and_parse(packs_dir: Path) -> list[IntegrationDef]:
    """Walk Packs/ and parse all integration YAMLs (no filtering)."""
    integrations: list[IntegrationDef] = []
    for yml_path in sorted(packs_dir.glob("*/Integrations/*/*.yml")):
        integration = parse_integration_yaml(yml_path)
        if integration and integration.commands:
            integrations.append(integration)
            logger.debug("Indexed %s with %d commands", integration.name, len(integration.commands))
    return integrations


def index_content(content_root: Path, enabled_packs: list[str] | None = None) -> list[IntegrationDef]:
    """Walk content/Packs/ and parse all integration YAMLs.

    Results are cached to disk (keyed by the content/ git commit hash) so
    subsequent startups skip YAML parsing entirely.

    Args:
        content_root: Path to the demisto/content checkout root.
        enabled_packs: If provided, only return these packs. Otherwise return all.

    Returns:
        List of parsed IntegrationDef objects (only those with commands).
    """
    packs_dir = content_root / "Packs"

    if not packs_dir.exists():
        logger.warning("Packs directory not found at %s", packs_dir)
        return []

    content_hash = _get_content_hash(content_root)
    cache_path = content_root.parent / "config" / _CACHE_FILENAME

    integrations: list[IntegrationDef] | None = None
    if content_hash:
        integrations = _load_cache(cache_path, content_hash)

    if integrations is None:
        integrations = _walk_and_parse(packs_dir)
        if content_hash:
            _save_cache(cache_path, content_hash, integrations)

    if enabled_packs:
        enabled_set = set(enabled_packs)
        integrations = [i for i in integrations if i.pack in enabled_set]

    logger.info("Indexed %d integrations from %s", len(integrations), packs_dir)
    return integrations
