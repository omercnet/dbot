"""Tests for Fernet encryption module."""

import stat
from pathlib import Path

import pytest
from cryptography.fernet import InvalidToken

from dbot.config.encryption import decrypt_value, encrypt_value, load_or_create_key


class TestKeyManagement:
    def test_generate_key_creates_file(self, tmp_path: Path) -> None:
        key_path = tmp_path / ".dbot-key"
        assert not key_path.exists()
        key = load_or_create_key(key_path)
        assert key_path.exists()
        assert len(key) > 0

    def test_load_existing_key_returns_same(self, tmp_path: Path) -> None:
        key_path = tmp_path / ".dbot-key"
        key1 = load_or_create_key(key_path)
        key2 = load_or_create_key(key_path)
        assert key1 == key2

    def test_key_file_permissions(self, tmp_path: Path) -> None:
        key_path = tmp_path / ".dbot-key"
        load_or_create_key(key_path)
        mode = key_path.stat().st_mode
        assert mode & stat.S_IRUSR  # owner read
        assert mode & stat.S_IWUSR  # owner write
        assert not (mode & stat.S_IRGRP)  # no group read
        assert not (mode & stat.S_IROTH)  # no other read


class TestEncryptDecrypt:
    def test_roundtrip(self, fernet_key: bytes) -> None:
        original = "my-secret-api-key-12345"
        encrypted = encrypt_value(original, fernet_key)
        decrypted = decrypt_value(encrypted, fernet_key)
        assert decrypted == original
        assert encrypted != original

    def test_different_values_different_tokens(self, fernet_key: bytes) -> None:
        t1 = encrypt_value("secret1", fernet_key)
        t2 = encrypt_value("secret2", fernet_key)
        assert t1 != t2

    def test_decrypt_wrong_key_fails(self, tmp_path: Path) -> None:
        key1 = load_or_create_key(tmp_path / "key1")
        key2 = load_or_create_key(tmp_path / "key2")
        encrypted = encrypt_value("secret", key1)
        with pytest.raises(InvalidToken):
            decrypt_value(encrypted, key2)

    def test_decrypt_tampered_token_fails(self, fernet_key: bytes) -> None:
        encrypted = encrypt_value("secret", fernet_key)
        tampered = encrypted[:-5] + "XXXXX"
        with pytest.raises((InvalidToken, Exception)):
            decrypt_value(tampered, fernet_key)

    def test_empty_string_roundtrip(self, fernet_key: bytes) -> None:
        encrypted = encrypt_value("", fernet_key)
        assert decrypt_value(encrypted, fernet_key) == ""

    def test_unicode_roundtrip(self, fernet_key: bytes) -> None:
        original = "пароль-密码-🔑"
        assert decrypt_value(encrypt_value(original, fernet_key), fernet_key) == original
