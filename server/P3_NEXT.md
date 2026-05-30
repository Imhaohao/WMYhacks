# P3 handoff — next steps

Self-contained brief for an agent picking up P3 work mid-stream. **Read this top to bottom before touching anything.** Then read `server/INTEGRATION_P3.md` for the current-state contract.

---

## 1 — What this project is

YC Voice Agents Hackathon. The team is converting a Pipecat starter (Field & Flower flower-shop) into a **personal voicemail proxy**: the bot answers calls on the owner's behalf, takes a complete message, can email/text the owner, and books a tentative callback slot. The win thesis is "best system that **scales, persists, and learns**" — anchored by a Cekura auto-improve loop.

Three teammates own three vertical slices, defined in `server/interfaces.py`:

| Owner | Slice | Files |
|---|---|---|
| P1 | Voice path · Twilio/SMS · Pipecat Cloud · interface freeze | `bot-nemotron.py`, `interfaces.py`, `nemotron_llm.py`, `nvidia_stt.py` |
| P2 | Owner context (iMessage, Calendar read) · Cekura eval loop | (not yet pushed) |
| **P3 — you** | **Caller snapshot · email/calendar actions · AWS persistence** | `caller_snapshot.py`, `actions.py`, `persistence.py`, `action_bridge.py`, `persona_tools.py`, `demo_p3.py`, `test_p3.py` |

Primary bot: **`server/bot-nemotron.py`** (Nemotron LLM + Nemotron STT + Gradium TTS, NVIDIA prize eligibility). **Do not switch to `bot-gpt.py`.**

---

## 2 — What's already built (P3 only)

Everything below is in `server/`. Tests pass: `uv run pytest test_p3.py -q` → 37 passing.

- **`caller_snapshot.py`** — Ephemeral, current-call-only snapshot. Tone inferred from transcript wording only (no audio). `normalize_to_contract()` projects rich fields onto P1's `CallerSnapshot` TypedDict (`tone`/`sentiment_score`/`intent`/`known_caller`/`persona_id`).
- **`actions.py`** — `send_owner_email` (SMTP if `OWNER_EMAIL`+`GMAIL_APP_PASSWORD` set, else queue to outbox) and `book_callback_slot` (always queue to outbox; bridge fulfills). Both never hard-fail.
- **`persistence.py`** — Lazy boto3. Backend resolution: DynamoDB table (env `PERSIST_DDB_TABLE`, default `ff-voicemails`) → S3 (`PERSIST_S3_BUCKET`) → local JSONL file (`server/aws_store/records.jsonl`). `persist_voicemail()` and `persist_eval_run()` are the two write paths.
- **`action_bridge.py`** — Drains `server/outbox/` JSON files into real Gmail draft + Calendar event MCP calls. CLI entry: `python action_bridge.py`. Not yet wired against live MCPs end-to-end.
- **`persona_tools.py`** — Integration shim. Importing it appends 4 tools to `interfaces.TOOL_REGISTRY` (`update_caller_snapshot`, `lookup_persona`, `send_owner_email`, `book_callback_slot`) and exposes `on_call_finished(call_state)` for P1's disconnect handler. Also exposes `calendar_free_busy_check` for P2 to plug into.
- **`demo_p3.py`** — Standalone A+B+C harness. Run with `uv run demo_p3.py` from `server/`.
- **`test_p3.py`** — 37 tests.

Verify everything still works:

```bash
cd server
uv run pytest test_p3.py -q              # → 37 passed
uv run pyright persona_tools.py caller_snapshot.py actions.py persistence.py action_bridge.py
uv run python demo_p3.py                 # → end-to-end Part A+B+C, no bot needed
```

---

## 3 — Hard invariants (do not break)

These come from the project brief and `CLAUDE.md`. Violating any of these loses the demo.

1. **Snapshot is ephemeral / current-call only.** Fresh per call. No persistent caller profile without opt-in.
2. **Tone inferred from transcript wording only** — never from audio, pitch, prosody, or any speaker biometric.
3. **No raw audio, voiceprint, speaker-ID, or caller voice cloning anywhere.** Persisted records contain only structured, owner-facing fields.
4. **Outward actions target the owner's own email/calendar/phone only.** No real third parties in the demo.
5. **Do not fork `run_bot()`.** P1 owns it. P3 wires in via `persona_tools` (a single import + a single hook line).
6. **Do not edit P2's modules or P1's `bot-nemotron.py` beyond the two-line diff in §4 Task 1.** Coordinate via the freeze in `interfaces.py`.
7. **No secrets in code or chat.** Read `os.getenv(...)` only. Never print env values.
8. **Primary bot is `bot-nemotron.py`** (NVIDIA prize). Do not refactor to `bot-gpt.py`.

---

## 4 — Tasks, in priority order

### Task 1 — Apply the two-line diff to `bot-nemotron.py`  *(BLOCKS DEMO)*

**Goal:** Wire P3's tools into the live bot. Without this, none of P3's work runs in the actual voice pipeline.

**The diff** (already documented in `INTEGRATION_P3.md`):

```python
# server/bot-nemotron.py — top of file, around line 57
# replace the commented stub with an actual import:
import persona_tools   # P3 — registers 4 tools + on_call_finished hook

# in on_client_disconnected (around line 327), before `await worker.cancel()`:
persona_tools.on_call_finished(call_state)
```

**Who applies it:** Coordinate with P1 first — it's their file. If P1 is unreachable and you have authorization, apply it yourself **in a separate commit** with message `P3: wire persona_tools into bot-nemotron`. Keep the diff to exactly those two lines.

**Acceptance:**
- `uv run bot-nemotron.py` starts without traceback (you'll need `GRADIUM_API_KEY`, `NVIDIA_ASR_URL`, `NEMOTRON_LLM_URL` in `server/.env`).
- Open http://localhost:7860, click Connect, talk to the bot once. After disconnect, `server/aws_store/records.jsonl` has one new record.
- `cat server/aws_store/records.jsonl | tail -1 | jq .record_id` returns a UUID.

**Delegation hint:** Flagship only — touching P1's file is high-blast-radius. Do not delegate.

---

### Task 2 — Auto-feed `last_caller_utterance` from the transcript

**Goal:** Today, tone inference only runs if the LLM remembers to pass `last_caller_utterance` to `update_caller_snapshot`. That's flaky. Better: subscribe to Pipecat's transcript stream and call `caller_snapshot.observe_caller_tone(snap, text)` directly on every final user utterance.

**Investigation needed:** find the Pipecat event/processor that emits final user transcripts after the user aggregator. Candidates:
- `TranscriptionFrame` (Pipecat frame type, look in `pipecat.frames.frames`)
- An event on the user aggregator
- A custom processor inserted between `stt` and `user_aggregator` in `bot-nemotron.py` line ~292

**Where to add the hook:** Best as a `persona_tools.attach_transcript_listener(pipeline, call_state)` function P1 calls once in `run_bot`. That keeps P3's code in P3's file.

**Acceptance:**
- Talk to the bot in a rushed tone (`"need them quick, gotta go before 4"`) without the LLM ever calling `update_caller_snapshot`. `call_state["caller_snapshot"]["emotional_tone"]` should be `"rushed"` after that utterance.
- Add a test in `test_p3.py` that feeds a fake transcript frame through the listener and asserts tone updates.

**Delegation hint:** Sonnet subagent. Pipecat frame mechanics are well-documented; the change is small and contained. Give Sonnet `bot-nemotron.py` lines 280–310 and `persona_tools.py` plus the goal.

**Gotcha:** Pipecat's user aggregator emits both interim and final transcripts. Listen for finals only, or tone will flicker on every word.

---

### Task 3 — SMTP credentials so email actually lands in inbox

**Goal:** `send_owner_email` currently queues to `server/outbox/` because no SMTP credentials are set. For the demo we need the email to actually arrive in the owner's inbox.

**Two paths:**

(a) **SMTP via Gmail app password** (preferred — works without MCP):
- In `server/.env`, add:
  ```
  OWNER_EMAIL=imzihaoi@gmail.com
  GMAIL_APP_PASSWORD=<16-char app password from https://myaccount.google.com/apppasswords>
  ```
- `actions._send_via_smtp` already handles this. Test: `uv run python demo_p3.py` should now log `status=sent` (not `status=queued`).

(b) **Gmail MCP `create_draft`** (fallback — draft, not inbox):
- The connected Gmail MCP exposes only `create_draft` (no send tool).
- `action_bridge.py` should drain the outbox into drafts. Verify that path end-to-end once an outbox entry exists.

**Acceptance:**
- A test email from `demo_p3.py` arrives in `imzihaoi@gmail.com` inbox **or** in the Gmail Drafts folder.

**Delegation hint:** Haiku — this is config + a smoke test.

**Gotcha:** Don't print the app password in chat. Read it via `os.getenv`. Don't commit `.env`.

---

### Task 4 — End-to-end `action_bridge.py` against live MCPs

**Goal:** Confirm that draining `server/outbox/` produces real Gmail drafts and real Calendar events. The bridge code exists; it has not been driven against the live connectors in one flow.

**Steps:**
1. Use `demo_p3.py` to produce outbox entries (one email + one calendar event).
2. Verify Calendar MCP is reconnected with **write scope** (the connector previously returned a permissions error — see `INTEGRATION_P3.md` §Connector status). If it's still read-only, escalate to the human; do not pretend it worked.
3. Run `uv run python action_bridge.py` from `server/`. Expect: one Gmail draft created, one Calendar event created on the owner's primary calendar with a "TENTATIVE — " title prefix.
4. After fulfillment, the outbox entries should be marked `fulfilled: true` (or moved/archived — check `action_bridge.py` for the exact convention).

**Acceptance:**
- One observed Gmail draft and one observed Calendar event from a single bridge run.
- Re-running the bridge with no new outbox entries is a no-op (no duplicate events).

**Delegation hint:** Sonnet — debugging against live MCPs may need iteration, but the goal is well-scoped.

**Gotcha:** The calendar event must be visibly TENTATIVE (event title prefix, color, or description). The MCP `create_event` schema does not expose an event-status field. Encode tentativeness in the title.

---

### Task 5 — Cekura eval-run persistence (with P2)

**Goal:** P3's brief task #9 says "Persist Cekura eval-run records so 'evaluation history persists' is a real shown artifact." `persistence.persist_eval_run(run)` already exists; it just needs a caller.

**Coordination:** P2 owns the Cekura loop. They need to call `persistence.persist_eval_run(...)` after each Cekura run completes. Two options:

(a) Hook into P2's Cekura runner — they call `persist_eval_run()` directly. Cleanest. Wait for P2 to land their module, then add the one-line call.
(b) Post-hoc poller — read Cekura run results via MCP (`mcp__plugin_cekura_cekura__results_list`) and persist. Works without P2 but duplicates effort.

**Acceptance:**
- After a Cekura eval run, `persistence.list_records(record_type="eval_run")` returns the run record.
- The record contains: run id, metric name, pass/fail, timestamp, and (ideally) the metric prompt diff if auto-improve fired.

**Delegation hint:** Flagship — needs cross-team coordination and Cekura SDK familiarity. Read `cekura:list-metrics` and `cekura:run-evals` skills first.

**Gotcha:** Don't persist transcripts containing private owner context. Strip or hash anything that could leak iMessage-derived info.

---

### Task 6 — Light up the AWS cloud path  *(optional flourish)*

**Goal:** Move persistence from local JSONL to DynamoDB or S3 so the demo can claim a real AWS artifact ("the agent's memory lives in DynamoDB"). The code path already exists — this is purely config.

**Steps:**
1. Get AWS creds (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`) into `server/.env`.
2. **DynamoDB path** (preferred): create a table named `ff-voicemails` with partition key `record_id` (string). `aws dynamodb create-table --table-name ff-voicemails --attribute-definitions AttributeName=record_id,AttributeType=S --key-schema AttributeName=record_id,KeyType=HASH --billing-mode PAY_PER_REQUEST`
3. **S3 path** (simpler): create a bucket, set `PERSIST_S3_BUCKET=<bucket-name>` in `.env`.
4. Restart and verify: `persistence.backend_kind()` should return `"dynamodb"` or `"s3"`, not `"local"`.

**Acceptance:**
- After one voicemail, the record is visible via `aws dynamodb scan --table-name ff-voicemails` or `aws s3 ls s3://<bucket>/`.

**Delegation hint:** Haiku — pure config + verification commands.

**Gotcha:** Costs are negligible at demo volume. PAY_PER_REQUEST is the right billing mode — don't provision throughput.

---

### Task 7 — Stage "memory" viewer

**Goal:** A one-command CLI that pretty-prints the most recent persisted record. Used live on stage to show "the agent's memory."

**Suggested implementation:** Extend `persistence.py`'s `__main__` block. Today it has a `--selftest` mode; add `--show-last [N]` that pretty-prints the last N records as readable JSON.

**Acceptance:**
- `uv run python persistence.py --show-last 1` prints one full record formatted for legibility (snapshot fields labeled, actions itemized, timestamp humanized).
- Output fits in a terminal screenshot.

**Delegation hint:** Sonnet — small, clean CLI work with clear acceptance.

---

## 5 — Coordination notes

- **P1 freeze:** `server/interfaces.py`. Don't add ad-hoc keys to `CallState`; extend the appropriate sub-TypedDict.
- **P1 tool signature convention:** `async def my_tool(params: FunctionCallParams, call_state: CallState, …) -> None`. `bind_call_state` in `run_bot` strips `call_state` from the LLM-visible signature.
- **P2 seam:** `persona_tools.calendar_free_busy_check` is the attribute P2 sets to plug their calendar read-side into `book_callback_slot`. If P2 hasn't set it, calendar bookings are queued without conflict checks.
- **Field & Flower environment:** `OWNER_EMAIL` defaults to `imzihaoi@gmail.com` (the human's address). `OWNER_PHONE_NUMBER` is unset until P1 wires Twilio.

---

## 6 — Delegation summary (per `CLAUDE.md`)

| Task | Model | Why |
|---|---|---|
| 1. Apply two-line diff | Flagship | Touches P1's file. High blast radius. |
| 2. Auto-feed transcript | Sonnet | Well-scoped Pipecat investigation + small edit. |
| 3. SMTP credentials | Haiku | Config + smoke test. |
| 4. action_bridge E2E | Sonnet | Live-MCP debugging; iteration likely. |
| 5. Cekura persistence | Flagship | Cross-team + Cekura SDK familiarity. |
| 6. AWS cloud path | Haiku | Pure config. |
| 7. Memory viewer CLI | Sonnet | Small CLI with clear acceptance. |

For bulk transcript→tone work at eval time (privacy-sensitive caller wording), prefer `compute-box` running `llama3.2:3b` over SSH. Don't use OpenAI/Anthropic for raw caller text if it can be avoided.

---

## 7 — When you're done

End-of-session checklist:

- [ ] `uv run pytest test_p3.py -q` still passes (37+ tests).
- [ ] `uv run pyright persona_tools.py caller_snapshot.py actions.py persistence.py action_bridge.py` → 0 errors.
- [ ] `uv run ruff check` → 0 errors in P3 files.
- [ ] If you added a new file, add a one-line entry in §2 of this doc.
- [ ] If you changed an invariant or the freeze contract, **stop** and flag to the human before proceeding.
- [ ] Commit with a clear message naming P3 (e.g. `P3: auto-feed transcript into tone inference`).

Demo readiness signal: a fresh `uv run bot-nemotron.py` produces one Gmail email/draft, one Calendar event, and one persisted record per call — with no manual intervention.
