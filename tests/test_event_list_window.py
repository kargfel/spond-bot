# GET /events slices: the admin queue loads a window around today, "past" arrives in pages.
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models.event import Event
from app.models.user import User

NOW = datetime(2026, 10, 8, 17, 0, tzinfo=timezone.utc)
D = timedelta(days=1)


@pytest.fixture
async def world(test_db):
    mara = User(id=uuid.uuid4(), display_name="Mara", login="m@example.com", encrypted_password="x", profile_id="P1")
    jonas = User(id=uuid.uuid4(), display_name="Jonas", login="j@example.com", encrypted_password="x", profile_id="P2")
    test_db.add_all([mara, jonas])

    def ev(name, owner, start, invite=None, status="pending", choice="manual"):
        e = Event(id=uuid.uuid4(), spond_event_id=name, user_id=owner.id, heading=name, user_choice=choice, status=status,
                  start_timestamp=start, invite_time=invite if invite is not None else (start - 7 * D if start else None))
        test_db.add(e)
        return e

    events = {
        "ancient": ev("ancient", mara, NOW - 126 * D),
        "last-month": ev("last-month", mara, NOW - 30 * D),
        "yesterday": ev("yesterday", mara, NOW - D),
        "tonight": ev("tonight", mara, NOW + timedelta(hours=3)),
        "next-week": ev("next-week", mara, NOW + 7 * D),
        "next-quarter": ev("next-quarter", mara, NOW + 90 * D),
        "no-start": ev("no-start", mara, None, invite=NOW + D),
        "jonas-next": ev("jonas-next", jonas, NOW + 2 * D),
    }
    await test_db.commit()
    return events


def iso(dt):
    return dt.isoformat().replace("+00:00", "Z")


async def names(client, **params):
    resp = await client.get("/api/v1/events", params={"all": "true", **params})
    assert resp.status_code == 200, resp.text
    return [e["heading"] for e in resp.json()]


@pytest.mark.asyncio
async def test_without_parameters_everything_is_returned_as_before(admin_client, world):
    assert sorted(await names(admin_client)) == sorted(world)


@pytest.mark.asyncio
async def test_the_admin_window_is_two_days_back_to_two_months_ahead(admin_client, world):
    got = await names(admin_client, start_from=iso(NOW - 2 * D), start_to=iso(NOW + 60 * D), order="start")
    assert got == ["yesterday", "tonight", "jonas-next", "next-week"]      # no ancient, no next quarter, no start-less


@pytest.mark.asyncio
async def test_upcoming_has_no_upper_bound_and_keeps_events_without_a_start_time(admin_client, world):
    got = await names(admin_client, start_from=iso(NOW), order="start")
    assert got == ["tonight", "jonas-next", "next-week", "next-quarter", "no-start"]


@pytest.mark.asyncio
async def test_past_comes_newest_first_in_pages(admin_client, world):
    first = await names(admin_client, start_to=iso(NOW), order="-start", limit=2)
    assert first == ["yesterday", "last-month"]
    second = await names(admin_client, start_to=iso(NOW), order="-start", limit=2, offset=2)
    assert second == ["ancient"]                                            # a short page means: no more
    assert await names(admin_client, start_to=iso(NOW), order="-start", limit=2, offset=4) == []


@pytest.mark.asyncio
async def test_a_bounded_past_range_and_the_failed_extra_request(admin_client, world, test_db):
    world["last-month"].status = "failed"
    await test_db.commit()
    got = await names(admin_client, start_from=iso(NOW - 40 * D), start_to=iso(NOW - 2 * D), status="failed")
    assert got == ["last-month"]


@pytest.mark.asyncio
async def test_times_without_an_offset_mean_utc(admin_client, world):
    naive = (NOW - 2 * D).replace(tzinfo=None).isoformat()
    got = await names(admin_client, start_from=naive, start_to=iso(NOW + 60 * D), order="start")
    assert got[0] == "yesterday"


@pytest.mark.asyncio
async def test_slices_combine_with_the_account_filter(admin_client, world):
    jonas_id = world["jonas-next"].user_id
    assert await names(admin_client, user_id=str(jonas_id), start_from=iso(NOW)) == ["jonas-next"]


@pytest.mark.asyncio
async def test_a_member_only_ever_gets_their_own_events_whatever_they_ask_for(client_as, world):
    claims = {"sub": str(uuid.uuid4()), "username": "jonas", "is_admin": False,
              "linked_user_id": str(world["jonas-next"].user_id)}
    async with client_as(claims) as client:
        resp = await client.get("/api/v1/events", params={"all": "true", "start_from": iso(NOW - 200 * D)})
        assert [e["heading"] for e in resp.json()] == ["jonas-next"]


@pytest.mark.asyncio
@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 5000}, {"offset": -1}, {"order": "random"}, {"start_from": "yesterday"}])
async def test_bad_slices_are_rejected(admin_client, world, params):
    resp = await admin_client.get("/api/v1/events", params={"all": "true", **params})
    assert resp.status_code == 422
