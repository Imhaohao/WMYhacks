"""Privacy-focused tests for caller-facing Google Calendar explanations."""

from __future__ import annotations

import google_calendar


def test_explain_date_conflict_returns_one_competing_commitment(monkeypatch):
    monkeypatch.setattr(
        google_calendar,
        "list_events_for_date",
        lambda *_a, **_k: [
            {
                "summary": "David Wu Birthday",
                "start": "2026-05-30",
                "end": "2026-05-31",
                "all_day": True,
                "visibility": "default",
            },
            {
                "summary": "Voice Agent YC Hackathon",
                "start": "2026-05-30T09:00:00-07:00",
                "end": "2026-05-30T18:00:00-07:00",
                "all_day": False,
                "visibility": "default",
            },
            {
                "summary": "Unrelated Dinner",
                "start": "2026-05-30T19:00:00-07:00",
                "end": "2026-05-30T20:00:00-07:00",
                "all_day": False,
                "visibility": "default",
            },
        ],
    )

    result = google_calendar.explain_date_conflict(
        "2026-05-30",
        caller_name="David Wu",
        topic="birthday",
    )

    assert result == {
        "date": "2026-05-30",
        "related_event_found": True,
        "reason": "Voice Agent YC Hackathon",
    }
    assert "Unrelated Dinner" not in str(result)


def test_explain_date_conflict_hides_private_title(monkeypatch):
    monkeypatch.setattr(
        google_calendar,
        "list_events_for_date",
        lambda *_a, **_k: [
            {
                "summary": "David Wu Birthday",
                "all_day": True,
                "visibility": "default",
            },
            {
                "summary": "Sensitive Medical Appointment",
                "all_day": False,
                "visibility": "private",
            },
        ],
    )

    result = google_calendar.explain_date_conflict(
        "2026-05-30",
        caller_name="David Wu",
        topic="birthday",
    )

    assert result is not None
    assert result["reason"] == "another commitment"
    assert "Medical" not in str(result)


def test_status_reports_unreadable_when_api_probe_fails(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-secret")
    monkeypatch.setattr(google_calendar, "is_connected", lambda: True)
    monkeypatch.setattr(
        google_calendar,
        "probe_read_access",
        lambda: (False, "Google Calendar API is disabled for this OAuth project."),
    )

    result = google_calendar.status()

    assert result["connected"] is True
    assert result["readable"] is False
    assert "disabled" in result["detail"].lower()
