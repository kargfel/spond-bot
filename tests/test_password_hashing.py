# tests/test_password_hashing.py — bcrypt reads 72 bytes, not 72 characters. A passphrase with
# umlauts or emoji must never crash sign-in or registration.
import pytest

from app.core.security import hash_password, verify_password


@pytest.mark.parametrize("password", [
    "ä" * 40,                     # 80 bytes
    "😀" * 20,                    # 80 bytes
    "Größenwahn" * 10,            # umlauts mixed in, > 72 bytes
    "a" * 200,
    "über-lange-Passphrase-mit-Umlauten-äöüß-und-noch-mehr-Text-damit-es-lang-wird",
    "short",
    "Paßwort-ß",
])
def test_any_password_can_be_hashed_and_verified(password):
    hashed = hash_password(password)
    assert verify_password(password, hashed)
    assert not verify_password(password[:-1] + "x" if len(password.encode()) < 72 else "different", hashed)


def test_a_wrong_password_with_multibyte_characters_is_rejected_not_an_error():
    hashed = hash_password("ä" * 40)
    assert verify_password("ö" * 40, hashed) is False


def test_the_first_72_bytes_decide_as_bcrypt_always_did():
    long = "x" * 72
    assert verify_password(long + "anything", hash_password(long))


def test_existing_ascii_hashes_still_verify():
    """Hashes made before the fix (plain[:72]) are byte-for-byte what the new code checks."""
    import bcrypt

    old = bcrypt.hashpw(("p" * 90)[:72].encode(), bcrypt.gensalt()).decode()
    assert verify_password("p" * 90, old)
