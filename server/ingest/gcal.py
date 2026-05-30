"""Real Google Calendar availability -> persona_context.md (Part B).

The running bot can't call Claude's MCP tools and macOS Calendar.app has no
local account, so we read the owner's Google Calendar directly via the Calendar
API and write a derived free/busy summary into the Availability section. A
machine-readable `availability.json` is also emitted for a future per-call bot
seam (P1, optional).

Setup (one time):
  1. Create an OAuth *Desktop app* client in Google Cloud Console; download the
     JSON to `server/credentials.json` (or set GCAL_CREDENTIALS).
  2. First run opens a browser consent page once; the token is cached to
     `server/token.json` (or GCAL_TOKEN) and refreshed automatically after.

Graceful: if the libraries or credentials are missing, this source is skipped
with a clear message — the refresh never hard-fails.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

from . import persona_sections as ps

_SERVER_DIR = Path(__file__).resolve().parent.parent
CREDENTIALS_PATH = Path(os.getenv("GCAL_CREDENTIALS", str(_SERVER_DIR / "credentials.json")))
TOKEN_PATH = Path(os.getenv("GCAL_TOKEN", str(_SERVER_DIR / "token.json")))
AVAILABILITY_JSON = _SERVER_DIR / "availability.json"

SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]
WORK_START, WORK_END = 9, 18  # local working-hours window for "free" computation


class _Skip(Exception):
    """Raised to skip this source with a human-readable reason."""


def _load_service():
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError as e:
        raise _Skip(
            f"google calendar libraries not installed ({e}). "
            "Run: uv sync (adds google-api-python-client + google-auth-oauthlib)."
        ) from e

    creds = None
    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CREDENTIALS_PATH.exists():
                raise _Skip(
                    f"no OAuth client at {CREDENTIALS_PATH}. Create a Desktop OAuth "
                    "client in Google Cloud Console and save it there (or set "
                    "GCAL_CREDENTIALS)."
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_PATH), SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def _merge(intervals: list[tuple[dt.datetime, dt.datetime]]) -> list[tuple[dt.datetime, dt.datetime]]:
    intervals = sorted(intervals)
    merged: list[tuple[dt.datetime, dt.datetime]] = []
    for s, e in intervals:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


def _free_windows(work_start: dt.datetime, work_end: dt.datetime,
                  busy: list[tuple[dt.datetime, dt.datetime]]) -> list[tuple[dt.datetime, dt.datetime]]:
    free: list[tuple[dt.datetime, dt.datetime]] = []
    cursor = work_start
    for s, e in busy:
        s = max(s, work_start)
        e = min(e, work_end)
        if s > cursor:
            free.append((cursor, s))
        cursor = max(cursor, e)
    if cursor < work_end:
        free.append((cursor, work_end))
    return free


def _fmt_time(t: dt.datetime) -> str:
    return t.strftime("%-I%p").lower() if t.minute == 0 else t.strftime("%-I:%M%p").lower()


def _fmt_window(s: dt.datetime, e: dt.datetime) -> str:
    return f"{_fmt_time(s)}–{_fmt_time(e)}"


def fetch_availability(days: int) -> dict:
    """Query free/busy for the next `days` days and derive a structured summary."""
    service = _load_service()
    tz = dt.datetime.now().astimezone().tzinfo
    now = dt.datetime.now(tz)
    start = now
    end = (now + dt.timedelta(days=days)).replace(hour=WORK_END, minute=0, second=0, microsecond=0)

    fb = service.freebusy().query(body={
        "timeMin": start.isoformat(),
        "timeMax": end.isoformat(),
        "items": [{"id": "primary"}],
    }).execute()
    raw_busy = fb["calendars"]["primary"].get("busy", [])
    busy = _merge([
        (dt.datetime.fromisoformat(b["start"]).astimezone(tz),
         dt.datetime.fromisoformat(b["end"]).astimezone(tz))
        for b in raw_busy
    ])

    per_day = []
    for d in range(days):
        day = (now + dt.timedelta(days=d)).date()
        ws = dt.datetime.combine(day, dt.time(WORK_START), tzinfo=tz)
        we = dt.datetime.combine(day, dt.time(WORK_END), tzinfo=tz)
        if d == 0:
            ws = max(ws, now)  # today: only future windows
        day_busy = [(s, e) for s, e in busy if e > ws and s < we]
        frees = _free_windows(ws, we, day_busy)
        per_day.append({
            "date": day.isoformat(),
            "label": "today" if d == 0 else day.strftime("%a"),
            "busy": [[_fmt_time(s), _fmt_time(e)] for s, e in day_busy],
            "free": [_fmt_window(s, e) for s, e in frees if (e - s).total_seconds() >= 1800],
        })

    return {"generated": now.isoformat(), "days": days, "per_day": per_day}


def _to_bullets(avail: dict) -> list[str]:
    lines: list[str] = []
    today = avail["per_day"][0] if avail["per_day"] else None
    if today:
        free = ", ".join(today["free"]) or "no open slots in working hours"
        lines.append(f"- Today: free {free}." if today["free"] else f"- Today: {free}.")
        if today["busy"]:
            busy = ", ".join(f"{b[0]}–{b[1]}" for b in today["busy"])
            lines.append(f"- Today's commitments: {busy} (don't promise these).")
    week_free = [d["label"] for d in avail["per_day"][1:] if d["free"]]
    if week_free:
        lines.append(f"- Has open callback windows this week on: {', '.join(week_free)}.")
    lines.append("- Offer a callback and capture best time/number; never confirm a specific meeting.")
    return lines


def ingest(days: int = 7, *, dry_run: bool = False) -> dict:
    report: dict = {"source": "calendar", "status": "skipped", "blocks": []}
    try:
        avail = fetch_availability(days)
    except _Skip as e:
        report["reason"] = str(e)
        return report
    except Exception as e:  # noqa: BLE001 - network/API errors shouldn't crash refresh
        report["reason"] = f"calendar fetch failed: {e}"
        return report

    if not dry_run:
        AVAILABILITY_JSON.write_text(json.dumps(avail, indent=2), encoding="utf-8")
    lines = _to_bullets(avail)
    changed, _ = ps.update_file("Availability", "calendar", lines, dry_run=dry_run)
    report["blocks"].append(
        {"section": "Availability", "block_id": "calendar", "lines": lines, "changed": changed}
    )
    report["status"] = "ok"
    report["engine"] = "calendar-api"
    report["stats"] = {"days": days, "availability_json": str(AVAILABILITY_JSON)}
    return report


if __name__ == "__main__":
    import pprint

    pprint.pp(ingest(dry_run=True))
