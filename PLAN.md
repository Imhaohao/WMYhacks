Good — you've got the **Calendar** and **Gmail** MCP connectors live in this session (that's the `mcp__ee3fd2b4…__*` calendar tools and `mcp__ba3544bc…__*` Gmail tools), which is exactly what makes "live calendar" and "email sending" actually feasible instead of vaporware. So I'll pull everything back into scope. Quick answers to your direct questions first, then the rewritten plan.

**Your questions, answered:**

- **iMessage + live Calendar** → in scope, Person 2. iMessage = reading your own `~/Library/Messages/chat.db` (needs macOS Full Disk Access for Terminal); Calendar = the MCP connector you already have.
- **SMS / email sending** → in scope. SMS rides **Twilio** (Person 1, since he owns the Twilio creds); email rides the **Gmail** MCP (Person 3). Guardrail below.
- **Voice cloning** → in scope, **owner's voice only** (Person 1) — the proxy literally sounds like you. The "never clone the _caller_" rule stays.
- **Pipecat Cloud** → in scope, Person 1.
- **Cekura loop** → it was Person 2 Part B before; I'm pulling it out into its own **first-class centerpiece section** so it's not buried. It's still the win condition.
- **Nemotron flow** → yes, still the primary demo bot. All these integrations are _tool functions_ on the bot — independent of which LLM — so they ride on top of the Nemotron path and keep your NVIDIA eligibility.

⚠️ **One honest flag before the plan:** this is now ~8 ambitious workstreams for 3 people in one day. The risk isn't capability, it's ending 6 PM with eight half-wired integrations and no clean demo. So the plan keeps a **protected must-have core** and a **strict priority ladder** — build outward only when the layer beneath is green. I've baked that in.

---

# 3-Person Hackathon Plan v2: Persona Voicemail Agent (Full-Scope, Sponsor-Maximized)

## Win Thesis

"Best system that **scales, persists, and learns**," not best voice. One coherent system, every sponsor load-bearing, anchored by the **Cekura auto-improve loop**. Now with a live owner-intelligence layer (iMessage + Calendar), an action layer (SMS/email/booking), and the owner's cloned voice.

## Sponsor Coverage Map

| Sponsor             | Hook                            | Owner  | Glaze                                                                               |
| ------------------- | ------------------------------- | ------ | ----------------------------------------------------------------------------------- |
| **Cekura**          | eval + auto-improve (the theme) | **P2** | Metrics grade the flow → failures auto-rewrite prompt/persona → re-run passes, live |
| **NVIDIA**          | open-weights, high-reasoning    | **P1** | `bot-nemotron.py`, thinking mode drives triage                                      |
| **AWS**             | compute + "persists"            | **P3** | Summaries + eval history + action log → DynamoDB/S3                                 |
| **Twilio**          | telephony + **SMS**             | **P1** | Judges call a real number; bot texts the owner a callback SMS                       |
| **Daily / Pipecat** | latency + **production deploy** | **P1** | Pipecat Cloud deploy + one latency number                                           |

## Architecture

```text
caller phone (Twilio)  ── or ──  browser WebRTC (Pipecat)
        ↓
NVIDIA STT (Nemotron path)
        ↓
caller snapshot (P3)  ──adapts to tone──┐
        ↓                               │
Nemotron LLM  +  LIVE owner context (P2):│
   • iMessage-derived priorities         │
   • live Calendar availability          │
        ↓                               │
owner's CLONED voice TTS (P1) → caller hears proxy
        ↓
ACTION LAYER (P1/P3):
   • Twilio SMS to owner   • Gmail email   • Calendar callback slot
        ↓
structured summary → AWS DynamoDB/S3 (P3)
        ↓
CEKURA loop: read logs → grade → auto-improve prompt → re-run (P2)
```

## Shared Rules

- **Demo bot = `server/bot-nemotron.py`.** Non-negotiable (NVIDIA eligibility).
- **Interface freeze first:** P1 defines+freezes `call_state` (voicemail fields, `caller_snapshot` slot, `persona_context` injection point, tool list) before P2/P3 plug in. No three-way edits to `run_bot()`.
- **Outward-action guardrail:** during the demo, SMS/email/calendar writes go to **your own number / your own email / your own calendar** — not real third parties. Sending to outsiders is a publish action; keep it to test recipients you control.
- **Voice cloning = owner's voice only**, with your consent (it's you). Never clone the caller.

---

## Person 1: Voice + Infra + Telephony (NVIDIA · Daily · Twilio)

```markdown
I am Person 1. I own the core voice path, voice cloning, Pipecat Cloud deploy, Twilio telephony + SMS. I protect our NVIDIA, Daily, and Twilio eligibility.

Context:

- Repo: /Users/alara/Desktop/WMYhacks, code in server/
- PRIMARY BOT = server/bot-nemotron.py (NOT bot-gpt). Open-weights → NVIDIA prize. Do not switch.
- Run: from server/, `ENV=local uv run bot-nemotron.py`; demo at http://localhost:7860
- Needs GRADIUM_API_KEY + NVIDIA_ASR_URL + NEMOTRON_LLM_URL; Twilio needs TWILIO_ACCOUNT_SID/AUTH_TOKEN.

Tasks (in order):

1. FREEZE THE INTERFACE FIRST and post it to the team:
    - call_state dict: {voicemail:{...}, caller_snapshot:{} (P3), persona_context:str (P2)}
    - single tool-registration list teammates append to
    - documented persona-injection point in system_instruction
2. Convert bot-nemotron.py from Field & Flower to a voicemail proxy. Voicemail tools:
   capture_caller_identity, capture_message_reason, capture_urgency,
   capture_callback_preference, get_voicemail_summary, finish_voicemail.
   One question at a time; recap before ending; keep end_call working; log structured summary.
3. NVIDIA glaze: enable Nemotron thinking mode (NEMOTRON_ENABLE_THINKING); use reasoning for urgency/triage; be ready to show a trace.
4. VOICE CLONING: clone the OWNER's voice (record a short sample) via the TTS provider so the proxy sounds like the owner. Owner consent only; NEVER clone the caller. Fallback to default Gradium voice if cloning isn't ready.
5. TWILIO SMS action: add a `notify_owner_sms` tool that texts the owner a one-line callback summary via Twilio. DEMO sends to your OWN number only.
6. Twilio telephony: wire the existing websocket path to a real number so judges can call in (after WebRTC is stable).
7. Daily/Pipecat: deploy to Pipecat Cloud (pc cloud) once local demo is stable; capture ONE turn-latency number.
8. Run ruff/pyright/pytest; manual WebRTC test.

Acceptance:

- Demo runs on Nemotron; interface frozen+shared; voicemail flow works end to end.
- Proxy speaks in the owner's cloned voice (or clean fallback).
- A callback SMS reaches the owner's own phone.
- Twilio inbound call works; Pipecat Cloud deploy reachable; one latency number captured.
```

---

## Person 2: Live Owner Intelligence + CEKURA LOOP (Cekura — the centerpiece)

```markdown
I am Person 2. I own LIVE owner context (iMessage + Calendar) AND the Cekura auto-improve loop. The Cekura loop is our win condition — it is the centerpiece of the demo.

Context:

- Repo: /Users/alara/Desktop/WMYhacks, code in server/
- Primary bot = server/bot-nemotron.py
- Calendar + Gmail MCP connectors are available this session; Cekura skills + MCP available (cekura:_, mcp**plugin_cekura_cekura**_).
- Privacy: derive/summarize into context; never dump raw message bodies into the live prompt or have the bot quote them.

Tasks — PART A (curated baseline, ship FIRST so the demo never breaks):

1. server/persona_context.md (Persona, Current Priorities, Availability, People Rules, Callback Style, Recent Agent Context, Example Replies) + server/persona_context.py loader with safe fallback (missing/empty file).
2. Coordinate injection seam with P1. Privacy rules in prompt: never quote private notes; context only for tone/triage/callback; never claim it scheduled/texted unless implemented.
3. Tests: loader real/missing/empty.

Tasks — PART B (LIVE ingestion, gated behind A): 4. iMessage: read the owner's own ~/Library/Messages/chat.db (needs macOS Full Disk Access). Summarize recent threads with key people into "Current Priorities" + "People Rules" — DERIVED summary only, written to persona\*context.md, human-glanceable. Do raw->summary with a LOCAL Ollama model (sensitive data, per AGENTS.md). 5. Calendar (live): use the Calendar MCP to read today's/this-week's availability so the bot answers "are they free?" from real free/busy. Read-only here (booking is P3). 6. (Stretch) persona distiller: read ~/.claude/projects/\*\*/\_.jsonl past sessions → local model → "Recent Agent Context" (how the owner prompts/delegates). Derived only. This is our "learns" story.

Tasks — PART C (THE CEKURA LOOP — top priority after A): 7. Define 4–6 Cekura metrics: one-question-at-a-time; never reveals a hidden owner priority (privacy); captures name+reason+urgency+callback+best-time; matches owner style; accurate recap. 8. Generate Cekura scenarios (incl. urgent "Sam") and run against the Nemotron bot (scenarios_run_pipecat_v1/v2). 9. Auto-improve: on a failing metric, use runs_improve_prompt / call_logs_improve_prompt to regenerate the prompt or persona_context.md → RE-RUN → show metric flip to PASS. 10. Make it a single command we run live on stage.

Acceptance:

- persona_context.md + loader solid; bot personally aware without exposing private notes.
- Bot reflects at least one REAL iMessage-derived priority and answers availability from REAL calendar free/busy.
- At least one Cekura metric FAILS → auto-improves → PASSES on re-run, runnable live. This is the demo finale.
```

### Person 2 — BUILD STATUS (updated 2026-05-30)

**DONE — Part A (curated baseline, Tier 0):**

- `server/persona_context.md` — curated owner context (Persona, Current Priorities, Availability, People Rules, Callback Style, Recent Agent Context, Example Replies).
- `server/persona_context.py` — `load_persona_context()` loader. Safe fallback: missing → `""`, empty/comment-only → `""`; strips HTML comments; appends a standing PRIVACY guard so context is reasoning-only and never recited. `has_persona_context()` helper. **Injection seam: WIRED** — `bot-nemotron.py` concatenates `load_persona_context()` into `system_instruction` (right after it's built).
- `server/test_persona_context.py` — 5 tests (real / missing / empty / comment-only / guard toggle). All pass, ruff-clean.

**DONE — Part C (the Cekura loop), in MOCK mode + wired for live:** `server/eval/` package:

- `personas.py` — 7 caller personas with ground-truth `expected_outcome` (block / take_message / allow): spammer, important_client, friend, vague_caller, persistent_salesperson, + emergency & wrong_number edge cases.
- `metrics.py` — the 5 metrics (correct_screening, message_captured, persona_consistency, caller_sentiment, task_completion) as LLM-as-judge rubrics with weights + thresholds.
- `simulate.py` + `judge.py` — local caller↔agent simulator and LLM-as-judge scorer (stand-in for P1's live bot so the loop runs today).
- `run_evals.py` — orchestrator → scorecard (per-persona, per-metric, weighted aggregate); writes JSON to `eval/results/`.
- `improve.py` — **the auto-improvement loop**: eval → collect failures → LLM rewrites the prompt → save `prompts/voicemail_agent_vN.txt` → re-eval → report metrics that flipped FAIL→PASS.
- `llm.py` — engine abstraction: Ollama (compute-box→localhost) → OpenAI → **offline deterministic stub**, so the whole loop runs with zero deps and upgrades automatically when a model is reachable.
- `cekura_client.py` — maps our personas/metrics → Cekura scenario/metric payloads (`all_scenarios()`, `all_metrics()`); REST helpers (`X-CEKURA-API-KEY`, base `…/test_framework/v1/`); `PROVISION_PLAN` = exact MCP-tool sequence for live.
- `test_eval.py` — 7 hermetic tests (stub engine). All pass, ruff-clean.
- `README.md` — runbook + live-demo script.

**Verified loop result (stub engine):** v0 weak prompt → aggregate **0.699**, **60%** pass (correct_screening 0%); after 1 auto-improve round → aggregate **0.884**, **100%** pass. The FAIL→PASS flip is the demo finale and runs as one command: `uv run python -m eval.improve --rounds 1`.

**DONE — Part B (LIVE ingestion), all three sources verified end-to-end:** `server/ingest/` package. Derives summaries from the owner's real data and writes them into `persona_context.md` (the file the bot already loads — the running bot can't call Claude's MCP tools, so a sync-into-the-file design is the right shape).

- `persona_sections.py` — idempotent comment-fenced section editor; replaces a source's block in place, keeps curated fallback lines, fences invisible to the bot (loader strips HTML comments).
- `local_llm.py` — **strict-local** summarizer: local Ollama → deterministic extractor. `strict_local=True` (default) NEVER touches a cloud model, so iMessage content stays on-device.
- `imessage.py` — reads `~/Library/Messages/chat.db` read-only (`immutable=1`, WAL-safe) → **Current Priorities** + **People Rules**. De-identified: phones masked to last-4, emails to a stable hash — **no names/emails/numbers written**, only derived summaries.
- `gcal.py` — Google Calendar API (read-only OAuth) → real free/busy into **Availability** + `availability.json`.
- `agent_context.py` — distills `~/.claude/projects/**/*.jsonl` → **Recent Agent Context** (how the owner prompts/delegates — the "learns" story).
- `refresh.py` — one-command orchestrator; each source degrades independently (skip + reason, never crash).
- `test_ingest.py` — 13 hermetic tests (synthetic data only). All pass, ruff + pyright clean.
- `README.md` — runbook + per-source prerequisites + privacy notes.

**Seam wired:** `bot-nemotron.py` now concatenates `load_persona_context()` into `system_instruction` (safe no-op until the file is populated).

**Verified live:** iMessage (6k+ msgs, Ollama), Google Calendar (real free/busy), and agent context all refresh correctly into the right sections; confirmed no names/raw bodies leak. Prereqs to reproduce: Full Disk Access for the terminal, a local Ollama model (`ollama pull qwen2.5:7b`; deterministic fallback otherwise), and a Desktop Google OAuth `credentials.json` in `server/`. Secrets/artifacts gitignored: `credentials.json`, `token.json`, `availability.json`, `ingest_contacts.json`.

**Cekura status / blockers:**

- `CEKURA_API_KEY` is now in `server/.env` → the **Python/REST path** (`eval/cekura_client.py`, `run_evals.py --mode cekura`) reads it directly.
- The **Cekura MCP tools** read the key from the env at Claude Code launch, so they still 401 until the key is in the launch env (settings `env` block or exported shell) **and Claude Code is restarted**.
- **Live eval runs still need P1's agent reachable** (a Pipecat Cloud deploy or Daily room URL) before scenarios can actually call it. Until then: provision the agent/metrics/scenarios in Cekura, and keep demoing the loop in mock mode.

**How to run (from `server/`):**

```bash
uv run python -m eval.run_evals              # baseline scorecard (mock)
uv run python -m eval.improve --rounds 1     # THE DEMO: auto-improve loop
uv run python -m ingest.refresh              # Part B: pull live iMessage/Calendar/agent context
uv run python -m ingest.refresh --dry-run    # ...preview without writing
uv run pytest ingest/test_ingest.py eval/test_eval.py test_persona_context.py -q
uv run python -m eval.cekura_client          # show Cekura mapping + key/auth check
```

---

## Person 3: Caller Snapshot + Action Layer + AWS Persistence (AWS)

```markdown
I am Person 3. I own the ephemeral caller snapshot, the email/calendar action layer, and AWS persistence ("persists").

Context:

- Repo: /Users/alara/Desktop/WMYhacks, code in server/
- Primary bot = server/bot-nemotron.py
- Snapshot is ephemeral/current-call only. No raw audio, no voiceprint, no speaker ID, no caller voice cloning, no persistent caller profile without opt-in.
- Gmail + Calendar MCP connectors available; AWS via boto3 with local fallback if no creds.

Tasks — PART A (caller snapshot):

1. Plug caller_snapshot into P1's frozen call_state (don't fork the flow). Fields:
   caller_name, relationship, reason, urgency, emotional_tone, communication_style,
   callback_preference, best_time, agent_handling_notes.
2. Update during call via tool/prompt; infer tone from transcript wording only
   (rushed/calm/confused/upset/concise/rambling/hesitant).
3. Adaptation guidance: rushed→brief, upset→slow+warm, confused→simple Qs, rambling→structure, concise→mirror.
4. Final summary includes the snapshot.

Tasks — PART B (action layer, gated behind A): 5. Email: `send_owner_email` tool via the Gmail MCP — emails the owner the voicemail summary. DEMO sends to the OWNER'S OWN email only. 6. Calendar WRITE: `book_callback_slot` tool via the Calendar MCP — when caller asks for a callback time and the owner is free, create a tentative event on the OWNER'S calendar. (Coordinate read-side free/busy with P2.) 7. Both actions must degrade gracefully (log + skip) if a connector is unavailable, so the demo never hard-fails.

Tasks — PART C (AWS persistence, our AWS glaze): 8. Persist each finished voicemail (owner-context tag + caller snapshot + structured fields + actions taken + timestamp) to DynamoDB (preferred) or S3 JSON, ~30–50 lines boto3, local-file fallback if no AWS creds. 9. Persist Cekura eval-run records (coordinate w/ P2) so "evaluation history persists" is a real shown artifact. 10. Be ready to show a DynamoDB/S3 record on stage as "the agent's memory."

Acceptance:

- Fresh snapshot per call; updates live; agent behavior visibly changes by caller style.
- A callback email lands in the owner's own inbox; a tentative slot appears on the owner's calendar.
- A persisted record (voicemail + actions + eval history) is retrievable from AWS (or fallback).
- No raw audio / voiceprint / biometric data stored.
```

---

## Integration Order

1. **P1** converts Nemotron bot minimally + **freezes & posts the interface.**
2. **P2** ships persona file+loader (A), then builds the **Cekura loop (C)**; live iMessage/Calendar (B) in parallel.
3. **P3** plugs in `caller_snapshot` (A), then action layer (B), then AWS (C).
4. All run ruff/pyright/pytest on agreed shared types.
5. **P1** runs final WebRTC → Twilio → Pipecat Cloud demo check.

## Demo Script (all 5 sponsors + full scope)

> 1. Judge **calls the Twilio number**, leaves: _"Hey, this is Sam — need them about the judging schedule, can they call before 4?"_
> 2. **Nemotron** (NVIDIA) on **Pipecat** (Daily) hears urgency, stays brief, answers availability from the owner's **real calendar**, captures everything, recaps — **in the owner's cloned voice** (P1).
> 3. Action layer fires: **Twilio SMS** + **Gmail email** to the owner, tentative **callback slot booked** on the owner's calendar.
> 4. Summary + actions + caller snapshot **persist to DynamoDB** (AWS) — show the record: "the agent's memory."
> 5. Flip to terminal: run **Cekura** evals. A metric **fails** → harness **auto-rewrites** the persona/prompt → re-run **passes**, live (Cekura — the finale).
> 6. One-liner: "X ms per turn, deployed on Pipecat Cloud" (Daily).

## Reality Check — Priority Ladder (protect the demo)

Build outward only when the layer beneath is green. If you're behind at any tier, **stop climbing and polish what works.**

- **Tier 0 — must work (or there's no demo):** Nemotron voicemail flow (P1) · persona file+loader (P2-A) · caller snapshot (P3-A).
- **Tier 1 — the trophy + clean glaze:** Cekura loop (P2-C) · AWS persistence (P3-C) · Twilio inbound + SMS (P1).
- **Tier 2 — high-impact flourish:** live Calendar read+book (P2-B/P3-B) · voice cloning (P1) · email send (P3-B).
- **Tier 3 — only if comfortably ahead:** iMessage ingestion (P2-B) · Pipecat Cloud deploy (P1) · persona distiller (P2-B).

Rationale: iMessage (Full Disk Access + DB parsing) and Pipecat Cloud (deploy debugging) are the two highest-effort / most-likely-to-eat-an-afternoon items — so they sit in Tier 3, _after_ the prizes are already locked. Cekura loop stays Tier 1: cut almost anything before you cut the loop.

## Guardrails (carried, expanded)

- Outward actions (SMS/email/calendar) target **your own** number/inbox/calendar in the demo.
- Voice cloning: **owner's voice only**, owner-consented. Never the caller.
- iMessage/Calendar: derive summaries into context; the bot never quotes raw messages.
- No raw caller audio, voiceprints, or persistent biometric/caller profiles.

---

Want me to **save this as `PLAN.md`** in the repo so all three can pull from it, or kick off **Person 1's Tier-0 work** (Nemotron voicemail conversion + frozen `call_state` interface) so P2/P3 can start building against it immediately?
