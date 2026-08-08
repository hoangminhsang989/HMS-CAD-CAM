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
EXPORT_PROFILE_VERSION = 1


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
    """Versioned export options whose format-specific values are writer-backed."""

    format_id: ExportFormatId
    standard: str | None = None
    tolerance: float | None = None
    stl_encoding: StlEncoding | None = None
    unit_policy: ExportUnitPolicy = ExportUnitPolicy.MODEL_UNITS
    compatibility: str | None = None
    mesh_options: StlMeshOptions | None = None
    format_version: int = EXPORT_PROFILE_VERSION

    def __post_init__(self) -> None:
        if self.format_version != EXPORT_PROFILE_VERSION:
            raise ValueError("Unsupported CAD export profile version")
        if not isinstance(self.format_id, ExportFormatId):
            raise TypeError("Export profile format must be ExportFormatId")
        if not isinstance(self.unit_policy, ExportUnitPolicy):
            raise TypeError("Export unit policy is invalid")
        capability = EXPORT_CAPABILITIES[self.format_id]
        if self.standard is not None and self.standard not in capability.standards:
            raise ValueError(f"Unsupported {capability.label} version/standard")
        if self.tolerance is not None and (
            not math.isfinite(self.tolerance) or self.tolerance <= 0.0
        ):
            raise ValueError("Export tolerance must be finite and positive")
        if self.compatibility is not None:
            raise ValueError("No compatibility override is supported by this backend")
        if self.format_id is ExportFormatId.STL:
            if self.standard is not None:
                raise ValueError("STL does not use a version/standard option")
            if self.stl_encoding is None or self.mesh_options is None:
                raise ValueError("STL profile requires encoding and mesh options")
            if self.tolerance is not None and (
                self.tolerance != self.mesh_options.linear_deflection
            ):
                raise ValueError("STL tolerance must match linear deflection")
        elif (
            self.stl_encoding is not None
            or self.mesh_options is not None
            or self.tolerance is not None
        ):
            raise ValueError("STL-only options cannot be used for this format")

    @classmethod
    def default_for(cls, format_id: ExportFormatId) -> "ExportProfile":
        """Return one deterministic, safe profile for a registered format."""
        if not isinstance(format_id, ExportFormatId):
            raise TypeError("Export format must be ExportFormatId")
        standard = {
            ExportFormatId.STEP: "AP242",
            ExportFormatId.BREP: "3",
        }.get(format_id)
        if format_id is ExportFormatId.STL:
            mesh = StlMeshOptions()
            return cls(
                format_id,
                tolerance=mesh.linear_deflection,
                stl_encoding=StlEncoding.BINARY,
                mesh_options=mesh,
            )
        return cls(format_id, standard=standard)

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
        )


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
