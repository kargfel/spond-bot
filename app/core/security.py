"""
Fernet symmetric encryption helpers and bcrypt password helpers.

encrypt() / decrypt() are the only functions that should touch plaintext
credentials anywhere in the codebase. The key is loaded once at startup
from the FERNET_KEY environment variable.

Key generation (run once, store in .env):
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""
import hashlib

import bcrypt
from cryptography.fernet import Fernet

from app.config import settings

_cipher = Fernet(
    settings.fernet_key.encode()
    if isinstance(settings.fernet_key, str)
    else settings.fernet_key
)


def encrypt(plaintext: str) -> str:
    """Encrypt a UTF-8 string and return a URL-safe base64 ciphertext string."""
    return _cipher.encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    """Decrypt a ciphertext string previously produced by encrypt()."""
    return _cipher.decrypt(ciphertext.encode()).decode()


def _bcrypt_input(plain: str) -> bytes:
    """bcrypt only looks at the first 72 *bytes*; umlauts and emoji take 2 to 4 each, so cut bytes, not characters."""
    return plain.encode("utf-8")[:72]


def hash_password(plain: str) -> str:
    """Hash a password with bcrypt. Truncates to 72 bytes (bcrypt limit)."""
    return bcrypt.hashpw(_bcrypt_input(plain), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a plaintext password against a bcrypt hash."""
    return bcrypt.checkpw(_bcrypt_input(plain), hashed.encode("utf-8"))


def password_version(hashed_password: str) -> str:
    """A short fingerprint of the current password hash. Sessions carry it, so changing the password ends them."""
    return hashlib.sha256(hashed_password.encode("utf-8")).hexdigest()[:16]
