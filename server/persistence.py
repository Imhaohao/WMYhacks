#
# Copyright (c) 2024–2026, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""AWS persistence for finished voicemails and Cekura eval runs (Person 3, Part C).

This is "the agent's memory": every finished voicemail (owner-context tag +
caller snapshot + structured fields + actions taken + timestamp) and every
Cekura eval-run record is written here so it can be shown on stage and queried
later.

Backend resolution (first available wins), all behind a tiny shared API:

    1. DynamoDB  — preferred. Table name from ``PERSIST_DDB_TABLE``
       (default ``ff-voicemails``). Used when boto3 + AWS creds + table access
       are all present.
    2. S3 JSON   — one object per record under ``PERSIST_S3_PREFIX`` in
       ``PERSIST_S3_BUCKET``. Used when a bucket is configured and reachable.
    3. local file — newline-delimited JSON at ``PERSIST_LOCAL_PATH``
       (default ``server/aws_store/records.jsonl``). Always works, zero deps,
       zero creds. This is the demo default.

boto3 is imported lazily so the bot never hard-depends on it. To enable the
cloud path: ``uv add boto3`` and configure AWS creds (env / ``~/.aws``) — the
code lights up automatically, no edits needed.

No raw audio, voiceprints, or biometric data are ever stored — only the
structured fields the owner is meant to see.

CLI (for the stage demo):
    uv run python persistence.py --show          # print all persisted records
    uv run python persistence.py --show --type voicemail
    uv run python persistence.py --selftest      # write a sample record locally
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from loguru import logger

_ENV_DIR = Path(__file__).resolve().parent
load_dotenv(_ENV_DIR / ".env", override=True)
load_dotenv(_ENV_DIR / ".env.local", override=True)

# Record type tags.
TYPE_VOICEMAIL = "voicemail"
TYPE_EVAL_RUN = "eval_run"
TYPE_CALLER_PROFILE = "caller_profile"

_DEFAULT_LOCAL_PATH = Path(__file__).parent / "aws_store" / "records.jsonl"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _owner_tag(explicit: str | None = None) -> str:
    """Which owner/persona this record belongs to (owner-context tag)."""
    return explicit or os.getenv("OWNER_CONTEXT_TAG") or os.getenv("OWNER_EMAIL") or "owner"


# --- Backend resolution -----------------------------------------------------

class _Backend:
    """Resolved persistence backend. Picks DynamoDB > S3 > local file once,
    caches the choice, and degrades gracefully if a cloud backend errors at
    write time."""

    def __init__(self) -> None:
        self.kind = "local"
        self._ddb_table = None
        self._s3 = None
        self._s3_bucket = None
        self._s3_prefix = os.getenv("PERSIST_S3_PREFIX", "voicemails/")
        self._local_path = Path(os.getenv("PERSIST_LOCAL_PATH", str(_DEFAULT_LOCAL_PATH)))
        self._resolve()

    def _resolve(self) -> None:
        # Try DynamoDB first, then S3. Any failure falls through to local.
        try:
            import boto3  # noqa: PLC0415  (lazy, optional)
            from botocore.exceptions import BotoCoreError, ClientError

            session = boto3.Session()
            if session.get_credentials() is None:
                logger.info("persistence: no AWS credentials — using local fallback")
            else:
                table_name = os.getenv("PERSIST_DDB_TABLE", "ff-voicemails")
                try:
                    ddb: Any = session.resource("dynamodb")
                    table = ddb.Table(table_name)
                    table.load()  # raises if missing / no access
                    self._ddb_table = table
                    self.kind = "dynamodb"
                    logger.info(f"persistence: using DynamoDB table '{table_name}'")
                    return
                except (BotoCoreError, ClientError) as e:
                    logger.warning(
                        f"persistence: DynamoDB table unavailable ({type(e).__name__}); "
                        "trying S3"
                    )
                bucket = os.getenv("PERSIST_S3_BUCKET")
                if bucket:
                    self._s3 = session.client("s3")
                    self._s3_bucket = bucket
                    self.kind = "s3"
                    logger.info(f"persistence: using S3 bucket '{bucket}'")
                    return
        except ModuleNotFoundError:
            logger.info("persistence: boto3 not installed — using local fallback")
        except Exception as e:  # never let backend resolution crash the caller
            logger.warning(f"persistence: backend resolution error ({type(e).__name__}); local")

        self.kind = "local"
        self._local_path.parent.mkdir(parents=True, exist_ok=True)
        logger.info(f"persistence: using local file '{self._local_path}'")

    def write(self, record: dict[str, Any]) -> str:
        """Persist one record. Returns a short human string naming where it went.
        Cloud failures fall back to the local file rather than raising."""
        if self.kind == "dynamodb":
            try:
                self._ddb_table.put_item(Item=record)  # type: ignore[union-attr]
                return f"dynamodb:{self._ddb_table.name}"  # type: ignore[union-attr]
            except Exception as e:
                logger.warning(f"persistence: DynamoDB write failed ({type(e).__name__}); local")
        elif self.kind == "s3":
            try:
                key = f"{self._s3_prefix}{record['type']}/{record['record_id']}.json"
                self._s3.put_object(  # type: ignore[union-attr]
                    Bucket=self._s3_bucket,
                    Key=key,
                    Body=json.dumps(record, default=str).encode("utf-8"),
                    ContentType="application/json",
                )
                return f"s3:{self._s3_bucket}/{key}"
            except Exception as e:
                logger.warning(f"persistence: S3 write failed ({type(e).__name__}); local")
        return self._write_local(record)

    def _write_local(self, record: dict[str, Any]) -> str:
        self._local_path.parent.mkdir(parents=True, exist_ok=True)
        with self._local_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")
        return f"local:{self._local_path}"

    def read_all(self, record_type: str | None = None) -> list[dict[str, Any]]:
        """Read back records (best-effort). DynamoDB scan / S3 list / local read."""
        records: list[dict[str, Any]] = []
        try:
            if self.kind == "dynamodb":
                resp = self._ddb_table.scan()  # type: ignore[union-attr]
                records = list(resp.get("Items", []))
            elif self.kind == "s3":
                paginator = self._s3.get_paginator("list_objects_v2")  # type: ignore[union-attr]
                for page in paginator.paginate(
                    Bucket=self._s3_bucket, Prefix=self._s3_prefix
                ):
                    for obj in page.get("Contents", []):
                        body = self._s3.get_object(  # type: ignore[union-attr]
                            Bucket=self._s3_bucket, Key=obj["Key"]
                        )["Body"].read()
                        records.append(json.loads(body))
        except Exception as e:
            logger.warning(f"persistence: cloud read failed ({type(e).__name__}); local")
        # Always include the local file too (covers degraded-write fallbacks).
        if self._local_path.exists():
            for line in self._local_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        if record_type:
            records = [r for r in records if r.get("type") == record_type]
        return records


_backend: _Backend | None = None


def _get_backend() -> _Backend:
    global _backend
    if _backend is None:
        _backend = _Backend()
    return _backend


def backend_kind() -> str:
    """Return the active backend name: 'dynamodb' | 's3' | 'local'."""
    return _get_backend().kind


# --- Public API -------------------------------------------------------------

def persist_voicemail(
    voicemail: dict[str, Any] | None,
    caller_snapshot: dict[str, Any] | None,
    actions_taken: list[dict[str, Any]] | None = None,
    owner_context_tag: str | None = None,
) -> dict[str, Any]:
    """Persist one finished voicemail. Returns the stored record (with a
    ``_stored_at`` marker naming the backend). Never raises — persistence must
    not be the thing that breaks the demo."""
    record = {
        "record_id": str(uuid.uuid4()),
        "type": TYPE_VOICEMAIL,
        # Hoisted to the top level so calls are indexable by caller (see
        # list_calls_for_caller / upsert_caller_profile).
        "caller_number": _normalize_number((voicemail or {}).get("caller_number")),
        "owner_context_tag": _owner_tag(owner_context_tag),
        "timestamp": _now_iso(),
        "caller_snapshot": _strip_internal(caller_snapshot or {}),
        "voicemail": voicemail or {},
        "actions_taken": actions_taken or [],
    }
    try:
        where = _get_backend().write(record)
    except Exception as e:  # absolute last-resort guard
        logger.error(f"persist_voicemail failed hard: {type(e).__name__}: {e}")
        where = "unwritten"
    record["_stored_at"] = where
    logger.info(f"Persisted voicemail {record['record_id']} -> {where}")
    return record


def persist_eval_run(
    run: dict[str, Any],
    owner_context_tag: str | None = None,
) -> dict[str, Any]:
    """Persist one Cekura eval-run record so "evaluation history persists" is a
    real, retrievable artifact (Part C, task 9). Coordinated with P2: pass the
    Cekura run id, the per-metric pass/fail, and any summary.

    ``run`` is stored as-is under ``cekura_run`` plus a few hoisted top-level
    fields for easy scanning. Never raises."""
    record = {
        "record_id": str(uuid.uuid4()),
        "type": TYPE_EVAL_RUN,
        "owner_context_tag": _owner_tag(owner_context_tag),
        "timestamp": _now_iso(),
        "cekura_run_id": str(run.get("run_id") or run.get("id") or ""),
        "passed": run.get("passed"),
        "metrics": run.get("metrics") or run.get("metric_results") or [],
        "cekura_run": run,
    }
    try:
        where = _get_backend().write(record)
    except Exception as e:
        logger.error(f"persist_eval_run failed hard: {type(e).__name__}: {e}")
        where = "unwritten"
    record["_stored_at"] = where
    logger.info(f"Persisted eval_run {record['record_id']} -> {where}")
    return record


# --- Per-caller profiles ----------------------------------------------------

def _normalize_number(number: str | None) -> str:
    """Canonical key for a caller so the same phone maps to one profile.

    Keeps only digits and a leading ``+``; everything else (spaces, dashes,
    parentheses) is dropped. Empty/None → "" (an anonymous, non-indexable call)."""
    if not number:
        return ""
    cleaned = "".join(ch for ch in number.strip() if ch.isdigit() or ch == "+")
    return cleaned


def get_caller_profile(caller_number: str | None) -> dict[str, Any] | None:
    """Return the current accumulated profile for a caller, or None if unknown.

    Resolved from the newest ``caller_profile`` record for this number. On
    DynamoDB the record upserts in place; the local file keeps one version per
    call, so we take the latest by timestamp."""
    key = _normalize_number(caller_number)
    if not key:
        return None
    matches = [
        r
        for r in _get_backend().read_all(TYPE_CALLER_PROFILE)
        if _normalize_number(r.get("caller_number")) == key
    ]
    if not matches:
        return None
    return max(matches, key=lambda r: r.get("timestamp", ""))


def list_calls_for_caller(caller_number: str | None) -> list[dict[str, Any]]:
    """Every voicemail record left by this caller, newest first."""
    key = _normalize_number(caller_number)
    if not key:
        return []
    calls = [
        r
        for r in _get_backend().read_all(TYPE_VOICEMAIL)
        if _normalize_number(
            r.get("caller_number") or (r.get("voicemail") or {}).get("caller_number")
        )
        == key
    ]
    calls.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
    return calls


def upsert_caller_profile(
    caller_number: str | None,
    *,
    caller_name: str | None = None,
    reason: str | None = None,
    urgency: str | None = None,
    callback_number: str | None = None,
    summary: str | None = None,
    persona_id: str | None = None,
    call_record_id: str | None = None,
) -> dict[str, Any] | None:
    """Create or expand the per-caller profile at end of call.

    Indexed by phone number and grows on every call: call count, names seen,
    topics, urgency history, callback numbers, and recent summaries. Stored with
    ``record_id`` = the normalized number, so DynamoDB upserts the single profile
    item in place while the local file appends a new version (latest wins on
    read). Never raises.

    Returns the updated profile, or None for an anonymous caller (no number to
    index by — e.g. withheld caller ID or a local WebRTC test)."""
    key = _normalize_number(caller_number)
    if not key:
        logger.info("caller profile: no caller number — skipping (anonymous call)")
        return None

    now = _now_iso()
    prior = get_caller_profile(key) or {}

    def _append_unique(seq: Any, value: Any, cap: int = 50) -> list[Any]:
        out = list(seq or [])
        if value and value not in out:
            out.append(value)
        return out[-cap:]

    def _append(seq: Any, value: Any, cap: int) -> list[Any]:
        out = list(seq or [])
        if value:
            out.append(value)
        return out[-cap:]

    profile = {
        "record_id": key,  # stable key → DynamoDB upserts in place
        "type": TYPE_CALLER_PROFILE,
        "caller_number": key,
        "timestamp": now,  # last updated
        "first_seen": prior.get("first_seen") or now,
        "last_seen": now,
        "call_count": int(prior.get("call_count", 0)) + 1,
        "display_name": caller_name or prior.get("display_name") or "",
        "names": _append_unique(prior.get("names"), caller_name),
        "topics": _append(prior.get("topics"), reason, cap=30),
        "urgency_history": _append(prior.get("urgency_history"), urgency, cap=30),
        "callback_numbers": _append_unique(prior.get("callback_numbers"), callback_number),
        "persona_ids": _append_unique(prior.get("persona_ids"), persona_id),
        "recent_summaries": _append(prior.get("recent_summaries"), summary, cap=10),
        "call_ids": _append_unique(prior.get("call_ids"), call_record_id, cap=200),
    }

    try:
        where = _get_backend().write(profile)
    except Exception as e:  # absolute last-resort guard
        logger.error(f"upsert_caller_profile failed hard: {type(e).__name__}: {e}")
        where = "unwritten"
    profile["_stored_at"] = where
    logger.info(
        f"Caller profile {key}: call #{profile['call_count']} "
        f"(name={profile['display_name']!r}) -> {where}"
    )
    return profile


def list_records(record_type: str | None = None) -> list[dict[str, Any]]:
    """Return persisted records, newest first. ``record_type`` filters to
    'voicemail' or 'eval_run'."""
    records = _get_backend().read_all(record_type)
    records.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
    return records


def _strip_internal(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Drop any non-owner-facing bookkeeping before persisting. We never store
    raw audio / voiceprints — those never exist here in the first place; this is
    just hygiene on the snapshot dict."""
    out = dict(snapshot)
    out.pop("_internal", None)
    return out


# --- CLI --------------------------------------------------------------------

def _humanize_ts(iso: str | None) -> str:
    """Render an ISO-8601 timestamp as an absolute + relative string.

    Falls back to the raw value if it can't be parsed, so a malformed
    timestamp never crashes the memory viewer.
    """
    if not iso:
        return "unknown time"
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return str(iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    secs = int((datetime.now(UTC) - dt).total_seconds())
    if secs < 0:
        rel = "in the future"
    elif secs < 60:
        rel = f"{secs}s ago"
    elif secs < 3600:
        rel = f"{secs // 60}m ago"
    elif secs < 86400:
        rel = f"{secs // 3600}h ago"
    else:
        rel = f"{secs // 86400}d ago"
    return f"{dt.strftime('%b %d, %Y %I:%M %p %Z').strip()} ({rel})"


def _pretty_record(rec: dict[str, Any]) -> str:
    """Human-legible block for one record: snapshot fields labeled, actions
    itemized, timestamp humanized. Used by ``--show-last``."""
    rtype = rec.get("type", "?")
    lines: list[str] = [
        f"Record {rec.get('record_id', '?')}  [{rtype}]",
        f"  When:   {_humanize_ts(rec.get('timestamp'))}",
        f"  Stored: {rec.get('_stored_at', '(in-store)')}",
        f"  Owner:  {rec.get('owner_context_tag') or '—'}",
    ]

    if rtype == TYPE_VOICEMAIL:
        snap = rec.get("caller_snapshot") or {}
        if snap:
            lines.append("")
            try:
                from caller_snapshot import format_snapshot_summary

                lines.append("  " + format_snapshot_summary(snap).replace("\n", "\n  "))
            except Exception:
                lines.append(f"  Snapshot: {json.dumps(snap, default=str)}")
        vm = rec.get("voicemail") or {}
        if vm:
            lines.append("")
            lines.append("  Voicemail:")
            for k, v in vm.items():
                if v not in (None, "", [], {}):
                    lines.append(f"    {k.replace('_', ' ').title()}: {v}")
        acts = rec.get("actions_taken") or []
        lines.append("")
        lines.append(f"  Actions taken ({len(acts)}):")
        if not acts:
            lines.append("    (none)")
        for a in acts:
            target = a.get("to") or a.get("calendar") or a.get("when") or a.get("requested_time") or ""
            lines.append(
                f"    - {a.get('type', '?'):8} status={a.get('status', '?')} {target}".rstrip()
            )
    elif rtype == TYPE_EVAL_RUN:
        lines.append(f"  Cekura run: {rec.get('cekura_run_id') or '—'}")
        lines.append(f"  Passed:     {rec.get('passed')}")
        metrics = rec.get("metrics") or []
        lines.append(f"  Metrics ({len(metrics)}):")
        for m in metrics:
            if isinstance(m, dict):
                lines.append(f"    - {m.get('name', '?')}: passed={m.get('passed')}")
            else:
                lines.append(f"    - {m}")
    return "\n".join(lines)


def _main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Field & Flower persistence (the agent's memory)")
    parser.add_argument("--show", action="store_true", help="print persisted records as raw JSON")
    parser.add_argument(
        "--show-last",
        type=int,
        metavar="N",
        nargs="?",
        const=1,
        default=None,
        help="pretty-print the N most recent records (newest first; default 1)",
    )
    parser.add_argument("--type", choices=[TYPE_VOICEMAIL, TYPE_EVAL_RUN], default=None)
    parser.add_argument("--selftest", action="store_true", help="write a sample voicemail record")
    args = parser.parse_args()

    print(f"Active backend: {backend_kind()}")

    if args.selftest:
        rec = persist_voicemail(
            voicemail={"reason": "callback about judging schedule", "urgency": "high"},
            caller_snapshot={
                "caller_name": "Sam",
                "relationship": "judge",
                "reason": "judging schedule",
                "urgency": "high",
                "emotional_tone": "rushed",
                "callback_preference": "call back",
                "best_time": "before 4pm",
            },
            actions_taken=[{"type": "email", "status": "queued"}],
        )
        print("Wrote:", rec["record_id"], "->", rec["_stored_at"])

    if args.show_last is not None:
        n = max(1, args.show_last)
        records = list_records(args.type)[:n]
        label = f" {args.type}" if args.type else ""
        print(f"\nMost recent {len(records)}{label} record(s):\n")
        if not records:
            print("(no records yet)")
        for r in records:
            print(_pretty_record(r))
            print("=" * 64)
        return

    if args.show or not args.selftest:
        records = list_records(args.type)
        print(f"\n{len(records)} record(s):\n")
        for r in records:
            print(json.dumps(r, indent=2, default=str))
            print("-" * 60)


if __name__ == "__main__":
    _main()
