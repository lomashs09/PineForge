"""Simple encryption for MT5 passwords stored in DB.

Uses Fernet symmetric encryption with the JWT_SECRET_KEY as the key source.
The password is encrypted before storage and decrypted only on the worker.
"""

import base64
import hashlib

from cryptography.fernet import Fernet


def _get_key(secret: str) -> bytes:
    """Derive a Fernet key from the app secret."""
    key = hashlib.sha256(secret.encode()).digest()
    return base64.urlsafe_b64encode(key)


def encrypt_password(password: str, secret: str) -> str:
    """Encrypt a password. Returns base64 string."""
    f = Fernet(_get_key(secret))
    return f.encrypt(password.encode()).decode()


def decrypt_password(encrypted: str, secret: str) -> str:
    """Decrypt a password."""
    f = Fernet(_get_key(secret))
    return f.decrypt(encrypted.encode()).decode()
