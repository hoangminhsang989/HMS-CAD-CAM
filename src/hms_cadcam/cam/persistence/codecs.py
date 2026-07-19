"""Canonical JSON helpers for SQLite CAM payload columns."""

from __future__ import annotations

import json
from typing import Any

from hms_cadcam.cam.persistence.errors import CamPersistencePayloadError

MAX_CAM_JSON_BYTES = 16 * 1024 * 1024


def encode_json(payload: Any) -> str:
    """Encode deterministic finite JSON for one versioned CAM payload."""
    try:
        encoded = json.dumps(payload, allow_nan=False, ensure_ascii=False,
                             separators=(",", ":"), sort_keys=True)
    except (TypeError, ValueError) as error:
        raise CamPersistencePayloadError("CAM payload is not finite JSON") from error
    if len(encoded.encode("utf-8")) > MAX_CAM_JSON_BYTES:
        raise CamPersistencePayloadError("CAM payload exceeds the SQLite JSON limit")
    return encoded


def decode_json(value: object) -> dict[str, Any]:
    """Decode one bounded JSON object and reject non-object roots."""
    if not isinstance(value, str) or len(value.encode("utf-8")) > MAX_CAM_JSON_BYTES:
        raise CamPersistencePayloadError("CAM SQLite JSON value is invalid or oversized")
    try:
        payload = json.loads(value, parse_constant=lambda token: (_ for _ in ()).throw(ValueError(token)))
    except (json.JSONDecodeError, ValueError) as error:
        raise CamPersistencePayloadError("CAM SQLite JSON is malformed") from error
    if not isinstance(payload, dict):
        raise CamPersistencePayloadError("CAM SQLite JSON root must be an object")
    return payload
