"""Owner onboarding config — written by the web UI, read by the voice bot."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

CONFIG_PATH = Path(__file__).parent / "owner_config.json"

DEFAULT: dict[str, Any] = {
    "display_name": "",
    "owner_phone": "",
    "owner_email": "",
    "timezone": "America/Los_Angeles",
    "manual_availability": "",
    "setup_complete": False,
    # ── Message context (Gate C) ──────────────────────────────────────────────
    # Redacted summary of recent threads (topics/urgency only — never raw bodies).
    # `source` records who last wrote it so an owner's manual edit is not clobbered
    # by an auto-sync until the owner explicitly re-syncs from Messages.
    "message_context_summary": "",
    "message_context_source": "manual",  # "manual" | "imessage"
    "message_context_synced_at": "",      # ISO-8601 UTC, set on auto-sync only
    # Public onboarding stores only safe, owner-authored metadata here. The
    # cloned voice ID remains in .env.local so it never leaks through the API.
    "voicemail_forwarding_confirmed": False,
    "voice_clone_name": "",
    "voice_clone_status": "not_started",
    "voice_clone_updated_at": "",
    # Derived, privacy-bounded owner diction. Raw onboarding replies are never
    # stored; only short recurring snippets and coarse style notes survive.
    "diction_examples": "",
    "diction_profile_phrases": [],
    "diction_profile_style_notes": [],
    "diction_profile_lines": [],
    "diction_profile_updated_at": "",
}

# Soft cap on the total persona_context string injected into the system prompt.
_PERSONA_CONTEXT_LIMIT = 1500


def load_owner_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        return dict(DEFAULT)
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULT)
    merged = dict(DEFAULT)
    merged.update({k: v for k, v in data.items() if k in DEFAULT})
    return merged


def save_owner_config(data: dict[str, Any]) -> dict[str, Any]:
    merged = load_owner_config()
    for key in DEFAULT:
        if key in data:
            merged[key] = data[key]
    CONFIG_PATH.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
    return merged


def build_persona_context() -> str:
    """Build the P2 ``persona_context`` string from saved owner config."""
    cfg = load_owner_config()
    parts: list[str] = []
    name = (cfg.get("display_name") or "").strip()
    if name:
        parts.append(f"You are answering on behalf of {name}.")
    tz = (cfg.get("timezone") or "").strip()
    if tz:
        parts.append(f"Owner timezone: {tz}.")
    avail = (cfg.get("manual_availability") or "").strip()
    if avail:
        parts.append(f"Owner is generally available for callbacks: {avail}.")
    summary = (cfg.get("message_context_summary") or "").strip()
    if summary:
        source = (cfg.get("message_context_source") or "manual").strip()
        prefix = (
            "Recent message context (auto-synced summary):"
            if source == "imessage"
            else "Recent context (owner-provided summary):"
        )
        parts.append(f"{prefix} {summary}")
    diction_lines = cfg.get("diction_profile_lines") or []
    if isinstance(diction_lines, list) and diction_lines:
        clean_lines = [str(line).strip() for line in diction_lines if str(line).strip()]
        if clean_lines:
            parts.append("Owner diction profile:\n" + "\n".join(clean_lines))
    else:
        # Compatibility for existing single-owner configs written before the
        # derived onboarding profile shipped.
        diction = str(cfg.get("diction_examples") or "").strip()
        if diction:
            parts.append(
                "Owner diction examples: "
                f"{diction}. Mirror this phrasing lightly and naturally; do not force slang."
            )
    email = (cfg.get("owner_email") or "").strip()
    if email:
        parts.append(f"After the call, email summaries go to {email}.")
    if not parts:
        return ""
    context = " ".join(parts)
    if len(context) > _PERSONA_CONTEXT_LIMIT:
        context = context[: _PERSONA_CONTEXT_LIMIT - 1].rstrip() + "…"
    return context
