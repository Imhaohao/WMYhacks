#
# Copyright (c) 2024–2026, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Owner contact book → caller recognition (Person 3).

Turns ``lookup_persona`` from a stub into a real feature: when a call comes in,
the bot can recognise *"that's Sarah"* from the owner's own contact book and
greet by name instead of asking.

Design
------
The contact book is loaded **once at import time** into an in-memory index keyed
by a normalised phone number, so the live voice path does a plain dict lookup —
no SQLite query and no API call mid-call.

Sources (both are read, vCard overlaid last so a curated file wins):

  1. **macOS Contacts SQLite DB** — the literal user contact book at
     ``~/Library/Application Support/AddressBook/**/AddressBook-v22.abcddb``.
     Read-only. Requires macOS **Full Disk Access** for the process running the
     bot (System Settings → Privacy & Security → Full Disk Access → your
     Terminal). Same toggle iMessage ingestion needs.

  2. **vCard export** — ``server/contacts.vcf`` (or ``CONTACTS_VCF`` path). Zero
     permissions, fully offline, deterministic. The recommended demo fallback:
     export the exact contacts you want on stage and you control the set.

Privacy invariants (consistent with the rest of P3)
---------------------------------------------------
- Only **name** and an optional **relationship** hint are derived. The raw
  contact book is never dumped into the prompt or persisted.
- The index is in-memory and rebuilt per process. The macOS DB is opened
  read-only (``mode=ro``).
- No phone numbers other than the inbound caller's are ever sent anywhere.

CLI (sanity check, prints counts only — never dumps numbers):
    uv run python contacts.py --status
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

from loguru import logger

# ─── Phone-number normalisation ──────────────────────────────────────────────
#
# Contacts store numbers in many shapes ("(415) 555-1234", "+1 415-555-1234");
# Twilio hands us E.164 ("+14155551234"). We normalise both sides to a stable
# key. ``phonenumbers`` is used when available for proper E.164 parsing;
# otherwise we fall back to the last-10-digits heuristic, which is correct for
# US/CA numbers and good enough for a demo.

try:  # optional, better matching when present
    import phonenumbers  # type: ignore
except ImportError:  # pragma: no cover - exercised only when dep is absent
    phonenumbers = None  # type: ignore[assignment]

_DEFAULT_REGION = os.getenv("CONTACTS_DEFAULT_REGION", "US")


def normalize_number(raw: str | None) -> str | None:
    """Normalise a phone number to a comparable key.

    Returns an E.164 string (``"+14155551234"``) when ``phonenumbers`` can parse
    it; otherwise the last 10 digits. Returns ``None`` for empty / un-numeric
    input so callers can skip non-matches cleanly.
    """
    if not raw:
        return None
    digits = "".join(c for c in str(raw) if c.isdigit())
    if len(digits) < 10:
        return None
    if phonenumbers is not None:
        try:
            parsed = phonenumbers.parse(str(raw), _DEFAULT_REGION)
            if phonenumbers.is_valid_number(parsed):
                return phonenumbers.format_number(
                    parsed, phonenumbers.PhoneNumberFormat.E164
                )
        except Exception:  # noqa: BLE001 - fall through to digit heuristic
            pass
    return digits[-10:]


# ─── macOS Contacts DB source ────────────────────────────────────────────────


def _macos_db_paths() -> list[Path]:
    """All AddressBook SQLite databases for the current user (may be several —
    one per account/source). Returns an empty list off macOS or if none exist."""
    base = Path.home() / "Library" / "Application Support" / "AddressBook"
    if not base.exists():
        return []
    return sorted(base.glob("**/AddressBook-v22.abcddb"))


def _load_from_macos_db() -> dict[str, dict[str, Any]]:
    """Build a ``{normalized_number: {name, relationship, source}}`` index from
    the macOS Contacts DB. Never raises — a locked / permission-denied / missing
    DB just yields no entries (and logs why)."""
    index: dict[str, dict[str, Any]] = {}
    paths = _macos_db_paths()
    if not paths:
        return index

    query = """
        SELECT r.ZFIRSTNAME, r.ZLASTNAME, r.ZORGANIZATION, p.ZFULLNUMBER
        FROM ZABCDPHONENUMBER p
        JOIN ZABCDRECORD r ON p.ZOWNER = r.Z_PK
        WHERE p.ZFULLNUMBER IS NOT NULL
    """
    for db in paths:
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            try:
                rows = con.execute(query).fetchall()
            finally:
                con.close()
        except sqlite3.Error as exc:
            # Most common cause: no Full Disk Access -> "unable to open database".
            logger.info(f"contacts: skipped macOS DB '{db.name}' ({type(exc).__name__})")
            continue
        for first, last, org, number in rows:
            key = normalize_number(number)
            if not key:
                continue
            name = " ".join(part for part in (first, last) if part).strip() or (org or "")
            if not name:
                continue
            # Don't clobber a name we already have with an org-only later row.
            index.setdefault(key, {"name": name, "relationship": None, "source": "macos"})
    return index


# ─── vCard source (tiny hand-rolled parser, no extra dependency) ─────────────


def _vcard_path() -> Path:
    return Path(os.getenv("CONTACTS_VCF", str(Path(__file__).parent / "contacts.vcf")))


def parse_vcards(text: str) -> dict[str, dict[str, Any]]:
    """Parse a minimal subset of vCard 3.0/4.0: ``FN`` (full name), ``TEL``
    (phone, possibly with type params), and ``CATEGORIES`` (first one used as a
    coarse relationship hint, e.g. ``CATEGORIES:Family``).

    One card may have several TELs — each maps to the same name. Returns the
    same ``{normalized_number: {name, relationship, source}}`` shape.
    """
    index: dict[str, dict[str, Any]] = {}
    name: str | None = None
    relationship: str | None = None
    numbers: list[str] = []

    def flush() -> None:
        nonlocal name, relationship, numbers
        if name:
            for num in numbers:
                key = normalize_number(num)
                if key:
                    index.setdefault(
                        key,
                        {"name": name, "relationship": relationship, "source": "vcard"},
                    )
        name, relationship, numbers = None, None, []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        upper = line.upper()
        if upper == "BEGIN:VCARD":
            name, relationship, numbers = None, None, []
        elif upper == "END:VCARD":
            flush()
        elif upper.startswith("FN"):
            name = line.split(":", 1)[1].strip() if ":" in line else None
        elif upper.startswith("TEL"):
            if ":" in line:
                numbers.append(line.split(":", 1)[1].strip())
        elif upper.startswith("CATEGORIES"):
            if ":" in line:
                cats = line.split(":", 1)[1].strip()
                relationship = cats.split(",")[0].strip() or None
    # Trailing card without an explicit END (defensive).
    flush()
    return index


def _load_from_vcard() -> dict[str, dict[str, Any]]:
    path = _vcard_path()
    if not path.exists():
        return {}
    try:
        return parse_vcards(path.read_text(encoding="utf-8"))
    except OSError as exc:
        logger.info(f"contacts: could not read vCard '{path}' ({type(exc).__name__})")
        return {}


# ─── Public index ────────────────────────────────────────────────────────────


def build_contact_index() -> dict[str, dict[str, Any]]:
    """Build the merged contact index from all sources. macOS DB first, vCard
    overlaid last so a curated ``contacts.vcf`` wins on conflict."""
    index = _load_from_macos_db()
    for key, entry in _load_from_vcard().items():
        index[key] = entry  # vCard overrides DB
    return index


# Built once at import time; the live path just does dict lookups.
_INDEX: dict[str, dict[str, Any]] = build_contact_index()


def reload_index() -> int:
    """Rebuild the in-memory index (e.g. after exporting a fresh vCard). Returns
    the number of numbers indexed."""
    global _INDEX
    _INDEX = build_contact_index()
    return len(_INDEX)


def lookup(number: str | None) -> dict[str, Any] | None:
    """Resolve an inbound caller number to ``{name, relationship, source}``.

    Returns ``None`` when the number isn't in the owner's contact book.
    """
    key = normalize_number(number)
    if not key:
        return None
    return _INDEX.get(key)


def index_size() -> int:
    """How many phone numbers are indexed (for logging / the status CLI)."""
    return len(_INDEX)


def source_summary() -> str:
    """Human string naming where contacts came from, with counts — no numbers."""
    by_source: dict[str, int] = {}
    for entry in _INDEX.values():
        by_source[entry.get("source", "?")] = by_source.get(entry.get("source", "?"), 0) + 1
    if not by_source:
        macos = bool(_macos_db_paths())
        vcf = _vcard_path().exists()
        hint = []
        if not macos:
            hint.append("no macOS Contacts DB (off-mac or no Full Disk Access)")
        if not vcf:
            hint.append(f"no vCard at {_vcard_path().name}")
        return "0 contacts — " + ("; ".join(hint) if hint else "empty sources")
    parts = ", ".join(f"{n} from {src}" for src, n in by_source.items())
    return f"{index_size()} contacts ({parts})"


logger.info(f"contacts loaded — {source_summary()}")


# ─── CLI ─────────────────────────────────────────────────────────────────────


def _main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Owner contact book status (no numbers printed)")
    parser.add_argument("--status", action="store_true", help="print source + counts")
    parser.add_argument(
        "--check",
        metavar="NUMBER",
        help="resolve one number to a name (prints name only, for a quick smoke test)",
    )
    args = parser.parse_args()

    print(source_summary())
    if args.check:
        hit = lookup(args.check)
        if hit:
            rel = f" ({hit['relationship']})" if hit.get("relationship") else ""
            print(f"match: {hit['name']}{rel} [via {hit.get('source')}]")
        else:
            print("no match")
    if not args.status and not args.check:
        print("(use --status or --check <number>)")


if __name__ == "__main__":
    _main()
