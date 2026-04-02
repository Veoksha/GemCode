"""Session keys for human-in-the-loop (HITL) tool approval."""

# When True in ADK session state, mutating/shell tools skip request_confirmation
# (after the user approved at least once this session, if sticky mode is on).
HITL_STICKY_SESSION_KEY = "gemcode:hitl_approved_session"
