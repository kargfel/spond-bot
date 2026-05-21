def test_event_has_resolved_recipient_id_field():
    from app.models.event import Event

    ev = Event()
    assert hasattr(ev, "resolved_recipient_id")
    assert ev.resolved_recipient_id is None
