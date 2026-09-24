# tests/test_accounts_api.py
import uuid
import pytest


@pytest.mark.asyncio
async def test_list_accounts(admin_client):
    resp = await admin_client.get("/api/v1/accounts")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_create_and_delete_account(admin_client):
    resp = await admin_client.post(
        "/api/v1/accounts",
        json={"username": "newuser", "password": "pass1234", "is_admin": False, "linked_user_id": None},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["username"] == "newuser"
    assert data["is_admin"] is False

    account_id = data["id"]
    resp = await admin_client.delete(f"/api/v1/accounts/{account_id}")
    assert resp.status_code == 204

    resp = await admin_client.delete(f"/api/v1/accounts/{account_id}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_duplicate_username_rejected(admin_client):
    await admin_client.post(
        "/api/v1/accounts",
        json={"username": "dupeuser", "password": "pass1234", "is_admin": False, "linked_user_id": None},
    )
    resp = await admin_client.post(
        "/api/v1/accounts",
        json={"username": "dupeuser", "password": "pass1234", "is_admin": False, "linked_user_id": None},
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_old_auth_users_route_removed(admin_client):
    # The catch-all SPA route returns 200 HTML for unknown paths.
    # Verify this path is no longer a JSON API endpoint.
    resp = await admin_client.get("/api/v1/auth/users")
    assert resp.headers.get("content-type", "").startswith("application/json") is False
