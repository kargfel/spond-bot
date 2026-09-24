"""
Fernet symmetric encryption helpers and bcrypt password helpers.

encrypt() / decrypt() are the only functions that should touch plaintext
credentials anywhere in the codebase. The key is loaded once at startup
from the FERNET_KEY environment variable.

Key generation (run once, store in .env):
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""
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


def hash_password(plain: str) -> str:
    """Hash a password with bcrypt. Truncates to 72 bytes (bcrypt limit)."""
    return bcrypt.hashpw(plain[:72].encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a plaintext password against a bcrypt hash."""
    return bcrypt.checkpw(plain[:72].encode("utf-8"), hashed.encode("utf-8"))
