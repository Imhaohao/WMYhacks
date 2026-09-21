"""Box-side deploy steps, driven by ops/deploy_voicemail.sh.

Run ON the always-on Mac, inside the synced repo dir. Reads config from env:

    DEPLOY_URL    funnel URL (e.g. https://box.tail123.ts.net)
    DEPLOY_UV     absolute path to uv
    DEPLOY_PHONE  the Twilio number that owns inbound calls

Actions (all idempotent):
    1. set PUBLIC_BASE_URL in server/.env
    2. point the Twilio number's voice webhook at {DEPLOY_URL}/   (skipped when
       TWILIO creds or DEPLOY_PHONE are missing, e.g. dry runs)
    3. write the launchd plist that keeps the bot running
"""

import base64
import os
import plistlib
import sys
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parent.parent
PLIST_LABEL = "com.voicemail.elevenlabs"
PLIST_PATH = Path.home() / "Library/LaunchAgents" / f"{PLIST_LABEL}.plist"


def patch_public_base_url() -> None:
    env_file = SERVER_DIR / ".env"
    lines = env_file.read_text().splitlines() if env_file.exists() else []
    out: list[str] = []
    found = False
    for line in lines:
        if line.startswith("PUBLIC_BASE_URL="):
            out.append(f"PUBLIC_BASE_URL={os.environ['DEPLOY_URL']}")
            found = True
        else:
            out.append(line)
    if not found:
        out.append(f"PUBLIC_BASE_URL={os.environ['DEPLOY_URL']}")
    env_file.write_text("\n".join(out) + "\n")
    print(f"PUBLIC_BASE_URL -> {os.environ['DEPLOY_URL']}")


async def set_twilio_webhook() -> None:
    """Point the voice webhook of DEPLOY_PHONE at the funnel URL."""
    import aiohttp
    from dotenv import dotenv_values

    url = os.environ["DEPLOY_URL"]
    phone = os.environ.get("DEPLOY_PHONE", "")
    env = dotenv_values(SERVER_DIR / ".env")
    sid, tok = env.get("TWILIO_ACCOUNT_SID", ""), env.get("TWILIO_AUTH_TOKEN", "")
    if not phone or not sid or not tok:
        print("twilio webhook skipped (missing DEPLOY_PHONE or TWILIO creds)")
        return
    headers = {
        "Authorization": "Basic "
        + base64.b64encode(f"{sid}:{tok}".encode()).decode()
    }
    base = f"https://api.twilio.com/2010-04-01/Accounts/{sid}"
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{base}/IncomingPhoneNumbers.json", headers=headers) as resp:
            numbers = (await resp.json()).get("incoming_phone_numbers", [])
        number = next(
            (n for n in numbers if n.get("phone_number") == phone), None
        )
        if number is None:
            print(f"twilio: {phone} not owned by account {sid} — webhook NOT updated")
            return
        async with session.post(
            f"{base}/IncomingPhoneNumbers/{number['sid']}.json",
            headers=headers,
            data={"VoiceUrl": f"{url}/", "VoiceMethod": "POST"},
        ) as resp:
            print(f"twilio webhook -> {url}/  (HTTP {resp.status})")


def install_launchd() -> None:
    uv = os.environ["DEPLOY_UV"]
    plist = {
        "Label": PLIST_LABEL,
        "ProgramArguments": [uv, "run", "python", "bot-nemotron.py"],
        "WorkingDirectory": str(SERVER_DIR),
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "StandardOutPath": str(Path.home() / "Library/Logs/voicemail-bot.log"),
        "StandardErrorPath": str(Path.home() / "Library/Logs/voicemail-bot.err.log"),
        "EnvironmentVariables": {"ENV": "local"},
    }
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(PLIST_PATH, "wb") as fh:
        plistlib.dump(plist, fh)
    print(f"plist written: {PLIST_PATH}")


def main() -> None:
    import asyncio

    if "DEPLOY_URL" not in os.environ or "DEPLOY_UV" not in os.environ:
        print("usage: DEPLOY_URL=... DEPLOY_UV=/path/to/uv [DEPLOY_PHONE=+1...] uv run python ops/deploy_on_box.py")
        sys.exit(2)
    patch_public_base_url()
    install_launchd()
    asyncio.run(set_twilio_webhook())


if __name__ == "__main__":
    main()
