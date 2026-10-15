"""Owner text and inbox shape for screened calls."""

from screened_calls import OWNER_TEXT_LIMIT, inbox_entry, owner_text


def test_owner_text_says_who_and_why():
    text = owner_text(
        {
            "caller_name": "Maya Okafor",
            "reason": "Moving Thursday's site visit.",
            "wants_callback": True,
            "callback_preferred_time": "after 3pm",
        },
        "+14155550143",
    )
    assert text == (
        "Gotchu: Maya Okafor (+14155550143) called about moving Thursday's site visit."
        " Wants a call back after 3pm."
    )


def test_owner_text_flags_urgent_and_other_callback_number():
    text = owner_text(
        {
            "caller_name": "Dr. Patel's office",
            "reason": "test results",
            "urgency": "urgent",
            "wants_callback": True,
            "callback_number": "+16505550199",
        },
        "+16505550100",
    )
    assert text.startswith("Gotchu, urgent: Dr. Patel's office (+16505550100) called about")
    assert text.endswith("Wants a call back at +16505550199.")


def test_owner_text_handles_unknown_caller_and_caps_length():
    assert owner_text({}, "") == "Gotchu: Someone called but did not say why."
    long_text = owner_text({"caller_name": "A", "reason": "x " * 400}, "")
    assert len(long_text) <= OWNER_TEXT_LIMIT
    assert long_text.endswith("...")


def test_inbox_entry_exposes_only_owner_facing_fields():
    entry = inbox_entry(
        {
            "record_id": "r1",
            "timestamp": "2026-10-14T21:04:00+00:00",
            "caller_snapshot": {"internal": "x"},
            "voicemail": {"message": {"caller_name": "Maya"}, "caller_number": "+1415"},
        }
    )
    assert entry["id"] == "r1"
    assert entry["caller_name"] == "Maya"
    assert entry["urgency"] == "normal"
    assert "caller_snapshot" not in entry


def test_owner_text_keeps_acronyms_capitalised():
    assert "about IRS paperwork." in owner_text({"caller_name": "Lee", "reason": "IRS paperwork"}, "")
