"""Simple encryption for MT5 passwords stored in DB.

Uses Fernet symmetric encryption with a properly derived key (PBKDF2-SHA256).
The password is encrypted before storage and decrypted only on the worker.
"""

import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

# Fixed salt — changing this will invalidate all existing encrypted passwords.
# In a full production system, a per-record random salt stored alongside the
# ciphertext would be ideal, but for Fernet compatibility a fixed salt is acceptable.
_SALT = b"pineforge-mt5-credential-salt-v1"


def _get_key(secret: str) -> bytes:
    """Derive a Fernet-compatible key from the app secret using PBKDF2."""
    raw = hashlib.pbkdf2_hmac("sha256", secret.encode(), _SALT, iterations=100_000)
    return base64.urlsafe_b64encode(raw)


def encrypt_password(password: str, secret: str) -> str:
    """Encrypt a password. Returns base64 string."""
    f = Fernet(_get_key(secret))
    return f.encrypt(password.encode()).decode()


def decrypt_password(encrypted: str, secret: str) -> str:
    """Decrypt a password. Raises ValueError on failure (wrong key or corrupted data)."""
    try:
        f = Fernet(_get_key(secret))
        return f.decrypt(encrypted.encode()).decode()
    except InvalidToken:
        logger.error("Failed to decrypt credential — key may have changed or data is corrupted")
        raise ValueError(
            "Unable to decrypt stored credential. The encryption key may have changed. "
            "Please re-link your broker account."
        )
