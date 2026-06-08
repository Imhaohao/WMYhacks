"""macOS Reminders client for the voice agent (osascript bridge).

Pure subprocess helper so it can be tested without Pipecat. Works only when the
bot runs ON a Mac with the Reminders app (local dev). On Pipecat Cloud / Linux it
degrades cleanly: ``is_available()`` is False and the tools report that reminders
are not reachable.

First use triggers a one-time macOS automation-permission prompt for the
controlling process (Terminal / python). Grant it once in
System Settings -> Privacy & Security -> Automation.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from typing import Any

from loguru import logger

_TIMEOUT = 20


def is_available() -> bool:
    return sys.platform == "darwin" and shutil.which("osascript") is not None


def _esc(s: str) -> str:
    """Escape a Python string for an AppleScript double-quoted literal."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _run(script: str) -> tuple[bool, str]:
    if not is_available():
        return False, "Reminders is only available when the agent runs on macOS."
    try:
        proc = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return False, "Reminders timed out."
    except OSError as exc:
        return False, f"Could not run osascript ({type(exc).__name__})."
    if proc.returncode != 0:
        err = (proc.stderr or "").strip()
        logger.warning(f"reminders_client: osascript failed: {err}")
        if "1743" in err or "not authoriz" in err.lower() or "not allowed" in err.lower():
            return False, (
                "Not authorized to control Reminders — grant access in "
                "System Settings > Privacy & Security > Automation."
            )
        return False, err or "Reminders command failed."
    return True, (proc.stdout or "").strip()


def add_reminder(
    title: str,
    notes: str | None = None,
    list_name: str | None = None,
) -> dict[str, Any]:
    """Create a reminder in the macOS Reminders app. Returns {ok, detail, title}."""
    title = (title or "").strip()
    if not title:
        return {"ok": False, "detail": "Empty reminder title."}
    props = f'name:"{_esc(title)}"'
    if notes:
        props += f', body:"{_esc(notes)}"'
    if list_name:
        script = (
            f'tell application "Reminders" to make new reminder at '
            f'list "{_esc(list_name)}" with properties {{{props}}}'
        )
    else:
        script = (
            f'tell application "Reminders" to make new reminder '
            f"with properties {{{props}}}"
        )
    ok, out = _run(script)
    if ok:
        return {"ok": True, "title": title, "detail": f"Added reminder: {title}"}
    return {"ok": False, "detail": out}


def list_reminders(limit: int = 10, include_completed: bool = False) -> dict[str, Any]:
    """List reminder titles from the default list. Returns {ok, reminders, total}.

    Scoped to the *default list* on purpose: a global ``reminders whose completed
    is false`` query scans every iCloud list and routinely takes >15s (it times
    out), whereas the default list returns in a few seconds. ``add_reminder`` also
    targets the default list, so add + list stay consistent.
    """
    completed_clause = "" if include_completed else " whose completed is false"
    # Join names with newlines so titles containing commas survive parsing.
    script = (
        'tell application "Reminders"\n'
        f"set theNames to name of (reminders of default list{completed_clause})\n"
        "end tell\n"
        "set AppleScript's text item delimiters to linefeed\n"
        "return theNames as text"
    )
    ok, out = _run(script)
    if not ok:
        return {"ok": False, "detail": out, "reminders": [], "total": 0}
    items = [line.strip() for line in out.split("\n") if line.strip()]
    return {"ok": True, "reminders": items[: max(limit, 1)], "total": len(items)}
