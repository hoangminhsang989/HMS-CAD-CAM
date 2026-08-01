"""Strict bounded canonical JSON codecs for Lathe persistence V1."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping

from hms_cadcam.cam.lathe.domain import (
    LatheOperationState,
    lathe_operation_from_canonical_mapping,
    lathe_operation_to_canonical_mapping,
)
from hms_cadcam.cam.lathe.lathe_post.basic_types import (
    BasicFinalSafeTool,
    BasicPostMetadata,
    BasicPostReadiness,
    BasicToolMapping,
)
from hms_cadcam.cam.lathe.persistence.models import (
    MAX_BASIC_NC_TEXT_BYTES,
    MAX_CONFORMANCE_FINDINGS,
    MAX_DERIVED_PAYLOAD_BYTES,
    MAX_GEOMETRY_REFERENCES,
    MAX_JSON_DEPTH,
    MAX_MOTIONS,
    MAX_OPERATION_PAYLOAD_BYTES,
    MAX_SEMANTIC_STRING,
    LatheDerivedKind,
    LathePostConfiguration,
)

_TRANSIENT_STATES = frozenset(
    {"ACTIVE", "RUNNING", "PENDING", "QUEUED", "COMPUTING", "CANCELLING"}
)


class LatheCodecError(ValueError):
    """Raised before any malformed or oversized Lathe payload is hydrated."""


def _pairs_without_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise LatheCodecError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _validate_string(value: str, *, allow_multiline: bool = False) -> None:
    if len(value) > MAX_SEMANTIC_STRING and not allow_multiline:
        raise LatheCodecError("Semantic string bound exceeded")
    for char in value:
        code = ord(char)
        if code == 127 or (code < 32 and not (allow_multiline and char in "\r\n")):
            raise LatheCodecError("Control character is not permitted")


def _validate_json_value(
    value: object,
    *,
    depth: int = 1,
    key_path: tuple[str, ...] = (),
) -> None:
    if depth > MAX_JSON_DEPTH:
        raise LatheCodecError("JSON nesting depth exceeded")
    if value is None or type(value) in {bool, int}:
        if type(value) is int and not -(2**63) <= value <= 2**63 - 1:
            raise LatheCodecError("JSON integer is outside SQLite bounds")
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise LatheCodecError("Non-finite JSON number is forbidden")
        return
    if isinstance(value, str):
        allow_multiline = bool(key_path and key_path[-1] in {"text", "nc_text", "listing"})
        _validate_string(value, allow_multiline=allow_multiline)
        if key_path and key_path[-1] == "nc_text" and len(
            value.encode("utf-8")
        ) > MAX_BASIC_NC_TEXT_BYTES:
            raise LatheCodecError("Basic NC text bound exceeded")
        return
    if isinstance(value, list):
        limit = MAX_MOTIONS if key_path and key_path[-1] in {"motions", "blocks"} else MAX_MOTIONS
        if len(value) > limit:
            raise LatheCodecError("JSON array bound exceeded")
        if key_path and key_path[-1] == "findings" and len(value) > MAX_CONFORMANCE_FINDINGS:
            raise LatheCodecError("Conformance finding bound exceeded")
        for item in value:
            _validate_json_value(item, depth=depth + 1, key_path=key_path)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise LatheCodecError("JSON object keys must be strings")
            _validate_string(key)
            _validate_json_value(
                item,
                depth=depth + 1,
                key_path=(*key_path, key),
            )
        return
    raise LatheCodecError("Unsupported JSON value type")


def canonical_json_dumps(value: object, *, max_bytes: int) -> str:
    """Encode validated JSON with stable UTF-8 canonical formatting."""

    _validate_json_value(value)
    try:
        encoded = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as error:
        raise LatheCodecError("Payload is not canonical JSON data") from error
    if len(encoded.encode("utf-8")) > max_bytes:
        raise LatheCodecError("Canonical JSON payload bound exceeded")
    return encoded


def strict_json_loads(
    payload: str,
    *,
    max_bytes: int,
    require_object: bool = True,
) -> object:
    """Decode canonical JSON while rejecting duplicates and non-canonical bytes."""

    if not isinstance(payload, str) or len(payload.encode("utf-8")) > max_bytes:
        raise LatheCodecError("JSON payload is not bounded text")
    try:
        value = json.loads(
            payload,
            object_pairs_hook=_pairs_without_duplicates,
            parse_constant=lambda token: (_ for _ in ()).throw(
                LatheCodecError(f"Non-finite JSON token: {token}")
            ),
        )
    except (json.JSONDecodeError, UnicodeError, TypeError, ValueError) as error:
        if isinstance(error, LatheCodecError):
            raise
        raise LatheCodecError("Malformed JSON payload") from error
    if require_object and not isinstance(value, dict):
        raise LatheCodecError("JSON root must be an object")
    _validate_json_value(value)
    if canonical_json_dumps(value, max_bytes=max_bytes) != payload:
        raise LatheCodecError("JSON payload is not in canonical form")
    return value


def encode_operation(operation: LatheOperationState) -> str:
    """Encode the existing strict foundation snapshot contract."""

    if operation.geometry_binding is not None and len(
        operation.geometry_binding.entity_ids
    ) > MAX_GEOMETRY_REFERENCES:
        raise LatheCodecError("Lathe geometry reference bound exceeded")
    return canonical_json_dumps(
        lathe_operation_to_canonical_mapping(operation),
        max_bytes=MAX_OPERATION_PAYLOAD_BYTES,
    )


def decode_operation(payload: str) -> LatheOperationState:
    value = strict_json_loads(payload, max_bytes=MAX_OPERATION_PAYLOAD_BYTES)
    assert isinstance(value, dict)
    try:
        operation = lathe_operation_from_canonical_mapping(value)
    except (TypeError, ValueError) as error:
        raise LatheCodecError("Invalid Lathe operation payload") from error
    if operation.geometry_binding is not None and len(
        operation.geometry_binding.entity_ids
    ) > MAX_GEOMETRY_REFERENCES:
        raise LatheCodecError("Lathe geometry reference bound exceeded")
    return operation


def encode_post_configuration(config: LathePostConfiguration) -> str:
    if not isinstance(config, LathePostConfiguration):
        raise TypeError("config must be LathePostConfiguration")
    payload = {
        "schema_version": 1,
        "final_safe_tool": {
            "offset_number": config.final_safe_tool.offset_number,
            "tool_number": config.final_safe_tool.tool_number,
        },
        "metadata": {
            "file_stem": config.metadata.file_stem,
            "tool_descriptions": [
                {"description": description, "tool_id": tool_id}
                for tool_id, description in config.metadata.tool_descriptions
            ],
        },
        "tool_mappings": [
            {
                "description": item.description,
                "enabled": item.enabled,
                "geometry_offset_number": item.geometry_offset_number,
                "tool_id": item.tool_id,
                "tool_number": item.tool_number,
                "wear_offset_number": item.wear_offset_number,
            }
            for item in config.tool_mappings
        ],
    }
    return canonical_json_dumps(payload, max_bytes=MAX_OPERATION_PAYLOAD_BYTES)


def _exact_mapping(value: object, fields: set[str], subject: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise LatheCodecError(f"{subject} fields are malformed")
    return value


def decode_post_configuration(payload: str) -> LathePostConfiguration:
    value = strict_json_loads(payload, max_bytes=MAX_OPERATION_PAYLOAD_BYTES)
    root = _exact_mapping(
        value,
        {"schema_version", "final_safe_tool", "metadata", "tool_mappings"},
        "Post configuration",
    )
    if root["schema_version"] != 1:
        raise LatheCodecError("Unsupported Post configuration schema")
    safe = _exact_mapping(
        root["final_safe_tool"], {"tool_number", "offset_number"}, "safe tool"
    )
    metadata = _exact_mapping(
        root["metadata"], {"file_stem", "tool_descriptions"}, "Post metadata"
    )
    raw_descriptions = metadata["tool_descriptions"]
    raw_mappings = root["tool_mappings"]
    if not isinstance(raw_descriptions, list) or not isinstance(raw_mappings, list):
        raise LatheCodecError("Post mapping collections are malformed")
    descriptions: list[tuple[str, str]] = []
    mappings: list[BasicToolMapping] = []
    try:
        for raw in raw_descriptions:
            item = _exact_mapping(raw, {"tool_id", "description"}, "tool description")
            descriptions.append((item["tool_id"], item["description"]))  # type: ignore[arg-type]
        for raw in raw_mappings:
            item = _exact_mapping(
                raw,
                {
                    "tool_id",
                    "tool_number",
                    "geometry_offset_number",
                    "wear_offset_number",
                    "enabled",
                    "description",
                },
                "tool mapping",
            )
            mappings.append(
                BasicToolMapping(
                    item["tool_id"],  # type: ignore[arg-type]
                    item["tool_number"],  # type: ignore[arg-type]
                    item["geometry_offset_number"],  # type: ignore[arg-type]
                    item["wear_offset_number"],  # type: ignore[arg-type]
                    item["enabled"],  # type: ignore[arg-type]
                    item["description"],  # type: ignore[arg-type]
                )
            )
        return LathePostConfiguration(
            BasicFinalSafeTool(
                safe["tool_number"],  # type: ignore[arg-type]
                safe["offset_number"],  # type: ignore[arg-type]
            ),
            BasicPostMetadata(
                metadata["file_stem"],  # type: ignore[arg-type]
                tuple(descriptions),
            ),
            tuple(mappings),
        )
    except (TypeError, ValueError) as error:
        raise LatheCodecError("Invalid typed Post configuration") from error


def encode_derived_payload(kind: LatheDerivedKind, payload: Mapping[str, object]) -> str:
    if not isinstance(kind, LatheDerivedKind) or not isinstance(payload, Mapping):
        raise TypeError("Derived kind and payload are invalid")
    normalized = dict(payload)
    _validate_derived_semantics(kind, normalized)
    return canonical_json_dumps(normalized, max_bytes=MAX_DERIVED_PAYLOAD_BYTES)


def decode_derived_payload(kind: LatheDerivedKind, payload: str) -> dict[str, object]:
    value = strict_json_loads(payload, max_bytes=MAX_DERIVED_PAYLOAD_BYTES)
    assert isinstance(value, dict)
    _validate_derived_semantics(kind, value)
    return value


def _validate_derived_semantics(
    kind: LatheDerivedKind, payload: Mapping[str, object]
) -> None:
    if any(
        isinstance(value, str) and value.upper() in _TRANSIENT_STATES
        for value in _walk_values(payload)
    ):
        raise LatheCodecError("Transient runtime state cannot be persisted")
    if kind is LatheDerivedKind.ACCEPTED_TOOLPATH:
        if payload.get("status") != "SUCCESS" or payload.get("stable") is not True:
            raise LatheCodecError("Only stable successful toolpaths may be persisted")
        motions = payload.get("motions")
        if not isinstance(motions, list) or len(motions) > MAX_MOTIONS:
            raise LatheCodecError("Accepted toolpath motions are malformed")
    elif kind is LatheDerivedKind.ACCEPTED_PROGRAM_IR:
        if payload.get("complete") is not True or not isinstance(
            payload.get("blocks"), list
        ):
            raise LatheCodecError("Program IR persistence is all-or-nothing")
    elif kind is LatheDerivedKind.NEUTRAL_LISTING:
        if not isinstance(payload.get("text"), str):
            raise LatheCodecError("Neutral listing text is missing")
    elif kind is LatheDerivedKind.BASIC_NC_PREVIEW:
        if (
            payload.get("readiness")
            != BasicPostReadiness.BASIC_NC_PREVIEW_READY_UNVERIFIED.value
            or not isinstance(payload.get("nc_text"), str)
        ):
            raise LatheCodecError("Basic NC may restore only as unverified preview")
        if len(payload["nc_text"].encode("utf-8")) > MAX_BASIC_NC_TEXT_BYTES:  # type: ignore[union-attr]
            raise LatheCodecError("Basic NC text bound exceeded")
    elif kind is LatheDerivedKind.CONFORMANCE_REVIEW:
        findings = payload.get("findings")
        if not isinstance(findings, list) or len(findings) > MAX_CONFORMANCE_FINDINGS:
            raise LatheCodecError("Conformance findings are malformed")


def _walk_values(value: object):  # type: ignore[no-untyped-def]
    if isinstance(value, Mapping):
        for item in value.values():
            yield from _walk_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_values(item)
    else:
        yield value


def payload_sha256(payload: str) -> str:
    if not isinstance(payload, str):
        raise TypeError("payload must be text")
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


__all__ = [
    "LatheCodecError",
    "canonical_json_dumps",
    "decode_derived_payload",
    "decode_operation",
    "decode_post_configuration",
    "encode_derived_payload",
    "encode_operation",
    "encode_post_configuration",
    "payload_sha256",
    "strict_json_loads",
]
