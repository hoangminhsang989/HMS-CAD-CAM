"""Manifest-authoritative, fail-closed loader for Stage 13B numerical data."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import math
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

MAX_MODEL_BYTES = 65_536


class ModelLoadError(ValueError):
    """Structured model loading failure; the text is the stable failure code."""


def _fail(code: str) -> None:
    raise ModelLoadError(code)


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("MODEL_DUPLICATE_KEY")
        result[key] = value
    return result


def _parse(raw: bytes, invalid: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                           parse_constant=lambda _: _fail("MODEL_NON_FINITE"))
    except ModelLoadError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        _fail(invalid)
    if not isinstance(value, dict):
        _fail(invalid)
    return value


def _finite(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def _range(value: Any) -> tuple[float, float, float]:
    if not isinstance(value, Mapping) or set(value) != {"minimum", "nominal", "maximum"}:
        _fail("MODEL_SCHEMA_INVALID")
    values = tuple(value[name] for name in ("minimum", "nominal", "maximum"))
    if not all(_finite(number) and number > 0 for number in values):
        _fail("MODEL_NON_FINITE" if not all(_finite(number) for number in values) else "MODEL_RANGE_INVALID")
    minimum, nominal, maximum = (float(number) for number in values)
    if not minimum <= nominal <= maximum:
        _fail("MODEL_RANGE_INVALID")
    return minimum, nominal, maximum


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


@dataclass(frozen=True, slots=True)
class CuttingModel:
    model_id: str
    model_version: str
    sha256: str
    byte_size: int
    data: Mapping[str, Any]


def load_canonical_model(manifest_path: Path) -> CuttingModel:
    """Load exactly the model named by *manifest_path*, with no bypass route."""
    manifest_path = Path(manifest_path)
    try:
        manifest_raw = manifest_path.read_bytes()
    except OSError:
        _fail("MODEL_MANIFEST_MISSING")
    manifest = _parse(manifest_raw, "MODEL_MANIFEST_INVALID")
    if manifest.get("manifest_schema_version") != 1:
        _fail("MODEL_MANIFEST_UNSUPPORTED_VERSION")
    required = {"manifest_schema_version", "model_id", "model_version", "relative_model_path", "byte_size", "sha256", "maximum_bytes", "units", "domain_version", "worker_protocol", "role"}
    if set(manifest) != required or manifest["units"] != "metric" or manifest["role"] != "immutable_offline_numerical_model":
        _fail("MODEL_MANIFEST_INVALID")
    size, maximum = manifest["byte_size"], manifest["maximum_bytes"]
    digest, relative = manifest["sha256"], manifest["relative_model_path"]
    if type(size) is not int or type(maximum) is not int or not 0 < size <= maximum <= MAX_MODEL_BYTES or not isinstance(digest, str) or len(digest) != 64:
        _fail("MODEL_MANIFEST_INVALID")
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
        _fail("MODEL_PATH_INVALID")
    root = manifest_path.parent.resolve()
    candidate = manifest_path.parent / relative
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError):
        _fail("MODEL_PATH_ESCAPE")
    try:
        raw = resolved.read_bytes()
    except OSError:
        _fail("MODEL_MANIFEST_MISSING")
    if len(raw) > maximum:
        _fail("MODEL_TOO_LARGE")
    if len(raw) != size:
        _fail("MODEL_SIZE_MISMATCH")
    actual = sha256(raw).hexdigest()
    if actual != digest:
        _fail("MODEL_CHECKSUM_INVALID")
    data = _parse(raw, "MODEL_SCHEMA_INVALID")
    _validate_model(data, manifest)
    return CuttingModel(str(data["model_id"]), str(data["model_version"]), actual, len(raw), _freeze(data))


def _validate_model(data: dict[str, Any], manifest: Mapping[str, Any]) -> None:
    required = {"schema_version", "model_id", "model_version", "units", "materials", "tool_materials", "profiles", "rigidity", "families"}
    if set(data) != required or data.get("schema_version") != 1 or data.get("units") != "metric":
        _fail("MODEL_SCHEMA_INVALID")
    if data["model_id"] != manifest["model_id"] or data["model_version"] != manifest["model_version"]:
        _fail("MODEL_SCHEMA_INVALID")
    for name in ("materials", "tool_materials", "profiles", "rigidity", "families"):
        if not isinstance(data[name], dict) or not data[name]:
            _fail("MODEL_SCHEMA_INVALID")
    if set(data["materials"]) != {"ISO_P", "ISO_M", "ISO_K", "ISO_N", "ISO_S", "ISO_H"} or set(data["tool_materials"]) != {"HSS", "CARBIDE"} or set(data["profiles"]) != {"CONSERVATIVE", "BALANCED", "PRODUCTIVE"}:
        _fail("MODEL_SCHEMA_INVALID")
    for factor in (*data["materials"].values(), *data["tool_materials"].values(), *data["profiles"].values(), *data["rigidity"].values()):
        if not _finite(factor):
            _fail("MODEL_NON_FINITE")
        if factor <= 0:
            _fail("MODEL_RANGE_INVALID")
    for family, fields in {"milling": {"cutting_speed", "feed_per_tooth", "plunge_factor", "axial_depth_factor", "radial_engagement_factor"}, "drilling": {"cutting_speed", "feed_per_revolution", "peck_depth_factor"}, "turning": {"cutting_speed", "feed_per_revolution", "depth_of_cut_factor"}}.items():
        entry = data["families"].get(family)
        if not isinstance(entry, dict) or set(entry) != fields:
            _fail("MODEL_SCHEMA_INVALID")
        for value in entry.values():
            _range(value)


__all__ = ["CuttingModel", "MAX_MODEL_BYTES", "ModelLoadError", "load_canonical_model"]
