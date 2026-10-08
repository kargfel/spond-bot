# The Spond client against a real local HTTP server: what we send, and how every kind of answer is
# turned into the exceptions the executioner's retry rules depend on.
import json
from datetime import timezone

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from app.core import spond_client
from app.core.spond_client import SpondAPIError, SpondAuthError


class FakeSpond:
    """Records requests; `routes` maps (method, path) to (status, json-or-text)."""

    def __init__(self):
        self.routes = {}
        self.seen = []
        self.server = None

    async def handler(self, request: web.Request):
        body = await request.text()
        self.seen.append({"method": request.method, "path": request.path, "query": dict(request.query),
                          "headers": {k.lower(): v for k, v in request.headers.items()}, "body": json.loads(body) if body else None})
        status, payload = self.routes.get((request.method, request.path), (404, {"error": "no route"}))
        if isinstance(payload, str):
            return web.Response(status=status, text=payload)
        return web.json_response(payload, status=status)


@pytest.fixture
async def spond(monkeypatch):
    fake = FakeSpond()
    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", fake.handler)
    server = TestServer(app)
    await server.start_server()
    monkeypatch.setattr(spond_client, "_API_BASE", str(server.make_url("/core/v1/")))
    fake.server = server
    yield fake
    await server.close()


@pytest.fixture
async def http():
    async with aiohttp.ClientSession() as session:
        yield session


# --- login -------------------------------------------------------------------

@pytest.mark.asyncio
async def test_login_with_email_uses_the_auth2_endpoint_and_passes_the_token_unchanged(spond, http):
    spond.routes[("POST", "/core/v1/auth2/login")] = (200, {"accessToken": {"token": "AbC+/=raw"}})
    token, acquired = await spond_client.login(http, "m@example.com", "pw")
    assert token == "AbC+/=raw"                       # never base64-decoded
    assert acquired.tzinfo == timezone.utc
    assert spond.seen[0]["body"] == {"email": "m@example.com", "password": "pw"}


@pytest.mark.asyncio
async def test_login_with_phone_number_sends_phone_number(spond, http):
    spond.routes[("POST", "/core/v1/auth2/login")] = (200, {"accessToken": {"token": "t"}})
    await spond_client.login(http, "+4915112345678", "pw")
    assert spond.seen[0]["body"] == {"phoneNumber": "+4915112345678", "password": "pw"}


@pytest.mark.asyncio
async def test_login_still_understands_the_old_login_token_field(spond, http):
    spond.routes[("POST", "/core/v1/auth2/login")] = (200, {"loginToken": "legacy"})
    assert (await spond_client.login(http, "a@b.c", "pw"))[0] == "legacy"


@pytest.mark.asyncio
async def test_login_without_a_token_is_an_auth_error(spond, http):
    spond.routes[("POST", "/core/v1/auth2/login")] = (200, {"error": "bad credentials"})
    with pytest.raises(SpondAuthError):
        await spond_client.login(http, "a@b.c", "wrong")


# --- reading -----------------------------------------------------------------

@pytest.mark.asyncio
async def test_requests_carry_bearer_token_and_a_mobile_user_agent(spond, http):
    spond.routes[("GET", "/core/v1/profile")] = (200, {"id": "PROFILE1"})
    assert await spond_client.get_profile_id(http, "tok") == "PROFILE1"
    headers = spond.seen[0]["headers"]
    assert headers["authorization"] == "Bearer tok"
    assert headers["user-agent"].startswith("Spond-iOS/")


@pytest.mark.asyncio
async def test_profile_errors(spond, http):
    spond.routes[("GET", "/core/v1/profile")] = (401, {})
    with pytest.raises(SpondAuthError):
        await spond_client.get_profile_id(http, "t")
    spond.routes[("GET", "/core/v1/profile")] = (500, "boom")
    with pytest.raises(SpondAPIError) as err:
        await spond_client.get_profile_id(http, "t")
    assert err.value.status == 500
    spond.routes[("GET", "/core/v1/profile")] = (200, {"name": "no id"})
    with pytest.raises(SpondAPIError):
        await spond_client.get_profile_id(http, "t")


@pytest.mark.asyncio
async def test_upcoming_events_sends_filters_and_maps_errors(spond, http):
    from datetime import datetime
    spond.routes[("GET", "/core/v1/sponds/upcoming")] = (200, [{"id": "e1", "heading": "H"}])
    out = await spond_client.get_upcoming_events(http, "t", min_end_ts=datetime(2026, 10, 8, 12, 30, tzinfo=timezone.utc))
    assert out == [{"id": "e1", "heading": "H"}]
    assert spond.seen[0]["query"] == {"includeDeclined": "true", "minEndTimestamp": "2026-10-08T12:30:00.000Z"}
    spond.routes[("GET", "/core/v1/sponds/upcoming")] = (401, {})
    with pytest.raises(SpondAuthError):
        await spond_client.get_upcoming_events(http, "t")
    spond.routes[("GET", "/core/v1/sponds/upcoming")] = (502, "bad gateway")
    with pytest.raises(SpondAPIError) as err:
        await spond_client.get_upcoming_events(http, "t")
    assert err.value.status == 502


@pytest.mark.asyncio
async def test_bulk_events_joins_ids_and_skips_the_call_for_none(spond, http):
    assert await spond_client.get_bulk_events(http, "t", []) == []
    assert spond.seen == []
    spond.routes[("GET", "/core/v1/sponds/getBulk")] = (200, [{"id": "a"}, {"id": "b"}])
    assert len(await spond_client.get_bulk_events(http, "t", ["a", "b"])) == 2
    assert spond.seen[0]["query"] == {"ids": "a,b"}
    spond.routes[("GET", "/core/v1/sponds/getBulk")] = (401, {})
    with pytest.raises(SpondAuthError):
        await spond_client.get_bulk_events(http, "t", ["a"])


# --- RSVP: the status code decides what the executioner does next --------------

@pytest.mark.asyncio
@pytest.mark.parametrize("accepted", [True, False])
async def test_rsvp_puts_the_answer_for_the_member(spond, http, accepted):
    spond.routes[("PUT", "/core/v1/sponds/EV1/responses/MEM1")] = (200, {})
    await spond_client.rsvp(http, "tok", "EV1", "MEM1", accepted)
    sent = spond.seen[0]
    assert (sent["method"], sent["path"], sent["body"]) == ("PUT", "/core/v1/sponds/EV1/responses/MEM1", {"accepted": accepted})


@pytest.mark.asyncio
async def test_rsvp_accepts_204(spond, http):
    spond.routes[("PUT", "/core/v1/sponds/EV1/responses/MEM1")] = (204, "")
    await spond_client.rsvp(http, "tok", "EV1", "MEM1", True)


@pytest.mark.asyncio
async def test_rsvp_401_is_an_auth_error(spond, http):
    spond.routes[("PUT", "/core/v1/sponds/EV1/responses/MEM1")] = (401, {})
    with pytest.raises(SpondAuthError):
        await spond_client.rsvp(http, "tok", "EV1", "MEM1", True)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 403, 404, 429, 500, 503])
async def test_rsvp_other_failures_carry_their_status_and_body(spond, http, status):
    spond.routes[("PUT", "/core/v1/sponds/EV1/responses/MEM1")] = (status, "member not found")
    with pytest.raises(SpondAPIError) as err:
        await spond_client.rsvp(http, "tok", "EV1", "MEM1", True)
    assert err.value.status == status
    assert "member not found" in str(err.value)


@pytest.mark.asyncio
async def test_rsvp_to_a_dead_server_raises_a_connection_error():
    async with aiohttp.ClientSession() as session:
        spond_client._API_BASE, saved = "http://127.0.0.1:9/core/v1/", spond_client._API_BASE
        try:
            with pytest.raises(aiohttp.ClientError):
                await spond_client.rsvp(session, "t", "E", "M", True)
        finally:
            spond_client._API_BASE = saved


# --- member ID lookup ----------------------------------------------------------

def groups_payload(*members, group_id="G1"):
    return [{"id": "OTHER", "members": [{"id": "WRONG", "profile": {"id": "P"}}]},
            {"id": group_id, "members": list(members)}]


EVENT = {"recipients": {"group": {"id": "G1"}}}


@pytest.mark.asyncio
async def test_member_id_is_found_by_profile_id_in_the_events_group(spond, http):
    spond.routes[("GET", "/core/v1/groups")] = (200, groups_payload(
        {"id": "M-OTHER", "profile": {"id": "someone-else"}}, {"id": "M-ME", "profile": {"id": "P"}}))
    assert await spond_client.resolve_recipient_id(http, "t", EVENT, "me@x.de", "P") == "M-ME"


@pytest.mark.asyncio
async def test_member_id_falls_back_to_email_then_phone(spond, http):
    spond.routes[("GET", "/core/v1/groups")] = (200, groups_payload(
        {"id": "M-1", "email": "Me@X.de", "profile": {}}, {"id": "M-2", "profile": {"phoneNumber": "+49151"}}))
    assert await spond_client.resolve_recipient_id(http, "t", EVENT, " me@x.DE ", "NOPE") == "M-1"
    assert await spond_client.resolve_recipient_id(http, "t", EVENT, "+49151", "NOPE") == "M-2"


@pytest.mark.asyncio
async def test_member_id_is_not_taken_from_another_group(spond, http):
    spond.routes[("GET", "/core/v1/groups")] = (200, groups_payload({"id": "M-X", "profile": {"id": "nobody"}}))
    # profile P exists only in group OTHER (as WRONG): must not be used for G1
    assert await spond_client.resolve_recipient_id(http, "t", EVENT, "me@x.de", "P") == "P"


@pytest.mark.asyncio
async def test_member_id_falls_back_to_profile_id_when_nothing_can_be_resolved(spond, http):
    assert await spond_client.resolve_recipient_id(http, "t", {"recipients": {}}, "a@b.c", "P") == "P"   # direct invite
    assert spond.seen == []                                                                              # no lookup needed
    spond.routes[("GET", "/core/v1/groups")] = (500, "down")
    assert await spond_client.resolve_recipient_id(http, "t", EVENT, "a@b.c", "P") == "P"
    spond.routes[("GET", "/core/v1/groups")] = (200, [{"id": "ELSEWHERE", "members": []}])
    assert await spond_client.resolve_recipient_id(http, "t", EVENT, "a@b.c", "P") == "P"


# --- timestamps ----------------------------------------------------------------

def test_event_timestamps_are_parsed_to_utc_and_bad_values_become_none():
    out = spond_client.parse_event_timestamps({
        "startTimestamp": "2026-10-08T18:15:00.000Z", "inviteTime": "2026-10-01T14:00:00.000Z", "rsvpDate": "garbage"})
    assert out["start_timestamp"].isoformat() == "2026-10-08T18:15:00+00:00"
    assert out["invite_time"].isoformat() == "2026-10-01T14:00:00+00:00"
    assert out["rsvp_date"] is None
    assert spond_client.parse_event_timestamps({}) == {"start_timestamp": None, "invite_time": None, "rsvp_date": None}
