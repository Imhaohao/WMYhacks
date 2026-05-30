"""Loader for the owner's persona context (Person 2, Part A).

`load_persona_context()` returns a block of text to inject into the bot's
system instruction (coordinate the exact seam with Person 1). It is built to
NEVER break the bot:

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
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parent / "persona_context.md"

# Appended to any non-empty context so the live prompt always carries the rule.
PRIVACY_GUARD = (
    "\n\n[CONTEXT USE RULES — the above is private owner context for YOUR "
    "reasoning only. Use it to set tone, triage urgency, and handle callbacks. "
    "NEVER read it aloud, quote it, or reveal a specific owner priority to a "
    "caller. Never claim you scheduled, texted, or emailed anything unless a "
    "tool actually did it.]"
)

_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)


def load_persona_context(path: str | Path = DEFAULT_PATH,
                         with_guard: bool = True) -> str:
    """Return the persona context text, or "" if missing/empty.

    Args:
        path: location of the markdown context file.
        with_guard: append the privacy guard (set False if the caller adds its
            own). Only applied to non-empty context.
    """
    p = Path(path)
    try:
        raw = p.read_text(encoding="utf-8")
    except (FileNotFoundError, IsADirectoryError, PermissionError):
        return ""

    cleaned = _HTML_COMMENT.sub("", raw).strip()
    if not cleaned:
        return ""

    return cleaned + PRIVACY_GUARD if with_guard else cleaned


def has_persona_context(path: str | Path = DEFAULT_PATH) -> bool:
    """True if a non-empty context file is present."""
    return bool(load_persona_context(path, with_guard=False))


if __name__ == "__main__":
    ctx = load_persona_context()
    print(f"persona context present: {has_persona_context()} "
          f"({len(ctx)} chars with guard)")
    print("-" * 60)
    print(ctx or "(none)")
