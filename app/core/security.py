"""
Fernet symmetric encryption helpers.

encrypt() / decrypt() are the only functions that should touch plaintext
credentials anywhere in the codebase. The key is loaded once at startup
from the FERNET_KEY environment variable.

Key generation (run once, store in .env):
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""
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
