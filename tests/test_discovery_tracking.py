def test_last_discovery_at_initially_none():
    import app.workers.discovery as discovery
    # Before any run, the timestamp is None
    assert discovery.last_discovery_at is None

def test_last_discovery_at_is_exported():
    from app.workers.discovery import last_discovery_at
    # just confirming the name is importable
    assert True
