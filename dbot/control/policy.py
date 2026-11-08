"""Command risk classification."""

from fnmatch import fnmatchcase
from typing import Literal

from dbot.catalog.models import CommandDef
from dbot.control.config import PolicyConfig

Risk = Literal["read", "destructive"]


def _matches(command: str, patterns: list[str]) -> bool:
    name = command.lower()
    return any(fnmatchcase(name, p.lower()) for p in patterns)


def risk(command: str, policy: PolicyConfig, command_def: CommandDef | None = None) -> Risk:
    """Classify ``command``: ``policy.read`` overrides win, then ``policy.destructive`` globs,
    then the integration's ``dangerous: true`` flag, else read. Globs match the command name case-insensitively."""
    if _matches(command, policy.read):
        return "read"
    if _matches(command, policy.destructive):
        return "destructive"
    if command_def is not None and command_def.dangerous:
        return "destructive"
    return "read"
