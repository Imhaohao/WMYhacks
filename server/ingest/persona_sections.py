"""Idempotent editing of comment-fenced blocks inside persona_context.md.

Part B writes DERIVED summaries into specific sections of the curated
`persona_context.md` without clobbering the hand-written baseline. Each source
owns a fenced block:

    # Current Priorities

    - curated baseline line (kept as fallback)
    <!-- BEGIN:ingest:imessage -->
    - (derived) ...
    <!-- END:ingest:imessage -->

Because `persona_context.load_persona_context()` strips HTML comments on load,
the fence markers are invisible to the bot — only the derived bullets reach the
prompt. Re-running a refresh replaces the block in place, so the operation is
idempotent and never duplicates content.
"""

from __future__ import annotations

import re
from pathlib import Path

# persona_context.md lives one level up, in server/.
DEFAULT_PATH = Path(__file__).resolve().parent.parent / "persona_context.md"


def _begin(block_id: str) -> str:
    return f"<!-- BEGIN:ingest:{block_id} -->"


def _end(block_id: str) -> str:
    return f"<!-- END:ingest:{block_id} -->"


def render_block(block_id: str, lines: list[str]) -> str:
    """Render a fenced block from derived lines (bullets already formatted)."""
    body = "\n".join(lines).strip()
    return f"{_begin(block_id)}\n{body}\n{_end(block_id)}"


def read_block(text: str, block_id: str) -> str | None:
    """Return the current body of a fenced block, or None if absent."""
    pattern = re.compile(
        re.escape(_begin(block_id)) + r"\n?(.*?)\n?" + re.escape(_end(block_id)),
        re.DOTALL,
    )
    m = pattern.search(text)
    return m.group(1).strip() if m else None


def apply_block(text: str, section_header: str, block_id: str, lines: list[str]) -> str:
    """Return `text` with the block replaced (if present) or inserted.

    If the fence already exists anywhere in the document it is replaced in
    place. Otherwise the block is inserted right after the `# <section_header>`
    line. Curated content in the section is preserved either way.

    Raises ValueError if the section header is missing and no fence exists yet.
    """
    block = render_block(block_id, lines)

    fence = re.compile(
        re.escape(_begin(block_id)) + r".*?" + re.escape(_end(block_id)),
        re.DOTALL,
    )
    if fence.search(text):
        # Replace via a function so backslashes in `block` aren't treated as
        # regex backreferences.
        return fence.sub(lambda _: block, text, count=1)

    header = re.compile(r"^#\s+" + re.escape(section_header) + r"\s*$", re.MULTILINE)
    m = header.search(text)
    if not m:
        raise ValueError(f"section header not found: {section_header!r}")
    insert_at = m.end()
    return text[:insert_at] + "\n\n" + block + text[insert_at:]


def update_file(
    section_header: str,
    block_id: str,
    lines: list[str],
    path: str | Path = DEFAULT_PATH,
    dry_run: bool = False,
) -> tuple[bool, str]:
    """Apply a block to the persona file on disk.

    Returns (changed, new_text). When dry_run is True the file is not written.
    """
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    new = apply_block(text, section_header, block_id, lines)
    changed = new != text
    if changed and not dry_run:
        p.write_text(new, encoding="utf-8")
    return changed, new


if __name__ == "__main__":
    _, preview = update_file(
        "Current Priorities",
        "demo",
        ["- (derived) demo bullet"],
        dry_run=True,
    )
    print(preview)
