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


def test_send_owner_email_queued(action_state):
    state, outbox = action_state
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
        hit = idx[contacts.normalize_number("+14155550142")]
        assert hit["name"] == "Sam Rivera"
        assert hit["relationship"] == "Work"
        assert hit["source"] == "vcard"

    def test_category_optional(self):
        idx = contacts.parse_vcards(self.SAMPLE)
        hit = idx[contacts.normalize_number("4155550168")]
        assert hit["name"] == "Mom"
        assert hit["relationship"] is None

    def test_empty_text(self):
        assert contacts.parse_vcards("") == {}


class TestLookup:
    def test_lookup_hits_indexed_number(self, monkeypatch):
        idx = {
            contacts.normalize_number("+14155550142"): {
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
    def _state(self, number):
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
