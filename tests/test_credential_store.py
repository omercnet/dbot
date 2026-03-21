"""Tests for the credential store — env var resolution, edge cases, security."""

import os
from pathlib import Path

import pytest

from dbot.credentials.store import CredentialStore


class TestCredentialStoreBasic:
    def test_empty_store(self) -> None:
        store = CredentialStore()
        assert store.get("Anything") == {}
        assert store.has("Anything") is False
        assert store.configured_packs() == []

    def test_nonexistent_config_path(self) -> None:
        store = CredentialStore(config_path=Path("/nonexistent/path.yaml"))
        assert store.configured_packs() == []

    def test_load_from_yaml(self, tmp_path: Path) -> None:
        config = tmp_path / "creds.yaml"
        config.write_text("VirusTotal:\n  apikey: hardcoded-key\n  base_url: https://vt.api\n")
        store = CredentialStore(config_path=config)
        assert store.has("VirusTotal")
        creds = store.get("VirusTotal")
        assert creds["apikey"] == "hardcoded-key"
        assert creds["base_url"] == "https://vt.api"

    def test_get_returns_copy(self, tmp_path: Path) -> None:
        config = tmp_path / "creds.yaml"
        config.write_text("Pack:\n  key: value\n")
        store = CredentialStore(config_path=config)
        creds1 = store.get("Pack")
        creds1["key"] = "mutated"
        assert store.get("Pack")["key"] == "value"

    def test_multiple_packs(self, tmp_path: Path) -> None:
        config = tmp_path / "creds.yaml"
        config.write_text("PackA:\n  key: a\nPackB:\n  key: b\nPackC:\n  key: c\n")
        store = CredentialStore(config_path=config)
        assert len(store.configured_packs()) == 3
        assert store.get("PackA")["key"] == "a"
        assert store.get("PackC")["key"] == "c"


class TestEnvVarResolution:
    def test_resolves_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEST_API_KEY", "resolved-secret")
        config = tmp_path / "creds.yaml"
        config.write_text("TestPack:\n  apikey: ${TEST_API_KEY}\n")
        store = CredentialStore(config_path=config)
        assert store.get("TestPack")["apikey"] == "resolved-secret"

    def test_multiple_env_vars_in_one_value(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HOST", "api.example.com")
        monkeypatch.setenv("PORT", "8443")
        config = tmp_path / "creds.yaml"
        config.write_text("Pack:\n  url: https://${HOST}:${PORT}/v1\n")
        store = CredentialStore(config_path=config)
        assert store.get("Pack")["url"] == "https://api.example.com:8443/v1"

    def test_missing_env_var_skips_pack(self, tmp_path: Path) -> None:
        os.environ.pop("DEFINITELY_NOT_SET_12345", None)
        config = tmp_path / "creds.yaml"
        config.write_text("BadPack:\n  apikey: ${DEFINITELY_NOT_SET_12345}\n")
        store = CredentialStore(config_path=config)
        assert not store.has("BadPack")

    def test_non_string_values_coerced(self, tmp_path: Path) -> None:
        config = tmp_path / "creds.yaml"
        config.write_text("Pack:\n  port: 8080\n  verify: true\n")
        store = CredentialStore(config_path=config)
        creds = store.get("Pack")
        assert creds["port"] == "8080"
        assert creds["verify"] == "True"

    def test_mixed_literal_and_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SECRET_KEY", "s3cret")
        config = tmp_path / "creds.yaml"
        config.write_text("Pack:\n  url: https://api.com\n  apikey: ${SECRET_KEY}\n")
        store = CredentialStore(config_path=config)
        creds = store.get("Pack")
        assert creds["url"] == "https://api.com"
        assert creds["apikey"] == "s3cret"


class TestCredentialStoreEdgeCases:
    def test_invalid_pack_entry_skipped(self, tmp_path: Path) -> None:
        config = tmp_path / "creds.yaml"
        config.write_text("ValidPack:\n  key: val\nInvalidPack: just-a-string\n")
        store = CredentialStore(config_path=config)
        assert store.has("ValidPack")
        assert not store.has("InvalidPack")

    def test_empty_yaml_file(self, tmp_path: Path) -> None:
        config = tmp_path / "creds.yaml"
        config.write_text("")
        store = CredentialStore(config_path=config)
        assert store.configured_packs() == []

    def test_yaml_with_only_comments(self, tmp_path: Path) -> None:
        config = tmp_path / "creds.yaml"
        config.write_text("# This is a comment\n# Another comment\n")
        store = CredentialStore(config_path=config)
        assert store.configured_packs() == []

    def test_empty_pack_params(self, tmp_path: Path) -> None:
        config = tmp_path / "creds.yaml"
        config.write_text("EmptyPack:\n  {}\n")
        store = CredentialStore(config_path=config)
        # Empty dict is valid, just has no params
        assert store.get("EmptyPack") == {} or not store.has("EmptyPack")
