"""Hermetic pytest suite for caller_snapshot.py, persistence.py, actions.py."""
from __future__ import annotations

import asyncio
import sys
import types
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Stub out pipecat so make_snapshot_tools / make_action_tools can import it
# ---------------------------------------------------------------------------

def _install_pipecat_stub() -> None:
    """Insert a minimal pipecat stub so local imports don't crash."""
    if "pipecat" in sys.modules:
        return
    pipecat = types.ModuleType("pipecat")
    services = types.ModuleType("pipecat.services")
    llm = types.ModuleType("pipecat.services.llm_service")

    class FunctionCallParams:  # minimal stub
        pass

    llm.FunctionCallParams = FunctionCallParams  # type: ignore[attr-defined]
    pipecat.services = services  # type: ignore[attr-defined]
    services.llm_service = llm  # type: ignore[attr-defined]
    sys.modules["pipecat"] = pipecat
    sys.modules["pipecat.services"] = services
    sys.modules["pipecat.services.llm_service"] = llm


_install_pipecat_stub()

import actions  # noqa: E402
import caller_snapshot  # noqa: E402
import persistence  # noqa: E402

# ---------------------------------------------------------------------------
# Shared FakeParams helper
# ---------------------------------------------------------------------------

class FakeParams:
    last: Any = None

    async def result_callback(self, result: Any, properties: Any = None) -> None:
        self.last = result


# ===========================================================================
# 1. caller_snapshot.py
# ===========================================================================

class TestInferTone:
    def test_rushed(self):
        assert caller_snapshot.infer_tone_from_text("I'm in a hurry, gotta go") == "rushed"

    def test_upset(self):
        assert caller_snapshot.infer_tone_from_text("this is unacceptable, I'm so mad!!") == "upset"

    def test_confused(self):
        assert caller_snapshot.infer_tone_from_text("wait what? I don't understand??") == "confused"

    def test_hesitant(self):
        assert caller_snapshot.infer_tone_from_text("um, well, maybe, I'm not sure") == "hesitant"

    def test_concise(self):
        assert caller_snapshot.infer_tone_from_text("Yes.") == "concise"

    def test_rambling(self):
        # 45+ word run-on chained with several " and " — no hesitant/upset/confused keywords
        text = (
            "I called last week and the shop was closed and I tried again on Tuesday "
            "and the line was busy and I left a note and nobody called back and I "
            "stopped by on Thursday and there was a sign on the door and I walked "
            "around the block and came back and the door was still locked and now "
            "I am calling again"
        )
        word_count = len(text.split())
        assert word_count >= 45, f"only {word_count} words"
        assert caller_snapshot.infer_tone_from_text(text) == "rambling"

    def test_calm(self):
        assert caller_snapshot.infer_tone_from_text("Hi, I'd like to leave a message.") == "calm"

    def test_empty_returns_none(self):
        assert caller_snapshot.infer_tone_from_text("") is None

    def test_whitespace_returns_none(self):
        assert caller_snapshot.infer_tone_from_text("   ") is None


class TestApplySnapshotUpdate:
    def _fresh(self):
        return caller_snapshot.fresh_snapshot()

    def test_ignores_none(self):
        snap = self._fresh()
        snap["caller_name"] = "Alice"
        caller_snapshot.apply_snapshot_update(snap, caller_name=None)
        assert snap["caller_name"] == "Alice"

    def test_ignores_empty_string(self):
        snap = self._fresh()
        snap["reason"] = "order flowers"
        caller_snapshot.apply_snapshot_update(snap, reason="")
        assert snap["reason"] == "order flowers"

    def test_ignores_unknown_keys(self):
        snap = self._fresh()
        before = dict(snap)
        caller_snapshot.apply_snapshot_update(snap, nonexistent_field="value")
        # Only updated_at should differ
        before.pop("updated_at")
        snap_copy = dict(snap)
        snap_copy.pop("updated_at")
        assert snap_copy == before

    def test_normalizes_urgency_uppercase(self):
        snap = self._fresh()
        caller_snapshot.apply_snapshot_update(snap, urgency="HIGH")
        assert snap["urgency"] == "high"

    def test_normalizes_emotional_tone_uppercase(self):
        snap = self._fresh()
        caller_snapshot.apply_snapshot_update(snap, emotional_tone="RUSHED")
        assert snap["emotional_tone"] == "rushed"

    def test_sets_updated_at(self):
        snap = self._fresh()
        assert snap["updated_at"] is None
        caller_snapshot.apply_snapshot_update(snap, caller_name="Bob")
        assert snap["updated_at"] is not None


class TestObserveCallerTone:
    def test_appends_tone_history(self):
        snap = caller_snapshot.fresh_snapshot()
        caller_snapshot.observe_caller_tone(snap, "I'm in a hurry, gotta go")
        assert len(snap["tone_history"]) == 1
        assert snap["tone_history"][0] == "rushed"

    def test_sets_emotional_tone(self):
        snap = caller_snapshot.fresh_snapshot()
        caller_snapshot.observe_caller_tone(snap, "this is unacceptable, I'm so mad!!")
        assert snap["emotional_tone"] == "upset"

    def test_sets_nonempty_communication_style(self):
        snap = caller_snapshot.fresh_snapshot()
        caller_snapshot.observe_caller_tone(snap, "Yes.")
        assert snap["communication_style"] and snap["communication_style"].strip()


class TestAdaptationGuidance:
    def test_rushed_contains_brief(self):
        assert "brief" in caller_snapshot.adaptation_guidance("rushed").lower()

    def test_upset_mentions_slowing_or_warmth(self):
        text = caller_snapshot.adaptation_guidance("upset").lower()
        assert "slow" in text or "warm" in text

    def test_none_returns_calm_directive(self):
        assert caller_snapshot.adaptation_guidance(None) == caller_snapshot.adaptation_guidance("calm")


class TestLiveStyleDirective:
    def test_starts_with_caller_adaptation(self):
        state: dict[str, Any] = {"caller_snapshot": caller_snapshot.fresh_snapshot()}
        assert caller_snapshot.live_style_directive(state).startswith("CALLER ADAPTATION:")

    def test_high_urgency_includes_time_sensitive(self):
        snap = caller_snapshot.fresh_snapshot()
        snap["urgency"] = "high"
        state = {"caller_snapshot": snap}
        directive = caller_snapshot.live_style_directive(state).lower()
        assert "time" in directive or "priorit" in directive or "urgent" in directive


class TestFormatSnapshotSummary:
    def test_shows_em_dash_for_unknown(self):
        snap = caller_snapshot.fresh_snapshot()
        summary = caller_snapshot.format_snapshot_summary(snap)
        assert "—" in summary

    def test_shows_caller_name_when_set(self):
        snap = caller_snapshot.fresh_snapshot()
        snap["caller_name"] = "Maria"
        summary = caller_snapshot.format_snapshot_summary(snap)
        assert "Maria" in summary


class TestMakeSnapshotTools:
    def test_returns_two_callables(self):
        state: dict[str, Any] = {"caller_snapshot": caller_snapshot.fresh_snapshot()}
        tools = caller_snapshot.make_snapshot_tools(state)
        assert len(tools) == 2
        assert all(callable(t) for t in tools)

    def test_update_tool_updates_state_and_returns_result(self):
        state: dict[str, Any] = {"caller_snapshot": caller_snapshot.fresh_snapshot()}
        tools = caller_snapshot.make_snapshot_tools(state)
        update_fn = tools[0]
        fp = FakeParams()
        asyncio.run(update_fn(fp, caller_name="Sam", urgency="high"))
        assert state["caller_snapshot"]["caller_name"] == "Sam"
        assert isinstance(fp.last, dict)
        assert "ok" in fp.last
        assert "snapshot" in fp.last
        assert "style_directive" in fp.last
        assert fp.last["ok"] is True


# ===========================================================================
# 2. persistence.py
# ===========================================================================

@pytest.fixture(autouse=False)
def local_persistence(tmp_path, monkeypatch):
    """Reset persistence to local-file backend pointing at a tmp path."""
    local_file = tmp_path / "records.jsonl"
    monkeypatch.setenv("PERSIST_LOCAL_PATH", str(local_file))
    # Remove AWS env vars that might redirect to cloud
    for var in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_DEFAULT_REGION",
                "PERSIST_DDB_TABLE", "PERSIST_S3_BUCKET"):
        monkeypatch.delenv(var, raising=False)
    # Reset the cached backend so it re-resolves with the new env
    persistence._backend = None
    yield local_file
    # Cleanup
    persistence._backend = None


def test_backend_kind_is_local(local_persistence):
    assert persistence.backend_kind() == "local"


def test_persist_voicemail_keys_and_values(local_persistence):
    rec = persistence.persist_voicemail(
        voicemail={"caller_name": "Sam", "reason": "test"},
        caller_snapshot={"caller_name": "Sam", "urgency": "normal"},
        actions_taken=[{"type": "email", "status": "queued"}],
        owner_context_tag="t",
    )
    assert "record_id" in rec
    assert rec["type"] == "voicemail"
    assert rec["owner_context_tag"] == "t"
    assert "timestamp" in rec
    assert "caller_snapshot" in rec
    assert "voicemail" in rec
    assert "actions_taken" in rec
    assert rec["_stored_at"].startswith("local:")


def test_persist_voicemail_no_audio_keys(local_persistence):
    rec = persistence.persist_voicemail(
        voicemail={"caller_name": "Sam"},
        caller_snapshot={},
    )
    serialized = str(rec)
    for forbidden in ("audio", "voiceprint", "raw_audio"):
        assert forbidden not in serialized


def test_list_records_contains_voicemail(local_persistence):
    rec = persistence.persist_voicemail(
        voicemail={"reason": "hello"},
        caller_snapshot={},
        owner_context_tag="t",
    )
    records = persistence.list_records("voicemail")
    ids = [r["record_id"] for r in records]
    assert rec["record_id"] in ids


def test_persist_eval_run(local_persistence):
    rec = persistence.persist_eval_run(
        {"run_id": "r1", "passed": True, "metrics": [{"name": "m", "passed": True}]}
    )
    assert rec["type"] == "eval_run"
    assert rec["cekura_run_id"] == "r1"
    assert rec["passed"] is True
    eval_records = persistence.list_records("eval_run")
    ids = [r["record_id"] for r in eval_records]
    assert rec["record_id"] in ids


# ===========================================================================
# 3. actions.py
# ===========================================================================

@pytest.fixture
def action_state(tmp_path, monkeypatch):
    """Set up env + call_state for action tool tests."""
    outbox = tmp_path / "actions.jsonl"
    monkeypatch.setenv("PERSIST_OUTBOX_PATH", str(outbox))
    monkeypatch.setenv("OWNER_EMAIL", "owner@example.com")
    monkeypatch.delenv("GMAIL_APP_PASSWORD", raising=False)
    state: dict[str, Any] = {
        "voicemail": {"caller_name": "Sam", "reason": "check on order"},
        "caller_snapshot": caller_snapshot.fresh_snapshot(),
        "actions": [],
    }
    return state, outbox


def test_make_action_tools_returns_two_callables(action_state):
    state, _ = action_state
    tools = actions.make_action_tools(state)
    assert len(tools) == 2
    assert all(callable(t) for t in tools)


def test_send_owner_email_queued(action_state, monkeypatch):
    state, outbox = action_state
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    send_fn, _ = actions.make_action_tools(state)
    fp = FakeParams()
    asyncio.run(send_fn(fp))
    assert fp.last["ok"] is True
    assert fp.last["status"] == "queued"
    assert fp.last["to"] == "owner@example.com"
    # action appended to call_state
    assert len(state["actions"]) == 1
    assert state["actions"][0]["type"] == "email"
    # outbox file written
    assert outbox.exists()
    lines = [l for l in outbox.read_text().splitlines() if l.strip()]
    assert len(lines) >= 1


def test_book_callback_no_free_busy(action_state):
    state, outbox = action_state
    _, book_fn = actions.make_action_tools(state)
    fp = FakeParams()
    asyncio.run(book_fn(fp, requested_time="before 4pm"))
    assert fp.last["ok"] is True
    assert fp.last["tentative"] is True
    assert fp.last["status"] == "queued"
    assert state["actions"][0]["type"] == "calendar"


def test_book_callback_busy_needs_reschedule(action_state):
    state, _ = action_state
    _, book_fn = actions.make_action_tools(state, free_busy_check=lambda s, e: False)
    fp = FakeParams()
    asyncio.run(book_fn(fp, requested_time="before 4pm"))
    assert fp.last["status"] == "needs_reschedule"


def test_book_callback_resolves_before_today(action_state, monkeypatch):
    state, _ = action_state
    monkeypatch.setenv("OWNER_TZ", "America/Los_Angeles")
    state["voicemail"] = {
        "recorded_at": "2026-05-30T20:00:00+00:00",
        "message": {"caller_name": "Sam"},
    }
    _, book_fn = actions.make_action_tools(state)
    fp = FakeParams()
    asyncio.run(book_fn(fp, requested_time="before 4pm today"))

    event = state["actions"][0]
    assert event["start_iso"].startswith("2026-05-30T15:30:00")
    assert event["end_iso"].startswith("2026-05-30T16:00:00")


def test_book_callback_resolves_tomorrow_morning(action_state, monkeypatch):
    state, _ = action_state
    monkeypatch.setenv("OWNER_TZ", "America/Los_Angeles")
    state["voicemail"] = {
        "recorded_at": "2026-05-30T20:00:00+00:00",
        "message": {"caller_name": "Sam"},
    }
    _, book_fn = actions.make_action_tools(state)
    fp = FakeParams()
    asyncio.run(book_fn(fp, requested_time="tomorrow morning"))

    event = state["actions"][0]
    assert event["start_iso"].startswith("2026-05-31T09:00:00")
    assert event["end_iso"].startswith("2026-05-31T09:30:00")


def test_book_callback_free_busy_sees_resolved_time(action_state, monkeypatch):
    state, _ = action_state
    monkeypatch.setenv("OWNER_TZ", "America/Los_Angeles")
    state["voicemail"] = {
        "recorded_at": "2026-05-30T20:00:00+00:00",
        "message": {"caller_name": "Sam"},
    }
    seen: dict[str, str | None] = {}

    def free_busy(start, end):
        seen["start"] = start
        seen["end"] = end
        return True

    _, book_fn = actions.make_action_tools(state, free_busy_check=free_busy)
    fp = FakeParams()
    asyncio.run(book_fn(fp, requested_time="after 3pm today"))

    assert seen["start"] is not None
    assert seen["end"] is not None
    assert seen["start"].startswith("2026-05-30T15:00:00")
    assert seen["end"].startswith("2026-05-30T15:30:00")
    assert fp.last["status"] == "queued"


def test_tools_do_not_raise_on_minimal_state(tmp_path, monkeypatch):
    outbox = tmp_path / "min_actions.jsonl"
    monkeypatch.setenv("PERSIST_OUTBOX_PATH", str(outbox))
    monkeypatch.setenv("OWNER_EMAIL", "owner@example.com")
    monkeypatch.delenv("GMAIL_APP_PASSWORD", raising=False)
    state: dict[str, Any] = {}
    send_fn, book_fn = actions.make_action_tools(state)
    fp = FakeParams()
    asyncio.run(send_fn(fp))
    asyncio.run(book_fn(fp))
    # No exception raised — that's the assertion


def test_build_summary_text_reads_nested_voicemail_message(action_state):
    state, _ = action_state
    state["voicemail"] = {
        "message": {
            "caller_name": "Mina",
            "reason": "delivery timing",
            "urgency": "urgent",
            "callback_preferred_time": "before 4pm today",
        }
    }
    state["caller_snapshot"]["caller_name"] = "Mina"
    summary = actions.build_summary_text(state)
    assert "Caller Name: Mina" in summary
    assert "Reason: delivery timing" in summary
    assert "Callback Preferred Time: before 4pm today" in summary
    assert "Caller snapshot:" in summary


# ===========================================================================
# 4. persona_tools transcript → tone listener (Task 2)
# ===========================================================================

class _FakeFrame:
    """Stand-in for pipecat's TranscriptionFrame — just carries .text."""

    def __init__(self, text: str) -> None:
        self.text = text


def test_observe_transcript_frame_infers_tone():
    import persona_tools

    state: dict[str, Any] = {"caller_snapshot": caller_snapshot.fresh_snapshot()}
    tone = persona_tools.observe_transcript_frame(
        state, _FakeFrame("I'm in a hurry, gotta go before 4")
    )
    assert tone == "rushed"
    assert state["caller_snapshot"]["emotional_tone"] == "rushed"
    # Contract fields stay normalized after the listener folds tone in.
    assert state["caller_snapshot"]["tone"] in {"urgent", "casual", "hostile", "distressed"}


def test_observe_transcript_frame_ignores_blank():
    import persona_tools

    state: dict[str, Any] = {"caller_snapshot": caller_snapshot.fresh_snapshot()}
    assert persona_tools.observe_transcript_frame(state, _FakeFrame("   ")) is None
    assert persona_tools.observe_transcript_frame(state, _FakeFrame("")) is None


def test_sync_voicemail_to_snapshot_mirrors_p1_captures():
    import persona_tools

    state: dict[str, Any] = {
        "voicemail": {
            "message": {
                "caller_name": "Ana",
                "reason": "needs bouquet changed",
                "urgency": "urgent",
                "wants_callback": True,
                "callback_number": "+14155550123",
                "callback_preferred_time": "before 4pm today",
            }
        },
        "caller_snapshot": caller_snapshot.fresh_snapshot(),
    }

    snap = persona_tools.sync_voicemail_to_snapshot(state)

    assert snap["caller_name"] == "Ana"
    assert snap["reason"] == "needs bouquet changed"
    assert snap["urgency"] == "high"
    assert snap["best_time"] == "before 4pm today"
    assert snap["callback_preference"] == "call back at +14155550123"
    assert snap["intent"] == "urgent_callback"


class _FakeContext:
    def __init__(self) -> None:
        self._messages: list[dict[str, str]] = []

    def get_messages(self) -> list[dict[str, str]]:
        return list(self._messages)

    def set_messages(self, messages: list[dict[str, str]]) -> None:
        self._messages = list(messages)


def test_refresh_live_style_directive_upserts_one_system_message():
    import persona_tools

    state: dict[str, Any] = {"caller_snapshot": caller_snapshot.fresh_snapshot()}
    context = _FakeContext()

    assert persona_tools.refresh_live_style_directive(state, context) is True
    assert len(context.get_messages()) == 1

    caller_snapshot.observe_caller_tone(
        state["caller_snapshot"],
        "I'm in a hurry, gotta go before 4",
    )
    caller_snapshot.normalize_to_contract(state["caller_snapshot"])

    assert persona_tools.refresh_live_style_directive(state, context) is True
    messages = context.get_messages()
    assert len(messages) == 1
    assert messages[0]["role"] == "system"
    assert "rushed" in messages[0]["content"].lower()

    assert persona_tools.refresh_live_style_directive(state, context) is False
    assert len(context.get_messages()) == 1


# ===========================================================================
# 5. contacts.py — owner contact book → caller recognition
# ===========================================================================

import contacts  # noqa: E402


class TestNormalizeNumber:
    def test_e164_passthrough_or_last10(self):
        # With or without phonenumbers installed, these two map to the same key.
        assert contacts.normalize_number("+1 (415) 555-0142") == contacts.normalize_number(
            "+14155550142"
        )

    def test_formatting_is_ignored(self):
        assert contacts.normalize_number("415-555-0142") == contacts.normalize_number(
            "(415) 555 0142"
        )

    def test_too_short_returns_none(self):
        assert contacts.normalize_number("12345") is None

    def test_empty_returns_none(self):
        assert contacts.normalize_number("") is None
        assert contacts.normalize_number(None) is None


class TestParseVcards:
    SAMPLE = (
        "BEGIN:VCARD\nVERSION:3.0\nFN:Sam Rivera\n"
        "TEL;TYPE=CELL:+14155550142\nCATEGORIES:Work\nEND:VCARD\n"
        "BEGIN:VCARD\nVERSION:3.0\nFN:Mom\nTEL:+1 415-555-0168\nEND:VCARD\n"
    )

    def test_parses_name_and_number(self):
        idx = contacts.parse_vcards(self.SAMPLE)
        key = contacts.normalize_number("+14155550142")
        assert key is not None
        hit = idx[key]
        assert hit["name"] == "Sam Rivera"
        assert hit["relationship"] == "Work"
        assert hit["source"] == "vcard"

    def test_category_optional(self):
        idx = contacts.parse_vcards(self.SAMPLE)
        key = contacts.normalize_number("4155550168")
        assert key is not None
        hit = idx[key]
        assert hit["name"] == "Mom"
        assert hit["relationship"] is None

    def test_empty_text(self):
        assert contacts.parse_vcards("") == {}


class TestLookup:
    def test_lookup_hits_indexed_number(self, monkeypatch):
        key = contacts.normalize_number("+14155550142")
        assert key is not None
        idx = {
            key: {
                "name": "Sam Rivera",
                "relationship": "Work",
                "source": "vcard",
            }
        }
        monkeypatch.setattr(contacts, "_INDEX", idx)
        hit = contacts.lookup("+1 (415) 555-0142")
        assert hit is not None
        assert hit["name"] == "Sam Rivera"

    def test_lookup_miss(self, monkeypatch):
        monkeypatch.setattr(contacts, "_INDEX", {})
        assert contacts.lookup("+14155550100") is None
        assert contacts.lookup("") is None

    def test_lookup_name_requires_exact_unique_contact(self, monkeypatch):
        monkeypatch.setattr(
            contacts,
            "_NAME_INDEX",
            {
                "david wu": {
                    "name": "David Wu",
                    "relationship": "Friend",
                    "source": "vcard",
                }
            },
        )
        assert contacts.lookup_name("david wu")["name"] == "David Wu"
        assert contacts.lookup_name("David") is None


class TestPersonaLookupWiring:
    def test_owner_number_recognised(self, monkeypatch):
        import persona_tools

        monkeypatch.setenv("OWNER_PHONE_NUMBER", "+14155559999")
        match = persona_tools._persona_lookup("+14155559999")
        assert match is not None
        assert match["persona_id"] == "owner"

    def test_contact_number_recognised(self, monkeypatch):
        import persona_tools

        monkeypatch.setattr(
            contacts,
            "lookup",
            lambda n: {"name": "Sam Rivera", "relationship": "Work", "source": "vcard"},
        )
        match = persona_tools._persona_lookup("+14155550142")
        assert match is not None
        assert match["name"] == "Sam Rivera"
        assert match["persona_id"] == "Sam Rivera"
        assert match["relationship"] == "Work"

    def test_unknown_number(self, monkeypatch):
        import persona_tools

        monkeypatch.delenv("OWNER_PHONE_NUMBER", raising=False)
        monkeypatch.setattr(contacts, "lookup", lambda n: None)
        assert persona_tools._persona_lookup("+14155550100") is None


class TestGreetingForCaller:
    def _state(self, number: str) -> dict[str, Any]:
        return {
            "voicemail": {"caller_number": number},
            "caller_snapshot": caller_snapshot.fresh_snapshot(),
        }

    def test_known_caller_returns_name_and_fills_snapshot(self, monkeypatch):
        import persona_tools

        monkeypatch.setattr(
            contacts,
            "lookup",
            lambda n: {"name": "Sam Rivera", "relationship": "Work", "source": "vcard"},
        )
        state = self._state("+14155550142")
        name = persona_tools.greeting_for_caller(state)
        assert name == "Sam Rivera"
        assert state["caller_snapshot"]["known_caller"] is True
        assert state["caller_snapshot"]["caller_name"] == "Sam Rivera"
        assert state["caller_snapshot"]["relationship"] == "Work"

    def test_unknown_caller_returns_none(self, monkeypatch):
        import persona_tools

        monkeypatch.delenv("OWNER_PHONE_NUMBER", raising=False)
        monkeypatch.setattr(contacts, "lookup", lambda n: None)
        state = self._state("+14155550100")
        assert persona_tools.greeting_for_caller(state) is None
        assert state["caller_snapshot"]["known_caller"] is False

    def test_webrtc_no_number_returns_none(self, monkeypatch):
        import persona_tools

        monkeypatch.delenv("OWNER_PHONE_NUMBER", raising=False)
        state = self._state("")
        assert persona_tools.greeting_for_caller(state) is None


# ===========================================================================
# 6. Contact-gated calendar context
# ===========================================================================


def test_lookup_persona_allows_exact_name_for_webrtc_demo(monkeypatch):
    import persona_tools

    monkeypatch.setattr(
        contacts,
        "lookup_name",
        lambda name: {"name": "David Wu", "relationship": "Friend", "source": "vcard"}
        if name == "David Wu"
        else None,
    )
    state: dict[str, Any] = {
        "voicemail": {"caller_number": ""},
        "caller_snapshot": caller_snapshot.fresh_snapshot(),
    }
    fp = FakeParams()
    asyncio.run(persona_tools.lookup_persona(fp, state, caller_name="David Wu"))

    assert fp.last["private_context_allowed"] is True
    assert fp.last["verification_method"] == "stated_name_demo"
    assert state["caller_snapshot"]["known_caller"] is True
    assert state["caller_snapshot"]["caller_name"] == "David Wu"


def test_calendar_context_refuses_unknown_caller(monkeypatch):
    import persona_tools

    called = {"calendar": False}
    monkeypatch.setattr(
        persona_tools.google_calendar,
        "explain_date_conflict",
        lambda *_a, **_k: called.__setitem__("calendar", True),
    )
    state: dict[str, Any] = {
        "caller_snapshot": caller_snapshot.fresh_snapshot(),
    }
    fp = FakeParams()
    asyncio.run(
        persona_tools.get_calendar_context_for_caller(
            fp,
            state,
            date="2026-05-30",
            topic="David Wu birthday",
        )
    )

    assert fp.last["authorized"] is False
    assert called["calendar"] is False


def test_calendar_context_falls_back_to_persona_when_live_calendar_unavailable(monkeypatch):
    import persona_tools

    monkeypatch.setattr(
        persona_tools.google_calendar,
        "explain_date_conflict",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        persona_tools,
        "_fallback_calendar_reason",
        lambda *_a, **_k: {
            "date": "2026-05-30",
            "related_event_found": True,
            "reason": "a hackathon project",
            "source": "persona_fallback",
        },
    )
    snap = caller_snapshot.fresh_snapshot()
    snap["known_caller"] = True
    snap["caller_name"] = "David Wu"
    state: dict[str, Any] = {"caller_snapshot": snap}
    fp = FakeParams()
    asyncio.run(
        persona_tools.get_calendar_context_for_caller(
            fp,
            state,
            date="2026-05-30",
            topic="my birthday",
        )
    )

    assert fp.last["authorized"] is True
    assert fp.last["context_available"] is True
    assert fp.last["source"] == "persona_fallback"
    assert "hackathon project" in fp.last["answer_hint"]


def test_calendar_context_returns_one_first_person_reason_for_known_contact(monkeypatch):
    import persona_tools

    monkeypatch.setattr(
        persona_tools.google_calendar,
        "explain_date_conflict",
        lambda *_a, **_k: {
            "date": "2026-05-30",
            "related_event_found": True,
            "reason": "Voice Agent YC Hackathon",
        },
    )
    snap = caller_snapshot.fresh_snapshot()
    snap["known_caller"] = True
    snap["caller_name"] = "David Wu"
    state: dict[str, Any] = {"caller_snapshot": snap}
    fp = FakeParams()
    asyncio.run(
        persona_tools.get_calendar_context_for_caller(
            fp,
            state,
            date="2026-05-30",
            topic="my birthday",
        )
    )

    assert fp.last["authorized"] is True
    assert fp.last["related_event_found"] is True
    assert "I couldn't make it because I was at Voice Agent YC Hackathon" in fp.last["answer_hint"]
    assert "David Wu" not in fp.last["answer_hint"]
