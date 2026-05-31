"""Conversation-policy regressions for the owner-proxy voice agent."""

from __future__ import annotations

from interfaces import (
    build_caller_confirmation,
    build_system_instruction,
    default_call_state,
)


def test_caller_confirmation_omits_internal_fields() -> None:
    confirmation = build_caller_confirmation(
        {
            "caller_name": "David Wu",
            "reason": "a test call",
            "urgency": "urgent",
            "wants_callback": True,
            "callback_number": "650656",
            "callback_preferred_time": "tomorrow morning",
            "communication_style": "short and direct",
        }
    )

    assert confirmation == (
        "I have David Wu calling about a test call, and you'd like a callback "
        "at 650656 tomorrow morning. Is that right?"
    )
    assert "urgent" not in confirmation
    assert "snapshot" not in confirmation.lower()
    assert "communication style" not in confirmation.lower()


def test_caller_confirmation_does_not_announce_missing_callback() -> None:
    confirmation = build_caller_confirmation(
        {"caller_name": "Sam", "reason": "the delivery", "wants_callback": False}
    )

    assert confirmation == "I have Sam calling about the delivery. Is that right?"
    assert "No callback requested" not in confirmation


def test_system_instruction_web_demo_skips_callback_number() -> None:
    state = default_call_state(caller_number="")
    instruction = build_system_instruction(state)

    assert "do NOT ask for a phone number" in instruction
    assert "always leave callback_number empty" in instruction
    assert "Urgency is 'urgent' (no callback number on this channel)" in instruction


def test_system_instruction_phone_asks_for_callback_number() -> None:
    state = default_call_state(caller_number="+14155550100")
    instruction = build_system_instruction(state)

    assert "do NOT ask for a phone number" not in instruction
    assert "Urgency is 'urgent' AND a callback number was given" in instruction


def test_system_instruction_supports_questions_without_leaking_private_context() -> None:
    state = default_call_state()
    state["persona_context"] = "Owner likes concise answers."

    instruction = build_system_instruction(state)

    assert "take a message, or help with a quick question" in instruction
    assert "Treat Owner Context as private reasoning material" in instruction
    assert "never read it back or mention it" in instruction
    assert "Do not say you read the owner's emails, AI prompts, or messages." in instruction
    assert "follow this order exactly" not in instruction
    assert "How urgent is this" not in instruction


def test_system_instruction_contact_gates_calendar_and_uses_first_person() -> None:
    state = default_call_state()
    instruction = build_system_instruction(state)

    assert "speak in first person: say I, me, and my" in instruction
    assert "private_context_allowed=true" in instruction
    assert "get_calendar_context_for_caller" in instruction
    assert "Never disclose the calendar event list" in instruction


def test_system_instruction_deescalates_instead_of_mirroring_hostility() -> None:
    state = default_call_state()
    instruction = build_system_instruction(state)

    assert "Never mirror hostility" in instruction
    assert "spit it out" in instruction
