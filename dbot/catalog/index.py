"""Index demisto/content integration YAMLs into IntegrationDef models."""

import json
import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from dbot.catalog.models import ArgDef, CommandDef, IntegrationDef, OutputDef, ParamDef

logger = logging.getLogger("dbot.catalog.index")

# Bump whenever parsing output changes; a mismatch invalidates the disk cache.
PARSER_VERSION = 2

CACHE_FILENAME = ".index_cache.json"

_APIMODULE_RE = re.compile(r"^from (\w+ApiModule) import \*", re.MULTILINE)

_Loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


def parse_apimodules(source: str) -> list[str]:
    """Return ApiModule names star-imported by an integration source, in first-seen order."""
    return list(dict.fromkeys(_APIMODULE_RE.findall(source)))


def _coerce_options(raw: Any) -> list[str] | None:
    """Coerce YAML predefined/options (sometimes booleans) to a list of strings."""
    if raw is None:
        return None
    if not isinstance(raw, list):
        return [str(raw)]
    return [str(v) for v in raw]


def _parse_arg(arg_data: dict[str, Any]) -> ArgDef:
    default_val = arg_data.get("defaultValue")
    return ArgDef(
        name=arg_data.get("name", ""),
        description=arg_data.get("description", "") or "",
        required=bool(arg_data.get("required", False)),
        default=str(default_val) if default_val is not None else None,
        is_array=bool(arg_data.get("isArray", False)),
        secret=bool(arg_data.get("secret", False)),
        options=_coerce_options(arg_data.get("predefined") or arg_data.get("options")),
    )


def _parse_output(output_data: dict[str, Any]) -> OutputDef:
    return OutputDef(
        context_path=output_data.get("contextPath", "") or "",
        description=output_data.get("description", "") or "",
        type=str(output_data.get("type", "Unknown")),
    )


def _parse_command(cmd_data: dict[str, Any]) -> CommandDef:
    raw_args = cmd_data.get("arguments") or []
    raw_outputs = cmd_data.get("outputs") or []
    return CommandDef(
        name=cmd_data.get("name", ""),
        description=cmd_data.get("description", "") or "",
        args=[_parse_arg(a) for a in raw_args if isinstance(a, dict)],
        outputs=[_parse_output(o) for o in raw_outputs if isinstance(o, dict)],
        dangerous=bool(cmd_data.get("execution", False)),
        deprecated=bool(cmd_data.get("deprecated", False)),
    )


def _resolve_py_path(yml_path: Path) -> Path:
    """Find the Python file for an integration (same stem, else any non-test .py in the directory)."""
    py_path = yml_path.with_suffix(".py")
    if py_path.exists():
        return py_path
    candidates = sorted(p for p in yml_path.parent.glob("*.py") if "_test" not in p.name and "test_" not in p.name)
    return candidates[0] if candidates else py_path


def _read_apimodules(py_path: Path, script_data: dict[str, Any]) -> list[str]:
    try:
        source = py_path.read_text(encoding="utf-8")
    except OSError:
        inline = script_data.get("script")
        source = inline if isinstance(inline, str) else ""
    return parse_apimodules(source)


def _pack_name(yml_path: Path) -> str:
    parts = yml_path.parts
    for i, part in enumerate(parts[:-1]):
        if part == "Packs":
            return parts[i + 1]
    return ""


def parse_integration(yml_path: Path) -> tuple[IntegrationDef, bool] | None:
    """Parse one integration YAML into (IntegrationDef, deprecated).

    Returns None if the file is not a valid integration definition.
    """
    try:
        with open(yml_path, encoding="utf-8") as f:
            data = yaml.load(f, Loader=_Loader)  # noqa: S506 - safe loader (C or pure Python)
    except (yaml.YAMLError, OSError) as e:
        logger.warning("Failed to parse %s: %s", yml_path, e)
        return None

    if not isinstance(data, dict):
        return None
    script_data = data.get("script")
    if not isinstance(script_data, dict):
        return None

    pack_name = _pack_name(yml_path)
    if not pack_name:
        logger.warning("Could not determine pack name from %s", yml_path)
        return None

    params: list[ParamDef] = []
    credential_params: list[str] = []
    for cfg in data.get("configuration") or []:
        if not isinstance(cfg, dict):
            continue
        param = ParamDef(
            name=cfg.get("name", ""),
            display=str(cfg.get("display", cfg.get("displaypassword", ""))),
            display_password=str(cfg.get("displaypassword", "")),
            type=cfg.get("type", 0),
            required=bool(cfg.get("required", False)),
            default=(str(cfg["defaultvalue"]) if cfg.get("defaultvalue") is not None else None),
            is_credential=cfg.get("type") in (4, 9),
            hidden=bool(cfg.get("hidden", False) or cfg.get("hiddenusername", False)),
            options=_coerce_options(cfg.get("options")),
        )
        params.append(param)
        if param.is_credential:
            credential_params.append(param.name)

    commands = [_parse_command(c) for c in script_data.get("commands") or [] if isinstance(c, dict)]
    py_path = _resolve_py_path(yml_path).resolve()
    script_type = str(script_data.get("type") or "")

    integration = IntegrationDef(
        pack=pack_name,
        name=str(data.get("name") or yml_path.stem),
        display=str(data.get("display") or ""),
        description=str(data.get("description") or ""),
        category=str(data.get("category") or ""),
        py_path=str(py_path),
        yml_path=str(yml_path.resolve()),
        dockerimage=str(script_data.get("dockerimage") or ""),
        script_type=script_type,
        subtype=str(script_data.get("subtype") or ""),
        apimodules=_read_apimodules(py_path, script_data) if script_type == "python" else [],
        commands=commands,
        params=params,
        credential_params=credential_params,
    )
    return integration, bool(data.get("deprecated", False))


def _git_head(content_root: Path) -> str | None:
    if not (content_root / ".git").exists():
        return None
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],  # noqa: S607
            cwd=content_root,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def get_content_hash(content_root: Path) -> str | None:
    """Content commit: git HEAD of the checkout, else env DBOT_CONTENT_COMMIT (images ship without .git)."""
    return _git_head(content_root) or os.environ.get("DBOT_CONTENT_COMMIT") or None


_Entry = tuple[IntegrationDef, bool]


def load_cache(cache_path: Path, content_hash: str) -> list[_Entry] | None:
    """Load cached entries if the cache matches both the parser version and the content commit."""
    if not cache_path.exists():
        return None
    try:
        with open(cache_path, encoding="utf-8") as f:
            data = json.load(f)
        if data.get("parser_version") != PARSER_VERSION or data.get("content_hash") != content_hash:
            logger.info("Index cache stale, re-indexing")
            return None
        return [(IntegrationDef.model_validate(e["integration"]), bool(e["deprecated"])) for e in data["entries"]]
    except Exception:
        logger.warning("Failed to load index cache, re-indexing", exc_info=True)
        return None


def save_cache(cache_path: Path, content_hash: str, entries: list[_Entry]) -> None:
    """Persist all parsed entries (unfiltered) keyed by parser version and content commit."""
    data = {
        "parser_version": PARSER_VERSION,
        "content_hash": content_hash,
        "entries": [{"integration": i.model_dump(), "deprecated": dep} for i, dep in entries],
    }
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache_path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, separators=(",", ":"))
        tmp.replace(cache_path)
    except OSError:
        logger.warning("Failed to save index cache", exc_info=True)


def walk_and_parse(packs_dir: Path) -> list[_Entry]:
    """Parse every Packs/*/Integrations/*/*.yml that defines at least one command."""
    entries: list[_Entry] = []
    for yml_path in sorted(packs_dir.glob("*/Integrations/*/*.yml")):
        parsed = parse_integration(yml_path)
        if parsed and parsed[0].commands:
            entries.append(parsed)
    return entries


def index_content(
    content_root: Path,
    *,
    cache_dir: Path | None = None,
    include_deprecated: bool = False,
    python3_only: bool = True,
    enabled_packs: list[str] | None = None,
) -> list[IntegrationDef]:
    """Index content/Packs into IntegrationDefs.

    By default only non-deprecated python/python3 integrations are returned. The full parse is
    cached under `cache_dir` (default `$DBOT_DATA_DIR/cache`, else `<content_root>/../config`),
    keyed by PARSER_VERSION and the content commit; without a commit no cache is used.
    """
    packs_dir = content_root / "Packs"
    if not packs_dir.is_dir():
        logger.warning("Packs directory not found at %s", packs_dir)
        return []

    content_hash = get_content_hash(content_root)
    if cache_dir is None:
        data_dir = os.environ.get("DBOT_DATA_DIR")
        cache_dir = Path(data_dir) / "cache" if data_dir else content_root.parent / "config"
    cache_path = cache_dir / CACHE_FILENAME

    entries = load_cache(cache_path, content_hash) if content_hash else None
    if entries is None:
        entries = walk_and_parse(packs_dir)
        if content_hash:
            save_cache(cache_path, content_hash, entries)

    enabled = set(enabled_packs) if enabled_packs else None
    integrations = [
        i
        for i, deprecated in entries
        if (include_deprecated or not deprecated)
        and (not python3_only or (i.script_type == "python" and i.subtype == "python3"))
        and (enabled is None or i.pack in enabled)
    ]
    logger.info("Indexed %d integrations from %s", len(integrations), packs_dir)
    return integrations
