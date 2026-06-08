"""Resolve tenant runtime records for inbound voice calls."""

from __future__ import annotations

import os
from html import escape
from typing import Any
from urllib.parse import urlparse

from loguru import logger

import tenant_store


def _first_str(params: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = params.get(key)
        if isinstance(value, list | tuple):
            value = value[-1] if value else ""
        value = str(value or "").strip()
        if value:
            return value
    return ""


def call_info_from_twilio_parameters(params: dict[str, Any] | None) -> dict[str, str]:
    """Normalize Twilio webhook/custom-parameter call metadata."""
    params = params or {}
    info = {
        "from_number": _first_str(params, "From", "from", "Caller", "caller"),
        "to_number": _first_str(params, "To", "to", "Called", "called"),
        "forwarded_from": _first_str(
            params,
            "ForwardedFrom",
            "forwarded_from",
            "CalledVia",
            "called_via",
        ),
    }
    return {key: value for key, value in info.items() if value}


def stream_proxy_host(public_base_url: str | None, request_host: str | None = None) -> str:
    """Return the hostname Twilio should use for the bidirectional media stream."""
    candidate = (public_base_url or "").strip().rstrip("/")
    if candidate:
        parsed = urlparse(candidate if "://" in candidate else f"https://{candidate}")
        if parsed.netloc:
            return parsed.netloc
        if parsed.path:
            return parsed.path
    return (request_host or "").strip().rstrip("/")


def build_twilio_stream_twiml(proxy_host: str, params: dict[str, Any] | None = None) -> str:
    """Build TwiML that forwards Twilio's first-webhook metadata into /ws."""
    host = stream_proxy_host(proxy_host)
    call_info = call_info_from_twilio_parameters(params)
    parameter_lines = [
        f'      <Parameter name="{escape(name)}" value="{escape(value)}"/>'
        for name, value in {
            "From": call_info.get("from_number", ""),
            "To": call_info.get("to_number", ""),
            "ForwardedFrom": call_info.get("forwarded_from", ""),
        }.items()
        if value
    ]
    parameter_xml = "\n".join(parameter_lines)
    if parameter_xml:
        stream_xml = f'    <Stream url="wss://{escape(host)}/ws">\n{parameter_xml}\n    </Stream>'
    else:
        stream_xml = f'    <Stream url="wss://{escape(host)}/ws"></Stream>'
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<Response>\n"
        "  <Connect>\n"
        f"{stream_xml}\n"
        "  </Connect>\n"
        '  <Pause length="40"/>\n'
        "</Response>"
    )


def resolve_for_call(
    to_number: str | None,
    *,
    forwarded_from: str | None = None,
) -> dict[str, Any]:
    """Resolve a tenant from a dedicated Twilio number or shared forwarded call."""
    if forwarded_from:
        runtime = tenant_store.get_agent_by_owner_phone(forwarded_from)
        if runtime and (
            not runtime.get("forwarding_phone")
            or not to_number
            or runtime.get("forwarding_phone") == to_number
        ):
            logger.info(
                "Tenant runtime: matched forwarded-from {} to tenant {}",
                forwarded_from,
                runtime.get("tenant_id"),
            )
            return runtime
    if not to_number:
        return {}
    runtime = tenant_store.get_agent_by_phone(to_number)
    if runtime:
        logger.info(
            "Tenant runtime: matched agent phone {} to tenant {}",
            to_number,
            runtime.get("tenant_id"),
        )
        return runtime
    if os.getenv("TENANT_RUNTIME_REQUIRE_MATCH", "").strip().lower() == "true":
        raise RuntimeError(f"No tenant activation record for called number {to_number}.")
    logger.warning("Tenant runtime: no match for {}; using legacy demo owner", to_number)
    return {}
