"""Tests for the YAML indexer — parsing, edge cases, real content validation."""

from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped]

from dbot.registry.indexer import (
    _coerce_options,
    _parse_arg,
    _parse_command,
    _parse_output,
    index_content,
    parse_integration_yaml,
)


class TestCoerceOptions:
    def test_none_returns_none(self) -> None:
        assert _coerce_options(None) is None

    def test_string_list_unchanged(self) -> None:
        assert _coerce_options(["a", "b", "c"]) == ["a", "b", "c"]

    def test_boolean_list_coerced(self) -> None:
        result = _coerce_options([True, False])
        assert result == ["True", "False"]

    def test_int_list_coerced(self) -> None:
        result = _coerce_options([1, 2, 3])
        assert result == ["1", "2", "3"]

    def test_mixed_types_coerced(self) -> None:
        result = _coerce_options(["yes", True, 42])
        assert result == ["yes", "True", "42"]

    def test_single_non_list_value(self) -> None:
        result = _coerce_options("single")
        assert result == ["single"]

    def test_empty_list(self) -> None:
        result = _coerce_options([])
        assert result == []


class TestParseArg:
    def test_minimal_arg(self) -> None:
        arg = _parse_arg({"name": "ip"})
        assert arg.name == "ip"
        assert arg.required is False
        assert arg.secret is False
        assert arg.is_array is False

    def test_full_arg(self) -> None:
        arg = _parse_arg(
            {
                "name": "file_hash",
                "description": "The file hash to check",
                "required": True,
                "defaultValue": "abc123",
                "isArray": True,
                "secret": False,
                "predefined": ["md5", "sha1", "sha256"],
            }
        )
        assert arg.name == "file_hash"
        assert arg.required is True
        assert arg.default == "abc123"
        assert arg.is_array is True
        assert arg.options == ["md5", "sha1", "sha256"]

    def test_secret_arg(self) -> None:
        arg = _parse_arg({"name": "apikey", "secret": True})
        assert arg.secret is True

    def test_boolean_predefined(self) -> None:
        arg = _parse_arg({"name": "verbose", "predefined": [True, False]})
        assert arg.options == ["True", "False"]

    def test_none_default_value(self) -> None:
        arg = _parse_arg({"name": "x"})
        assert arg.default is None

    def test_zero_default_value(self) -> None:
        arg = _parse_arg({"name": "x", "defaultValue": 0})
        assert arg.default == "0"

    def test_empty_dict(self) -> None:
        arg = _parse_arg({})
        assert arg.name == ""


class TestParseOutput:
    def test_full_output(self) -> None:
        out = _parse_output(
            {
                "contextPath": "VirusTotal.File.MD5",
                "description": "The MD5 hash",
                "type": "String",
            }
        )
        assert out.context_path == "VirusTotal.File.MD5"
        assert out.type == "String"

    def test_int_type_coerced(self) -> None:
        out = _parse_output({"contextPath": "X", "type": 18})
        assert out.type == "18"

    def test_missing_fields(self) -> None:
        out = _parse_output({})
        assert out.context_path == ""
        assert out.type == "Unknown"


class TestParseCommand:
    def test_basic_command(self) -> None:
        cmd = _parse_command(
            {
                "name": "vt-get-file",
                "description": "Check file hash",
                "arguments": [{"name": "file", "required": True}],
                "outputs": [{"contextPath": "VT.File", "type": "String"}],
            }
        )
        assert cmd.name == "vt-get-file"
        assert len(cmd.args) == 1
        assert len(cmd.outputs) == 1
        assert cmd.dangerous is False

    def test_dangerous_command(self) -> None:
        cmd = _parse_command({"name": "isolate-host", "execution": True})
        assert cmd.dangerous is True

    def test_deprecated_command(self) -> None:
        cmd = _parse_command({"name": "old-cmd", "deprecated": True})
        assert cmd.deprecated is True

    def test_none_arguments(self) -> None:
        cmd = _parse_command({"name": "cmd", "arguments": None, "outputs": None})
        assert cmd.args == []
        assert cmd.outputs == []

    def test_non_dict_args_filtered(self) -> None:
        cmd = _parse_command({"name": "cmd", "arguments": ["not-a-dict", {"name": "valid"}]})
        assert len(cmd.args) == 1
        assert cmd.args[0].name == "valid"


class TestParseIntegrationYaml:
    def _write_yaml(self, tmp_path: Path, name: str, data: dict) -> Path:
        pack_dir = tmp_path / "Packs" / "TestPack" / "Integrations" / name
        pack_dir.mkdir(parents=True)
        yml = pack_dir / f"{name}.yml"
        yml.write_text(yaml.dump(data))
        (pack_dir / f"{name}.py").write_text("def main(): pass")
        return yml

    def test_basic_integration(self, tmp_path: Path) -> None:
        yml = self._write_yaml(
            tmp_path,
            "TestInt",
            {
                "name": "TestInt",
                "display": "Test Integration",
                "description": "A test",
                "category": "Utilities",
                "configuration": [
                    {"name": "url", "type": 0, "required": True},
                    {"name": "apikey", "type": 9, "required": True},
                ],
                "script": {
                    "type": "python",
                    "commands": [
                        {"name": "test-cmd", "description": "Do a thing", "arguments": [{"name": "arg1"}]},
                    ],
                },
            },
        )
        result = parse_integration_yaml(yml)
        assert result is not None
        assert result.pack == "TestPack"
        assert result.name == "TestInt"
        assert len(result.commands) == 1
        assert result.commands[0].name == "test-cmd"
        assert "apikey" in result.credential_params

    def test_no_script_returns_none(self, tmp_path: Path) -> None:
        yml = self._write_yaml(tmp_path, "NoScript", {"name": "NoScript"})
        assert parse_integration_yaml(yml) is None

    def test_no_commands_returns_valid_but_empty(self, tmp_path: Path) -> None:
        yml = self._write_yaml(
            tmp_path,
            "NoCmd",
            {
                "name": "NoCmd",
                "script": {"type": "python", "commands": []},
            },
        )
        result = parse_integration_yaml(yml)
        assert result is not None
        assert result.commands == []

    def test_dangerous_command_flagged(self, tmp_path: Path) -> None:
        yml = self._write_yaml(
            tmp_path,
            "DangerInt",
            {
                "name": "DangerInt",
                "script": {
                    "commands": [
                        {"name": "safe-cmd", "execution": False},
                        {"name": "nuke-it", "execution": True},
                    ],
                },
            },
        )
        result = parse_integration_yaml(yml)
        assert result is not None
        assert result.commands[0].dangerous is False
        assert result.commands[1].dangerous is True

    def test_credential_params_detected(self, tmp_path: Path) -> None:
        yml = self._write_yaml(
            tmp_path,
            "CredInt",
            {
                "name": "CredInt",
                "configuration": [
                    {"name": "url", "type": 0},
                    {"name": "credentials", "type": 9},
                    {"name": "token", "type": 9},
                ],
                "script": {"commands": [{"name": "cmd"}]},
            },
        )
        result = parse_integration_yaml(yml)
        assert result is not None
        assert set(result.credential_params) == {"credentials", "token"}

    def test_malformed_yaml_returns_none(self, tmp_path: Path) -> None:
        pack_dir = tmp_path / "Packs" / "Bad" / "Integrations" / "Bad"
        pack_dir.mkdir(parents=True)
        yml = pack_dir / "Bad.yml"
        yml.write_text(": : : invalid yaml {{{}}")
        assert parse_integration_yaml(yml) is None


class TestIndexContent:
    def _setup_packs(self, tmp_path: Path) -> Path:
        for pack_name, cmd_names in [("PackA", ["cmd-a1", "cmd-a2"]), ("PackB", ["cmd-b1"])]:
            pack_dir = tmp_path / "Packs" / pack_name / "Integrations" / pack_name
            pack_dir.mkdir(parents=True)
            (pack_dir / f"{pack_name}.py").write_text("def main(): pass")
            data = {
                "name": pack_name,
                "script": {"commands": [{"name": n} for n in cmd_names]},
            }
            (pack_dir / f"{pack_name}.yml").write_text(yaml.dump(data))
        return tmp_path

    def test_indexes_all_packs(self, tmp_path: Path) -> None:
        root = self._setup_packs(tmp_path)
        integrations = index_content(root)
        assert len(integrations) == 2
        total_cmds = sum(len(i.commands) for i in integrations)
        assert total_cmds == 3

    def test_filters_by_enabled_packs(self, tmp_path: Path) -> None:
        root = self._setup_packs(tmp_path)
        integrations = index_content(root, enabled_packs=["PackA"])
        assert len(integrations) == 1
        assert integrations[0].pack == "PackA"

    def test_nonexistent_packs_dir(self, tmp_path: Path) -> None:
        result = index_content(tmp_path / "nonexistent")
        assert result == []

    def test_skips_integrations_with_no_commands(self, tmp_path: Path) -> None:
        pack_dir = tmp_path / "Packs" / "Empty" / "Integrations" / "Empty"
        pack_dir.mkdir(parents=True)
        (pack_dir / "Empty.py").write_text("def main(): pass")
        data = {"name": "Empty", "script": {"commands": []}}
        (pack_dir / "Empty.yml").write_text(yaml.dump(data))
        result = index_content(tmp_path)
        assert len(result) == 0


class TestIndexRealContent:
    """Tests against the actual demisto/content submodule."""

    CONTENT_ROOT = Path(__file__).parent.parent / "content"

    @pytest.fixture(autouse=True)
    def _skip_if_no_content(self) -> None:
        if not (self.CONTENT_ROOT / "Packs").exists():
            pytest.skip("content submodule not initialized")

    def test_helloworld_parses(self) -> None:
        yml = self.CONTENT_ROOT / "Packs" / "HelloWorld" / "Integrations" / "HelloWorld" / "HelloWorld.yml"
        result = parse_integration_yaml(yml)
        assert result is not None
        assert result.pack == "HelloWorld"
        assert len(result.commands) > 0
        cmd_names = [c.name for c in result.commands]
        assert "helloworld-say-hello" in cmd_names

    def test_virustotal_has_credential_params(self) -> None:
        vt_dir = self.CONTENT_ROOT / "Packs" / "VirusTotal" / "Integrations"
        ymls = list(vt_dir.glob("*/*.yml"))
        if not ymls:
            pytest.skip("VirusTotal not in sparse checkout")
        result = parse_integration_yaml(ymls[0])
        assert result is not None
        assert len(result.credential_params) > 0

    def test_full_index_succeeds(self) -> None:
        integrations = index_content(self.CONTENT_ROOT)
        assert len(integrations) > 5
        total_cmds = sum(len(i.commands) for i in integrations)
        assert total_cmds > 50
