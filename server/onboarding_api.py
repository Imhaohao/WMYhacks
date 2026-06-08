"""FastAPI onboarding API for the Gotchu web UI."""

from __future__ import annotations

import html
import os
import secrets
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from loguru import logger
from pydantic import BaseModel, Field

import action_bridge
import contacts
import google_calendar
import google_gmail
import message_digest
import owner_config
import tenant_google
import tenant_store
import twilio_provisioning
from ingest.voice_lingo import derive_onboarding_lingo, render_lingo_lines

# Where the OAuth popup posts back after it stores tokens. In the public demo
# the frontend is Vercel and the API is the voice/ngrok origin, so WEB_APP_URL
# must be allowed to differ from PUBLIC_BASE_URL.
WEB_APP_URL = (
    os.getenv("WEB_APP_URL", "").strip()
    or os.getenv("PUBLIC_BASE_URL", "").strip()
    or "http://localhost:5173"
).rstrip("/")

SERVER_DIR = Path(__file__).parent
VCF_PATH = SERVER_DIR / "contacts.vcf"
_MIN_VOICE_SAMPLE_SECONDS = 20.0
_MAX_VOICE_SAMPLE_SECONDS = 90.0

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
    voicemail_forwarding_confirmed: bool | None = None
    voice_clone_name: str | None = None
    voice_clone_status: str | None = None
    voice_clone_updated_at: str | None = None
    diction_examples: str | None = None
    diction_profile_phrases: list[str] | None = None
    diction_profile_style_notes: list[str] | None = None
    diction_profile_lines: list[str] | None = None
    diction_profile_updated_at: str | None = None


class VoicemailForwardingBody(BaseModel):
    confirmed: bool


class DictionProfileBody(BaseModel):
    samples: list[str] = Field(default_factory=list)
    phrases: list[str] = Field(default_factory=list)


class TenantSessionBody(BaseModel):
    display_name: str = ""
    owner_phone: str = ""
    owner_email: str = ""
    timezone: str = "America/Los_Angeles"


class TenantPhoneAssignmentBody(BaseModel):
    agent_phone: str


def _env_ok(name: str) -> tuple[bool, str]:
    val = os.getenv(name, "").strip()
    if val:
        return True, "configured"
    return False, f"{name} not set in .env"


def _owner_email_value() -> str:
    return os.getenv("OWNER_EMAIL", "").strip() or str(
        owner_config.load_owner_config().get("owner_email") or ""
    ).strip()


def _owner_provisioning_enabled() -> bool:
    """Keep the single-owner mutation API private unless explicitly enabled."""
    return os.getenv("ALLOW_OWNER_PROVISIONING", "").strip().lower() == "true"


def _require_owner_provisioning() -> None:
    if not _owner_provisioning_enabled():
        raise HTTPException(
            status_code=403,
            detail="Private owner provisioning is disabled on this deployment.",
        )


def _public_tenant_onboarding_enabled() -> bool:
    return os.getenv("ENABLE_PUBLIC_TENANT_ONBOARDING", "").strip().lower() == "true"


def _require_public_tenant_onboarding() -> None:
    if not _public_tenant_onboarding_enabled():
        raise HTTPException(
            status_code=503,
            detail="Public tenant onboarding is not enabled on this deployment.",
        )


def _require_public_voice_cloning() -> None:
    if os.getenv("ENABLE_PUBLIC_VOICE_CLONING", "").strip().lower() != "true":
        raise HTTPException(
            status_code=503,
            detail="Public voice cloning is not enabled on this deployment.",
        )


def _normalize_google_service(service: str) -> str:
    service = service.strip().lower()
    if service not in {"gmail", "calendar"}:
        raise HTTPException(status_code=404, detail="Unsupported Google service.")
    return service


def _require_tenant_token(x_onboarding_token: str | None) -> str:
    token = (x_onboarding_token or "").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Missing onboarding token.")
    return token


def _require_admin_key(x_provisioning_key: str | None) -> None:
    expected = os.getenv("ONBOARDING_ADMIN_KEY", "").strip()
    supplied = (x_provisioning_key or "").strip()
    if not expected or not supplied or not secrets.compare_digest(expected, supplied):
        raise HTTPException(status_code=403, detail="Invalid provisioning key.")


def _auto_phone_assignment_enabled() -> bool:
    return os.getenv("ENABLE_AUTO_AGENT_PHONE_ASSIGNMENT", "").strip().lower() == "true"


def _agent_phone_pool() -> list[str]:
    raw = os.getenv("TENANT_AGENT_PHONE_POOL", "")
    return [phone.strip() for phone in raw.split(",") if phone.strip()]


def _auto_phone_purchase_enabled() -> bool:
    return os.getenv("ENABLE_AUTO_AGENT_PHONE_PURCHASE", "").strip().lower() == "true"


def _auto_phone_purchase_daily_limit() -> int:
    raw = os.getenv("TWILIO_AUTO_PURCHASE_DAILY_LIMIT", "3").strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return 0


def _auto_phone_purchase_requires_cloud() -> bool:
    return os.getenv("TWILIO_AUTO_PURCHASE_REQUIRE_CLOUD", "true").strip().lower() == "true"


def _shared_forwarding_enabled() -> bool:
    return os.getenv("ENABLE_SHARED_FORWARDING_ROUTING", "").strip().lower() == "true"


def _shared_forwarding_phone() -> str:
    return os.getenv("SHARED_FORWARDING_PHONE_NUMBER", "").strip() or os.getenv(
        "TWILIO_PHONE_NUMBER",
        "",
    ).strip()


def _nanp_area_code(phone: str | None) -> str:
    digits = "".join(ch for ch in str(phone or "") if ch.isdigit())
    if len(digits) == 11 and digits.startswith("1"):
        return digits[1:4]
    if len(digits) == 10:
        return digits[:3]
    return ""


def _assign_from_agent_phone_pool(tenant_id: str) -> tuple[dict | None, dict]:
    """Assign the first free pre-purchased Twilio number from the configured pool."""
    if not _auto_phone_assignment_enabled():
        return None, {"mode": "disabled", "assigned": False}
    pool = _agent_phone_pool()
    if not pool:
        return None, {"mode": "pool", "assigned": False, "detail": "No pool numbers configured."}
    conflicts: list[str] = []
    webhook_failures: list[dict] = []
    for phone in pool:
        if tenant_store.get_agent_by_phone(phone):
            conflicts.append(phone)
            continue
        webhook = twilio_provisioning.wire_inbound_webhook(phone)
        if not webhook.get("ok"):
            logger.warning("pool agent-phone webhook not wired for {}: {}", phone, webhook.get("detail"))
            webhook_failures.append(
                {
                    "phone": phone,
                    "detail": webhook.get("detail", "Twilio webhook wiring failed."),
                }
            )
            continue
        try:
            agent = tenant_store.assign_agent_phone(tenant_id, phone, source="pool")
        except tenant_store.TenantConflict:
            conflicts.append(phone)
            continue
        return (
            agent,
            {
                "mode": "pool",
                "assigned": True,
                "agent_phone": agent.get("agent_phone"),
                "webhook": webhook,
            },
        )
    return (
        None,
        {
            "mode": "pool",
            "assigned": False,
            "detail": (
                "No configured pool numbers could be wired to the voice API."
                if webhook_failures
                else "All configured pool numbers are already assigned."
            ),
            "conflicts": conflicts,
            "webhook_failures": webhook_failures,
        },
    )


def _purchase_and_assign_agent_phone(
    tenant_id: str,
    *,
    owner_phone: str,
    prior_assignment: dict | None = None,
) -> tuple[dict | None, dict]:
    """Purchase one Twilio number for a tenant when explicitly enabled."""
    if not _auto_phone_purchase_enabled():
        return None, prior_assignment or {"mode": "disabled", "assigned": False}
    if _auto_phone_purchase_requires_cloud() and tenant_store.backend_kind() != "dynamodb":
        return (
            None,
            {
                "mode": "purchase",
                "assigned": False,
                "detail": "Live phone purchase requires cloud tenant storage.",
                "previous": prior_assignment or {"mode": "disabled", "assigned": False},
            },
        )
    daily_limit = _auto_phone_purchase_daily_limit()
    if daily_limit <= 0:
        return (
            None,
            {
                "mode": "purchase",
                "assigned": False,
                "detail": "Live phone purchase daily limit is not configured.",
                "previous": prior_assignment or {"mode": "disabled", "assigned": False},
            },
        )
    since_iso = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    purchases_today = tenant_store.count_agent_phone_assignments(
        source="purchase",
        since_iso=since_iso,
    )
    if purchases_today >= daily_limit:
        return (
            None,
            {
                "mode": "purchase",
                "assigned": False,
                "detail": "Live phone purchase daily limit reached.",
                "daily_limit": daily_limit,
                "purchases_today": purchases_today,
                "previous": prior_assignment or {"mode": "disabled", "assigned": False},
            },
        )

    country = os.getenv("TWILIO_AUTO_PURCHASE_COUNTRY", "US").strip().upper() or "US"
    configured_area = os.getenv("TWILIO_AUTO_PURCHASE_AREA_CODE", "").strip()
    area_code = configured_area or _nanp_area_code(owner_phone)
    contains = os.getenv("TWILIO_AUTO_PURCHASE_CONTAINS", "").strip() or None
    near_number = os.getenv("TWILIO_AUTO_PURCHASE_NEAR_NUMBER", "").strip() or None
    if not near_number and os.getenv("TWILIO_AUTO_PURCHASE_NEAR_OWNER", "true").lower() == "true":
        near_number = owner_phone.strip() or None

    purchase = twilio_provisioning.purchase_and_wire_local_number(
        country=country,
        area_code=area_code or None,
        contains=contains,
        near_number=near_number,
        friendly_name=f"Gotchu tenant {tenant_id[:8]}",
    )
    if not purchase.get("ok"):
        return (
            None,
            {
                "mode": "purchase",
                "assigned": False,
                "detail": purchase.get("detail", "Twilio number purchase failed."),
                "previous": prior_assignment or {"mode": "disabled", "assigned": False},
            },
        )

    agent_phone = str(purchase.get("agent_phone") or "").strip()
    try:
        agent = tenant_store.assign_agent_phone(tenant_id, agent_phone, source="purchase")
    except Exception as exc:
        return (
            None,
            {
                "mode": "purchase",
                "assigned": False,
                "detail": f"Purchased number could not be assigned: {type(exc).__name__}.",
                "purchased_phone": agent_phone,
            },
        )

    return (
        agent,
        {
            "mode": "purchase",
            "assigned": True,
            "agent_phone": agent.get("agent_phone"),
            "purchase": {
                "ok": True,
                "voice_url": purchase.get("voice_url"),
                "detail": purchase.get("detail"),
                "candidate": purchase.get("candidate"),
            },
        },
    )


def _assign_shared_forwarding_phone(
    tenant_id: str,
    *,
    prior_assignment: dict | None = None,
) -> tuple[dict | None, dict]:
    if not _shared_forwarding_enabled():
        return None, prior_assignment or {"mode": "disabled", "assigned": False}
    forwarding_phone = _shared_forwarding_phone()
    if not forwarding_phone:
        return (
            None,
            {
                "mode": "shared_forwarding",
                "assigned": False,
                "detail": "No shared forwarding phone is configured.",
                "previous": prior_assignment or {"mode": "disabled", "assigned": False},
            },
        )
    try:
        webhook = twilio_provisioning.wire_inbound_webhook(forwarding_phone)
        if not webhook.get("ok"):
            return (
                None,
                {
                    "mode": "shared_forwarding",
                    "assigned": False,
                    "detail": webhook.get("detail", "Shared forwarding webhook could not be wired."),
                    "webhook": webhook,
                    "previous": prior_assignment or {"mode": "disabled", "assigned": False},
                },
            )
        agent = tenant_store.assign_shared_forwarding_phone(tenant_id, forwarding_phone)
    except Exception as exc:
        return (
            None,
            {
                "mode": "shared_forwarding",
                "assigned": False,
                "detail": f"Shared forwarding could not be assigned: {type(exc).__name__}.",
                "previous": prior_assignment or {"mode": "disabled", "assigned": False},
            },
        )
    return (
        agent,
        {
            "mode": "shared_forwarding",
            "assigned": True,
            "agent_phone": agent.get("forwarding_phone"),
            "forwarding_phone": agent.get("forwarding_phone"),
            "routing_mode": agent.get("routing_mode"),
            "webhook": webhook,
            "detail": (
                "Calls forwarded to this shared number are routed by Twilio's "
                "ForwardedFrom field when the carrier provides it."
            ),
        },
    )


def _tenant_activation_missing(record: dict) -> list[str]:
    missing: list[str] = []
    if not str(record.get("display_name") or "").strip():
        missing.append("display_name")
    if not str(record.get("owner_phone") or "").strip():
        missing.append("owner_phone")
    if not str(record.get("owner_email") or "").strip():
        missing.append("owner_email")
    if record.get("voice_clone_status") != "ready":
        missing.append("voice_clone")
    if not record.get("diction_profile_lines"):
        missing.append("diction_profile")
    return missing


def _tenant_phone_provisioning_status() -> dict:
    purchase_enabled = _auto_phone_purchase_enabled()
    daily_limit = _auto_phone_purchase_daily_limit()
    purchases_today = 0
    if purchase_enabled and daily_limit > 0:
        since_iso = (datetime.now(UTC) - timedelta(days=1)).isoformat()
        try:
            purchases_today = tenant_store.count_agent_phone_assignments(
                source="purchase",
                since_iso=since_iso,
            )
        except Exception as exc:
            logger.warning("tenant phone provisioning status count failed: {}", type(exc).__name__)
    return {
        "pool_enabled": _auto_phone_assignment_enabled(),
        "pool_size": len(_agent_phone_pool()),
        "purchase_enabled": purchase_enabled,
        "purchase_daily_limit": daily_limit,
        "purchases_today": purchases_today,
        "purchase_requires_cloud": _auto_phone_purchase_requires_cloud(),
        "shared_forwarding_enabled": _shared_forwarding_enabled(),
        "shared_forwarding_phone": _shared_forwarding_phone() if _shared_forwarding_enabled() else "",
    }


def _tenant_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, tenant_store.TenantNotFound):
        return HTTPException(status_code=404, detail="Onboarding session not found.")
    if isinstance(exc, tenant_store.TenantAuthError):
        return HTTPException(status_code=403, detail="Invalid onboarding token.")
    if isinstance(exc, tenant_store.TenantConflict):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, tenant_store.TenantStoreUnavailable):
        return HTTPException(status_code=503, detail=str(exc))
    return HTTPException(status_code=503, detail="Tenant storage is unavailable.")


def _tenant_google_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, tenant_google.TenantGoogleUnavailable):
        return HTTPException(status_code=503, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=503, detail="Tenant Google connection is unavailable.")


def _write_env_local(updates: dict[str, str]) -> None:
    """Update owner-only runtime settings without returning them through the API."""
    env_path = SERVER_DIR / ".env.local"
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line else ""
        if key in updates:
            out.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            out.append(line)
    for key, value in updates.items():
        if key not in seen:
            out.append(f"{key}={value}")
    env_path.write_text("\n".join(out) + "\n", encoding="utf-8")


def _derive_diction_profile(body: DictionProfileBody) -> dict:
    """Derive a bounded owner profile without retaining raw onboarding replies."""
    samples = [" ".join(sample.split()).strip() for sample in body.samples]
    samples = [sample for sample in samples if sample]
    if len(samples) < 3:
        raise HTTPException(status_code=422, detail="Add at least three example replies.")
    if any(len(sample) > 600 for sample in samples) or sum(map(len, samples)) > 2400:
        raise HTTPException(status_code=422, detail="Keep diction samples brief.")

    profile = derive_onboarding_lingo(samples, explicit_phrases=body.phrases)
    lines = render_lingo_lines(profile, "Owner onboarding")
    if not lines:
        raise HTTPException(
            status_code=422,
            detail="Add a recurring phrase or use a little more of your natural texting style.",
        )
    return {
        "ready": True,
        "sample_count": len(samples),
        "phrases": profile.phrases,
        "style_notes": profile.style_notes,
        "lines": lines,
    }


def _clone_gradium_voice(
    *,
    content: bytes,
    filename: str,
    content_type: str,
    voice_name: str,
    duration_seconds: float,
) -> tuple[str, str]:
    """Create one consented Gradium clone without retaining the uploaded audio."""
    api_key = os.getenv("GRADIUM_API_KEY", "").strip()
    if not api_key:
        raise HTTPException(status_code=503, detail="Gradium is not configured.")
    if duration_seconds < _MIN_VOICE_SAMPLE_SECONDS:
        raise HTTPException(
            status_code=422,
            detail="Please record at least 20 seconds of speech.",
        )
    if duration_seconds > _MAX_VOICE_SAMPLE_SECONDS:
        raise HTTPException(
            status_code=422,
            detail="Please keep voice samples under 90 seconds.",
        )
    if len(content) < 20_000:
        raise HTTPException(status_code=422, detail="Voice sample audio is too small.")
    if len(content) > 25_000_000:
        raise HTTPException(status_code=413, detail="Voice samples must be smaller than 25 MB.")

    safe_name = " ".join(voice_name.split()).strip()[:80] or "Owner clone"
    try:
        response = requests.post(
            "https://api.gradium.ai/api/voices/",
            headers={"X-API-Key": api_key},
            files={
                "audio_file": (
                    filename or "owner-voice.webm",
                    content,
                    content_type or "application/octet-stream",
                )
            },
            data={"name": safe_name, "language": "en"},
            timeout=180,
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail="Gradium voice creation failed.") from exc
    if not response.ok:
        raise HTTPException(status_code=502, detail="Gradium rejected the voice sample.")
    body = response.json()
    voice_id = str(body.get("uid") or "").strip()
    if not voice_id:
        raise HTTPException(status_code=502, detail="Gradium did not return a voice ID.")
    return voice_id, safe_name


def _delete_gradium_voice(voice_id: str) -> dict:
    """Best-effort deletion for tenant-owned Gradium clones."""
    voice_id = voice_id.strip()
    if not voice_id:
        return {"deleted": False, "skipped": True, "detail": "No voice clone id was stored."}
    api_key = os.getenv("GRADIUM_API_KEY", "").strip()
    if not api_key:
        return {"deleted": False, "skipped": True, "detail": "Gradium is not configured."}
    try:
        response = requests.delete(
            f"https://api.gradium.ai/api/voices/{voice_id}",
            headers={"X-API-Key": api_key},
            timeout=30,
        )
    except requests.RequestException as exc:
        logger.warning("Gradium voice deletion failed for {}: {}", voice_id, type(exc).__name__)
        return {"deleted": False, "skipped": False, "detail": "Gradium voice deletion failed."}
    if response.status_code in {204, 404}:
        return {"deleted": True, "skipped": False}
    logger.warning(
        "Gradium voice deletion rejected for {} with status {}",
        voice_id,
        response.status_code,
    )
    return {"deleted": False, "skipped": False, "detail": "Gradium rejected voice deletion."}


def _pending_action_counts() -> dict[str, int]:
    counts = {"email": 0, "calendar": 0}
    for action in action_bridge.pending_actions():
        kind = action.get("action")
        if kind == "send_email":
            counts["email"] += 1
        elif kind == "create_event":
            counts["calendar"] += 1
    return counts


def _contacts_sample_names(limit: int = 8) -> list[str]:
    names: list[str] = []
    for entry in contacts._INDEX.values():  # noqa: SLF001 — onboarding preview only
        n = entry.get("name")
        if n and n not in names:
            names.append(str(n))
        if len(names) >= limit:
            break
    return names


def _calendar_service_status(cfg: dict, pending: int) -> dict:
    """Calendar block for /api/services, backed by the real Google connection."""
    gstatus = google_calendar.status()
    connected = bool(gstatus.get("connected"))
    readable = bool(gstatus.get("readable", connected))
    tz = cfg.get("timezone") or os.getenv("OWNER_TZ", "America/Los_Angeles")
    detail = gstatus.get("detail", "")
    if connected and not readable:
        detail = gstatus.get("api_error") or detail
    return {
        "ok": readable,
        "mode": "google_oauth",
        "detail": detail,
        "connected": connected,
        "readable": readable,
        "configured": bool(gstatus.get("configured")),
        "target": gstatus.get("email") or "",
        "timezone": tz,
        "pending_actions": pending,
    }


def _gmail_service_status(email: str, smtp_ready: bool, pending: int) -> dict:
    """Gmail block for /api/services, preferring direct OAuth when connected."""
    gstatus = google_gmail.status()
    connected = bool(gstatus.get("connected"))
    if connected:
        return {
            "ok": True,
            "mode": "google_oauth",
            "detail": gstatus.get("detail", "Gmail connected."),
            "connected": True,
            "configured": bool(gstatus.get("configured")),
            "target": gstatus.get("email") or email,
            "pending_actions": pending,
        }
    if smtp_ready:
        return {
            "ok": True,
            "mode": "smtp",
            "detail": "SMTP is configured for real inbox delivery.",
            "connected": False,
            "configured": bool(gstatus.get("configured")),
            "target": email,
            "pending_actions": pending,
        }
    return {
        "ok": bool(email),
        "mode": "mcp_draft_bridge",
        "detail": (
            "Owner email is saved; connect Gmail for OAuth delivery or use the MCP draft bridge."
            if email
            else "Add your owner email first."
        ),
        "connected": False,
        "configured": bool(gstatus.get("configured")),
        "target": email,
        "pending_actions": pending,
    }


_cors_origins = ["http://localhost:5173", "http://127.0.0.1:5173"]
for origin in (WEB_APP_URL, os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")):
    if origin and origin not in _cors_origins:
        _cors_origins.append(origin)

app = FastAPI(title="Gotchu Onboarding API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/owner-config")
def get_owner_config() -> dict:
    _require_owner_provisioning()
    return owner_config.load_owner_config()


@app.post("/api/owner-config")
def post_owner_config(body: OwnerConfigBody) -> dict:
    _require_owner_provisioning()
    patch = body.model_dump(exclude_none=True)
    # An owner editing the summary by hand reclaims it as "manual" so the next
    # auto-sync (force=False) won't clobber it until an explicit re-sync.
    if "message_context_summary" in patch and "message_context_source" not in patch:
        patch["message_context_source"] = "manual"
    return owner_config.save_owner_config(patch)


@app.post("/api/onboarding/sessions", status_code=201)
def create_tenant_session(body: TenantSessionBody) -> dict:
    """Create a tenant-isolated onboarding session and one-time capability."""
    _require_public_tenant_onboarding()
    try:
        agent, token = tenant_store.create_agent_session(**body.model_dump())
        return {
            "ok": True,
            "onboarding_token": token,
            "storage": tenant_store.backend_kind(),
            "agent": agent,
            "phone_assignment": {
                "mode": "deferred",
                "assigned": False,
                "detail": "Phone provisioning runs during activation.",
            },
        }
    except Exception as exc:
        raise _tenant_http_error(exc) from exc


@app.post("/api/onboarding/sessions/{tenant_id}/activate")
def activate_tenant_agent(
    tenant_id: str,
    x_onboarding_token: str | None = Header(default=None),
) -> dict:
    """Provision the tenant's callable phone line after setup is complete."""
    _require_public_tenant_onboarding()
    try:
        token = _require_tenant_token(x_onboarding_token)
        record = tenant_store.authenticate(tenant_id, token)
        missing = _tenant_activation_missing(record)
        if missing:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": "Finish the required setup steps before activation.",
                    "missing": missing,
                },
            )
        if record.get("agent_phone"):
            webhook = twilio_provisioning.wire_inbound_webhook(str(record.get("agent_phone")))
            agent = tenant_store.update_agent(tenant_id, token, {})
            return {
                "ok": True,
                "agent": agent,
                "phone_assignment": {
                    "mode": "existing",
                    "assigned": True,
                    "agent_phone": agent.get("agent_phone"),
                    "webhook": webhook,
                },
            }

        assigned_agent, phone_assignment = _assign_from_agent_phone_pool(tenant_id)
        if not assigned_agent:
            assigned_agent, phone_assignment = _purchase_and_assign_agent_phone(
                tenant_id,
                owner_phone=str(record.get("owner_phone") or ""),
                prior_assignment=phone_assignment,
            )
        if not assigned_agent:
            assigned_agent, phone_assignment = _assign_shared_forwarding_phone(
                tenant_id,
                prior_assignment=phone_assignment,
            )
        if assigned_agent:
            return {"ok": True, "agent": assigned_agent, "phone_assignment": phone_assignment}
        agent = tenant_store.get_agent(tenant_id, token)
        return {"ok": True, "agent": agent, "phone_assignment": phone_assignment}
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("tenant activation failed")
        raise _tenant_http_error(exc) from exc


@app.get("/api/onboarding/sessions/{tenant_id}")
def get_tenant_session(
    tenant_id: str,
    x_onboarding_token: str | None = Header(default=None),
) -> dict:
    """Read back owner-safe activation state for one isolated agent."""
    _require_public_tenant_onboarding()
    try:
        return {
            "ok": True,
            "agent": tenant_store.get_agent(tenant_id, _require_tenant_token(x_onboarding_token)),
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise _tenant_http_error(exc) from exc


@app.post("/api/onboarding/sessions/{tenant_id}/profile")
def save_tenant_profile(
    tenant_id: str,
    body: TenantSessionBody,
    x_onboarding_token: str | None = Header(default=None),
) -> dict:
    """Persist owner profile fields to the tenant record."""
    _require_public_tenant_onboarding()
    try:
        agent = tenant_store.update_agent(
            tenant_id,
            _require_tenant_token(x_onboarding_token),
            body.model_dump(),
        )
        return {"ok": True, "agent": agent}
    except HTTPException:
        raise
    except Exception as exc:
        raise _tenant_http_error(exc) from exc


@app.post("/api/onboarding/sessions/{tenant_id}/voicemail-forwarding")
def save_tenant_voicemail_forwarding(
    tenant_id: str,
    body: VoicemailForwardingBody,
    x_onboarding_token: str | None = Header(default=None),
) -> dict:
    """Record forwarding confirmation for the owner-scoped agent."""
    _require_public_tenant_onboarding()
    try:
        agent = tenant_store.update_agent(
            tenant_id,
            _require_tenant_token(x_onboarding_token),
            {"voicemail_forwarding_confirmed": body.confirmed},
        )
        return {"ok": True, "agent": agent}
    except HTTPException:
        raise
    except Exception as exc:
        raise _tenant_http_error(exc) from exc


@app.post("/api/onboarding/sessions/{tenant_id}/diction")
def save_tenant_diction_profile(
    tenant_id: str,
    body: DictionProfileBody,
    x_onboarding_token: str | None = Header(default=None),
) -> dict:
    """Persist only the bounded diction profile for one owner."""
    _require_public_tenant_onboarding()
    profile = _derive_diction_profile(body)
    updated_at = datetime.now(UTC).isoformat()
    try:
        agent = tenant_store.update_agent(
            tenant_id,
            _require_tenant_token(x_onboarding_token),
            {
                "diction_profile_phrases": profile["phrases"],
                "diction_profile_style_notes": profile["style_notes"],
                "diction_profile_lines": profile["lines"],
                "diction_profile_updated_at": updated_at,
            },
        )
        return {"ok": True, "updated_at": updated_at, "profile": profile, "agent": agent}
    except HTTPException:
        raise
    except Exception as exc:
        raise _tenant_http_error(exc) from exc


@app.post("/api/onboarding/sessions/{tenant_id}/voice-clone")
async def create_tenant_voice_clone(
    tenant_id: str,
    file: UploadFile = File(...),
    consent: bool = Form(...),
    voice_name: str = Form("Owner clone"),
    duration_seconds: float = Form(...),
    x_onboarding_token: str | None = Header(default=None),
) -> dict:
    """Create and attach an owner-consented Gradium clone to one tenant."""
    _require_public_tenant_onboarding()
    _require_public_voice_cloning()
    if not consent:
        raise HTTPException(status_code=422, detail="Explicit owner voice consent is required.")
    token = _require_tenant_token(x_onboarding_token)
    try:
        tenant_store.authenticate(tenant_id, token)
    except Exception as exc:
        raise _tenant_http_error(exc) from exc
    content = await file.read()
    voice_id, safe_name = _clone_gradium_voice(
        content=content,
        filename=file.filename or "owner-voice.webm",
        content_type=file.content_type or "application/octet-stream",
        voice_name=voice_name,
        duration_seconds=duration_seconds,
    )
    updated_at = datetime.now(UTC).isoformat()
    try:
        agent = tenant_store.update_agent(
            tenant_id,
            token,
            {
                "voice_clone_id": voice_id,
                "voice_clone_consent": True,
                "voice_clone_name": safe_name,
                "voice_clone_status": "ready",
                "voice_clone_updated_at": updated_at,
            },
        )
        return {"ok": True, "updated_at": updated_at, "agent": agent}
    except Exception as exc:
        _delete_gradium_voice(voice_id)
        raise _tenant_http_error(exc) from exc


@app.delete("/api/onboarding/sessions/{tenant_id}/voice-clone")
def delete_tenant_voice_clone(
    tenant_id: str,
    x_onboarding_token: str | None = Header(default=None),
) -> dict:
    """Delete/reset one tenant's consented Gradium clone and clear tenant metadata."""
    _require_public_tenant_onboarding()
    _require_public_voice_cloning()
    token = _require_tenant_token(x_onboarding_token)
    try:
        record = tenant_store.authenticate(tenant_id, token)
    except Exception as exc:
        raise _tenant_http_error(exc) from exc

    deletion = _delete_gradium_voice(str(record.get("voice_clone_id") or ""))
    updated_at = datetime.now(UTC).isoformat()
    try:
        agent = tenant_store.update_agent(
            tenant_id,
            token,
            {
                "voice_clone_id": "",
                "voice_clone_consent": False,
                "voice_clone_name": "",
                "voice_clone_status": "not_started",
                "voice_clone_updated_at": updated_at,
            },
        )
        return {"ok": True, "updated_at": updated_at, "voice_deleted": deletion, "agent": agent}
    except Exception as exc:
        raise _tenant_http_error(exc) from exc


@app.get("/api/onboarding/sessions/{tenant_id}/google/{service}/status")
def tenant_google_status(
    tenant_id: str,
    service: str,
    x_onboarding_token: str | None = Header(default=None),
) -> dict:
    """Read one tenant's Google connection status without exposing tokens."""
    _require_public_tenant_onboarding()
    service = _normalize_google_service(service)
    try:
        agent = tenant_store.get_agent(tenant_id, _require_tenant_token(x_onboarding_token))
        return {
            "ok": True,
            "service": service,
            "status": tenant_google.status(tenant_id, service),
            "agent": agent,
        }
    except HTTPException:
        raise
    except (tenant_store.TenantNotFound, tenant_store.TenantAuthError) as exc:
        raise _tenant_http_error(exc) from exc
    except Exception as exc:
        raise _tenant_google_http_error(exc) from exc


@app.get("/api/onboarding/sessions/{tenant_id}/google/{service}/auth-url")
def tenant_google_auth_url(
    tenant_id: str,
    service: str,
    x_onboarding_token: str | None = Header(default=None),
) -> dict:
    """Start tenant-owned Google OAuth for Gmail or Calendar."""
    _require_public_tenant_onboarding()
    service = _normalize_google_service(service)
    try:
        tenant_store.authenticate(tenant_id, _require_tenant_token(x_onboarding_token))
        if not tenant_google.is_configured(service):
            raise HTTPException(
                status_code=503,
                detail="Google OAuth is not configured on this deployment.",
            )
        return {
            "ok": True,
            "service": service,
            "url": tenant_google.create_auth_url(tenant_id, service),
        }
    except HTTPException:
        raise
    except (tenant_store.TenantNotFound, tenant_store.TenantAuthError) as exc:
        raise _tenant_http_error(exc) from exc
    except Exception as exc:
        raise _tenant_google_http_error(exc) from exc


@app.get("/api/onboarding/google/{service}/callback")
def tenant_google_callback(
    service: str,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> HTMLResponse:
    """OAuth callback for tenant-owned Google connections."""
    service = _normalize_google_service(service)
    event = f"tenant-{service}-oauth-done"
    if error:
        return HTMLResponse(
            _oauth_result_html(False, error, service=service.title(), event=event),
            status_code=400,
        )
    if not code or not state:
        return HTMLResponse(
            _oauth_result_html(False, "Missing OAuth code or state.", service=service.title(), event=event),
            status_code=400,
        )
    try:
        tenant_id, email = tenant_google.exchange_code(service, code, state)
        tenant_store.mark_google_connected(tenant_id, service, email)
        message = f"Connected {email}." if email else f"{service.title()} connected."
        return HTMLResponse(_oauth_result_html(True, message, service=service.title(), event=event))
    except Exception as exc:
        message = str(exc) or "Google OAuth connection failed."
        return HTMLResponse(
            _oauth_result_html(False, message, service=service.title(), event=event),
            status_code=400,
        )


@app.post("/api/onboarding/sessions/{tenant_id}/google/{service}/disconnect")
def tenant_google_disconnect(
    tenant_id: str,
    service: str,
    x_onboarding_token: str | None = Header(default=None),
) -> dict:
    """Disconnect a tenant-owned Google integration."""
    _require_public_tenant_onboarding()
    service = _normalize_google_service(service)
    token = _require_tenant_token(x_onboarding_token)
    try:
        tenant_store.authenticate(tenant_id, token)
        removed = tenant_google.disconnect(tenant_id, service)
        agent = tenant_store.mark_google_disconnected(tenant_id, service, token)
        return {"ok": True, "service": service, "disconnected": removed, "agent": agent}
    except HTTPException:
        raise
    except (tenant_store.TenantNotFound, tenant_store.TenantAuthError) as exc:
        raise _tenant_http_error(exc) from exc
    except Exception as exc:
        raise _tenant_google_http_error(exc) from exc


@app.post("/api/admin/onboarding/sessions/{tenant_id}/agent-phone")
def assign_tenant_agent_phone(
    tenant_id: str,
    body: TenantPhoneAssignmentBody,
    x_provisioning_key: str | None = Header(default=None),
) -> dict:
    """Attach a paid Twilio number after an admin provisioning operation.

    Also points that number's Twilio Voice webhook at the public bot URL so
    inbound calls reach the agent — the same wiring the demo number has — so a
    newly onboarded user's phone is connected without a manual console step.
    """
    _require_admin_key(x_provisioning_key)
    try:
        agent = tenant_store.assign_agent_phone(tenant_id, body.agent_phone)
    except Exception as exc:
        raise _tenant_http_error(exc) from exc
    # Best-effort: never fail the assignment if webhook wiring can't complete.
    webhook = twilio_provisioning.wire_inbound_webhook(body.agent_phone)
    if not webhook.get("ok"):
        logger.warning(f"agent-phone assigned but webhook not wired: {webhook.get('detail')}")
    return {"ok": True, "agent": agent, "webhook": webhook}


@app.get("/api/onboarding/status")
def onboarding_status() -> dict:
    """Safe status readback for the streamlined public onboarding wizard."""
    cfg = owner_config.load_owner_config()
    private_activation_enabled = _owner_provisioning_enabled()
    voice_ready = private_activation_enabled and bool(
        os.getenv("GRADIUM_CLONED_VOICE_ID", "").strip()
        and os.getenv("OWNER_VOICE_CONSENT", "").strip().lower() == "true"
    )
    return {
        "private_activation_enabled": private_activation_enabled,
        "tenant_onboarding_enabled": _public_tenant_onboarding_enabled(),
        "tenant_storage": {
            "backend": tenant_store.backend_kind(),
            "require_cloud": os.getenv("TENANT_STORE_REQUIRE_CLOUD", "").strip().lower() == "true",
        },
        "tenant_phone_provisioning": _tenant_phone_provisioning_status(),
        "agent_phone": os.getenv("TWILIO_PHONE_NUMBER", "").strip()
        if private_activation_enabled
        else "",
        "voicemail_forwarding_confirmed": private_activation_enabled
        and bool(cfg.get("voicemail_forwarding_confirmed")),
        "voice_clone_ready": voice_ready,
        "voice_clone_name": (cfg.get("voice_clone_name") or "")
        if private_activation_enabled
        else "",
        "diction_examples": (cfg.get("diction_examples") or "")
        if private_activation_enabled
        else "",
        "diction_profile": {
            "ready": bool(cfg.get("diction_profile_lines")),
            "phrases": cfg.get("diction_profile_phrases") or [],
            "style_notes": cfg.get("diction_profile_style_notes") or [],
            "lines": cfg.get("diction_profile_lines") or [],
        }
        if private_activation_enabled
        else {"ready": False, "phrases": [], "style_notes": [], "lines": []},
    }


@app.post("/api/onboarding/voicemail-forwarding")
def confirm_voicemail_forwarding(body: VoicemailForwardingBody) -> dict:
    """Record the owner's confirmation after conditional forwarding is enabled."""
    _require_owner_provisioning()
    cfg = owner_config.save_owner_config({"voicemail_forwarding_confirmed": body.confirmed})
    return {
        "ok": True,
        "confirmed": bool(cfg.get("voicemail_forwarding_confirmed")),
    }


@app.post("/api/onboarding/diction/preview")
def preview_diction_profile(body: DictionProfileBody) -> dict:
    """Preview the bounded profile derived from owner-authored sample replies."""
    return _derive_diction_profile(body)


@app.post("/api/onboarding/diction")
def save_diction_profile(body: DictionProfileBody) -> dict:
    """Persist only a derived, privacy-bounded owner diction profile."""
    _require_owner_provisioning()
    profile = _derive_diction_profile(body)
    updated_at = datetime.now(UTC).isoformat()
    owner_config.save_owner_config(
        {
            "diction_examples": "",
            "diction_profile_phrases": profile["phrases"],
            "diction_profile_style_notes": profile["style_notes"],
            "diction_profile_lines": profile["lines"],
            "diction_profile_updated_at": updated_at,
        }
    )
    return {"ok": True, "updated_at": updated_at, "profile": profile}


@app.post("/api/onboarding/voice-clone")
async def create_voice_clone(
    file: UploadFile = File(...),
    consent: bool = Form(...),
    voice_name: str = Form("Owner clone"),
    duration_seconds: float = Form(...),
) -> dict:
    """Create a consented Gradium owner clone and activate it for future calls."""
    _require_owner_provisioning()
    if not consent:
        raise HTTPException(status_code=422, detail="Explicit owner voice consent is required.")
    content = await file.read()
    voice_id, safe_name = _clone_gradium_voice(
        content=content,
        filename=file.filename or "owner-voice.webm",
        content_type=file.content_type or "application/octet-stream",
        voice_name=voice_name,
        duration_seconds=duration_seconds,
    )

    updated_at = datetime.now(UTC).isoformat()
    _write_env_local(
        {
            "GRADIUM_CLONED_VOICE_ID": voice_id,
            "OWNER_VOICE_CONSENT": "true",
        }
    )
    owner_config.save_owner_config(
        {
            "voice_clone_name": safe_name,
            "voice_clone_status": "ready",
            "voice_clone_updated_at": updated_at,
        }
    )
    return {
        "ok": True,
        "voice_clone_ready": True,
        "voice_clone_name": safe_name,
        "updated_at": updated_at,
    }


@app.post("/api/context/sync-messages")
def sync_messages() -> dict:
    """Refresh the redacted message digest from the producer (P2 seam).

    Returns topics/urgency summary + sync metadata only — never raw messages.
    """
    _require_owner_provisioning()
    return message_digest.sync_message_context(force=True)


@app.get("/api/services")
def get_services() -> dict:
    """Return onboarding-facing service connection status.

    MCP connectors live in the agent client, not this FastAPI process, so this
    endpoint reports the local pieces the UI can honestly verify: owner config,
    SMTP readiness, outbox bridge readiness, and redacted Messages digest status.
    """
    _require_owner_provisioning()
    cfg = owner_config.load_owner_config()
    email = _owner_email_value()
    smtp_ready = bool(os.getenv("GMAIL_APP_PASSWORD", "").strip() and email)
    counts = _pending_action_counts()
    source = cfg.get("message_context_source") or "manual"
    summary = str(cfg.get("message_context_summary") or "").strip()

    return {
        "gmail": _gmail_service_status(email, smtp_ready, counts["email"]),
        "calendar": _calendar_service_status(cfg, counts["calendar"]),
        "messages": {
            "ok": bool(summary),
            "mode": "redacted_digest",
            "detail": (
                "Redacted message context is available to the voice agent."
                if summary
                else "Sync from Messages or add a manual summary."
            ),
            "source": source,
            "synced_at": cfg.get("message_context_synced_at") or "",
            "preview": summary[:140],
        },
        "bridge": {
            "ok": True,
            "detail": "Run action_bridge.py after calls to emit Gmail/Calendar MCP requests.",
            "pending_actions": counts["email"] + counts["calendar"],
        },
    }


@app.get("/api/bridge/specs")
def get_bridge_specs(kind: str = "all") -> dict:
    """Return ready-to-fire MCP connector specs for queued outbox actions."""
    _require_owner_provisioning()
    specs = []
    for action in action_bridge.pending_actions():
        action_kind = action.get("action")
        if kind == "gmail" and action_kind != "send_email":
            continue
        if kind == "calendar" and action_kind != "create_event":
            continue
        specs.append(action_bridge.connector_spec(action))
    return {
        "kind": kind,
        "count": len(specs),
        "specs": specs,
    }


# ─── Google Gmail OAuth ──────────────────────────────────────────────────────


@app.get("/api/gmail/google/status")
def gmail_google_status() -> dict:
    """Whether the owner's Gmail is connected (and to which account)."""
    _require_owner_provisioning()
    return google_gmail.status()


@app.get("/api/gmail/google/auth-url")
def gmail_google_auth_url() -> dict:
    """Return the Google consent-screen URL for the setup wizard to open."""
    _require_owner_provisioning()
    if not google_gmail.is_configured():
        return {
            "ok": False,
            "url": None,
            "detail": "Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in server/.env, then retry.",
        }
    try:
        return {"ok": True, "url": google_gmail.auth_url()}
    except Exception as exc:
        return {"ok": False, "url": None, "detail": f"Could not start OAuth: {exc}"}


@app.get("/api/gmail/google/callback")
def gmail_google_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> HTMLResponse:
    """OAuth redirect target: exchange the code, store the token, close popup."""
    _require_owner_provisioning()
    if error or not code:
        message = f"Authorization failed: {error}" if error else "No authorization code returned."
        return HTMLResponse(
            _oauth_result_html(False, message, service="Gmail", event="gmail-oauth-done"),
            status_code=400,
        )
    try:
        email = google_gmail.exchange_code(code, state=state)
        message = f"Connected {email}." if email else "Gmail connected."
        return HTMLResponse(_oauth_result_html(True, message, service="Gmail", event="gmail-oauth-done"))
    except Exception as exc:
        return HTMLResponse(
            _oauth_result_html(
                False,
                f"Token exchange failed: {exc}",
                service="Gmail",
                event="gmail-oauth-done",
            ),
            400,
        )


@app.post("/api/gmail/google/disconnect")
def gmail_google_disconnect() -> dict:
    """Forget the stored Gmail token."""
    _require_owner_provisioning()
    removed = google_gmail.disconnect()
    return {"ok": True, "disconnected": removed}


# ─── Google Calendar OAuth ───────────────────────────────────────────────────


@app.get("/api/calendar/google/status")
def calendar_google_status() -> dict:
    """Whether the owner's Google Calendar is connected (and to which account)."""
    _require_owner_provisioning()
    return google_calendar.status()


@app.get("/api/calendar/google/auth-url")
def calendar_google_auth_url() -> dict:
    """Return the Google consent-screen URL for the setup wizard to open."""
    _require_owner_provisioning()
    if not google_calendar.is_configured():
        return {
            "ok": False,
            "url": None,
            "detail": "Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in server/.env, then retry.",
        }
    try:
        return {"ok": True, "url": google_calendar.auth_url()}
    except Exception as exc:
        return {"ok": False, "url": None, "detail": f"Could not start OAuth: {exc}"}


@app.get("/api/calendar/google/callback")
def calendar_google_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> HTMLResponse:
    """OAuth redirect target: exchange the code, store the token, close popup."""
    _require_owner_provisioning()
    if error or not code:
        message = f"Authorization failed: {error}" if error else "No authorization code returned."
        return HTMLResponse(
            _oauth_result_html(False, message, service="Calendar", event="gcal-oauth-done"),
            status_code=400,
        )
    try:
        email = google_calendar.exchange_code(code, state=state)
        message = f"Connected {email}." if email else "Google Calendar connected."
        return HTMLResponse(
            _oauth_result_html(True, message, service="Calendar", event="gcal-oauth-done")
        )
    except Exception as exc:
        return HTMLResponse(
            _oauth_result_html(
                False,
                f"Token exchange failed: {exc}",
                service="Calendar",
                event="gcal-oauth-done",
            ),
            400,
        )


@app.post("/api/calendar/google/disconnect")
def calendar_google_disconnect() -> dict:
    """Forget the stored Google Calendar token."""
    _require_owner_provisioning()
    removed = google_calendar.disconnect()
    return {"ok": True, "disconnected": removed}


def _oauth_result_html(success: bool, message: str, *, service: str, event: str) -> str:
    """A tiny self-closing page shown inside the OAuth popup."""
    color = "#16794a" if success else "#b42318"
    # message comes from query params (e.g. ?error=) and exception strings, so it
    # must be escaped before interpolation to avoid reflected XSS on the API origin.
    safe_service = html.escape(service)
    safe_message = html.escape(message)
    heading = f"✓ {safe_service} connected" if success else "Connection failed"
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{heading}</title></head>
<body style="font-family:system-ui,sans-serif;background:#f7f6f3;color:#2b2b2b;
display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0">
  <div style="text-align:center;max-width:420px;padding:32px">
    <h2 style="color:{color};margin:0 0 8px">{heading}</h2>
    <p style="color:#555;margin:0 0 20px">{safe_message}</p>
    <p style="color:#999;font-size:13px">You can close this window and return to setup.</p>
  </div>
  <script>
    try {{ if (window.opener) window.opener.postMessage("{event}", "{WEB_APP_URL}"); }} catch (e) {{}}
    setTimeout(function () {{ try {{ window.close(); }} catch (e) {{}} }}, 1200);
  </script>
</body></html>"""


@app.post("/api/contacts/upload")
async def upload_contacts(file: UploadFile = File(...)) -> dict:
    _require_owner_provisioning()
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

    voice_provider = os.getenv("VOICE_LLM_PROVIDER", "nemotron").strip().lower()
    checks["voice_provider"] = {
        "ok": voice_provider in ("nemotron", "openai-realtime"),
        "detail": voice_provider,
    }
    voice_checks = (
        [
            ("openai_realtime", "OPENAI_API_KEY"),
            ("gradium_tts", "GRADIUM_API_KEY"),
        ]
        if voice_provider == "openai-realtime"
        else [
            ("gradium", "GRADIUM_API_KEY"),
            ("nemotron_llm", "NEMOTRON_LLM_URL"),
            ("nemotron_stt", "NVIDIA_ASR_URL"),
        ]
    )

    for key, env_name in [
        *voice_checks,
        ("twilio", "TWILIO_ACCOUNT_SID"),
        ("gmail_smtp", "GMAIL_APP_PASSWORD"),
    ]:
        ok, detail = _env_ok(env_name)
        checks[key] = {"ok": ok, "detail": detail}

    cloned_voice = bool(os.getenv("GRADIUM_CLONED_VOICE_ID", "").strip())
    cloned_voice_consent = os.getenv("OWNER_VOICE_CONSENT", "").strip().lower() == "true"
    checks["gradium_voice"] = {
        "ok": cloned_voice and cloned_voice_consent,
        "detail": (
            "owner clone configured with consent"
            if cloned_voice and cloned_voice_consent
            else "using fallback Gradium voice"
        ),
    }

    owner_email = _owner_email_value()
    checks["owner_email"] = {
        "ok": bool(owner_email),
        "detail": "configured from onboarding" if owner_email else "add owner email in setup",
    }

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


# ─── Static frontend (single-origin deploy) ──────────────────────────────────
#
# In local dev the Vite server (port 5173) serves the UI and proxies /api here.
# For a public tunnel we instead serve the *built* frontend from this same
# process, so the site and API share one origin (and one tunnel). This block is
# registered LAST so every /api/* route above takes precedence; the catch-all
# only handles real frontend asset/route requests.

_DIST = SERVER_DIR.parent / "web" / "dist"

if _DIST.is_dir():
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    _assets = _DIST / "assets"
    if _assets.is_dir():
        app.mount("/assets", StaticFiles(directory=str(_assets)), name="assets")

    _DIST_ROOT = _DIST.resolve()

    @app.get("/{full_path:path}")
    def spa_fallback(full_path: str) -> FileResponse:
        """Serve a real file when it exists, else index.html (SPA routing).

        ``full_path`` is attacker-controlled and uvicorn does not normalize ``..``
        segments, so the resolved candidate must stay inside the built web root —
        otherwise this becomes an arbitrary file read (``.env``, token vaults).
        """
        if full_path:
            candidate = (_DIST / full_path).resolve()
            if candidate.is_file() and candidate.is_relative_to(_DIST_ROOT):
                return FileResponse(str(candidate))
        return FileResponse(str(_DIST / "index.html"))


if __name__ == "__main__":
    import uvicorn

    # Bind to 0.0.0.0 when a public base URL is configured (tunnel/deploy);
    # otherwise stay on localhost for local dev.
    host = "0.0.0.0" if os.getenv("PUBLIC_BASE_URL", "").strip() else "127.0.0.1"
    port = int(os.getenv("PORT", "8787"))
    uvicorn.run("onboarding_api:app", host=host, port=port, reload=False)
