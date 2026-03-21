"""Pydantic models for integration metadata."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ArgDef(BaseModel):
    """A single command argument definition."""

    name: str
    description: str = ""
    required: bool = False
    default: str | None = None
    is_array: bool = False
    secret: bool = False
    options: list[str] | None = None


class OutputDef(BaseModel):
    """A single command output definition."""

    context_path: str
    description: str = ""
    type: str = "Unknown"


class CommandDef(BaseModel):
    """A single integration command."""

    name: str
    description: str = ""
    args: list[ArgDef] = Field(default_factory=list)
    outputs: list[OutputDef] = Field(default_factory=list)
    dangerous: bool = False
    deprecated: bool = False


class ParamDef(BaseModel):
    """An integration configuration parameter."""

    name: str
    display: str = ""
    type: int = 0
    required: bool = False
    default: str | None = None
    is_credential: bool = False
    hidden: bool = False
    options: list[str] | None = None


class IntegrationDef(BaseModel):
    """Full integration definition parsed from YAML."""

    pack: str
    name: str
    display: str = ""
    description: str = ""
    category: str = ""
    py_path: str
    commands: list[CommandDef] = Field(default_factory=list)
    params: list[ParamDef] = Field(default_factory=list)
    credential_params: list[str] = Field(default_factory=list)
