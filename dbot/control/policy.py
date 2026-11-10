"""Command risk classification."""

from fnmatch import fnmatchcase
from typing import Literal

from dbot.catalog.models import CommandDef
from dbot.control.config import INTEGRATION_DESTRUCTIVE, InstanceConfig, PolicyConfig

Risk = Literal["read", "destructive"]


def _matches(command: str, patterns: list[str]) -> bool:
    name = command.lower()
    return any(fnmatchcase(name, p.lower()) for p in patterns)


def risk(
    command: str,
    policy: PolicyConfig,
    command_def: CommandDef | None = None,
    instance: InstanceConfig | None = None,
) -> Risk:
    """Classify ``command``, failing closed. Precedence: instance read, instance destructive, dbot's built-in
    destructive commands for the instance's integration, the integration's ``execution: true`` flag, global
    destructive, global read, else destructive. Destructive wins a tie between global globs, so a read glob like
    ``*-get-*`` never unlocks ``*-run-get-*``. Globs match case-insensitively."""
    if instance is not None:
        if _matches(command, instance.policy.read):
            return "read"
        if _matches(command, instance.policy.destructive):
            return "destructive"
        if _matches(command, INTEGRATION_DESTRUCTIVE.get(instance.integration, [])):
            return "destructive"
    if command_def is not None and command_def.dangerous:
        return "destructive"
    if _matches(command, policy.destructive):
        return "destructive"
    if _matches(command, policy.read):
        return "read"
    return "destructive"
