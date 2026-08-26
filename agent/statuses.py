from __future__ import annotations

PROJECT_REMOTE_ACTIVE_STATUSES = frozenset(
    {
        "submitting",
        "submission_unknown",
        "generating",
        "running",
        "connection_required",
        "cancel_requested",
    }
)

JOB_TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled", "expired"})
