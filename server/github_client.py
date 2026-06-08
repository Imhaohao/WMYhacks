"""GitHub read-side client for the voice agent (owner developer-tool access).

Pure REST helper (no Pipecat dependency) so it can be unit-tested standalone,
mirroring ``google_calendar.py`` / ``google_gmail.py``. Wrapped as voice tools in
``owner_tools.py``.

Auth: ``GITHUB_TOKEN`` (a fine-grained or classic PAT). For the richest answers
grant it ``repo`` (read) + ``notifications``. ``GITHUB_USERNAME`` pins the account
for public-event lookups and is auto-resolved from the token when omitted.

Degrades gracefully:
  * No token -> public events only for ``GITHUB_USERNAME`` (no PRs / notifications).
  * No token AND no username -> ``is_configured()`` is False; tools say "not connected".
"""

from __future__ import annotations

import os
from typing import Any

import requests
from loguru import logger

_API = "https://api.github.com"
_TIMEOUT = 10


def _token() -> str:
    return os.getenv("GITHUB_TOKEN", "").strip()


def _headers() -> dict[str, str]:
    h = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    tok = _token()
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    return h


def username() -> str:
    """Resolve the GitHub login: env override first, then the token's own user."""
    name = os.getenv("GITHUB_USERNAME", "").strip()
    if name:
        return name
    if not _token():
        return ""
    try:
        r = requests.get(f"{_API}/user", headers=_headers(), timeout=_TIMEOUT)
        if r.ok:
            return str(r.json().get("login") or "")
    except requests.RequestException as exc:
        logger.warning(f"github_client: user lookup failed ({type(exc).__name__})")
    return ""


def is_configured() -> bool:
    return bool(_token() or os.getenv("GITHUB_USERNAME", "").strip())


def _recent_events(user: str, limit: int) -> list[dict[str, Any]]:
    try:
        r = requests.get(
            f"{_API}/users/{user}/events",
            headers=_headers(),
            params={"per_page": min(max(limit, 1), 30)},
            timeout=_TIMEOUT,
        )
        if not r.ok:
            return []
        out: list[dict[str, Any]] = []
        for ev in r.json()[:limit]:
            out.append(
                {
                    "type": str(ev.get("type", "")).replace("Event", ""),
                    "repo": (ev.get("repo") or {}).get("name", ""),
                    "created_at": ev.get("created_at", ""),
                }
            )
        return out
    except (requests.RequestException, ValueError) as exc:
        logger.warning(f"github_client: events failed ({type(exc).__name__})")
        return []


def _open_pull_requests(user: str, limit: int) -> list[dict[str, Any]]:
    if not _token():
        return []
    try:
        r = requests.get(
            f"{_API}/search/issues",
            headers=_headers(),
            params={"q": f"is:open is:pr author:{user}", "per_page": min(max(limit, 1), 20)},
            timeout=_TIMEOUT,
        )
        if not r.ok:
            return []
        out: list[dict[str, Any]] = []
        for it in r.json().get("items", [])[:limit]:
            out.append(
                {
                    "title": it.get("title", ""),
                    "repo": str(it.get("repository_url", "")).split("/repos/")[-1],
                    "url": it.get("html_url", ""),
                }
            )
        return out
    except (requests.RequestException, ValueError) as exc:
        logger.warning(f"github_client: PR search failed ({type(exc).__name__})")
        return []


def _unread_notifications() -> int | None:
    if not _token():
        return None
    try:
        r = requests.get(
            f"{_API}/notifications",
            headers=_headers(),
            params={"per_page": 50},
            timeout=_TIMEOUT,
        )
        if r.ok:
            return len(r.json())
    except (requests.RequestException, ValueError) as exc:
        logger.warning(f"github_client: notifications failed ({type(exc).__name__})")
    return None


def summary(limit: int = 5) -> dict[str, Any]:
    """Compact, voice-friendly snapshot of the owner's recent GitHub activity."""
    if not is_configured():
        return {
            "configured": False,
            "detail": "Set GITHUB_TOKEN (and optionally GITHUB_USERNAME) in server/.env.",
        }
    user = username()
    if not user:
        return {
            "configured": True,
            "detail": "Could not resolve a GitHub username; set GITHUB_USERNAME.",
        }
    return {
        "configured": True,
        "username": user,
        "has_token": bool(_token()),
        "recent_events": _recent_events(user, limit),
        "open_pull_requests": _open_pull_requests(user, limit),
        "unread_notifications": _unread_notifications(),
    }
