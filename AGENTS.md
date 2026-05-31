# AGENTS.md

Guidance for AI agents working in this repository (YC Voice Agents Hackathon — Pipecat voice agent).

## Model Routing (Executive Delegation)

The main agent routes work; it does **not** implement everything on the most expensive model. Approximate cost ratio: **flagship (Opus) ≈ 5× Sonnet ≈ 60× Haiku ≈ free for local Ollama** (compute only, no network, slower).

**Hackathon default:** use **Sonnet** for implementation when a spec or handoff doc exists (`P3_NEXT.md`, `INTEGRATION_P3.md`, a agreed plan). Use **Opus/flagship** only for routing, ambiguous architecture, cross-team coordination, or tasks in "Stay on the flagship" below. Do **not** set Opus as the default Claude Code model for coding loops — one long Opus session with tools can burn millions of input tokens while output stays tiny.

Delegation mechanisms:
- **Cloud (Anthropic) subagents**: `Agent` tool with `model: "sonnet" | "haiku" | "opus"`.
- **Local models**: `Bash` tool running `ollama run <model> "<prompt>"` (pipe long prompts via stdin or a temp file).

### Parallel and background execution (memory-aware)

This machine often runs a **dev server**, **voice-agent processes**, and **large local models** (for example `qwen3.5:35b-mlx`) at the same time. RAM is the bottleneck more often than API cost. Prefer throughput without piling heavy jobs on one core or one GPU context.

**Default: parallelize or background independent work**

- Launch **multiple subagents in one message** when their tasks do not depend on each other's output (e.g. explore repo layout + grep for a symbol + read one known file).
- Use **background subagents** (`run_in_background: true` on the `Task` tool, or equivalent) for work that can finish while you continue on the flagship thread — broad exploration, test runs, long `ollama run`, long-running bot processes.
- Do **not** block the executive turn waiting on a subagent if you can start it in the background and synthesize when it completes (or when the user asks for status).

**Serialize or avoid stacking when memory is tight**

- Do **not** run two **deliberate-lane** Ollama models (14B+) at the same time unless the user explicitly wants that and has headroom.
- Do **not** start a heavy local model while a **35B MLX** session is already loaded in another terminal unless necessary; prefer the **fast lane** (`qwen2.5-coder:7b`, `llama3.2:3b`) for concurrent local work.
- Prefer **Haiku/Sonnet subagents in parallel** over **multiple blocking `ollama run`** calls — cloud agents use remote compute; local models compete for the same machine RAM.

**Practical patterns**

| Situation | Prefer |
|---|---|
| 3+ independent lookups or file searches | One message, multiple Haiku/explore agents in parallel |
| Large repo exploration while you implement | Background explore agent; keep coding on flagship/Sonnet |
| Long local inference (35B, R1) | Background shell or single serial job; use fast lane for anything else |
| Running the bot + a WebRTC/voice check | Run `uv run bot-*.py` in a background terminal; verify in the browser in parallel |
| Dependent steps (A must finish before B) | Serial — no fake parallelism |
| Scoped implementation with a written spec | Sonnet (or Haiku for config/tests) — not Opus |
| Lint / pyright / pytest fix loops | Sonnet or Haiku subagent |
| Planning, critique, sponsor strategy (no code yet) | Short Opus turn, then **new session** to implement |

Before starting another heavy job, consider what is already running (a dev server, `ollama run`, a bot process). If the user reports slowness or swap, **reduce concurrent local models** and **background the slowest work** instead of adding another blocking call.

### Token budget and session hygiene

Agent billing is **`tokens_in ≈ (system + history + tool output) × turns`**, not "lines of code written." A 114:1 input-to-output ratio means context re-read dominates — normal for long tool loops, expensive on Opus.

**Session rules**

- **One session per deliverable** — do not mix planning, unrelated repo questions, skills transfer, and a multi-file build in one Opus thread.
- **Start fresh between phases** — after planning, after a large build, before handoff docs: `/clear`, new chat, or `claude --resume` only when continuing the *same* task.
- **Do not re-paste long briefs** — say "follow `server/P3_NEXT.md` task N" instead of quoting 200+ lines again.
- **Cap tool bloat** — prefer `rg` / targeted reads over full-file reads; never read `uv.lock` or `.venv/`; avoid re-reading the same file every turn unless it changed.
- **Stop the loop** — if a task needs many tool rounds, delegate the mechanical tail to Sonnet/Haiku or split into a new session with a one-paragraph handoff.

**Default delegations (do these without asking)**

| Work | Model |
|---|---|
| Tests for a known module | Sonnet |
| ruff / pyright / pytest fix loops | Sonnet or Haiku |
| Env/AWS/SMTP config + smoke test | Haiku |
| Pipecat frame hook with acceptance criteria | Sonnet |
| Two-line integration diff with frozen contract | Sonnet (flagship only if touching another owner's file without approval) |

### Stay on the flagship model (executive — do not delegate)

- Architecture decisions or **ambiguous** specs with no written acceptance criteria.
- Cross-owner coordination (P1/P2/P3 file boundaries, interface freeze changes).
- Routing decisions themselves — picking which subagent or model to use.
- Debugging where the root cause may span many unfamiliar subsystems **after** Sonnet has failed once.
- Anything where a wrong answer is expensive to undo (auth, credentials, billed external services like cloud deploys or telephony).

**Not flagship work** (even if the agent "could" do it): single-module implementation, handoff docs, integration shims, test suites, config, and refactors with a clear spec — use Sonnet/Haiku/local fast lane.

### Delegate to Sonnet 4.6 — default implementation lane

`Agent(model: "sonnet", ...)` — **prefer this over inline Opus** for any task with a clear spec.

- Single-file or well-scoped code changes with a clear spec (including tasks from `P3_NEXT.md`).
- Writing or updating tests for a known module.
- Code review of a small diff.
- Mechanical refactors (rename, extract function, inline) once the plan is decided.
- Pipecat integration hooks, CLI extensions, and "wire X into Y" when the contract is frozen.
- Lint/typecheck/test fix loops after the design is settled.
- Anything the flagship model *could* do but where the path is already obvious — Sonnet finishes faster and ~5× cheaper.

### Delegate to Haiku 4.5 — cheap, fast, simple

`Agent(model: "haiku", ...)`

- Simple lookups (find where X is defined, list files matching Y).
- One-line edits, commit message drafts, PR descriptions, changelog entries.
- Summarization, classification, format conversions (JSON ↔ YAML, kebab ↔ snake).
- Polishing prose where the substance is already correct.
- Avoid for multi-step reasoning — Haiku is shallow on purpose.

### Delegate to local models via Ollama — private, free, but **not fast**

**Default target: the remote compute node (`compute-box`), not this machine.** Local-model jobs run on the second Apple-Silicon box over Tailscale/SSH. Fall back to *this* machine only when (a) `compute-box` is unreachable/down, or (b) the job needs more than its **16GB RAM** (e.g. a model that won't fit alongside its working set). See "Where local jobs run" below for the decision + exact commands.

`Bash`: `ssh compute-box '/opt/homebrew/bin/ollama run <model> "<prompt>"'` (remote default) or `ollama run <model> "<prompt>"` (local fallback). Pipe long inputs via stdin. Strip Ollama's TUI escape codes with `sed 's/\x1b\[[0-9;]*[a-zA-Z]//g'` when capturing for downstream parsing — or avoid them entirely by using the HTTP API (see below).

#### Where local jobs run

1. **Default → `compute-box`** (remote, Apple Silicon, 16GB, Metal-accelerated). Reach it via the `compute-box` SSH alias (Tailscale name `mark-zuckerbergs-macbook-pro.tail5ca28d.ts.net`, IP `100.85.119.16`; LAN fallback `10.0.0.229`). Passwordless key auth is configured.
2. **Fall back to this machine** if either:
   - `compute-box` is **down/unreachable** — detect with `ssh -o BatchMode=yes -o ConnectTimeout=8 compute-box true` (non-zero exit ⇒ fall back), or
   - the job **needs more RAM than 16GB** allows (large model + context). This machine has more headroom for the deliberate-lane 31B/35B models.

**Remote invocation patterns** (note: SSH shell does *not* load Homebrew PATH, so always use the full `ollama` path):

- Quick CLI (emits spinner escape codes — strip them if parsing):
  ```bash
  ssh compute-box '/opt/homebrew/bin/ollama run llama3.2:3b "<prompt>"'
  ```
- Clean / parseable (recommended for programmatic use — HTTP API, no TUI junk):
  ```bash
  ssh compute-box 'curl -s http://localhost:11434/api/generate -d "{\"model\":\"qwen2.5-coder:7b\",\"prompt\":\"<prompt>\",\"stream\":false}"' \
    | python3 -c 'import sys,json;print(json.load(sys.stdin)["response"])'
  ```
- Long prompts: pipe via stdin, e.g. `ssh compute-box '/opt/homebrew/bin/ollama run <model>' < prompt.txt`.
- Long-running deliberate-lane jobs: launch with Bash `run_in_background: true` so the executive isn't blocked on slow inference.

**`compute-box` models** (verified 2026-05-30): `llama3.2:3b`, `qwen2.5:7b`, `qwen2.5-coder:7b` (+ `qwen2.5-coder:3b`, `deepseek-coder:6.7b`, and `local-coder:{fast,default,deepseek}` variants). Apple-Silicon Metal latencies are much lower than the CPU figures once feared: `llama3.2:3b` ≈ **2.4s** end-to-end over SSH; `qwen2.5-coder:7b` via API ≈ **9.5s** cold. The "Installed on this machine" tables below describe the **local fallback** host.

Use a local model when **at least one** applies:

- **Sensitive data**: code or content that shouldn't leave the machine.
- **Offline**: no network, or API quota exhausted.
- **Quality bulk** where you can wait: hundreds of items, each tolerant of ~30s–3min per call.

**Do not** reach for local for "bulk speed" — see latency numbers below. A Haiku API call finishes in ~1s; a local 35B model takes minutes. Local wins on price and privacy, not throughput.

**Installed on this machine** (verified 2026-05-27). Split by lane:

**Fast lane — no built-in CoT, seconds per call. Use these for bulk and inner loops.**

| Model | Best for | Avoid for | Cold latency |
|---|---|---|---|
| `qwen2.5-coder:7b` | Default local code worker. Code generation, refactor, structured extraction. Clean output, no thinking preamble. | Architecture / ambiguous specs (any local model). | **~4 s** |
| `llama3.2:3b` | Bulk classification, labeling, one-word answers. Most obedient to short-output constraints. | Anything needing depth. | **~10 s** |
| `qwen2.5:7b` | General fast-lane: structured prose, summaries, CHANGELOGs, light writing. Strong instruction-following — reliably honors strict-format prompts. Replaces `gemma3:4b` for English structured work. | Multilingual nuance — use `gemma3:4b` or `gemma4:31b-mlx` for that. | **~2 s warm, ~5 s cold** |
| `gemma3:4b` | Multilingual translation **only**. Empirically drops format constraints and hallucinates list contents on English structured-prose prompts. Reserve for: translating short copy, classifying multilingual text. | English structured prose, strict-format outputs, anything where missing one item is a real bug. | ~10 s |

**Deliberate lane — built-in CoT, 30s–3min per call. Use only when quality matters more than wall-clock.**

| Model | Best for | Avoid for | Cold latency |
|---|---|---|---|
| `gemma4:31b-mlx` | Multilingual / translation / classification with nuance. | Heavy coding — weaker than Qwen for code. | ~31 s |
| `deepseek-r1:14b` | Reasoning, math, structured decomposition where output is cheaply verifiable. Heavy chain-of-thought is the point. | Production prose, hallucination-sensitive output, anything time-sensitive. | ~40 s |
| `qwen3.5:35b-mlx` | Highest-quality local code generation. Built-in CoT — thinks before answering even simple prompts. | Anything latency-sensitive. Not a "-coder" variant, but base Qwen3.5 is still strong at code. | ~3 min 10 s |

Rule of thumb: stay in the fast lane unless you have a specific reason (hard reasoning step, code quality short, multilingual nuance) to pay the latency cost. For local code work, `qwen2.5-coder:7b` is ~48× faster than `qwen3.5:35b-mlx` on simple prompts and produces equivalent output. Always verify local-model output before acting on it — they hallucinate file paths and APIs more readily than Claude models.

### Decision order

1. Is the data **sensitive** (cannot leave the machine) or are we **offline**? → local Ollama, fast lane (pick model from table). Hard constraint, comes first. **Note:** "local" means `compute-box` by default (it's still your own hardware over a private Tailscale link); fall back to this machine if it's down or the job needs >16GB RAM. See "Where local jobs run".
2. Is this a **bulk batch** (hundreds–thousands of similar calls) where total cost matters more than wall-clock per item? → local fast lane (`llama3.2:3b` for classification, `qwen2.5-coder:7b` for code, `qwen2.5:7b` for general prose, `gemma3:4b` for multilingual). ~5–10 s/call vs Haiku's ~1 s/call, but $0 vs metered.
3. Otherwise, can **Haiku** do it correctly with a one-shot prompt? → Haiku subagent.
4. Can **Sonnet** do it given a clear spec or handoff doc? → **Sonnet subagent or inline Sonnet** (default for implementation — do not escalate to Opus first).
5. Need deeper reasoning, multilingual nuance, or top local code quality? → local **deliberate lane** (`deepseek-r1:14b`, `gemma4:31b-mlx`, `qwen3.5:35b-mlx`). Pay the 30s–3min latency for the quality bump.
6. Otherwise → **flagship inline** for routing, synthesis, or ambiguous work only — not for routine coding.

### Reporting agent activity in turn summaries

When the executive agent delegates to subagents (`Agent` tool, `ollama run`, or a Skill that fans out), the end-of-turn summary **must** include a per-agent line:

- **Who**: subagent type + model — e.g. `Explore (Sonnet)`, `Plan (flagship)`, `local qwen2.5-coder:7b`.
- **What**: one-phrase description of the task.
- **Cost signal**: approximate token usage for Anthropic agents (input + output + internal tool loops), or wall-clock seconds for local models.

The `Agent` tool does **not** return exact token counts to the parent. Estimate from prompt + response length plus a ~3–10× multiplier for internal tool-use loops (file reads, greps, web fetches add up fast). When unsure, write `~Nk tokens (est.)` rather than inventing precision.

This makes cost tracking and quality regressions visible across sessions.

## Project Orientation

- **Hackathon product spec:** [`PLAN.md`](PLAN.md) — 3-person **Persona Voicemail Agent**
  (owner proxy on inbound calls). Win thesis: scales, persists, and **learns** via the
  Cekura auto-improve loop. Priority ladder: Tier 0 (Nemotron voicemail + persona loader
  + caller snapshot) → Tier 1 (Cekura loop, AWS persistence, Twilio) → Tier 2+ (live
  calendar, voice clone, email) → Tier 3 (iMessage ingest, Pipecat Cloud).
- All application code lives in `server/`, managed by [`uv`](https://docs.astral.sh/uv/)
  (Python 3.11+).
- **Primary demo bot — `bot-nemotron.py`** (NVIDIA eligibility). Pipeline: Gradium STT →
  Nemotron 3 Super 120B LLM → Gradium TTS (optional owner voice clone). Do not switch
  the demo to `bot-gpt.py` without explicit approval.
- **Person 2 shipped (see PLAN.md build status):** `persona_context.py` +
  `persona_context.md`, `server/eval/` (mock Cekura loop + auto-improve), `server/ingest/`
  (iMessage / Calendar / agent-context → persona file). Refresh live context:
  `uv run python -m ingest.refresh`.
- **Person 3 lane:** caller snapshot (`caller_snapshot.py`), action outbox → MCP drain
  (`actions.py`, `action_bridge.py`), AWS persistence (`persistence.py`).
- Transports: **SmallWebRTC** (local dev), **Twilio** (telephony + owner SMS). Deploy
  target: **Pipecat Cloud**.
- Legacy starter (`bot-gpt.py`, `mock_backend.py`) remains for reference; not the demo path.

## Expected Workflow

- Read the existing code and local conventions before changing behavior.
- Prefer small, targeted edits that preserve the product direction already in the repository.
- Use `rg` / `rg --files` for search.
- Do not revert or overwrite user changes unless the user explicitly asks.
- When changing bot behavior, run the bot locally over WebRTC and verify the actual voice/transcript experience before pushing to the cloud or wiring up the phone.

## Local Commands

Run these from the `server/` directory.

- Configure keys: `cp .env.example .env` then edit `.env` (see Keys & External Services below).
- Install dependencies: `uv sync`
- Run Version 1 (GPT-4.1): `ENV=local uv run bot-gpt.py`
- Run Version 2 (Nemotron): `ENV=local uv run bot-nemotron.py`
- Lint: `uv run ruff check .`
- Typecheck: `uv run pyright`
- Tests: `uv run pytest` (e.g. `test_persona_context.py`, `eval/test_eval.py`, `ingest/test_ingest.py`)
- **Eval loop (P2, mock or live):** `uv run python -m eval.run_evals` ·
  `uv run python -m eval.improve --rounds 1` (demo finale: FAIL→PASS flip)
- **Live persona refresh (P2):** `uv run python -m ingest.refresh`

`ENV=local` skips the Krisp filter, which is only available when deployed to Pipecat Cloud.

### MCP & connectors

Before any Cekura / Gmail / Calendar tool call, read [`MCP_AGENTS.md`](MCP_AGENTS.md)
(session roles, env checklist, outbox drain SOP, verification). Per-client wiring
lives in [`docs/agent-setup/`](docs/agent-setup/).

## Local / Browser Checks

- After `uv run bot-*.py`, open [http://localhost:7860](http://localhost:7860) and click **Connect** to start talking. First launch takes ~20s while Pipecat downloads VAD and turn-detection models.
- Verify the agent actually responds (audio + transcript) after each meaningful change, not just that the process started.
- For telephony, the bot is reached via a Twilio number wired to the deployed bot — only test that when the user explicitly asks and has set up Twilio.

## Keys & External Services

- API keys live in `server/.env` (copied from `.env.example`). Relevant vars:
  - `OPENAI_API_KEY` — v1 LLM (GPT-4.1).
  - `GRADIUM_API_KEY` (+ optional `GRADIUM_VOICE_ID`) — TTS (and v1 STT). **Required**; the bot raises if unset.
  - `NVIDIA_ASR_URL` — v2 Nemotron STT endpoint (shared at the event).
  - `NEMOTRON_LLM_URL` (+ optional `NEMOTRON_ENABLE_THINKING`, `NEMOTRON_LLM_MODEL`) — v2 LLM endpoint.
  - `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` — only needed when wiring up the phone.
- Do not print secrets or copy sensitive env values into responses.
- Do not create, delete, or mutate external resources (Pipecat Cloud deploys, Twilio numbers, paid API usage at scale) unless the user requested that specific action.
- Deploying to Pipecat Cloud requires the Pipecat CLI (`uv tool install pipecat-ai-cli`, then `pc cloud auth login`) — only do this when the user asks.
