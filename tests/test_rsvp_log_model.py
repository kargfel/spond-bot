def test_rsvp_log_model_importable():
    from app.models.rsvp_log import RsvpLog, OUTCOME_SUCCESS, OUTCOME_RETRY_SUCCESS, OUTCOME_FAILED
    assert RsvpLog.__tablename__ == "rsvp_log"
    assert OUTCOME_SUCCESS == "success"
    assert OUTCOME_RETRY_SUCCESS == "retry_success"
    assert OUTCOME_FAILED == "failed"
