"""Tests for Pydantic models — validation, defaults, edge cases."""

import pytest
from pydantic import ValidationError

from dbot.registry.models import (
    ArgDef,
    CommandDef,
    IntegrationDef,
    OutputDef,
    ParamDef,
)


class TestArgDef:
    def test_minimal(self) -> None:
        arg = ArgDef(name="ip")
        assert arg.name == "ip"
        assert arg.required is False
        assert arg.secret is False
        assert arg.is_array is False
        assert arg.default is None
        assert arg.options is None

    def test_full(self) -> None:
        arg = ArgDef(
            name="hash",
            description="The hash",
            required=True,
            default="abc",
            is_array=True,
            secret=True,
            options=["md5", "sha256"],
        )
        assert arg.required is True
        assert arg.options == ["md5", "sha256"]

    def test_missing_name_fails(self) -> None:
        with pytest.raises(ValidationError):
            ArgDef()  # type: ignore[call-arg]


class TestOutputDef:
    def test_minimal(self) -> None:
        out = OutputDef(context_path="X.Y.Z")
        assert out.context_path == "X.Y.Z"
        assert out.type == "Unknown"

    def test_full(self) -> None:
        out = OutputDef(context_path="VT.File", description="The file", type="String")
        assert out.type == "String"

    def test_missing_context_path_fails(self) -> None:
        with pytest.raises(ValidationError):
            OutputDef()  # type: ignore[call-arg]


class TestCommandDef:
    def test_defaults(self) -> None:
        cmd = CommandDef(name="test-cmd")
        assert cmd.name == "test-cmd"
        assert cmd.args == []
        assert cmd.outputs == []
        assert cmd.dangerous is False
        assert cmd.deprecated is False

    def test_with_args_and_outputs(self) -> None:
        cmd = CommandDef(
            name="cmd",
            args=[ArgDef(name="a"), ArgDef(name="b")],
            outputs=[OutputDef(context_path="X")],
            dangerous=True,
        )
        assert len(cmd.args) == 2
        assert len(cmd.outputs) == 1
        assert cmd.dangerous is True


class TestParamDef:
    def test_defaults(self) -> None:
        param = ParamDef(name="url")
        assert param.type == 0
        assert param.is_credential is False

    def test_credential_type(self) -> None:
        param = ParamDef(name="apikey", type=9, is_credential=True)
        assert param.is_credential is True

    def test_hidden_param(self) -> None:
        param = ParamDef(name="secret", hidden=True)
        assert param.hidden is True


class TestIntegrationDef:
    def test_minimal(self) -> None:
        i = IntegrationDef(pack="Test", name="TestInt", py_path="/test.py")
        assert i.pack == "Test"
        assert i.commands == []
        assert i.credential_params == []

    def test_full(self) -> None:
        i = IntegrationDef(
            pack="VT",
            name="VirusTotalV3",
            display="VirusTotal",
            description="File reputation",
            category="Threat Intel",
            py_path="/vt.py",
            commands=[CommandDef(name="vt-file")],
            params=[ParamDef(name="apikey", type=9, is_credential=True)],
            credential_params=["apikey"],
        )
        assert len(i.commands) == 1
        assert i.credential_params == ["apikey"]

    def test_missing_required_fields_fails(self) -> None:
        with pytest.raises(ValidationError):
            IntegrationDef()  # type: ignore[call-arg]

    def test_missing_py_path_fails(self) -> None:
        with pytest.raises(ValidationError):
            IntegrationDef(pack="X", name="Y")  # type: ignore[call-arg]
