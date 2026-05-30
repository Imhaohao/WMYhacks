# P3 — Deferred task prompts (save & implement later)

These are the three P3 work items that are **blocked in the current environment**
(missing credentials / no live MCP connectors) but are fully scoped and ready to
build the moment the prerequisites exist. Each section below is a **self-contained
prompt** — paste one into a fresh agent session and it has everything it needs.

Orientation for any agent picking these up:
- Repo: `server/` (Python 3.11+, `uv`). Voice agent on Pipecat. P3 owns the
  caller snapshot, owner actions (email/calendar), and persistence (the agent's
  "memory").
- Contracts live in `interfaces.py` + `INTEGRATION_P3.md`. Read both first.
- P3 modules: `caller_snapshot.py`, `actions.py`, `persona_tools.py`,
  `persistence.py`, `action_bridge.py`. Tests in `test_p3.py`.
- Privacy invariants (non-negotiable): no raw audio stored, no voiceprints,
  tone is inferred from **wording only**, snapshots are ephemeral per-call.
- Every external-effect path must **degrade gracefully** (never crash the call):
  on failure, fall back (queue to outbox / write local file) and log a warning.
- Verify after any change: `uv run pytest -q && uv run pyright && uv run ruff check .`

---

## Prompt 1 — Real owner email via Gmail SMTP (Task 3)

> **Role:** You are implementing live email delivery for the Field & Flower voice
> agent's owner-notification path.
>
> **Goal:** When the agent calls the `send_owner_email` tool during a call, a real
> email must land in the shop owner's inbox. Today that path silently queues to an
> outbox because no SMTP credentials are configured.
>
> **Current state (already built — do not rewrite, just enable + verify):**
> - `server/actions.py` → `_send_via_smtp(to_addr, subject, body) -> bool`
>   (lines ~119–141). It uses `smtp.gmail.com:587` + STARTTLS, reads
>   `GMAIL_APP_PASSWORD` and `GMAIL_SENDER` (falls back to the recipient address),
>   and returns `False` on any failure.
> - `make_action_tools(...)` exposes the async `send_owner_email` tool. When
>   `_send_via_smtp` returns `False`, the tool queues the request to the outbox
>   (`outbox/actions.jsonl`) via `_append_outbox` instead of crashing.
> - The tool is registered into `TOOL_REGISTRY` through `persona_tools.py`, which
>   is imported in `bot-nemotron.py`.
> - `OWNER_EMAIL` is the destination address.
>
> **Prerequisites you must obtain first:**
> 1. A Gmail account with 2-Step Verification enabled.
> 2. A 16-character **App Password** (Google Account → Security → App passwords).
>    This is NOT the account login password.
>
> **Tasks:**
> 1. Add to `server/.env` (never commit real secrets):
>    - `OWNER_EMAIL=<shop owner address>`
>    - `GMAIL_APP_PASSWORD=<16-char app password>`
>    - `GMAIL_SENDER=<the gmail address that owns the app password>` (set this
>      explicitly — Gmail SMTP login must match the sending mailbox).
> 2. Confirm `_send_via_smtp` logs in and sends with these creds. If Gmail rejects
>    the login, fix `GMAIL_SENDER`/app-password pairing before touching code.
> 3. Drive the full tool path end-to-end (not just the SMTP helper in isolation):
>    invoke `send_owner_email` the way the agent would (e.g. via `demo_p3.py` or a
>    small integration script that calls the registered tool with a `call_state`),
>    and confirm the message actually arrives in the owner inbox.
> 4. Verify the **fallback** still works: temporarily unset `GMAIL_APP_PASSWORD`,
>    confirm the tool queues to `outbox/actions.jsonl` and the call does not error.
>
> **Acceptance criteria:**
> - A real email arrives in `OWNER_EMAIL`'s inbox when the tool is called.
> - With creds removed, the same call queues to the outbox and never raises.
> - No secret (app password) is ever printed to logs or the transcript.
>
> **Guardrails:**
> - Only ever email `OWNER_EMAIL` — do not let caller-supplied text choose the
>   recipient.
> - Keep `_send_via_smtp`'s try/except → return `False` contract intact so SMTP
>   problems degrade to the outbox rather than failing the call.

---

## Prompt 2 — Live connector fulfillment + Cekura eval persistence (Tasks 4 & 5)

> **Role:** You are wiring the Field & Flower agent's queued actions and evaluation
> results into the **live MCP connectors** (Gmail, Google Calendar, Cekura). These
> are grouped because all three are blocked on the same thing: a session that can
> actually reach the MCP connectors (the bot process cannot — it queues instead).
>
> ### Part A — Drain the action outbox into Gmail / Calendar (Task 4)
>
> **Goal:** Fulfill every pending action in the outbox by firing the real connector
> call, then mark it done — idempotently.
>
> **Current state (already built):** `server/action_bridge.py`
> - `pending_actions() -> list[dict]` — actions in `outbox/actions.jsonl` not yet in
>   `outbox/fulfilled.jsonl` (override paths via `PERSIST_OUTBOX_PATH` /
>   `PERSIST_FULFILLED_PATH`).
> - `connector_spec(action) -> dict` — translates one queued action into a
>   ready-to-fire call:
>   - `send_email`  → `gmail.create_draft(to=[…], subject, body)` (the Gmail
>     connector exposes **create_draft only**, not send; for true inbox delivery
>     use Prompt 1's SMTP path instead).
>   - `create_event` → `calendar.create_event(summary="[Tentative] …", startTime,
>     endTime, description, colorId="8" (Graphite), visibility="private")`. The
>     spec carries a `needs` field when `start_iso`/`end_iso` are unresolved.
> - `mark_done(action_id, result)` — appends to the fulfilled ledger.
> - **Gap:** the bridge only *prints* specs (`--emit`); it never calls a connector.
>
> **Prerequisites:** a live session with the **Gmail MCP** and a **Calendar MCP
> reconnected with write scope** (`create_event`).
>
> **Tasks:**
> 1. For each `pending_actions()` entry, build its `connector_spec(...)` and
>    actually invoke the corresponding MCP connector with `args`.
> 2. For `create_event` specs whose `needs` is set (no `start_iso`/`end_iso`),
>    resolve a concrete slot from `requested_time` before firing (default a
>    30-min window; mirror `actions._resolve_slot`). If it can't be resolved,
>    skip and log — do not fire a malformed event.
> 3. After a successful connector call, `mark_done(action_id, result=<connector
>    response: draft id / event id>)`. Re-running the bridge must **not** re-fire
>    already-fulfilled actions.
> 4. Add a `--run` mode (or equivalent) that performs steps 1–3, alongside the
>    existing `--list` / `--emit` / `--done`.
>
> **Acceptance criteria:**
> - A queued `send_email` produces a Gmail draft (or, if Prompt 1 is done, real
>   inbox delivery); a queued `create_event` produces a tentative Calendar event.
> - Both get an entry in `outbox/fulfilled.jsonl`; a second run is a no-op.
> - A connector failure logs a warning and leaves the action pending (retryable),
>   never crashing the bridge.
>
> ### Part B — Persist Cekura eval runs (Task 5)
>
> **Goal:** After a Cekura evaluation run completes, store a retrievable record so
> "evaluation history persists" is a real artifact.
>
> **Current state (already built + unit-tested):** `server/persistence.py`
> - `persist_eval_run(run: dict, owner_context_tag: str | None = None) -> dict`
>   stores `cekura_run_id` (from `run["run_id"]` or `run["id"]`), `passed`,
>   `metrics` (from `run["metrics"]` or `run["metric_results"]`), plus the full
>   `run` under `cekura_run`. Type tag is `eval_run`. Never raises.
> - **Gap:** nothing calls it with real Cekura data — needs P2's eval loop and the
>   **Cekura MCP** (`results_list`).
>
> **Prerequisites:** P2's Cekura integration landed + Cekura MCP access.
>
> **Tasks:**
> 1. Add a thin glue (e.g. `persist_cekura_run(run_id: str)` in a small module, or
>    a hook in P2's eval runner) that pulls a finished run's results from the
>    Cekura MCP and shapes them into the `run` dict `persist_eval_run` expects:
>    `{"run_id", "passed", "metrics": [{"name", "passed", ...}]}`.
> 2. Call `persist_eval_run(run, owner_context_tag=<shop tag>)`.
> 3. Coordinate with P2 so this fires once per completed eval run (don't double-store).
>
> **Acceptance criteria:**
> - A real Cekura run id's results are stored and visible via
>   `uv run python persistence.py --show-last --type eval_run`, with metrics intact.
> - The persistence backend used is reported correctly (local until Prompt 3).

---

## Prompt 3 — AWS cloud persistence path: DynamoDB / S3 (Task 6)

> **Role:** You are promoting the Field & Flower agent's "memory" from a local
> JSONL file to a real cloud backend so records survive restarts and are queryable.
>
> **Goal:** Make `persistence.py` resolve to DynamoDB (preferred) or S3 and confirm
> records actually land in the cloud. `boto3` is already a dependency and the code
> path already exists — this is configuration + infra + verification, ideally with
> **no code changes**.
>
> **Current state (already built):** `server/persistence.py` `_Backend`
> - Resolution order (first reachable wins): **DynamoDB → S3 → local file**.
> - DynamoDB: `session.resource("dynamodb").Table(PERSIST_DDB_TABLE).load()`;
>   table name from `PERSIST_DDB_TABLE` (default `ff-voicemails`). Writes via
>   `put_item(Item=record)`; reads via `scan()`.
> - S3: bucket from `PERSIST_S3_BUCKET`, key prefix `PERSIST_S3_PREFIX` (default
>   `voicemails/`); object key is `{prefix}{type}/{record_id}.json`.
> - Local fallback: `PERSIST_LOCAL_PATH` (default `server/aws_store/records.jsonl`).
> - Every record has top-level `record_id` (uuid str) and `type`
>   (`voicemail` | `eval_run`). `backend_kind()` returns the active backend.
> - Uses the standard boto3 credential chain; with no creds it logs and uses local.
>
> **Prerequisites:** AWS credentials available to the process (env vars, shared
> config, or an instance/role) **plus** one of:
> - a DynamoDB table whose **partition key is `record_id` (String)** — name it
>   `ff-voicemails` or set `PERSIST_DDB_TABLE`; **or**
> - an S3 bucket — set `PERSIST_S3_BUCKET` (and optionally `PERSIST_S3_PREFIX`).
>
> **Tasks:**
> 1. Provision the resource:
>    - **DynamoDB (preferred):** create the table with partition key `record_id`
>      (type S), on-demand billing. This key MUST match `put_item(Item=record)` —
>      `record_id` is the only guaranteed unique field; do not pick a different key.
>    - **or S3:** create a private bucket; decide a prefix.
> 2. Configure credentials + env in the bot's environment (and `server/.env` for
>    local runs). Do not commit credentials.
> 3. Verify resolution & round-trip:
>    - `uv run python persistence.py --selftest` writes a sample voicemail record.
>    - Confirm `backend_kind()` returns `dynamodb` (or `s3`), not `local`.
>    - Confirm the record is present in the cloud (DynamoDB scan / S3 object) and
>      readable back via `uv run python persistence.py --show-last`.
> 4. Confirm graceful degradation: with the cloud unreachable (bad creds/region),
>    writes fall back to the local file and the call never crashes.
>
> **Acceptance criteria:**
> - With creds + resource in place, `backend_kind()` is `dynamodb` or `s3`.
> - A written record is retrievable from the cloud store and via `--show-last`.
> - Removing creds reverts to local fallback with only a warning logged.
>
> **Guardrails:**
> - Least-privilege IAM: DynamoDB `PutItem`/`GetItem`/`Scan` on that one table, or
>   S3 `PutObject`/`GetObject`/`ListBucket` on that bucket/prefix — nothing wider.
> - No PII beyond what the snapshot already permits; the privacy invariants above
>   still hold in the cloud (no raw audio, no voiceprints).
```
