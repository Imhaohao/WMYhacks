# Part B — Live Owner Intelligence (`ingest/`)

**Status: live and verified** — all three sources (iMessage, Google Calendar,
recent agent context) run end-to-end into `server/persona_context.md`, and the
bot reads it via the wired seam in `bot-nemotron.py`.

Derives **real** owner context — recent iMessages, Google Calendar availability,
and how the owner prompts/delegates — and writes it into `server/persona_context.md`,
the file Part A's `load_persona_context()` injects into the bot's system
instruction. The bot just reads a richer file.

```
ingest/
  persona_sections.py   idempotent comment-fenced section editor for persona_context.md
  local_llm.py          strict-local summarizer (Ollama; deterministic fallback; never cloud for iMessage)
  imessage.py           chat.db (read-only) -> Current Priorities + People Rules
  gcal.py               Google Calendar API -> Availability (+ availability.json)
  agent_context.py      ~/.claude/projects/**/*.jsonl -> Recent Agent Context
  refresh.py            one-command orchestrator
```

## Run

```bash
# from server/
uv run python -m ingest.refresh --dry-run        # show derived bullets, write nothing
uv run python -m ingest.refresh                  # refresh all sources
uv run python -m ingest.refresh --sources agent  # one source (agent works with no setup)
uv run python -m ingest.refresh --days 7
uv run pytest ingest/test_ingest.py -q
```

Each source degrades independently — a missing permission, model, or credential
**skips** that source with a clear reason and never crashes the run.

## Prerequisites (per source)

| Source | Needs | Setup |
|---|---|---|
| **agent** | nothing | works today (reads your own Claude session logs) |
| **iMessage** | Full Disk Access | System Settings → Privacy & Security → **Full Disk Access** → add your terminal app, then restart it |
| **calendar** | Google OAuth client | create a **Desktop** OAuth client in Google Cloud Console → save JSON to `server/credentials.json` (or set `GCAL_CREDENTIALS`); first run opens a browser once, caches `server/token.json` |
| summarizer | local Ollama (optional) | `ollama serve` + `ollama pull qwen2.5:7b`; without it, sources use the deterministic extractor |

Optional `server/ingest_contacts.json` (gitignored) maps a handle → a display
name you choose deliberately, so derived People Rules read "Sam" instead of the
masked default `contact …4821`:

```json
{ "+15551234821": "Sam", "first.last@example.com": "Dana" }
```

## How it stays private

- Only **derived summaries** are written — never raw message bodies.
- iMessage is summarized **strict-local** (local Ollama or deterministic
  extractor); its content never reaches a cloud model.
- **No real names, emails, or phone numbers** are written. Contacts are
  de-identified: phones show the last 4 digits (`contact …4821`), emails show a
  stable 4-char hash (`contact …a3f9`) — the email local-part is often a name,
  so it's never exposed. A friendly name appears only for handles you list in
  `ingest_contacts.json`.
- Generated content lives inside `<!-- BEGIN:ingest:* -->` / `<!-- END -->`
  fences. `persona_context.py` strips HTML comments on load, so the markers are
  invisible to the bot and `PRIVACY_GUARD` still applies — context is
  reasoning-only and never recited.
- Secrets and generated artifacts are gitignored: `credentials.json`,
  `token.json`, `availability.json`, `ingest_contacts.json`.

## Wiring to the bot (done)

The seam is already wired in `bot-nemotron.py`, right after `system_instruction`
is built:

```python
from persona_context import load_persona_context
persona = load_persona_context()
if persona:
    system_instruction += "\n\n" + persona
```

`load_persona_context()` returns `""` if the file is missing/empty, so this is a
safe no-op until a refresh populates it. Refreshing `persona_context.md` before
the bot starts is enough for the demo. `availability.json` (written by the
calendar source) plus an optional per-call reload in `on_client_connected` is a
documented stretch for P1.
