"""Fernet key management — generate, load, encrypt, decrypt."""

import logging
import os
import stat
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger("dbot.config.encryption")

# Re-export for consumers
__all__ = ["EncryptionError", "InvalidToken", "decrypt_value", "encrypt_value", "load_or_create_key"]


class EncryptionError(Exception):
    """Raised on encryption/decryption failures."""


def load_or_create_key(key_path: Path) -> bytes:
    """Load Fernet key from disk, or generate and save a new one.

    The key file is created with mode 0o600 (owner read/write only).
    """
    if key_path.exists():
        key = key_path.read_bytes().strip()
        logger.debug("Loaded encryption key from %s", key_path)
        return key

    key = Fernet.generate_key()
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_bytes(key)

    # Set restrictive permissions (owner only)
    try:
        os.chmod(key_path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        logger.warning("Could not set key file permissions on %s", key_path)

    logger.info("Generated new encryption key at %s", key_path)
    return key


def encrypt_value(value: str, key: bytes) -> str:
    """Encrypt a string value. Returns URL-safe base64 Fernet token as string."""
    f = Fernet(key)
    token = f.encrypt(value.encode("utf-8"))
    return token.decode("ascii")


def decrypt_value(token: str, key: bytes) -> str:
    """Decrypt a Fernet token string. Raises InvalidToken on wrong key or tampering."""
    f = Fernet(key)
    plaintext = f.decrypt(token.encode("ascii"))
    return plaintext.decode("utf-8")
