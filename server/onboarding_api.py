"""FastAPI onboarding API for the Gotchu web UI."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import contacts
import message_digest
import owner_config

SERVER_DIR = Path(__file__).parent
VCF_PATH = SERVER_DIR / "contacts.vcf"

load_dotenv(SERVER_DIR / ".env")
load_dotenv(SERVER_DIR / ".env.local", override=True)


class OwnerConfigBody(BaseModel):
    display_name: str | None = None
    owner_phone: str | None = None
    owner_email: str | None = None
    timezone: str | None = None
    manual_availability: str | None = None
    setup_complete: bool | None = None
    message_context_summary: str | None = None
    message_context_source: str | None = None


def _env_ok(name: str) -> tuple[bool, str]:
    val = os.getenv(name, "").strip()
    if val:
        return True, "configured"
    return False, f"{name} not set in .env"


def _contacts_sample_names(limit: int = 8) -> list[str]:
    names: list[str] = []
    for entry in contacts._INDEX.values():  # noqa: SLF001 — onboarding preview only
        n = entry.get("name")
        if n and n not in names:
            names.append(str(n))
        if len(names) >= limit:
            break
    return names


app = FastAPI(title="Gotchu Onboarding API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/owner-config")
def get_owner_config() -> dict:
    return owner_config.load_owner_config()


@app.post("/api/owner-config")
def post_owner_config(body: OwnerConfigBody) -> dict:
    patch = body.model_dump(exclude_none=True)
    # An owner editing the summary by hand reclaims it as "manual" so the next
    # auto-sync (force=False) won't clobber it until an explicit re-sync.
    if "message_context_summary" in patch and "message_context_source" not in patch:
        patch["message_context_source"] = "manual"
    return owner_config.save_owner_config(patch)


@app.post("/api/context/sync-messages")
def sync_messages() -> dict:
    """Refresh the redacted message digest from the producer (P2 seam).

    Returns topics/urgency summary + sync metadata only — never raw messages.
    """
    return message_digest.sync_message_context(force=True)


@app.post("/api/contacts/upload")
async def upload_contacts(file: UploadFile = File(...)) -> dict:
    content = await file.read()
    VCF_PATH.write_bytes(content)
    count = contacts.reload_index()
    return {
        "ok": True,
        "count": count,
        "summary": contacts.source_summary(),
        "sample_names": _contacts_sample_names(),
    }


@app.get("/api/health")
def health_check() -> dict:
    checks: dict[str, dict[str, str | bool]] = {}

    for key, env_name in [
        ("gradium", "GRADIUM_API_KEY"),
        ("nemotron_llm", "NEMOTRON_LLM_URL"),
        ("nemotron_stt", "NVIDIA_ASR_URL"),
        ("twilio", "TWILIO_ACCOUNT_SID"),
        ("owner_email", "OWNER_EMAIL"),
        ("gmail_smtp", "GMAIL_APP_PASSWORD"),
    ]:
        ok, detail = _env_ok(env_name)
        checks[key] = {"ok": ok, "detail": detail}

    persist_table = os.getenv("PERSIST_DDB_TABLE", "ff-voicemails")
    checks["persistence"] = {
        "ok": True,
        "detail": f"DynamoDB table {persist_table} or local JSONL fallback",
    }

    cfg = owner_config.load_owner_config()
    checks["owner_profile"] = {
        "ok": bool(cfg.get("display_name") and cfg.get("owner_email")),
        "detail": "display name + email saved" if cfg.get("display_name") else "complete setup wizard",
    }

    required_ok = checks["owner_profile"]["ok"] and checks["persistence"]["ok"]

    return {
        "ok": required_ok,
        "checks": checks,
        "contacts": {
            "count": contacts.index_size(),
            "summary": contacts.source_summary(),
            "sample_names": _contacts_sample_names(),
        },
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("onboarding_api:app", host="127.0.0.1", port=8787, reload=True)
