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
    )
    assert len(obj.recent_failures) == 1
    assert obj.recent_failures[0].user_display_name == "Alice"
