# ensure_fresh_token: reuse a young token, log in again when it is old, missing or rejected.
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.core.security import decrypt, encrypt
from app.models.user import User
from app.services import auth


def make_user(*, token="old-token", age_hours=1.0):
    return User(
        id=uuid.uuid4(), display_name="Mara", login="m@example.com", encrypted_password=encrypt("pw"),
        encrypted_access_token=encrypt(token) if token else None,
        token_acquired_at=datetime.now(timezone.utc) - timedelta(hours=age_hours) if token else None,
        profile_id="P",
    )


@pytest.fixture
def login():
    with patch.object(auth.spond_client, "login", new_callable=AsyncMock) as m:
        m.return_value = ("new-token", datetime.now(timezone.utc))
        yield m


@pytest.mark.asyncio
async def test_a_young_token_is_reused_without_any_request(test_db, login):
    user = make_user(age_hours=22.9)
    test_db.add(user)
    await test_db.commit()
    assert await auth.ensure_fresh_token(test_db, user) == "old-token"
    login.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("age", [23.1, 30])
async def test_an_old_token_is_replaced_and_stored_encrypted(test_db, login, age):
    user = make_user(age_hours=age)
    test_db.add(user)
    await test_db.commit()
    assert await auth.ensure_fresh_token(test_db, user) == "new-token"
    login.assert_awaited_once()
    assert login.await_args.args[1:] == ("m@example.com", "pw")      # the stored password, decrypted
    assert user.encrypted_access_token != "new-token" and decrypt(user.encrypted_access_token) == "new-token"
    assert user.token_acquired_at > datetime.now(timezone.utc) - timedelta(minutes=1)


@pytest.mark.asyncio
async def test_a_missing_token_triggers_a_login(test_db, login):
    user = make_user(token=None)
    test_db.add(user)
    await test_db.commit()
    assert await auth.ensure_fresh_token(test_db, user) == "new-token"


@pytest.mark.asyncio
async def test_force_logs_in_even_with_a_young_token(test_db, login):
    user = make_user(age_hours=0.1)
    test_db.add(user)
    await test_db.commit()
    assert await auth.ensure_fresh_token(test_db, user, force=True) == "new-token"


@pytest.mark.asyncio
async def test_a_rejected_login_keeps_the_old_token_and_raises(test_db, login):
    from app.core.spond_client import SpondAuthError
    login.side_effect = SpondAuthError("bad password")
    user = make_user(age_hours=30)
    test_db.add(user)
    await test_db.commit()
    with pytest.raises(SpondAuthError):
        await auth.ensure_fresh_token(test_db, user)
    assert decrypt(user.encrypted_access_token) == "old-token"
