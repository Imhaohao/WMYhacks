# MCP_AGENTS.md

MCP operations contract for the WMYhacks voice agent. Read this **before** any
Cekura / Gmail / Calendar tool call. It is the bridge between the prompting
doctrine in [`AGENTS.md`](AGENTS.md) and the live connectors that the Pipecat
bot itself cannot reach. Per-client wiring lives in
[`docs/agent-setup/`](docs/agent-setup/).

## 1. Session roles (who can call what)

- The **bot** (`bot-gpt.py` / `bot-nemotron.py`) runs in a terminal. It has **no
  MCP access** — it queues email/calendar requests to an outbox JSONL via
  [`server/actions.py`](server/actions.py).
- The **agent session** (Claude Code / Cursor / Codex) is the only place MCP
  tools exist. Draining the outbox and calling Cekura happen here, never from
  the bot process. Do not try to add a headless MCP client to the bot.

## 2. Env checklist

Set in `server/.env` (see [`server/.env.example`](server/.env.example)); never
commit real values.

| Var | Used by | Notes |
|-----|---------|-------|
| `CEKURA_API_KEY` | Cekura MCP + `eval/cekura_client.py` | shell env or `.env`; restart client for MCP |
| `CEKURA_BASE_URL` | Python eval REST | default `https://api.cekura.ai` |
| `CEKURA_PROJECT_ID` | Cekura writes | owned project only (foreign → 403) |
| `CEKURA_EVAL_ENGINE` | Mock harness LLM | `auto` \| `ollama` \| `openai` \| `stub` |
| `OWNER_EMAIL` | SMTP inbox delivery | the only outbound email/calendar target for the demo |
| `GMAIL_APP_PASSWORD` | SMTP inbox delivery | 16-char app password; enables real inbox send |
| `NIA_API_KEY` | Nia docs search (Cursor) | env reference only — keep out of committed config |
| AWS persistence vars | `persistence.py` | for `persist_eval_run` / `persist_voicemail` records |

## 3. Outbox drain SOP (after a bot test call)

The bot queues actions; the agent fires them through MCP:

```bash
cd server
uv run python action_bridge.py --list           # show pending actions
uv run python action_bridge.py --emit            # print connector call specs
# agent executes each printed MCP call (gmail.create_draft / calendar.create_event)
uv run python action_bridge.py --done <action_id>  # mark fulfilled after success
```

The `--emit` output is a ready-to-fire spec (`connector` + `args`). Honor any
`needs` field (e.g. resolve `start_iso`/`end_iso` from `requested_time` before
firing a calendar event).

## 4. Gmail dual-path

- **SMTP (preferred for real inbox):** when `OWNER_EMAIL` + `GMAIL_APP_PASSWORD`
  are set, `actions.py` sends directly — no bridge needed, lands in the inbox.
- **MCP (`gmail.create_draft`):** the connector only exposes *create_draft*, not
  send. Use it as the drain lane fallback. Output is a **draft**, not a sent
  message. See [`server/INTEGRATION_P3.md`](server/INTEGRATION_P3.md).
- Only ever email `OWNER_EMAIL`. Never let caller-supplied text choose the
  recipient.

## 5. Calendar (write scope + tentative encoding)

- Requires a Calendar MCP connected with **write** scope (reconnect if read-only
  — current blocker noted in [`server/INTEGRATION_P3.md`](server/INTEGRATION_P3.md)).
- `create_event` has no status arg, so tentativeness is encoded by the bridge:
  title prefix `[Tentative]`, `colorId: "8"` (Graphite), and a `TENTATIVE —`
  note in the description. Keep this convention.

## 6. Cekura flows

Two paths — use the right one:

| Path | When | Entry |
|------|------|-------|
| **Python harness** (`server/eval/`) | Mock loop, auto-improve demo, offline CI | `uv run python -m eval.improve --rounds 1` |
| **Cekura MCP** | Provision agent/metrics/scenarios, live runs | skills + MCP tools in agent session |

**Python harness (works today, no deploy needed):**

```bash
cd server
uv run python -m eval.run_evals              # baseline scorecard (mock)
uv run python -m eval.improve --rounds 1     # auto-improve: FAIL → PASS flip
uv run python -m eval.cekura_client          # mapping + key/auth check
```

**Live Cekura (MCP session):**

- Create the agent with the **Pipecat** provider; run eval suites against a **reachable**
  agent URL (Pipecat Cloud / Daily room — blocker until P1 deploys).
- Persist results via `persistence.persist_eval_run` (pull with `results_list` first).
- MCP tools read `CEKURA_API_KEY` from the **launch environment** (restart client after
  setting). Python REST also reads `server/.env`.

**Owner context sync (bot cannot call MCP — file is the seam):**

```bash
cd server
uv run python -m ingest.refresh              # iMessage + Calendar + agent context → persona_context.md
uv run python -m ingest.refresh --dry-run
```

See [`PLAN.md`](PLAN.md) Person 2 build status and [`server/eval/README.md`](server/eval/README.md).

## 7. Verification checklist (after a demo call)

- [ ] One Gmail draft **or** one SMTP inbox email to `OWNER_EMAIL`
- [ ] One Calendar tentative event (`[Tentative]` title, Graphite color)
- [ ] One `eval_run` record persisted via `persistence.persist_eval_run`
- [ ] Every drained action marked `--done`
