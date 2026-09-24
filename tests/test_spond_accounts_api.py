# tests/test_spond_accounts_api.py
import pytest


@pytest.mark.asyncio
async def test_list_spond_accounts(admin_client):
    resp = await admin_client.get("/api/v1/spond-accounts")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_old_users_route_removed(admin_client):
    # The catch-all SPA route returns 200 HTML for unknown paths.
    # Verify this path is no longer a JSON API endpoint.
    resp = await admin_client.get("/api/v1/users")
    assert resp.headers.get("content-type", "").startswith("application/json") is False
