# Part B — Live Owner Intelligence (`ingest/`)

**Status: live and verified** — iMessage, Google Calendar, sent Gmail style,
recent Claude/Codex agent context, local Git workflow, and exported Discord context run into
`server/persona_context.md`, and the bot reads it via the wired seam in
`bot-nemotron.py`. ChatGPT desktop ingestion is best-effort because some app
versions store conversations as opaque binary files.

Derives **real** owner context — recent iMessages, Google Calendar availability,
and how the owner prompts/delegates across local agent tools — and writes it into
`server/persona_context.md`, the file Part A's `load_persona_context()` injects
into the bot's system instruction. The bot just reads a richer file.

```
ingest/
  persona_sections.py   idempotent comment-fenced section editor for persona_context.md
  local_llm.py          strict-local summarizer (Ollama; deterministic fallback; never cloud for iMessage)
  voice_lingo.py        repeated short owner phrases -> privacy-bounded Persona lingo
  imessage.py           chat.db (read-only) -> priorities + people rules + owner lingo
  gcal.py               Google Calendar API -> Availability (+ availability.json)
  gmail_context.py      sent Gmail (read-only) -> Persona writing style
  agent_context.py      ~/.claude/projects/**/*.jsonl -> Recent Agent Context
  codex_context.py      ~/.codex/state_5.sqlite -> Recent Agent Context
  chatgpt_context.py    ChatGPT desktop JSON/JSONL, when decodable -> Recent Agent Context
  git_context.py        this repo's local Git metadata -> Recent Agent Context
  refresh.py            one-command orchestrator
```

## Run

```bash
# from server/
uv run python -m ingest.refresh --dry-run        # show derived bullets, write nothing
uv run python -m ingest.refresh                  # refresh all sources
uv run python -m ingest.refresh --sources agent  # one source (agent works with no setup)
uv run python -m ingest.refresh --sources agent,codex,chatgpt,git --dry-run
uv run python persona_context.py --upload-cloud  # republish current cleaned persona only
uv run python -m ingest.refresh --days 7
uv run pytest ingest/test_ingest.py -q
```

Each source degrades independently — a missing permission, model, or credential
**skips** that source with a clear reason and never crashes the run.

After a non-dry-run refresh, the cleaned persona is also uploaded to the
existing AWS persona store. The bot checks that cloud record first at startup,
then falls back to `persona_context.md` for local development. Set
`PERSONA_CLOUD_REQUIRED=true` in production to disable the local fallback.
Grant the deployed AWS role `dynamodb:PutItem` and `dynamodb:GetItem` on the
persona table. A `Scan` read fallback keeps older demo IAM policies working,
but `GetItem` is the preferred production permission.

## Prerequisites (per source)

| Source | Needs | Setup |
|---|---|---|
| **agent** | nothing | works today (reads your own Claude session logs) |
| **codex** | nothing | works today (reads your own local Codex thread metadata) |
| **chatgpt** | ChatGPT desktop local data | best-effort: safely decodable JSON/JSONL works; opaque binary files skip with a clear reason |
| **git** | a local Git checkout | works today (reads this repo's local metadata only) |
| **iMessage** | Full Disk Access | System Settings → Privacy & Security → **Full Disk Access** → add your terminal app, then restart it |
| **calendar** | Google OAuth client | create a **Desktop** OAuth client in Google Cloud Console → save JSON to `server/credentials.json` (or set `GCAL_CREDENTIALS`); first run opens a browser once, caches `server/token.json` |
| **gmail** | Connected Gmail OAuth | reconnect Gmail in the setup wizard once to grant read-only sent-mail access; only aggregate style signals are retained |
| summarizer | local Ollama (optional) | `ollama serve` + `ollama pull qwen2.5:7b`; without it, sources use the deterministic extractor |

Optional `server/ingest_contacts.json` (gitignored) maps a handle → a display
name you choose deliberately, so derived People Rules read "Sam" instead of the
masked default `contact …4821`:

```json
{ "+15551234821": "Sam", "first.last@example.com": "Dana" }
```

## How it stays private

- Only **derived summaries** are written — never raw message bodies. Voice
  lingo is limited to short snippets repeated across multiple owner-authored
  texts, with URLs, emails, phone numbers, paths, and secret-like values
  filtered out. One-off phrases are discarded.
- iMessage is summarized **strict-local** (local Ollama or deterministic
  extractor); its content never reaches a cloud model.
- Claude, Codex, ChatGPT, and Git memo sources are also summarized
  **strict-local**. Their raw prompts and metadata are reduced to aggregate
  style/workflow signals before summarization; prompt adapters may also retain
  the same bounded recurring-lingo snippets.
- Gmail sent-mail bodies stay in memory and are reduced to aggregate style
  signals plus bounded recurring-lingo snippets before strict-local
  summarization. Raw email bodies are never written.
- The cloud persona upload contains only cleaned derived context. Local
  authoring comments are stripped before upload.
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

`load_persona_context()` reads the cleaned cloud record first and returns `""`
if both cloud and local context are unavailable. Refreshing locally republishes
the shared cloud persona for deployed bots and other machines.
