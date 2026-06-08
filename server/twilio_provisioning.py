"""Point a Twilio number's voice webhook at the public bot endpoint.

When a new tenant is assigned an ``agent_phone`` (their Twilio number), its
Voice webhook must point at the public URL that fronts the bot (the ngrok
tunnel / ``PUBLIC_BASE_URL``), exactly like the demo number already does — that
is how inbound calls reach the agent. This module does that wiring via the
Twilio REST API so onboarding a new number auto-connects it.

Pure ``requests`` helper (no Pipecat / FastAPI deps) so it is unit-testable and
degrades gracefully: missing creds or public URL → logged no-op, never raises
into the onboarding request.
"""

from __future__ import annotations

import os

import requests
from loguru import logger

_TIMEOUT = 12
_TWILIO_API_ROOT = "https://api.twilio.com/2010-04-01"


def _public_base_url() -> str:
    """The public origin that serves the Twilio voice webhook (ngrok / deploy)."""
    return os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")


def _twilio_auth() -> tuple[str, str] | None:
    account_sid = os.getenv("TWILIO_ACCOUNT_SID", "").strip()
    auth_token = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
    if not (account_sid and auth_token):
        return None
    return account_sid, auth_token


def _incoming_numbers_base(account_sid: str) -> str:
    return f"{_TWILIO_API_ROOT}/Accounts/{account_sid}/IncomingPhoneNumbers"


def voice_webhook_url(public_base_url: str | None = None) -> str:
    """Full Voice webhook URL for the bot. Defaults to ``<PUBLIC_BASE_URL><path>``.

    The path matches the existing demo number's webhook (root ``/``); override
    with ``TWILIO_VOICE_PATH`` if the TwiML is served elsewhere.
    """
    base = (public_base_url or _public_base_url()).rstrip("/")
    path = os.getenv("TWILIO_VOICE_PATH", "/").strip()
    if not path.startswith("/"):
        path = "/" + path
    return f"{base}{path}" if base else ""


def wire_inbound_webhook(
    agent_phone: str,
    public_base_url: str | None = None,
) -> dict:
    """Set ``agent_phone``'s Twilio Voice webhook to the public bot URL.

    Returns ``{ok, detail, voice_url?}``. Never raises — onboarding should not
    fail just because webhook wiring couldn't complete; the result is surfaced
    so the caller can warn the operator.
    """
    phone = (agent_phone or "").strip()
    auth_pair = _twilio_auth()
    if not auth_pair:
        return {"ok": False, "detail": "Twilio credentials not configured."}
    account_sid, auth_token = auth_pair
    if not phone:
        return {"ok": False, "detail": "No agent phone number to wire."}
    url = voice_webhook_url(public_base_url)
    if not url:
        return {
            "ok": False,
            "detail": "PUBLIC_BASE_URL not set — cannot derive a public webhook URL.",
        }
    auth = (account_sid, auth_token)
    base = _incoming_numbers_base(account_sid)
    try:
        # Resolve the number's SID.
        lookup = requests.get(
            f"{base}.json",
            params={"PhoneNumber": phone},
            auth=auth,
            timeout=_TIMEOUT,
        )
        if not lookup.ok:
            return {"ok": False, "detail": f"Twilio lookup failed ({lookup.status_code})."}
        numbers = lookup.json().get("incoming_phone_numbers", [])
        if not numbers:
            return {
                "ok": False,
                "detail": f"{phone} is not an IncomingPhoneNumber on this Twilio account.",
            }
        sid = numbers[0].get("sid")
        # Point its Voice webhook at the public bot URL.
        update = requests.post(
            f"{base}/{sid}.json",
            data={"VoiceUrl": url, "VoiceMethod": "POST"},
            auth=auth,
            timeout=_TIMEOUT,
        )
        if not update.ok:
            return {"ok": False, "detail": f"Twilio update failed ({update.status_code})."}
        logger.info(f"twilio_provisioning: wired {phone} voice webhook -> {url}")
        return {"ok": True, "voice_url": url, "detail": f"Voice webhook set to {url}"}
    except requests.RequestException as exc:
        logger.warning(f"twilio_provisioning: webhook wiring failed ({type(exc).__name__})")
        return {"ok": False, "detail": f"Twilio request error ({type(exc).__name__})."}


def wire_configured_demo_number(public_base_url: str | None = None) -> dict:
    """Wire the configured demo Twilio line to the current public voice worker."""
    phone = os.getenv("TWILIO_PHONE_NUMBER", "").strip()
    if not phone:
        return {"ok": False, "detail": "TWILIO_PHONE_NUMBER not configured."}
    return wire_inbound_webhook(phone, public_base_url)


def available_local_numbers(
    *,
    country: str = "US",
    area_code: str | None = None,
    contains: str | None = None,
    near_number: str | None = None,
    limit: int = 1,
    sms_enabled: bool = True,
    voice_enabled: bool = True,
) -> dict:
    """Search Twilio inventory for local numbers that are safe to auto-provision."""
    auth_pair = _twilio_auth()
    if not auth_pair:
        return {"ok": False, "detail": "Twilio credentials not configured.", "numbers": []}
    account_sid, auth_token = auth_pair
    country = (country or "US").strip().upper()
    if not country:
        return {"ok": False, "detail": "Country is required.", "numbers": []}

    params: dict[str, str] = {"PageSize": str(max(1, min(limit, 20)))}
    if area_code:
        params["AreaCode"] = str(area_code).strip()
    if contains:
        params["Contains"] = str(contains).strip()
    if near_number:
        params["NearNumber"] = str(near_number).strip()
    if sms_enabled:
        params["SmsEnabled"] = "true"
    if voice_enabled:
        params["VoiceEnabled"] = "true"

    url = f"{_TWILIO_API_ROOT}/Accounts/{account_sid}/AvailablePhoneNumbers/{country}/Local.json"
    try:
        response = requests.get(
            url,
            params=params,
            auth=(account_sid, auth_token),
            timeout=_TIMEOUT,
        )
        if not response.ok:
            return {
                "ok": False,
                "detail": f"Twilio number search failed ({response.status_code}).",
                "numbers": [],
            }
        numbers = response.json().get("available_phone_numbers", [])
        return {"ok": True, "detail": f"Found {len(numbers)} candidate number(s).", "numbers": numbers}
    except requests.RequestException as exc:
        logger.warning(f"twilio_provisioning: number search failed ({type(exc).__name__})")
        return {
            "ok": False,
            "detail": f"Twilio number search error ({type(exc).__name__}).",
            "numbers": [],
        }


def _auto_purchase_allows_address_required() -> bool:
    return os.getenv("TWILIO_ALLOW_ADDRESS_REQUIRED_PURCHASE", "").strip().lower() == "true"


def _voice_capable(candidate: dict) -> bool:
    return bool((candidate.get("capabilities") or {}).get("voice"))


def _candidate_phone(candidate: dict) -> str:
    return str(candidate.get("phone_number") or candidate.get("phoneNumber") or "").strip()


def _candidate_requires_address(candidate: dict) -> bool:
    return str(candidate.get("address_requirements") or candidate.get("addressRequirements") or "none").lower() != "none"


def purchase_phone_number(
    phone_number: str,
    *,
    public_base_url: str | None = None,
    friendly_name: str | None = None,
) -> dict:
    """Purchase one known-available Twilio number and point Voice at this bot."""
    auth_pair = _twilio_auth()
    if not auth_pair:
        return {"ok": False, "detail": "Twilio credentials not configured."}
    account_sid, auth_token = auth_pair
    phone = (phone_number or "").strip()
    if not phone:
        return {"ok": False, "detail": "No phone number selected for purchase."}
    url = voice_webhook_url(public_base_url)
    if not url:
        return {
            "ok": False,
            "detail": "PUBLIC_BASE_URL not set — cannot derive a public webhook URL.",
        }

    data = {
        "PhoneNumber": phone,
        "VoiceUrl": url,
        "VoiceMethod": "POST",
    }
    if friendly_name:
        data["FriendlyName"] = " ".join(friendly_name.split()).strip()[:64]

    try:
        response = requests.post(
            f"{_incoming_numbers_base(account_sid)}.json",
            data=data,
            auth=(account_sid, auth_token),
            timeout=_TIMEOUT,
        )
        if not response.ok:
            return {"ok": False, "detail": f"Twilio purchase failed ({response.status_code})."}
        body = response.json()
        purchased = str(body.get("phone_number") or phone).strip()
        return {
            "ok": True,
            "agent_phone": purchased,
            "sid": body.get("sid"),
            "voice_url": url,
            "detail": f"Purchased {purchased} and set Voice webhook to {url}",
        }
    except requests.RequestException as exc:
        logger.warning(f"twilio_provisioning: number purchase failed ({type(exc).__name__})")
        return {"ok": False, "detail": f"Twilio purchase error ({type(exc).__name__})."}


def purchase_and_wire_local_number(
    *,
    country: str = "US",
    area_code: str | None = None,
    contains: str | None = None,
    near_number: str | None = None,
    public_base_url: str | None = None,
    friendly_name: str | None = None,
) -> dict:
    """Find, buy, and Voice-wire one local Twilio number.

    This performs a paid external operation. Callers must guard it behind an
    explicit feature flag before invoking this helper.
    """
    search = available_local_numbers(
        country=country,
        area_code=area_code,
        contains=contains,
        near_number=near_number,
        limit=5,
        sms_enabled=True,
        voice_enabled=True,
    )
    if not search.get("ok"):
        return search
    candidates = search.get("numbers") or []
    skipped_address_required = 0
    for candidate in candidates:
        phone = _candidate_phone(candidate)
        if not phone or not _voice_capable(candidate):
            continue
        if _candidate_requires_address(candidate) and not _auto_purchase_allows_address_required():
            skipped_address_required += 1
            continue
        result = purchase_phone_number(
            phone,
            public_base_url=public_base_url,
            friendly_name=friendly_name,
        )
        if result.get("ok"):
            result["candidate"] = {
                "phone_number": phone,
                "locality": candidate.get("locality"),
                "region": candidate.get("region"),
            }
        return result
    detail = "No auto-purchasable voice-capable local numbers were returned."
    if skipped_address_required:
        detail += " Some candidates required an address."
    return {"ok": False, "detail": detail}
