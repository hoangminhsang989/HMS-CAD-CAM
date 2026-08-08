"""Typed, backend-honest contracts for versioned CAD export."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from hms_cadcam.cad.models import CadDocumentId, CadObjectId


EXPORT_PROFILE_FORMAT = "HMS_CAD_EXPORT_PROFILE"
EXPORT_PROFILE_SCHEMA_VERSION = 1
EXPORT_PROFILE_VERSION = EXPORT_PROFILE_SCHEMA_VERSION


class ExportFormatId(StrEnum):
    """Stable identifiers used by profiles, routing, and adapters."""

    STEP = "step"
    IGES = "iges"
    STL = "stl"
    BREP = "brep"
    PARASOLID = "parasolid"
    ACIS = "acis"
    DWG = "dwg"
    DXF = "dxf"


class ExportCapabilityClass(StrEnum):
    """Audit classification; availability is always stated separately."""

    NATIVE_SUPPORTED_NOW = "NATIVE_SUPPORTED_NOW"
    ARCHITECTURE_SUPPORTED_BUT_BACKEND_UNAVAILABLE = (
        "ARCHITECTURE_SUPPORTED_BUT_BACKEND_UNAVAILABLE"
    )
    DECLARED_UI_ONLY = "DECLARED_UI_ONLY"
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"


class ExportEntityKind(StrEnum):
    DOCUMENT = "document"
    SOLID = "solid"
    FACE = "face"
    WIRE = "wire"
    EDGE = "edge"
    MESH = "mesh"


class ExportUnitPolicy(StrEnum):
    """Unit behavior supported without changing source geometry."""

    MODEL_UNITS = "model_units"


class ExportOverwritePolicy(StrEnum):
    """Publication behavior serialized as part of the export profile contract."""

    FAIL_IF_EXISTS = "fail_if_exists"
    REPLACE_EXISTING = "replace_existing"


class StlEncoding(StrEnum):
    BINARY = "binary"
    ASCII = "ascii"


@dataclass(frozen=True, slots=True)
class ExportCapability:
    format_id: ExportFormatId
    extensions: tuple[str, ...]
    label: str
    classification: ExportCapabilityClass
    available: bool
    backend: str | None
    entity_kinds: frozenset[ExportEntityKind]
    standards: tuple[str, ...] = ()
    unavailable_reason: str | None = None

    def __post_init__(self) -> None:
        if not self.extensions or any(
            not item.startswith(".") or item.casefold() != item
            for item in self.extensions
        ):
            raise ValueError("Export extensions must be normalized and non-empty")
        if len(set(self.extensions)) != len(self.extensions):
            raise ValueError("Export extensions must be unique")
        if not self.label.strip():
            raise ValueError("Export capability label must not be empty")
        if self.available:
            if self.backend is None or self.unavailable_reason is not None:
                raise ValueError("Available export capability metadata is inconsistent")
        elif not self.unavailable_reason:
            raise ValueError("Unavailable export capability requires a reason")


_BREP_SELECTION = frozenset(
    {
        ExportEntityKind.DOCUMENT,
        ExportEntityKind.SOLID,
        ExportEntityKind.FACE,
        ExportEntityKind.WIRE,
        ExportEntityKind.EDGE,
    }
)


EXPORT_CAPABILITIES: Mapping[ExportFormatId, ExportCapability] = MappingProxyType(
    {
        ExportFormatId.STEP: ExportCapability(
            ExportFormatId.STEP,
            (".step", ".stp"),
            "STEP",
            ExportCapabilityClass.NATIVE_SUPPORTED_NOW,
            True,
            "OCP STEPControl_Writer",
            _BREP_SELECTION,
            ("AP203", "AP214", "AP242"),
        ),
        ExportFormatId.IGES: ExportCapability(
            ExportFormatId.IGES,
            (".iges", ".igs"),
            "IGES",
            ExportCapabilityClass.NATIVE_SUPPORTED_NOW,
            True,
            "OCP IGESControl_Writer",
            _BREP_SELECTION,
        ),
        ExportFormatId.STL: ExportCapability(
            ExportFormatId.STL,
            (".stl",),
            "STL",
            ExportCapabilityClass.NATIVE_SUPPORTED_NOW,
            True,
            "OCP StlAPI_Writer / RWStl",
            frozenset(
                {
                    ExportEntityKind.DOCUMENT,
                    ExportEntityKind.SOLID,
                    ExportEntityKind.FACE,
                    ExportEntityKind.MESH,
                }
            ),
        ),
        ExportFormatId.BREP: ExportCapability(
            ExportFormatId.BREP,
            (".brep", ".brp"),
            "Open CASCADE BREP",
            ExportCapabilityClass.NATIVE_SUPPORTED_NOW,
            True,
            "OCP BRepTools",
            _BREP_SELECTION,
            ("1", "2", "3"),
        ),
        ExportFormatId.PARASOLID: ExportCapability(
            ExportFormatId.PARASOLID,
            (".x_t", ".x_b"),
            "Parasolid",
            ExportCapabilityClass.ARCHITECTURE_SUPPORTED_BUT_BACKEND_UNAVAILABLE,
            False,
            None,
            frozenset(),
            unavailable_reason="Parasolid proprietary writer SDK is not present.",
        ),
        ExportFormatId.ACIS: ExportCapability(
            ExportFormatId.ACIS,
            (".sat", ".sab"),
            "ACIS",
            ExportCapabilityClass.ARCHITECTURE_SUPPORTED_BUT_BACKEND_UNAVAILABLE,
            False,
            None,
            frozenset(),
            unavailable_reason="ACIS proprietary writer SDK is not present.",
        ),
        ExportFormatId.DWG: ExportCapability(
            ExportFormatId.DWG,
            (".dwg",),
            "DWG",
            ExportCapabilityClass.NOT_IMPLEMENTED,
            False,
            None,
            frozenset(),
            unavailable_reason="No DWG export adapter is implemented.",
        ),
        ExportFormatId.DXF: ExportCapability(
            ExportFormatId.DXF,
            (".dxf",),
            "DXF",
            ExportCapabilityClass.DECLARED_UI_ONLY,
            False,
            None,
            frozenset(),
            unavailable_reason=(
                "DXF appears in the legacy source-file picker, but no export writer exists."
            ),
        ),
    }
)


@dataclass(frozen=True, slots=True)
class StlMeshOptions:
    """Options consumed by the native STL tessellation/writer path."""

    linear_deflection: float = 0.1
    angular_deflection: float = 0.5
    relative: bool = False

    def __post_init__(self) -> None:
        if isinstance(self.linear_deflection, bool) or not isinstance(
            self.linear_deflection, (int, float)
        ):
            raise TypeError("STL linear deflection must be a number")
        if isinstance(self.angular_deflection, bool) or not isinstance(
            self.angular_deflection, (int, float)
        ):
            raise TypeError("STL angular deflection must be a number")
        object.__setattr__(self, "linear_deflection", float(self.linear_deflection))
        object.__setattr__(self, "angular_deflection", float(self.angular_deflection))
        if not math.isfinite(self.linear_deflection) or self.linear_deflection <= 0.0:
            raise ValueError("STL linear deflection must be finite and positive")
        if (
            not math.isfinite(self.angular_deflection)
            or not 0.0 < self.angular_deflection <= math.pi
        ):
            raise ValueError("STL angular deflection must be in (0, pi]")
        if not isinstance(self.relative, bool):
            raise TypeError("STL relative option must be bool")


@dataclass(frozen=True, slots=True)
class ExportProfile:
    """Profile-schema-v1 options whose format-specific values are writer-backed.

    ``format_version`` versions this JSON profile schema. STEP standards and BREP
    file-format versions are carried separately in ``standard``.
    """

    format_id: ExportFormatId
    standard: str | None = None
    tolerance: float | None = None
    stl_encoding: StlEncoding | None = None
    unit_policy: ExportUnitPolicy = ExportUnitPolicy.MODEL_UNITS
    compatibility: str | None = None
    mesh_options: StlMeshOptions | None = None
    overwrite_policy: ExportOverwritePolicy = ExportOverwritePolicy.FAIL_IF_EXISTS
    format_version: int = EXPORT_PROFILE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if type(self.format_version) is not int:
            raise TypeError("CAD export profile schema version must be int")
        if self.format_version != EXPORT_PROFILE_SCHEMA_VERSION:
            raise ValueError("Unsupported CAD export profile version")
        if not isinstance(self.format_id, ExportFormatId):
            raise TypeError("Export profile format must be ExportFormatId")
        if self.standard is not None and not isinstance(self.standard, str):
            raise TypeError("Export profile standard/version must be str or None")
        if self.stl_encoding is not None and not isinstance(
            self.stl_encoding, StlEncoding
        ):
            raise TypeError("STL encoding is invalid")
        if not isinstance(self.unit_policy, ExportUnitPolicy):
            raise TypeError("Export unit policy is invalid")
        if not isinstance(self.overwrite_policy, ExportOverwritePolicy):
            raise TypeError("Export overwrite policy is invalid")
        if self.mesh_options is not None and not isinstance(
            self.mesh_options, StlMeshOptions
        ):
            raise TypeError("STL mesh options are invalid")
        capability = EXPORT_CAPABILITIES[self.format_id]
        if self.standard is not None and self.standard not in capability.standards:
            raise ValueError(f"Unsupported {capability.label} version/standard")
        if self.tolerance is not None:
            if isinstance(self.tolerance, bool) or not isinstance(
                self.tolerance, (int, float)
            ):
                raise TypeError("Export tolerance must be a number or None")
            object.__setattr__(self, "tolerance", float(self.tolerance))
            if not math.isfinite(self.tolerance) or self.tolerance <= 0.0:
                raise ValueError("Export tolerance must be finite and positive")
        if self.compatibility is not None:
            raise ValueError("No compatibility override is supported by this backend")
        if self.format_id is ExportFormatId.STL:
            if self.standard is not None:
                raise ValueError("STL does not use a version/standard option")
            if self.stl_encoding is None:
                raise ValueError("STL profile requires an encoding")
            if self.mesh_options is None:
                if self.tolerance is not None:
                    raise ValueError(
                        "Existing-mesh STL profile cannot carry tessellation tolerance"
                    )
            elif self.tolerance != self.mesh_options.linear_deflection:
                raise ValueError("STL tolerance must match linear deflection")
        elif (
            self.stl_encoding is not None
            or self.mesh_options is not None
            or self.tolerance is not None
        ):
            raise ValueError("STL-only options cannot be used for this format")

    @classmethod
    def default_for(
        cls,
        format_id: ExportFormatId,
        *,
        stl_tessellation_applicable: bool = True,
        overwrite_policy: ExportOverwritePolicy = ExportOverwritePolicy.FAIL_IF_EXISTS,
    ) -> "ExportProfile":
        """Return one deterministic, safe profile for a registered format."""
        if not isinstance(format_id, ExportFormatId):
            raise TypeError("Export format must be ExportFormatId")
        if not isinstance(stl_tessellation_applicable, bool):
            raise TypeError("STL tessellation applicability must be bool")
        if not isinstance(overwrite_policy, ExportOverwritePolicy):
            raise TypeError("Export overwrite policy is invalid")
        standard = {
            ExportFormatId.STEP: "AP242",
            ExportFormatId.BREP: "3",
        }.get(format_id)
        if format_id is ExportFormatId.STL:
            if not stl_tessellation_applicable:
                return cls(
                    format_id,
                    stl_encoding=StlEncoding.BINARY,
                    overwrite_policy=overwrite_policy,
                )
            mesh = StlMeshOptions()
            return cls(
                format_id,
                tolerance=mesh.linear_deflection,
                stl_encoding=StlEncoding.BINARY,
                mesh_options=mesh,
                overwrite_policy=overwrite_policy,
            )
        return cls(
            format_id,
            standard=standard,
            overwrite_policy=overwrite_policy,
        )

    def to_dict(self) -> dict[str, object]:
        """Return a deterministic JSON-compatible profile payload."""
        payload: dict[str, object] = {
            "format": EXPORT_PROFILE_FORMAT,
            "format_version": self.format_version,
            "format_id": self.format_id.value,
            "standard": self.standard,
            "tolerance": self.tolerance,
            "stl_encoding": (
                None if self.stl_encoding is None else self.stl_encoding.value
            ),
            "unit_policy": self.unit_policy.value,
            "compatibility": self.compatibility,
            "overwrite_policy": self.overwrite_policy.value,
            "mesh_options": (
                None if self.mesh_options is None else asdict(self.mesh_options)
            ),
        }
        return payload

    def to_json(self) -> str:
        """Serialize with stable key order and no locale-dependent values."""
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )

    @classmethod
    def from_dict(cls, data: object) -> "ExportProfile":
        """Decode the exact strict profile-schema-v1 object into typed values."""
        if not isinstance(data, dict):
            raise TypeError("CAD export profile payload must be an object")
        if any(not isinstance(key, str) for key in data):
            raise ValueError("CAD export profile keys must be strings")
        expected = {
            "compatibility",
            "format",
            "format_id",
            "format_version",
            "mesh_options",
            "overwrite_policy",
            "standard",
            "stl_encoding",
            "tolerance",
            "unit_policy",
        }
        actual = set(data)
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        if missing or unknown:
            raise ValueError(
                "CAD export profile fields are invalid: "
                f"missing={missing}, unknown={unknown}"
            )
        if data["format"] != EXPORT_PROFILE_FORMAT:
            raise ValueError("CAD export profile format marker is invalid")
        format_version = _strict_int(data["format_version"], "format_version")
        format_id = _strict_enum(ExportFormatId, data["format_id"], "format_id")
        unit_policy = _strict_enum(
            ExportUnitPolicy, data["unit_policy"], "unit_policy"
        )
        overwrite_policy = _strict_enum(
            ExportOverwritePolicy,
            data["overwrite_policy"],
            "overwrite_policy",
        )
        standard = _optional_str(data["standard"], "standard")
        tolerance = _optional_number(data["tolerance"], "tolerance")
        stl_encoding = (
            None
            if data["stl_encoding"] is None
            else _strict_enum(StlEncoding, data["stl_encoding"], "stl_encoding")
        )
        compatibility = _optional_str(data["compatibility"], "compatibility")
        mesh_options = _decode_mesh_options(data["mesh_options"])
        return cls(
            format_id=format_id,
            standard=standard,
            tolerance=tolerance,
            stl_encoding=stl_encoding,
            unit_policy=unit_policy,
            compatibility=compatibility,
            mesh_options=mesh_options,
            overwrite_policy=overwrite_policy,
            format_version=format_version,
        )

    @classmethod
    def from_json(cls, payload: str) -> "ExportProfile":
        """Decode strict JSON without coercing invalid values or schema versions."""
        if not isinstance(payload, str):
            raise TypeError("CAD export profile JSON must be str")
        try:
            data = json.loads(payload)
        except (json.JSONDecodeError, RecursionError) as error:
            raise ValueError("CAD export profile JSON is invalid") from error
        return cls.from_dict(data)


def _strict_int(value: object, field: str) -> int:
    if type(value) is not int:
        raise TypeError(f"CAD export profile {field} must be int")
    return value


def _strict_enum(enum_type, value: object, field: str):
    if not isinstance(value, str):
        raise TypeError(f"CAD export profile {field} must be str")
    try:
        return enum_type(value)
    except ValueError as error:
        raise ValueError(f"CAD export profile {field} is invalid") from error


def _optional_str(value: object, field: str) -> str | None:
    if value is None or isinstance(value, str):
        return value
    raise TypeError(f"CAD export profile {field} must be str or null")


def _optional_number(value: object, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"CAD export profile {field} must be numeric or null")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"CAD export profile {field} must be finite")
    return number


def _decode_mesh_options(value: object) -> StlMeshOptions | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise TypeError("CAD export profile mesh_options must be an object or null")
    expected = {"angular_deflection", "linear_deflection", "relative"}
    if any(not isinstance(key, str) for key in value):
        raise ValueError("CAD export profile mesh_options keys must be strings")
    actual = set(value)
    if actual != expected:
        raise ValueError(
            "CAD export profile mesh_options fields are invalid: "
            f"missing={sorted(expected - actual)}, unknown={sorted(actual - expected)}"
        )
    relative = value["relative"]
    if not isinstance(relative, bool):
        raise TypeError("CAD export profile mesh_options.relative must be bool")
    linear = _optional_number(value["linear_deflection"], "linear_deflection")
    angular = _optional_number(value["angular_deflection"], "angular_deflection")
    if linear is None or angular is None:
        raise TypeError("STL mesh deflections cannot be null")
    return StlMeshOptions(linear, angular, relative)


@dataclass(frozen=True, slots=True)
class ExportSelectionRef:
    """Stable selection identity resolved only inside the native adapter."""

    document_id: CadDocumentId
    selection_id: str
    entity_kind: ExportEntityKind
    object_id: CadObjectId | None = None

    def __post_init__(self) -> None:
        if not self.selection_id.strip():
            raise ValueError("Export selection identity must not be empty")
        if self.entity_kind in {ExportEntityKind.DOCUMENT, ExportEntityKind.MESH}:
            raise ValueError("Topology selection must identify a bounded BREP entity")


def capability_for_path(path: Path) -> ExportCapability | None:
    """Resolve a registered extension without guessing or format conversion."""
    suffix = path.suffix.casefold()
    return next(
        (
            capability
            for capability in EXPORT_CAPABILITIES.values()
            if suffix in capability.extensions
        ),
        None,
    )


def export_file_filter(*, include_unavailable: bool = True) -> str:
    """Build one deterministic Qt file filter from the registry."""
    capabilities = tuple(
        capability
        for capability in EXPORT_CAPABILITIES.values()
        if include_unavailable or capability.available
    )
    return ";;".join(
        f"{item.label} ({' '.join('*' + suffix for suffix in item.extensions)})"
        for item in capabilities
    )
