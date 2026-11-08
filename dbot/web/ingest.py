"""``POST /ingest/{source}``: HMAC-signed alert webhook. No proxy auth; the signature is the authentication."""

import hashlib
import hmac
import json
import logging
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse

from dbot.control.secrets import SecretError, resolve_value
from dbot.web.state import WebState, state

MAX_BODY = 1024 * 1024
MAX_OBSERVABLES = 100
SIGNATURE_HEADER = "x-dbot-signature"

log = logging.getLogger(__name__)


class Observable(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["ip", "domain", "url", "hash", "email", "user", "host"]
    value: str = Field(min_length=1, max_length=2048)


class Alert(BaseModel):
    """C8 alert schema. ``source`` in the body is informational; the URL's source is authoritative."""

    model_config = ConfigDict(coerce_numbers_to_str=True)
    id: str = Field(min_length=1, max_length=256)
    source: str | None = Field(default=None, max_length=256)
    severity: str = Field(default="unknown", max_length=64)
    title: str = Field(default="", max_length=1024)
    observables: list[Observable] = Field(default_factory=list, max_length=MAX_OBSERVABLES)
    raw: Any = None


class _BodyTooLargeError(Exception):
    pass


async def _read_body(request: Request) -> bytes:
    declared = request.headers.get("content-length")
    if declared is not None and (not declared.isdigit() or int(declared) > MAX_BODY):
        raise _BodyTooLargeError
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_BODY:
            raise _BodyTooLargeError
    return bytes(body)


def _signature_ok(secret: str, body: bytes, header: str) -> bool:
    scheme, _, given = header.partition("=")
    if scheme.strip().lower() != "sha256" or not given:
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected.encode("ascii"), given.strip().lower().encode("utf-8"))


async def _audit(web: WebState, result: str, source: str, request: Request, **fields: Any) -> None:
    """Count and audit. Only for accepted, duplicate, and bad-signature requests to a known source."""
    web.ingest_counts[result] += 1
    event = {
        "type": "ingest",
        "result": result,
        "source": source,
        "peer": request.client.host if request.client else None,
        **fields,
    }
    await run_in_threadpool(web.audit.append, event)


def _reject(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


async def ingest(request: Request) -> JSONResponse:
    web = state(request)
    source = request.path_params["source"]
    src_cfg = web.config.ingest.sources.get(source)
    if src_cfg is None:
        # Unauthenticated noise: counted, never audited, so nobody can grow the audit log for free.
        web.ingest_counts["rejected"] += 1
        return _reject(404, "unknown source")
    try:
        body = await _read_body(request)
    except _BodyTooLargeError:
        web.ingest_counts["rejected"] += 1
        return _reject(413, "body too large")
    try:
        secret = resolve_value(src_cfg.hmac_secret)
    except SecretError as e:
        # The message names the reference, never a value.
        log.error("ingest source %r: secret unavailable: %s", source, e)
        web.ingest_counts["rejected"] += 1
        return _reject(503, "ingest secret unavailable")
    if not _signature_ok(secret, body, request.headers.get(SIGNATURE_HEADER, "")):
        await _audit(web, "rejected", source, request, reason="bad signature")
        return _reject(401, "bad signature")
    try:
        alert = Alert.model_validate(json.loads(body))
    except (ValueError, ValidationError) as e:
        # Only field locations, never the submitted values.
        where = [".".join(str(p) for p in err["loc"]) for err in e.errors()] if isinstance(e, ValidationError) else []
        web.ingest_counts["rejected"] += 1
        return _reject(422, "invalid alert" + (f": {', '.join(where)}" if where else ": body is not JSON"))
    alert_id = await run_in_threadpool(
        web.store.enqueue,
        source,
        alert.id,
        alert.severity,
        alert.title,
        [o.model_dump() for o in alert.observables],
        alert.raw,
    )
    if alert_id is None:
        await _audit(web, "duplicate", source, request, ext_id=alert.id)
        return JSONResponse({"duplicate": True}, status_code=200)
    await _audit(web, "accepted", source, request, ext_id=alert.id, alert_id=alert_id)
    return JSONResponse({"alert_id": alert_id}, status_code=202)
