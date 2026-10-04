"""Tenant isolation regressions for owner-only live tools."""

from __future__ import annotations

import asyncio
from typing import Any

import owner_tools
from interfaces import default_call_state


class _Params:
    def __init__(self) -> None:
        self.result: dict[str, Any] | None = None

    async def result_callback(self, payload: dict[str, Any]) -> None:
        self.result = payload


def _tenant_call_state(caller_number: str) -> dict[str, Any]:
    return {
        "voicemail": {"caller_number": caller_number},
        "tenant_runtime": {
            "tenant_id": "tenant-one",
            "owner_phone": "+16505550100",
            "owner_email": "owner@example.com",
            "timezone": "America/Los_Angeles",
        },
    }


def test_tenant_private_tools_ignore_global_assistant_mode_for_non_owner(monkeypatch):
    monkeypatch.setenv("ASSISTANT_MODE", "1")
    params = _Params()

    asyncio.run(owner_tools.check_owner_inbox(params, _tenant_call_state("+16505550199")))

    assert params.result is not None
    assert params.result["authorized"] is False


def test_tenant_owner_inbox_uses_tenant_google_not_demo_gmail(monkeypatch):
    params = _Params()
    monkeypatch.delenv("ASSISTANT_MODE", raising=False)
    monkeypatch.setattr(
        owner_tools.tenant_google,
        "is_connected",
        lambda tenant_id, service: tenant_id == "tenant-one" and service == "gmail",
    )
    monkeypatch.setattr(
        owner_tools.tenant_google,
        "list_recent_gmail",
        lambda tenant_id, max_results, query: [
            {"from": "Ada <ada@example.com>", "subject": "Tenant inbox", "snippet": "hello"}
        ],
    )

    def fail_demo_gmail(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        raise AssertionError("tenant owner inbox must not read demo Gmail")

    monkeypatch.setattr(owner_tools.google_gmail, "list_recent", fail_demo_gmail)

    asyncio.run(owner_tools.check_owner_inbox(params, _tenant_call_state("+1 (650) 555-0100")))

    assert params.result is not None
    assert params.result["authorized"] is True
    assert params.result["connected"] is True
    assert params.result["source"] == "tenant_google_gmail"
    assert params.result["messages"][0]["subject"] == "Tenant inbox"


def test_tenant_owner_calendar_uses_tenant_google_not_demo_calendar(monkeypatch):
    params = _Params()
    monkeypatch.delenv("ASSISTANT_MODE", raising=False)
    monkeypatch.setattr(
        owner_tools.tenant_google,
        "list_calendar_events_for_date",
        lambda tenant_id, date, *, timezone=None: [
            {
                "summary": "Tenant standup",
                "start": f"{date}T09:00:00-07:00",
                "end": f"{date}T09:30:00-07:00",
                "all_day": False,
            }
        ],
    )

    def fail_demo_calendar(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        raise AssertionError("tenant owner calendar must not read demo Calendar")

    monkeypatch.setattr(owner_tools.google_calendar, "list_events_for_date", fail_demo_calendar)

    asyncio.run(
        owner_tools.check_owner_calendar(
            params,
            _tenant_call_state("+16505550100"),
            date="2026-06-07",
        )
    )

    assert params.result is not None
    assert params.result["authorized"] is True
    assert params.result["connected"] is True
    assert params.result["source"] == "tenant_google_calendar"
    assert params.result["events"][0]["title"] == "Tenant standup"


def test_tenant_reminder_is_captured_without_global_reminders(monkeypatch):
    state = default_call_state(caller_number="+16505550199")
    state["tenant_runtime"] = {
        "tenant_id": "tenant-one",
        "owner_phone": "+16505550100",
        "owner_email": "owner@example.com",
    }
    params = _Params()

    def fail_global_reminders(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("tenant reminder must not invoke global macOS Reminders")

    monkeypatch.setattr(owner_tools.reminders_client, "add_reminder", fail_global_reminders)

    asyncio.run(
        owner_tools.add_reminder(
            params,
            state,
            title="Call Alex back",
            notes="Discuss the contract",
        )
    )

    assert params.result == {
        "ok": True,
        "status": "captured",
        "source": "voicemail",
        "answer_hint": (
            "Tell the caller you captured the reminder request with the voicemail; "
            "do not say a native reminder was created."
        ),
    }
    assert state["voicemail"]["action_items"] == ["Call Alex back"]
    assert state["actions"] == [
        {
            "type": "owner_reminder",
            "status": "captured",
            "title": "Call Alex back",
            "notes": "Discuss the contract\nRequested by +16505550199.",
        }
    ]


def test_legacy_non_owner_reminder_is_captured_and_idempotent(monkeypatch):
    monkeypatch.delenv("ASSISTANT_MODE", raising=False)
    monkeypatch.setenv("OWNER_PHONE_NUMBER", "+16505550100")
    state = default_call_state(caller_number="+16505550199")
    params = _Params()

    def fail_global_reminders(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("legacy non-owner reminder must not invoke macOS Reminders")

    monkeypatch.setattr(owner_tools.reminders_client, "add_reminder", fail_global_reminders)

    for _ in range(2):
        asyncio.run(
            owner_tools.add_reminder(
                params,
                state,
                title="Call Alex back",
            )
        )

    assert params.result is not None
    assert params.result["status"] == "captured"
    assert state["voicemail"]["action_items"] == ["Call Alex back"]
    assert len(state["actions"]) == 1
    assert state["actions"][0]["notes"] == "Requested by +16505550199."


def test_legacy_owner_reminder_keeps_native_reminders_behavior(monkeypatch):
    monkeypatch.delenv("ASSISTANT_MODE", raising=False)
    monkeypatch.setenv("OWNER_PHONE_NUMBER", "+16505550100")
    state = default_call_state(caller_number="+16505550100")
    params = _Params()
    calls: list[tuple[str, str | None]] = []

    def fake_add_reminder(title: str, notes: str | None) -> dict[str, Any]:
        calls.append((title, notes))
        return {"ok": True, "title": title}

    monkeypatch.setattr(owner_tools.reminders_client, "add_reminder", fake_add_reminder)

    asyncio.run(
        owner_tools.add_reminder(
            params,
            state,
            title="Review the draft",
            notes="Before lunch",
        )
    )

    assert calls == [("Review the draft", "Before lunch")]
    assert params.result is not None
    assert params.result["ok"] is True
    assert params.result["answer_hint"] == "Confirm you've added the reminder: 'Review the draft'."
    assert state["voicemail"]["action_items"] == []
    assert state["actions"] == []


def test_empty_reminder_title_does_not_change_call_state(monkeypatch):
    state = default_call_state(caller_number="+16505550199")
    params = _Params()

    def fail_global_reminders(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("empty reminder title must not invoke macOS Reminders")

    monkeypatch.setattr(owner_tools.reminders_client, "add_reminder", fail_global_reminders)

    asyncio.run(owner_tools.add_reminder(params, state, title="  "))

    assert params.result == {
        "ok": False,
        "answer_hint": "Ask what the reminder should say.",
    }
    assert state["voicemail"]["action_items"] == []
    assert state["actions"] == []
