# UI — Contact & message context (plan + implementation prompts)

**Status:** Not built. Marketing copy and landing visuals promise “contacts, calendar, messages”; setup only collects vCard upload + a free-text availability line.

**When to implement:** Pick one of the gates below — they can ship independently.

| Gate | Unblocks | Can ship without |
|------|----------|------------------|
| **A — UI MVP (recommended first)** | Nothing external — extends `owner_config.json` + setup wizard | P2 iMessage MCP, Google Calendar OAuth |
| **B — Per-contact overrides** | Gate A merged | macOS Full Disk Access, live iMessage sync |
| **C — Live message ingestion** | P2 `imessage_context` module (or MCP) landed + privacy review | — |

**Default delegation:** Sonnet subagent with this file + `web/src/pages/setup-page.tsx` + `server/owner_config.py` + `server/onboarding_api.py`. Flagship only for P1/P2 interface changes.

**Verify after any change:**
```bash
cd server && uv run pytest -q && uv run pyright && uv run ruff check .
cd web && npm run build
```

---

## Problem (today)

| Surface | What exists | What’s missing |
|---------|-------------|----------------|
| `/setup` step “Contacts” | vCard upload → `contacts.vcf` → name/relationship from `CATEGORIES` | No per-contact notes, no manual contact editor, no message history |
| `/setup` step “Calendar” | `manual_availability` string | No OAuth, no structured free/busy |
| `OwnerConfig` / API | 6 fields in `web/src/lib/api.ts` | No `standing_instructions`, `priority_contacts`, `message_context_summary` |
| Bot prompt | `build_persona_context()` in `owner_config.py` → `call_state["persona_context"]` | Only name, tz, availability, email — no priorities from UI |
| `lookup_persona` | Name + vCard `relationship` | No owner-authored handling notes per contact |

Privacy invariants (carry forward from P3):
- No raw iMessage bodies in prompts or persistence unless the owner explicitly pastes a **summary** they control.
- No dumping the full contact book into the LLM — only matched caller + owner-configured snippets.
- Phone numbers in UI are for the owner’s book only; never log full books.

---

## Target UX (what “done” looks like)

### Setup wizard — new / expanded steps

Keep the existing 5-step shell; **insert content** rather than adding a 6th step unless product wants a dedicated “Context” step.

**Option 1 (minimal diff):** Extend step 0 “About you” + step 1 “Contacts”.

**Option 2 (clearer IA):** Rename steps to  
`You → Contacts → Context → Calendar → Services → Try`  
(6 steps — update `STEPS` in `setup-page.tsx`).

Recommended fields:

#### Owner-level context (step “You” or new “Context”)

| Field | Type | Stored as | Feeds |
|-------|------|-----------|-------|
| Standing instructions | textarea, ~500 chars | `owner_config.standing_instructions` | `persona_context` |
| Priority topics / people | textarea or tag input | `owner_config.priority_hints` | `persona_context` (“Always escalate if caller mentions X or is Y”) |
| Message context summary | textarea, owner-authored | `owner_config.message_context_summary` | `persona_context` — **not** auto-scraped iMessage in v1 |
| Agent tone | select: professional / casual / brief | `owner_config.agent_tone` | optional suffix on `persona_context` |

Copy for `message_context_summary` placeholder:
> “Optional: paste a short summary of recent threads the agent should know about (e.g. ‘Sarah texted about the lease — waiting on my reply’). Do not paste full chat logs.”

#### Per-contact context (step “Contacts”, after vCard upload)

After upload, show a **read-only list** of imported names with an optional “Add note” per row (or edit top N contacts only for hackathon scope).

| Field | Type | Stored as | Feeds |
|-------|------|-----------|-------|
| Note / handling | text, ~200 chars | `contact_overrides.json` keyed by normalized phone or `persona_id` (name) | `lookup_persona` + greeting path |

Example note: “Mom — always take the call, warm tone, never ask for callback number.”

**Hackathon MVP:** Skip per-row UI; allow one JSON file upload or a single “VIP contacts” textarea (`name: note` lines).

---

## Data model

### Extend `owner_config.py` DEFAULT

```python
DEFAULT: dict[str, Any] = {
    # ... existing fields ...
    "standing_instructions": "",
    "priority_hints": "",
    "message_context_summary": "",
    "agent_tone": "professional",  # professional | casual | brief
}
```

Update `build_persona_context()` to append blocks (order matters for prompt quality):

1. Acting on behalf of `{display_name}`
2. Standing instructions (if non-empty)
3. Priority hints (if non-empty)
4. Message context summary (if non-empty) — prefix with “Recent context (owner-provided summary):”
5. Availability + timezone + notification email (existing)

Keep total injected context **≤ ~1,500 characters**; truncate with ellipsis in code if needed.

### New file: `server/contact_overrides.json`

```json
{
  "+14155550142": {
    "handling_note": "VIP — escalate immediately",
    "relationship_override": "partner"
  },
  "Sam Rivera": {
    "handling_note": "Coworker on Project Atlas; expects quick callback"
  }
}
```

Key resolution order in `persona_tools._persona_lookup` (after contacts hit):
1. Normalized E.164 from `contacts.lookup`
2. Fallback `persona_id` (contact name string)

Merge `handling_note` into caller snapshot as `agent_handling_notes` when `lookup_persona` runs (or in `greeting_for_caller`).

---

## API changes (`onboarding_api.py`)

Extend `OwnerConfigBody` + GET/POST `/api/owner-config` with the four new owner fields.

Add (Gate B):

| Method | Path | Body | Response |
|--------|------|------|----------|
| GET | `/api/contacts/overrides` | — | `{ overrides: Record<string, { handling_note?, relationship_override? }> }` |
| PUT | `/api/contacts/overrides` | same shape | saved dict |
| GET | `/api/contacts/list` | — | `{ contacts: [{ name, relationship?, has_note }] }` — **no phone numbers in response** |

Proxy in `web/vite.config.ts` if not already forwarding `/api/*` to onboarding API.

Update `web/src/lib/api.ts` types + `fetchOwnerConfig` / `saveOwnerConfig`.

---

## Web UI tasks (`web/`)

1. **`setup-page.tsx`**
   - Add textarea fields (use shadcn-style `Input` or a plain `<textarea>` with matching classes).
   - Persist on Continue via existing `persist()` / `saveOwnerConfig`.
   - Step 4 summary: show truncated standing instructions + “N contact notes” if overrides exist.

2. **`api.ts`**
   - Extend `OwnerConfig` type.
   - Optional: `fetchContactList`, `saveContactOverrides`.

3. **Landing copy**
   - Either qualify “messages” as “paste a short summary” until Gate C, or leave as-is until live ingestion ships.

4. **Manual test**
   - `uv run onboarding_api.py` + `npm run dev`
   - Complete setup → `server/owner_config.json` contains new fields
   - `ENV=local uv run bot-nemotron.py` → log or debug-print `build_persona_context()` once at call start (do not commit debug prints)

---

## Bot integration (no P1 fork)

| Change | Owner file | Notes |
|--------|------------|-------|
| Richer `build_persona_context()` | `owner_config.py` | Already called from `bot-nemotron.py` |
| Read `contact_overrides.json` | `contacts.py` or new `contact_overrides.py` | Load at import; `reload_overrides()` after API PUT |
| Inject `handling_note` on match | `persona_tools.py` | Set `agent_handling_notes` on snapshot when override exists |
| Gate C: merge P2 iMessage digest | P2 module | Sets `message_context_summary` via API or env — **out of scope for Gate A** |

Do **not** register new LLM tools for context editing mid-call.

---

## Tests

**`server/test_owner_config.py`** (new, small):
- `build_persona_context()` includes standing instructions and priority hints when set.
- Truncation when summary > limit.

**`server/test_p3.py`** (extend):
- `_persona_lookup` + override file → snapshot gets `agent_handling_notes`.

**Optional Playwright / manual:** setup flow saves and reloads on refresh.

---

## Phased acceptance criteria

### Gate A — Owner context in UI (1 session)

- [ ] Setup collects standing instructions + priority hints + message summary textarea.
- [ ] Values persist in `owner_config.json` via onboarding API.
- [ ] `build_persona_context()` reflects all fields; bot system prompt includes them on WebRTC test call.
- [ ] `uv run pytest -q` green; `npm run build` green.

### Gate B — Per-contact notes (1 session, after A)

- [ ] Owner can add handling note for at least one imported contact name.
- [ ] Known caller test (`test_p3.py` Sam Rivera fixture) receives note in snapshot / prompt path.
- [ ] API never returns raw phone numbers to the browser (names only).

### Gate C — Live iMessage (blocked on P2)

- [ ] P2 produces a **redacted digest** (topics + urgency, not full threads).
- [ ] Digest writes to `message_context_summary` or a dedicated field consumed by `build_persona_context()`.
- [ ] UI shows “Synced from Messages” read-only block + last-updated timestamp; owner can edit/override.

---

## Prompt 1 — Gate A: Owner context fields (copy into a fresh agent session)

> **Role:** Implement owner-level context input in the Gotchu web setup flow and wire it into the voice bot’s `persona_context`.
>
> **Read first:** `server/UI_CONTEXT_PLAN.md` (Gate A), `server/owner_config.py`, `server/onboarding_api.py`, `web/src/pages/setup-page.tsx`, `web/src/lib/api.ts`, `server/bot-nemotron.py` (persona_context injection ~line 250).
>
> **Goal:** Let the owner type standing instructions, priority hints, and an optional message-context summary during setup. The Nemotron bot must see this text in `call_state["persona_context"]` on the next call.
>
> **Tasks:**
> 1. Add fields to `owner_config.DEFAULT`: `standing_instructions`, `priority_hints`, `message_context_summary`, `agent_tone` (default `"professional"`).
> 2. Extend `build_persona_context()` to include them with clear sentence boundaries; cap total length ~1500 chars.
> 3. Extend `OwnerConfigBody` in `onboarding_api.py` and merge in `save_owner_config`.
> 4. Extend `OwnerConfig` in `web/src/lib/api.ts`.
> 5. Add UI in setup step 0 or a new “Context” step: three textareas + optional tone select. Save on Continue.
> 6. Update setup summary step to show that context was configured (truncated preview).
> 7. Add `test_owner_config.py` with 2–3 tests for `build_persona_context()`.
>
> **Acceptance:**
> - Fill setup in browser → `owner_config.json` has new keys.
> - WebRTC call → agent behavior reflects a distinctive standing instruction (e.g. “Always say the owner is in a meeting until 5pm”).
> - `uv run pytest -q && uv run pyright && uv run ruff check .` pass.
>
> **Guardrails:**
> - Do not add iMessage scraping or new dependencies.
> - Do not change `interfaces.py` contract fields.
> - Minimize diff — match existing setup page styling.

---

## Prompt 2 — Gate B: Per-contact handling notes (after Prompt 1)

> **Role:** Add per-contact handling notes editable from the setup “Contacts” step and inject them when `lookup_persona` matches a caller.
>
> **Read first:** `server/UI_CONTEXT_PLAN.md` (Gate B), `server/contacts.py`, `server/persona_tools.py` (`_persona_lookup`, `greeting_for_caller`), `server/onboarding_api.py`.
>
> **Goal:** After vCard upload, the owner can attach a short handling note to a contact **by name**. When that person calls, `agent_handling_notes` (or equivalent) is set on the caller snapshot before the greeting.
>
> **Tasks:**
> 1. Create `contact_overrides.json` + load/save helpers (`contact_overrides.py` or functions in `contacts.py`).
> 2. API: `GET/PUT /api/contacts/overrides` and `GET /api/contacts/list` (names + `has_note` only — no phone numbers in JSON).
> 3. UI: After successful vCard upload, list sample names (reuse `sample_names` from health/upload) with a small note input per row **or** a single textarea “VIP notes (one line per contact: Name — note)”.
> 4. In `_persona_lookup` or immediately after match in `lookup_persona` / `greeting_for_caller`, merge override into snapshot `agent_handling_notes`.
> 5. Extend `test_p3.py` with override file fixture.
>
> **Acceptance:**
> - Note for “Sam Rivera” (test fixture number) appears in snapshot after lookup.
> - Overrides survive bot restart (JSON on disk).
> - List endpoint never exposes phone numbers.
>
> **Guardrails:**
> - Reload overrides after PUT without requiring bot restart (`reload_overrides()` + call from upload handler if needed).
> - Keep vCard as source of truth for names/numbers; overrides are annotations only.

---

## Prompt 3 — Gate C: P2 iMessage digest → UI (blocked on P2)

> **Role:** Wire P2’s redacted iMessage digest into owner config and show it read-only in setup with owner override.
>
> **Prerequisites:** P2 module landed that exports something like `build_message_digest() -> str` (topics/urgency only, no raw chat bodies).
>
> **Read first:** `server/UI_CONTEXT_PLAN.md` (Gate C), P2 handoff doc, `owner_config.py`, `interfaces.py` persona injection comment block.
>
> **Goal:** On sync, P2 updates `message_context_summary` (or `imessage_digest` + merge in `build_persona_context`). UI shows last synced time and editable override textarea.
>
> **Tasks:**
> 1. Coordinate field name with P2 — prefer reusing `message_context_summary` with a `message_context_source: "manual" | "imessage"` flag.
> 2. Add `POST /api/context/sync-messages` (or P2 cron) that refreshes digest; never store raw messages in API responses.
> 3. Setup UI: “Recent messages (summary)” read-only panel + “Edit summary” toggle.
> 4. Privacy test: grep persisted JSON / Dynamo records for iMessage-like content patterns in CI fixture.
>
> **Acceptance:**
> - Digest visible in setup after sync.
> - Bot prompt includes digest content.
> - Owner manual edit wins over auto-sync until next explicit re-sync.

---

## Coordination checklist

| Owner | Action |
|-------|--------|
| **Web / onboarding** | Gates A & B — this doc |
| **P3** | `contact_overrides` merge in `persona_tools` |
| **P2** | Gate C digest producer + `calendar_free_busy_check` (separate track) |
| **P1** | No `run_bot()` changes expected for A/B |

---

## Out of scope (explicit)

- Full contact CRUD in the browser (create/delete phone book entries).
- Storing or displaying raw iMessage/SMS transcripts.
- Google Calendar OAuth UI (separate P2 task; keep `manual_availability` until then).
- Cekura eval loop changes.
