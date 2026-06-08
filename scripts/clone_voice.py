#!/usr/bin/env python3
"""Re-clone the owner's voice on Gradium from a high-quality sample.

Why this exists
---------------
The agent's current clone ("jerry") was an *instant* clone built from a ~10s
``recording.webm``. Instant clones capture timbre but miss prosody and edge
phonemes, so they only sound "kind of" like you. The single biggest quality
lever is the SAMPLE: 60-90s of clean, varied, expressive speech (or a Pro clone
from 30+ min). This script makes re-cloning a one-liner and prints an optimized
read-aloud passage so the sample covers the sounds that matter.

Usage
-----
    # 1. See the script to read, then record yourself (Voice Memos / QuickTime),
    #    export a WAV or MP3, ~60-90 seconds, quiet room, normal speaking voice:
    uv run python scripts/clone_voice.py --script

    # 2. List your existing Gradium voices (confirms auth + shows current clone):
    uv run python scripts/clone_voice.py --list

    # 3. Create the new clone and wire it into .env.local in one step:
    uv run python scripts/clone_voice.py path/to/my_sample.wav \
        --name "Jerry v2" --set-env

Auth: GRADIUM_API_KEY is read from server/.env / server/.env.local. The Gradium
REST API authenticates with the ``X-API-Key`` header (not Bearer).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import requests

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - dotenv ships with the server deps
    load_dotenv = None

_REPO = Path(__file__).resolve().parent.parent
_SERVER = _REPO / "server"
_API_BASE = "https://api.gradium.ai/api/voices/"

# A phonetically varied ~80s passage that also seeds the exact words the STT and
# clone need to nail. Read it naturally and with normal expression — not flat.
READ_SCRIPT = """\
Hi, this is my real voice, recorded to train my personal voice agent.

I'm reading this slowly and clearly, the way I'd actually talk on the phone.
The quick brown fox jumps over the lazy dog. She sells seashells by the seashore.
I went to Stanford, and I'm currently on the Stanford waitlist for a seminar.
Can you grab a grape, a great grey goose, and a glass of grapefruit juice?

Here are some numbers I might say: one, two, three, fourteen, twenty-seven,
two thousand twenty-six, four-one-five, five-five-five, zero-one-double-three.
Names matter too: Sarah, Michael, Priya, Diego, Mr. Anderson, Dr. Okafor.

Sometimes I'm excited and a little loud, and sometimes I lower my voice and slow
right down to make a point. I ask questions, right? And I answer them plainly.
Thanks so much for calling — I'll make sure this gets where it needs to go. Bye for now.
"""


def _load_env() -> None:
    if load_dotenv is None:
        return
    for name in (".env", ".env.local"):
        path = _SERVER / name
        if path.exists():
            load_dotenv(path, override=True)


def _key() -> str:
    key = os.getenv("GRADIUM_API_KEY", "").strip()
    if not key:
        sys.exit("GRADIUM_API_KEY not found in environment or server/.env(.local).")
    return key


def _headers() -> dict[str, str]:
    return {"X-API-Key": _key()}


def cmd_list() -> int:
    resp = requests.get(
        _API_BASE, headers=_headers(), params={"limit": 200}, timeout=30
    )
    if not resp.ok:
        print(f"List failed: HTTP {resp.status_code} {resp.text[:200]}")
        return 1
    voices = resp.json()
    custom = [v for v in voices if not v.get("is_catalog")]
    if not custom:
        print("No custom voices yet. Create one with this script.")
        return 0
    print(f"Your custom Gradium voices ({len(custom)}):")
    for v in custom:
        kind = "pro-clone" if v.get("is_pro_clone") else "instant"
        print(
            f"  {v.get('uid'):20}  {v.get('name', ''):20}  [{kind}]  "
            f"lang={v.get('language')}  from={v.get('filename')}"
        )
    return 0


def _write_env(uid: str) -> None:
    """Point .env.local at the new clone (and enable the consent guard)."""
    env_path = _SERVER / ".env.local"
    lines = env_path.read_text().splitlines() if env_path.exists() else []
    updates = {
        "GRADIUM_CLONED_VOICE_ID": uid,
        "OWNER_VOICE_CONSENT": "true",
    }
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line else ""
        if key in updates:
            out.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            out.append(line)
    for key, val in updates.items():
        if key not in seen:
            out.append(f"{key}={val}")
    env_path.write_text("\n".join(out) + "\n")
    print(f"Updated {env_path}: GRADIUM_CLONED_VOICE_ID={uid}, OWNER_VOICE_CONSENT=true")


def cmd_create(args: argparse.Namespace) -> int:
    audio_path = Path(args.audio_file).expanduser()
    if not audio_path.exists():
        print(f"Audio file not found: {audio_path}")
        return 1
    size_mb = audio_path.stat().st_size / 1_000_000
    print(f"Uploading {audio_path.name} ({size_mb:.1f} MB) as voice '{args.name}'...")
    with audio_path.open("rb") as fh:
        files = {"audio_file": (audio_path.name, fh)}
        data: dict[str, str] = {"name": args.name, "language": args.language}
        if args.description:
            data["description"] = args.description
        if args.start_s:
            data["start_s"] = str(args.start_s)
        resp = requests.post(
            _API_BASE, headers=_headers(), files=files, data=data, timeout=180
        )
    if not resp.ok:
        print(f"Create failed: HTTP {resp.status_code} {resp.text[:300]}")
        return 1
    body = resp.json()
    if body.get("error"):
        print(f"Gradium error: {body['error']}")
        return 1
    uid = body.get("uid")
    if not uid:
        print(f"Unexpected response (no uid): {body}")
        return 1
    print(f"\n✅ Clone created. voice id (uid): {uid}")
    if args.set_env:
        _write_env(uid)
    else:
        print("Set it live with:")
        print(f"  GRADIUM_CLONED_VOICE_ID={uid}")
        print("  OWNER_VOICE_CONSENT=true")
        print("(or re-run with --set-env to write these to server/.env.local)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio_file", nargs="?", help="Audio sample (wav/mp3/etc.)")
    parser.add_argument("--name", default="Owner clone", help="Voice display name")
    parser.add_argument("--description", default="", help="Optional voice description")
    parser.add_argument("--language", default="en", help="Language code (default en)")
    parser.add_argument(
        "--start-s", type=float, default=0.0, help="Skip this many seconds of lead-in"
    )
    parser.add_argument(
        "--set-env", action="store_true", help="Write the new uid to server/.env.local"
    )
    parser.add_argument("--list", action="store_true", help="List existing voices")
    parser.add_argument(
        "--script", action="store_true", help="Print the read-aloud sample script"
    )
    args = parser.parse_args()

    if args.script:
        print(READ_SCRIPT)
        return 0

    _load_env()
    if args.list:
        return cmd_list()
    if not args.audio_file:
        parser.print_help()
        return 1
    return cmd_create(args)


if __name__ == "__main__":
    raise SystemExit(main())
