"""Cloud storage for the owner's cleaned persona context.

The voice agent uses one overwriteable record rather than appending persona
versions to the voicemail history. DynamoDB is preferred because the hackathon
stack already provisions that table; S3 is an optional fallback.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from loguru import logger

_ENV_DIR = Path(__file__).resolve().parent
load_dotenv(_ENV_DIR / ".env", override=True)
load_dotenv(_ENV_DIR / ".env.local", override=True)

TYPE_PERSONA_CONTEXT = "persona_context"


def _enabled(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def cloud_reads_enabled() -> bool:
    """Whether the bot should try the cloud before its local fallback."""
    return _enabled("PERSONA_CLOUD_ENABLED", True)


def cloud_required() -> bool:
    """Whether a missing cloud persona must disable the local fallback."""
    return _enabled("PERSONA_CLOUD_REQUIRED", False)


def _owner_key() -> str:
    explicit = os.getenv("PERSONA_CLOUD_RECORD_ID", "").strip()
    if explicit:
        return explicit
    owner = (
        os.getenv("OWNER_CONTEXT_TAG")
        or os.getenv("OWNER_EMAIL")
        or "owner"
    )
    digest = hashlib.sha256(owner.encode("utf-8")).hexdigest()[:16]
    return f"persona-context:{digest}"


def _table_name() -> str:
    return (
        os.getenv("PERSONA_CLOUD_DDB_TABLE")
        or os.getenv("PERSIST_DDB_TABLE")
        or "ff-voicemails"
    )


def _s3_bucket() -> str:
    return os.getenv("PERSONA_CLOUD_S3_BUCKET") or os.getenv("PERSIST_S3_BUCKET", "")


def _s3_key() -> str:
    prefix = os.getenv("PERSONA_CLOUD_S3_PREFIX", "personas/").strip("/")
    return f"{prefix}/{_owner_key()}.json"


def _session() -> Any | None:
    try:
        import boto3

        session = boto3.Session()
        return session if session.get_credentials() else None
    except Exception as exc:
        logger.warning(f"persona cloud: AWS session unavailable ({type(exc).__name__})")
        return None


def _record(content: str) -> dict[str, Any]:
    return {
        "record_id": _owner_key(),
        "type": TYPE_PERSONA_CONTEXT,
        "timestamp": datetime.now(UTC).isoformat(),
        "format": "cleaned_markdown",
        "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "content": content,
    }


def publish_persona(content: str) -> str | None:
    """Upload cleaned persona text. Returns the remote location, or ``None``."""
    if not content.strip():
        return None
    session = _session()
    if session is None:
        logger.warning("persona cloud: no AWS credentials; upload skipped")
        return None

    record = _record(content)
    try:
        table = session.resource("dynamodb").Table(_table_name())
        table.put_item(Item=record)
        where = f"dynamodb:{_table_name()}/{record['record_id']}"
        logger.info(f"persona cloud: published {record['content_sha256'][:12]} -> {where}")
        return where
    except Exception as exc:
        logger.warning(f"persona cloud: DynamoDB upload failed ({type(exc).__name__})")

    bucket = _s3_bucket()
    if not bucket:
        return None
    try:
        key = _s3_key()
        session.client("s3").put_object(
            Bucket=bucket,
            Key=key,
            Body=json.dumps(record).encode("utf-8"),
            ContentType="application/json",
        )
        where = f"s3:{bucket}/{key}"
        logger.info(f"persona cloud: published {record['content_sha256'][:12]} -> {where}")
        return where
    except Exception as exc:
        logger.warning(f"persona cloud: S3 upload failed ({type(exc).__name__})")
        return None


def fetch_persona() -> str | None:
    """Fetch cleaned persona text from DynamoDB or S3, returning ``None`` on failure."""
    if not cloud_reads_enabled():
        return None
    session = _session()
    if session is None:
        return None

    table = session.resource("dynamodb").Table(_table_name())
    try:
        response = table.get_item(Key={"record_id": _owner_key()})
        item = response.get("Item") or {}
        content = item.get("content")
        if item.get("type") == TYPE_PERSONA_CONTEXT and isinstance(content, str) and content.strip():
            logger.info("persona cloud: loaded persona from DynamoDB")
            return content
    except Exception as exc:
        logger.warning(
            f"persona cloud: DynamoDB GetItem failed ({type(exc).__name__}); trying Scan"
        )
        try:
            from boto3.dynamodb.conditions import Attr

            response = table.scan(FilterExpression=Attr("record_id").eq(_owner_key()))
            while True:
                for item in response.get("Items", []):
                    content = item.get("content")
                    if (
                        item.get("type") == TYPE_PERSONA_CONTEXT
                        and isinstance(content, str)
                        and content.strip()
                    ):
                        logger.info("persona cloud: loaded persona from DynamoDB Scan fallback")
                        return content
                last_key = response.get("LastEvaluatedKey")
                if not last_key:
                    break
                response = table.scan(
                    FilterExpression=Attr("record_id").eq(_owner_key()),
                    ExclusiveStartKey=last_key,
                )
        except Exception as scan_exc:
            logger.warning(
                f"persona cloud: DynamoDB Scan failed ({type(scan_exc).__name__})"
            )

    bucket = _s3_bucket()
    if not bucket:
        return None
    try:
        body = session.client("s3").get_object(Bucket=bucket, Key=_s3_key())["Body"].read()
        item = json.loads(body)
        content = item.get("content")
        if item.get("type") == TYPE_PERSONA_CONTEXT and isinstance(content, str) and content.strip():
            logger.info("persona cloud: loaded persona from S3")
            return content
    except Exception as exc:
        logger.warning(f"persona cloud: S3 read failed ({type(exc).__name__})")
    return None
