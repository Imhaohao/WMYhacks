"""Loader for the owner's persona context (Person 2, Part A).

`load_persona_context()` returns a block of text to inject into the bot's
system instruction (coordinate the exact seam with Person 1). It checks the
cloud persona first and degrades safely to the local curated file:

- cloud record present -> returns the cleaned cloud persona
- missing file        -> returns "" (bot runs with no extra context)
- empty / whitespace  -> returns ""
- present             -> returns the file text, HTML comments stripped, with a
                          standing PRIVACY guard appended so the model treats it
                          as reasoning-only context, never something to recite.

Part B overwrites sections of persona_context.md with summaries derived from
real iMessage / Calendar data; this loader doesn't care how the file was
produced.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import persona_cloud

DEFAULT_PATH = Path(__file__).resolve().parent / "persona_context.md"

# Appended to any non-empty context so the live prompt always carries the rule.
PRIVACY_GUARD = (
    "\n\n[CONTEXT USE RULES — the above is private owner context for YOUR "
    "reasoning only. Use it to set tone, triage urgency, and handle callbacks. "
    "NEVER read it aloud, quote it, or reveal a specific owner priority to a "
    "caller. A dedicated contact-gated tool may return one caller-facing calendar "
    "answer_hint; only that hint may be shared. Never claim you scheduled, texted, "
    "or emailed anything unless a tool actually did it.]"
)

_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)


def clean_persona_context(raw: str) -> str:
    """Strip local authoring comments and surrounding whitespace."""
    return _HTML_COMMENT.sub("", raw).strip()


def _read_local(path: str | Path) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except (FileNotFoundError, IsADirectoryError, PermissionError):
        return ""


def load_persona_context(path: str | Path = DEFAULT_PATH,
                         with_guard: bool = True,
                         prefer_cloud: bool | None = None) -> str:
    """Return the persona context text, or "" if missing/empty.

    Args:
        path: location of the markdown context file.
        with_guard: append the privacy guard (set False if the caller adds its
            own). Only applied to non-empty context.
        prefer_cloud: fetch cloud context before the local file. Defaults to
            true only for the standard persona path.
    """
    if prefer_cloud is None:
        prefer_cloud = Path(path) == DEFAULT_PATH

    raw = persona_cloud.fetch_persona() if prefer_cloud else None
    if not raw and not (prefer_cloud and persona_cloud.cloud_required()):
        raw = _read_local(path)
    cleaned = clean_persona_context(raw or "")
    if not cleaned:
        return ""

    return cleaned + PRIVACY_GUARD if with_guard else cleaned


def upload_persona_context(path: str | Path = DEFAULT_PATH) -> str | None:
    """Publish the cleaned local persona to the configured AWS cloud backend."""
    cleaned = clean_persona_context(_read_local(path))
    return persona_cloud.publish_persona(cleaned)


def has_persona_context(path: str | Path = DEFAULT_PATH,
                        prefer_cloud: bool | None = None) -> bool:
    """True if a non-empty context file is present."""
    return bool(load_persona_context(path, with_guard=False, prefer_cloud=prefer_cloud))


if __name__ == "__main__":
    if "--upload-cloud" in sys.argv:
        location = upload_persona_context()
        if location:
            print(f"persona uploaded: {location}")
            raise SystemExit(0)
        print("persona upload skipped: AWS cloud backend unavailable", file=sys.stderr)
        raise SystemExit(1)

    ctx = load_persona_context()
    print(f"persona context present: {has_persona_context()} "
          f"({len(ctx)} chars with guard)")
    print("-" * 60)
    print(ctx or "(none)")
