# Eval Harness — Voicemail Persona Agent (Person 2)

The Cekura auto-improvement loop and its local mock twin. This is Person 2's
deliverable: **simulate calls → score against metrics → auto-rewrite the
agent prompt from failures → re-score → show metrics flip FAIL → PASS.**

It runs **today with zero external dependencies** (offline stub engine), and
transparently upgrades to local Ollama / OpenAI / real Cekura as those become
available — so the demo never hard-fails.

## What's here

| File | Role |
|---|---|
| `personas.py` | 7 caller personas (spammer, important client, friend, vague caller, persistent salesperson, emergency, wrong number) with ground-truth expected outcomes. |
| `metrics.py` | 5 LLM-as-judge metrics: correct_screening, message_captured, persona_consistency, caller_sentiment, task_completion. |
| `simulate.py` | Local caller↔agent conversation simulator (stands in for Person 1's live bot). |
| `judge.py` | LLM-as-judge scoring of a transcript against a metric. |
| `run_evals.py` | Orchestrator → scorecard (per-persona, per-metric, weighted aggregate). |
| `improve.py` | **The auto-improvement loop. The demo finale.** |
| `cekura_client.py` | Maps personas/metrics → verified Cekura payloads; documents the MCP provisioning plan (`PROVISION_PLAN`). No network. |
| `llm.py` | Engine abstraction: Ollama (compute-box→localhost) → OpenAI → offline stub. |
| `prompts/voicemail_agent_v0.txt` | Deliberately weak starting prompt (so the loop has room to improve). |
| `test_eval.py` | Hermetic tests (run on the stub engine; no network needed). |

## Run it

From `server/`:

```bash
# Baseline scorecard for the current (weak) prompt
uv run python -m eval.run_evals

# THE DEMO: auto-improvement loop (baseline → rewrite → re-score)
uv run python -m eval.improve --rounds 1

# Tests (offline, deterministic)
CEKURA_EVAL_ENGINE=stub uv run pytest eval/test_eval.py -q
```

Pin the engine with `CEKURA_EVAL_ENGINE=stub|ollama|openai|auto`. With nothing
running, `auto` falls back to the deterministic **stub** so everything still
executes; bring up Ollama on `compute-box` (or set `OPENAI_API_KEY`) for real
LLM scoring.

### Example loop result (stub engine — illustrative)

```
v0 baseline (weak prompt):  correct_screening 0% pass,  overall 60%,  aggregate 0.698
v1 auto-improved:           correct_screening 100% pass, overall 100%, aggregate 0.888
```

These numbers come from the deterministic **stub** engine, so they are
illustrative of the loop's *shape* (failures → rewrite → flips to PASS), not a
real model's scores. Run on Ollama/OpenAI for meaningful numbers. Each improved
prompt is saved to `prompts/voicemail_agent_vN.txt` and every scorecard to
`results/*.json` (both gitignored as runtime output) — an auditable improvement
history Person 3 can persist to AWS.

## Real Cekura mode (MCP-driven, in-session)

The Python harness runs **mock only**. Real provisioning and real runs against
the live agent are driven by the Cekura **MCP tools** in-session (the verified
contract) — not from Python. The same personas/metrics are mapped to verified
payloads by `cekura_client` and created live. Prerequisites:

1. `CEKURA_API_KEY` configured for the **MCP server** (not just `server/.env`).
2. A project the key **owns** (never auto-derived from a foreign scenario — that
   returns 403).
3. For runs: Person 1's bot registered as a Cekura agent.

The provisioning sequence is kept in sync as data in
`cekura_client.PROVISION_PLAN`:

1. `user_organizations_list` / `projects_list` → pick an **owned** `project_id`
2. `personalities_list` → pick a `personality_id`
3. `metrics_create` ×5 ← `cekura_client.all_metric_payloads()`
4. `scenarios_create` ×7 ← `cekura_client.all_scenario_payloads()` (attach metrics)
5. `aiagents_create` → register Person 1's bot (pipecat / self_hosted)
6. `scenarios_run_text` / `scenarios_run_pipecat_v1` / `_v2` → run each scenario
7. `results_retrieve` → read scores from `runs[].evaluation.metrics[].result`
8. on failure: `runs_improve_prompt` → regenerate prompt → re-run → flip to PASS

Created IDs are written to `cekura_ids.json` (gitignored). Inspect the payloads
and plan any time (no network): `uv run python -m eval.cekura_client`.

## Status vs. PLAN.md (Person 2)

- **Part C (Cekura loop) — mock loop DONE; live path is MCP-driven.** Personas,
  metrics, scorecards, and the auto-improve loop all work end to end offline.
  Live metrics/scenarios are provisioned via the MCP tools into an owned project
  (see `PROVISION_PLAN`); running them needs Person 1's agent registered.
- **Part A/B (persona_context.md, iMessage, live Calendar)** — separate files
  at `server/persona_context.{md,py}` (not in this harness). Built next.

## Note on the "agent prompt" being improved

`prompts/voicemail_agent_v0.txt` is a stand-in target because Person 1's live
system prompt wasn't frozen yet. When P1 posts the frozen prompt, point the
loop at it: `uv run python -m eval.improve --prompt <path> --rounds 1`. The
improved versions are what feed back into the bot — that's the "auto-improve"
half of the hackathon's continuous-feedback theme.
