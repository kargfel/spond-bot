def test_admin_stats_schema_importable():
    from app.schemas.stats import AdminStatsResponse
    obj = AdminStatsResponse(
        active_users=3,
        total_events=50,
        events_pending=10,
        events_processed=35,
        events_failed=5,
        last_discovery_at=None,
        recent_failures=[],
        rsvp_p50_ms=None,
        rsvp_p95_ms=None,
        rsvp_sample_count=0,
    )
    assert obj.active_users == 3
    assert obj.events_failed == 5
    assert obj.last_discovery_at is None

def test_admin_stats_recent_failure_schema():
    from app.schemas.stats import AdminStatsResponse, RecentFailure
    import uuid
    from datetime import datetime, timezone
    failure = RecentFailure(
        event_id=uuid.uuid4(),
        user_display_name="Alice",
        heading="Soccer training",
        error_message="Token rejected",
        updated_at=datetime.now(timezone.utc),
    )
    obj = AdminStatsResponse(
        active_users=1,
        total_events=1,
        events_pending=0,
        events_processed=0,
        events_failed=1,
        last_discovery_at=None,
        recent_failures=[failure],
        rsvp_p50_ms=None,
        rsvp_p95_ms=None,
        rsvp_sample_count=0,
    )
    assert len(obj.recent_failures) == 1
    assert obj.recent_failures[0].user_display_name == "Alice"

def test_admin_stats_has_timing_fields():
    from app.schemas.stats import AdminStatsResponse
    import uuid
    obj = AdminStatsResponse(
        active_users=1,
        total_events=1,
        events_pending=0,
        events_processed=1,
        events_failed=0,
        last_discovery_at=None,
        recent_failures=[],
        rsvp_p50_ms=120,
        rsvp_p95_ms=450,
        rsvp_sample_count=10,
    )
    assert obj.rsvp_p50_ms == 120
    assert obj.rsvp_p95_ms == 450
    assert obj.rsvp_sample_count == 10
