# Person 3 integration seam (snapshot · actions · persistence)

P3's modules plug into P1's frozen `interfaces.py` contract. Wiring P3 into
`bot-nemotron.py` is **two lines** total.

## Modules

| File | Role |
|---|---|
| `caller_snapshot.py` | Part A. Ephemeral, current-call-only snapshot + wording-only tone inference + live style adaptation. Normalizes into P1's `CallerSnapshot` TypedDict contract fields. |
| `actions.py` | Part B. `send_owner_email`, `book_callback_slot` — graceful degrade to outbox if no connector. |
| `persistence.py` | Part C. Voicemail + Cekura eval-run records → DynamoDB → S3 → local file. |
| `action_bridge.py` | Drains the outbox into real Gmail/Calendar effects. |
| `persona_tools.py` | **Integration shim** — module-import side effect appends P3's tools to `TOOL_REGISTRY` and exposes the post-call hook. This is the only module `bot-nemotron.py` imports. |
| `demo_p3.py`, `test_p3.py` | Standalone harness + pytest suite (no bot required). |

## Wiring (already applied in `bot-nemotron.py`)

P3's three integration points are **applied** in `bot-nemotron.py`. P1: keep
these on your next push (don't revert the freeze stubs). All three are
side-effect-safe and additive.

**1. Tool registration — `import persona_tools` at the top of the file.**

Importing the module is enough; it appends 4 tools to `TOOL_REGISTRY`.
`persona_tools` follows P1's `(params: FunctionCallParams, call_state: CallState,
…)` convention, so `bind_call_state` inside `run_bot()` auto-injects per-call state.

**2. Auto tone listener — one processor in the pipeline (`run_bot`):**

```python
# between stt and the user aggregator
tone_listener = persona_tools.make_transcript_tone_processor(call_state)
pipeline = Pipeline([transport.input(), stt, tone_listener, user_aggregator, ...])
```

Passthrough `FrameProcessor`: infers caller tone from **final** transcripts
(ignores interim) with no LLM round-trip, folds it into the live snapshot.
Wording-only — reads frame text, never audio. Guarded so it can never break
the audio pipeline.

**3. Persistence hook — one line in `on_client_disconnected`:**

```python
@transport.event_handler("on_client_disconnected")
async def on_client_disconnected(transport, client):
    logger.info(f"Client disconnected — call_state summary: {call_state['voicemail']}")
    persona_tools.on_call_finished(call_state)   # ← persists voicemail
    await worker.cancel()
```

`on_call_finished` never raises — persistence is wrapped in a guard so a
disconnect handler can't fail because the agent's memory hiccupped.

> ⚠️ **Tool-name coordination (P2):** P3 registers `book_callback_slot`. When
> P2's `calendar_tools` lands it must **not** register a second
> `book_callback_slot` — pick one owner for that name to avoid a duplicate in
> `TOOL_REGISTRY`.

> 🟡 **FLAG FOR P1 — optional one-line greeting enhancement (`bot-nemotron.py`).**
> P3 added a contact book (`contacts.py` — macOS Contacts DB + `contacts.vcf`
> fallback). The LLM tool `lookup_persona` already recognises callers mid-call,
> but to greet a **known caller by name on the very first turn** (Twilio path,
> where the number is known at connect), call the P3 helper in
> `on_client_connected` **before** composing the opening message:
>
> ```python
> # in run_bot(), inside on_client_connected — replace the static opener:
> name = persona_tools.greeting_for_caller(call_state)   # P3 — None if unknown
> opener = (
>     f"A caller just connected. Your contacts show this is {name}. "
>     "Greet them by name and ask how you can help."
>     if name else
>     "A caller just connected. Greet them warmly and ask how you can help."
> )
> context.add_message({"role": "user", "content": opener})
> ```
>
> - `greeting_for_caller(call_state)` is side-effect-safe and **never raises**;
>   it also folds the recognised name/relationship into the live snapshot so the
>   rest of the call stays consistent.
> - Purely **additive** and **optional** — skip it and everything still works
>   (the bot just asks for the name as today). It does **not** touch the frozen
>   `interfaces.py` seam.
> - WebRTC calls have no caller number, so `name` is `None` there — the static
>   opener is used, no special-casing needed.
> - To demo this: a number in `server/contacts.vcf` (e.g. Sam Rivera
>   `+14155550142`) calling in → bot opens with the name. Edit that file to
>   control the recognised set on stage.

## Snapshot field contract (aligned with `interfaces.CallerSnapshot`)

`interfaces.CallerSnapshot` is `total=False`, so P3 stores both:

- **P1 contract fields** (always populated): `tone` (urgent | casual | hostile | distressed), `sentiment_score` (−1.0 … +1.0), `intent` (urgent_callback | leave_message), `known_caller` (bool), `persona_id` (str | None).
- **P3 rich fields** (used by style adaptation): `caller_name`, `relationship`, `reason`, `urgency`, `emotional_tone` (rushed | upset | confused | hesitant | rambling | concise | calm), `communication_style`, `callback_preference`, `best_time`, `agent_handling_notes`, plus internal `tone_history` / `updated_at`.

`caller_snapshot.normalize_to_contract(snap)` re-derives the contract fields
from the rich fields. It runs after every `update_caller_snapshot` call.

## Tools P3 adds to the LLM

| Tool | When the LLM calls it |
|---|---|
| `update_caller_snapshot` | As soon as it learns name / relationship / reason / urgency / callback prefs. Pass `last_caller_utterance` to re-infer tone from wording. |
| `lookup_persona` | Once per call, to flip `known_caller` / `persona_id` if the caller number matches a known contact. |
| `send_owner_email` | Once the voicemail is captured — emails the owner the summary. |
| `book_callback_slot` | Only when the caller requests a specific callback time. |

## P2 coordination seam

P2 owns the calendar read side. They plug into `book_callback_slot` by setting
one module attribute at import time:

```python
import persona_tools
persona_tools.calendar_free_busy_check = my_free_busy_check
# signature: (start_iso: str | None, end_iso: str | None) -> bool | None
# True = owner free, False = busy, None = unknown
```

When `free_busy_check` returns `False`, the event is marked
`status="needs_reschedule"` instead of booking over a conflict.

## Connector status (verified 2026-05-30)

- **Gmail MCP: connected**, but exposes only `create_draft` (no send). For email
  that lands in the owner's **inbox**, set `OWNER_EMAIL` + `GMAIL_APP_PASSWORD`
  and `actions.py` sends via SMTP. Otherwise `action_bridge.py` creates a draft.
- **Calendar MCP: needs reconnect with write scope.** Until then
  `book_callback_slot` queues to the outbox (no hard fail) and the bridge fires
  `create_event` once the connector is reconnected.

## Persistence backend

No AWS creds → **local file** (`server/aws_store/records.jsonl`). To light up
the cloud path with zero code changes: set AWS creds and either create a
DynamoDB table named `ff-voicemails` (override via `PERSIST_DDB_TABLE`) or set
`PERSIST_S3_BUCKET`. `boto3` is already in `pyproject.toml`.
`persistence.backend_kind()` reports the active backend; logged at import time.

## Privacy invariants

- Snapshot is **ephemeral / current-call only**. Fresh per call. No persistent
  caller profile without opt-in.
- Tone inferred from **transcript wording only** — never audio/pitch/prosody.
- No raw audio, voiceprint, speaker-ID, or caller voice cloning is stored.
- Outward actions (email/calendar) target the **owner's own** inbox/calendar.
