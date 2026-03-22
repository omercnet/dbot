"""Tests for ConfigDB — SQLite CRUD, encryption, migration."""

from pathlib import Path

import pytest

from dbot.config.db import ConfigDB


class TestConfigDBSchema:
    def test_creates_db_file(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        ConfigDB(db_path, tmp_path / ".key")
        assert db_path.exists()

    def test_schema_tables_exist(self, config_db: ConfigDB) -> None:
        rows = config_db._conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
        names = {r["name"] for r in rows}
        assert "config_sections" in names
        assert "credentials" in names
        assert "migrations" in names


class TestConfigSections:
    def test_get_returns_defaults_when_missing(self, config_db: ConfigDB) -> None:
        data = config_db.get_section("general")
        assert data["execution_mode"] == "inprocess"

    def test_set_and_get_roundtrip(self, config_db: ConfigDB) -> None:
        config_db.set_section(
            "general", {"execution_mode": "subprocess", "audit_log_path": "custom.log", "content_root": ""}
        )
        data = config_db.get_section("general")
        assert data["execution_mode"] == "subprocess"
        assert data["audit_log_path"] == "custom.log"

    def test_set_validates_model(self, config_db: ConfigDB) -> None:
        with pytest.raises((ValueError, Exception)):
            config_db.set_section("general", {"execution_mode": "invalid_mode"})

    def test_get_all_sections_fills_defaults(self, config_db: ConfigDB) -> None:
        all_sections = config_db.get_all_sections()
        assert "general" in all_sections
        assert "llm" in all_sections
        assert "guardrails" in all_sections
        assert "packs" in all_sections

    def test_update_preserves_other_sections(self, config_db: ConfigDB) -> None:
        config_db.set_section(
            "llm", {"default_model": "test-model", "available_models": {}, "temperature": 0.5, "max_tokens": 2048}
        )
        config_db.set_section(
            "general", {"execution_mode": "subprocess", "audit_log_path": "x.log", "content_root": ""}
        )
        llm = config_db.get_section("llm")
        assert llm["default_model"] == "test-model"


class TestCredentials:
    def test_set_and_get_params(self, config_db: ConfigDB) -> None:
        config_db.set_credential("VirusTotal", "apikey", "secret123")
        params = config_db.get_credential_params("VirusTotal")
        assert "apikey" in params

    def test_get_params_returns_no_values(self, config_db: ConfigDB) -> None:
        config_db.set_credential("TestPack", "key", "value")
        params = config_db.get_credential_params("TestPack")
        # Only names, never values
        assert params == ["key"]

    def test_decrypt_roundtrip(self, config_db: ConfigDB) -> None:
        config_db.set_credential("Pack", "secret", "my-api-key")
        decrypted = config_db.get_decrypted_pack("Pack")
        assert decrypted["secret"] == "my-api-key"

    def test_set_pack_credentials_replaces(self, config_db: ConfigDB) -> None:
        config_db.set_pack_credentials("Pack", {"key1": "v1", "key2": "v2"})
        assert set(config_db.get_credential_params("Pack")) == {"key1", "key2"}
        config_db.set_pack_credentials("Pack", {"key3": "v3"})
        assert config_db.get_credential_params("Pack") == ["key3"]

    def test_delete_pack_removes_all(self, config_db: ConfigDB) -> None:
        config_db.set_pack_credentials("Pack", {"a": "1", "b": "2"})
        config_db.delete_pack_credentials("Pack")
        assert config_db.get_credential_params("Pack") == []

    def test_get_all_credential_packs(self, config_db: ConfigDB) -> None:
        config_db.set_credential("PackA", "key", "val")
        config_db.set_credential("PackB", "key", "val")
        packs = config_db.get_all_credential_packs()
        assert "PackA" in packs
        assert "PackB" in packs

    def test_empty_pack_returns_empty(self, config_db: ConfigDB) -> None:
        assert config_db.get_decrypted_pack("Nonexistent") == {}


class TestMigration:
    def test_migration_from_yaml(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEST_KEY", "migrated-value")
        yaml_content = "TestPack:\n  apikey: ${TEST_KEY}\n  url: https://api.test\n"
        (tmp_path / "credentials.yaml").write_text(yaml_content)
        db = ConfigDB(tmp_path / "test.db", tmp_path / ".key")
        decrypted = db.get_decrypted_pack("TestPack")
        assert decrypted["apikey"] == "migrated-value"
        assert decrypted["url"] == "https://api.test"

    def test_migration_skipped_if_done(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KEY", "val")
        (tmp_path / "credentials.yaml").write_text("Pack:\n  k: ${KEY}\n")
        db1 = ConfigDB(tmp_path / "test.db", tmp_path / ".key")
        assert db1.get_decrypted_pack("Pack")["k"] == "val"
        db1.close()
        # Delete yaml, reopen — should NOT fail (migration already done)
        (tmp_path / "credentials.yaml").unlink()
        db2 = ConfigDB(tmp_path / "test.db", tmp_path / ".key")
        assert db2.get_decrypted_pack("Pack")["k"] == "val"

    def test_migration_without_yaml(self, tmp_path: Path) -> None:
        # No credentials.yaml — should not fail
        db = ConfigDB(tmp_path / "test.db", tmp_path / ".key")
        assert db.get_all_credential_packs() == []


class TestLLMMigration:
    def test_llm_yaml_migrates_config(self, tmp_path: Path) -> None:
        yaml_content = """
default_model: anthropic:claude-sonnet-4-5
temperature: 0.7
max_tokens: 2048
available_models:
  Claude: anthropic:claude-sonnet-4-5
  GPT: openai:gpt-4o
"""
        (tmp_path / "llm.yaml").write_text(yaml_content)
        db = ConfigDB(tmp_path / "test.db", tmp_path / ".key")
        llm = db.get_section("llm")
        assert llm["default_model"] == "anthropic:claude-sonnet-4-5"
        assert llm["temperature"] == 0.7
        assert llm["max_tokens"] == 2048
        assert llm["available_models"]["Claude"] == "anthropic:claude-sonnet-4-5"
        assert llm["available_models"]["GPT"] == "openai:gpt-4o"

    def test_llm_yaml_migrates_provider_keys(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEST_OPENAI_KEY", "sk-migrated")
        yaml_content = """
default_model: openai:gpt-4o
providers:
  openai:
    api_key: ${TEST_OPENAI_KEY}
    base_url: https://proxy.example.com/v1
"""
        (tmp_path / "llm.yaml").write_text(yaml_content)
        db = ConfigDB(tmp_path / "test.db", tmp_path / ".key")
        # Key should be encrypted in DB
        assert db.get_provider_key("openai") == "sk-migrated"
        # Base URL should be in LLM config
        llm = db.get_section("llm")
        assert llm["providers"]["openai"]["base_url"] == "https://proxy.example.com/v1"

    def test_llm_yaml_migration_skipped_if_done(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("K", "v")
        (tmp_path / "llm.yaml").write_text("default_model: test\nproviders:\n  openai:\n    api_key: ${K}\n")
        db1 = ConfigDB(tmp_path / "test.db", tmp_path / ".key")
        assert db1.get_section("llm")["default_model"] == "test"
        db1.close()
        # Remove yaml, reopen — migration already done
        (tmp_path / "llm.yaml").unlink()
        db2 = ConfigDB(tmp_path / "test.db", tmp_path / ".key")
        assert db2.get_section("llm")["default_model"] == "test"

    def test_llm_yaml_without_file(self, tmp_path: Path) -> None:
        db = ConfigDB(tmp_path / "test.db", tmp_path / ".key")
        # Should use defaults, not crash
        llm = db.get_section("llm")
        assert llm["default_model"] == "openai:gpt-4o"

    def test_llm_yaml_missing_env_var_skips_key(self, tmp_path: Path) -> None:
        import os

        os.environ.pop("NONEXISTENT_KEY_XYZ", None)
        yaml_content = """
providers:
  openai:
    api_key: ${NONEXISTENT_KEY_XYZ}
    base_url: https://proxy.com
"""
        (tmp_path / "llm.yaml").write_text(yaml_content)
        db = ConfigDB(tmp_path / "test.db", tmp_path / ".key")
        # Key not set (env var missing), but base_url still saved
        assert db.get_provider_key("openai") is None
        llm = db.get_section("llm")
        assert llm["providers"]["openai"]["base_url"] == "https://proxy.com"

    def test_llm_yaml_multiple_providers(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OAI_KEY", "sk-oai")
        monkeypatch.setenv("ANT_KEY", "sk-ant")
        yaml_content = """
providers:
  openai:
    api_key: ${OAI_KEY}
  anthropic:
    api_key: ${ANT_KEY}
    base_url: https://ant-proxy.com
"""
        (tmp_path / "llm.yaml").write_text(yaml_content)
        db = ConfigDB(tmp_path / "test.db", tmp_path / ".key")
        assert db.get_provider_key("openai") == "sk-oai"
        assert db.get_provider_key("anthropic") == "sk-ant"
        llm = db.get_section("llm")
        assert llm["providers"]["anthropic"]["base_url"] == "https://ant-proxy.com"
