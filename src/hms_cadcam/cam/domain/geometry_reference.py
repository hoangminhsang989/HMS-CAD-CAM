"""Persistent geometry references and native-free resolution contracts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, ClassVar, Protocol
from uuid import UUID

from hms_cadcam.cam.domain.errors import (
    CamValidationError,
    GeometryReferenceError,
    UnsupportedCamSchemaError,
)
from hms_cadcam.cam.domain.ids import GeometryReferenceId
from hms_cadcam.cam.domain.revision import GeometryFingerprint, Revision

_FORMAT = "HMS_CAM_GEOMETRY_REFERENCE"
_FORMAT_VERSION = 1
_SCHEME = re.compile(r"[a-z][a-z0-9_.-]{1,63}")
HMS_GEOMETRY_REFERENCE_SCHEME = "hms_persistent_geometry"
HMS_GEOMETRY_REFERENCE_SCHEME_VERSION = 1


class GeometryReferenceKind(StrEnum):
    """Semantic level targeted by a geometry reference."""

    DOCUMENT = "document"
    OCCURRENCE = "occurrence"
    BODY = "body"
    FACE = "face"
    EDGE = "edge"
    VERTEX = "vertex"
    DATUM = "datum"
    SKETCH_OR_PROFILE = "sketch_or_profile"


class GeometryRepresentationKind(StrEnum):
    """Persistent representation category, independent from CAD runtime IDs."""

    BREP = "brep"
    TRIANGLE_MESH = "triangle_mesh"
    CONSTRUCTION = "construction"


class GeometryResolutionStatus(StrEnum):
    """Exhaustive outcomes from a geometry resolver."""

    RESOLVED = "resolved"
    MISSING = "missing"
    STALE = "stale"
    AMBIGUOUS = "ambiguous"
    UNSUPPORTED_SCHEME = "unsupported_scheme"
    UNSUPPORTED_VERSION = "unsupported_version"
    SOURCE_MISMATCH = "source_mismatch"
    TOPOLOGY_CHANGED = "topology_changed"
    INVALID = "invalid"


@dataclass(frozen=True, slots=True)
class GeometryReference:
    """Versioned reference to geometry owned outside the CAM domain."""

    reference_id: GeometryReferenceId
    scheme: str
    scheme_version: int
    source_id: UUID
    kind: GeometryReferenceKind
    geometry_kind: GeometryRepresentationKind
    expected_geometry_fingerprint: GeometryFingerprint
    expected_source_revision: Revision
    occurrence_path: str | None = None
    subshape_selector: str | None = None
    hint: str | None = None
    diagnostic_fallback: tuple[tuple[str, str], ...] = ()
    SERIALIZATION_VERSION: ClassVar[int] = _FORMAT_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.reference_id, GeometryReferenceId):
            raise GeometryReferenceError("reference_id is invalid")
        if not isinstance(self.scheme, str) or not _SCHEME.fullmatch(self.scheme):
            raise GeometryReferenceError("Geometry reference scheme is invalid")
        if type(self.scheme_version) is not int or self.scheme_version <= 0:
            raise GeometryReferenceError("Geometry reference scheme version is invalid")
        if not isinstance(self.source_id, UUID):
            raise GeometryReferenceError("Geometry reference source_id must be UUID")
        if not isinstance(self.kind, GeometryReferenceKind):
            raise GeometryReferenceError("Geometry reference kind is invalid")
        if not isinstance(self.geometry_kind, GeometryRepresentationKind):
            raise GeometryReferenceError("Geometry representation kind is invalid")
        if not isinstance(self.expected_geometry_fingerprint, GeometryFingerprint):
            raise GeometryReferenceError("Expected geometry fingerprint is invalid")
        if not isinstance(self.expected_source_revision, Revision):
            raise GeometryReferenceError("Expected source revision is invalid")
        self._validate_optional_text("occurrence_path", self.occurrence_path)
        self._validate_optional_text("subshape_selector", self.subshape_selector)
        self._validate_optional_text("hint", self.hint)
        if self.kind is GeometryReferenceKind.DOCUMENT and (
            self.occurrence_path is not None or self.subshape_selector is not None
        ):
            raise GeometryReferenceError("Document reference cannot select a child")
        if self.kind is GeometryReferenceKind.OCCURRENCE:
            if self.occurrence_path is None or self.subshape_selector is not None:
                raise GeometryReferenceError("Occurrence reference requires only a path")
        if self.kind in {
            GeometryReferenceKind.BODY,
            GeometryReferenceKind.FACE,
            GeometryReferenceKind.EDGE,
            GeometryReferenceKind.VERTEX,
            GeometryReferenceKind.DATUM,
            GeometryReferenceKind.SKETCH_OR_PROFILE,
        } and self.subshape_selector is None:
            raise GeometryReferenceError("Subshape reference requires a selector")
        try:
            normalized = tuple(sorted(self.diagnostic_fallback))
        except TypeError as error:
            raise GeometryReferenceError("Diagnostic fallback is invalid") from error
        if any(
            not isinstance(item, tuple)
            or len(item) != 2
            or not all(isinstance(value, str) and value.strip() for value in item)
            for item in normalized
        ):
            raise GeometryReferenceError("Diagnostic fallback entries are invalid")
        if len({key for key, _value in normalized}) != len(normalized):
            raise GeometryReferenceError("Diagnostic fallback keys must be unique")
        object.__setattr__(self, "diagnostic_fallback", normalized)

    @staticmethod
    def _validate_optional_text(name: str, value: str | None) -> None:
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise GeometryReferenceError(f"{name} must be non-empty when provided")

    @property
    def target_key(self) -> tuple[object, ...]:
        """Return the persistent target identity, excluding editable reference ID."""
        return (
            self.scheme,
            self.scheme_version,
            self.source_id,
            self.kind,
            self.geometry_kind,
            self.occurrence_path,
            self.subshape_selector,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to the strict 7A.1 interchange payload."""
        return {
            "format": _FORMAT,
            "format_version": _FORMAT_VERSION,
            "reference_id": str(self.reference_id),
            "scheme": self.scheme,
            "scheme_version": self.scheme_version,
            "source_id": str(self.source_id),
            "reference_kind": self.kind.value,
            "geometry_kind": self.geometry_kind.value,
            "occurrence_path": self.occurrence_path,
            "subshape_selector": self.subshape_selector,
            "expected_geometry_fingerprint": self.expected_geometry_fingerprint.to_dict(),
            "expected_source_revision": self.expected_source_revision.to_dict(),
            "hint": self.hint,
            "diagnostic_fallback": [
                {"key": key, "value": value}
                for key, value in self.diagnostic_fallback
            ],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GeometryReference":
        """Deserialize atomically, rejecting unknown formats and versions."""
        required = {
            "format",
            "format_version",
            "reference_id",
            "scheme",
            "scheme_version",
            "source_id",
            "reference_kind",
            "geometry_kind",
            "occurrence_path",
            "subshape_selector",
            "expected_geometry_fingerprint",
            "expected_source_revision",
            "hint",
            "diagnostic_fallback",
        }
        if not isinstance(data, dict) or set(data) != required:
            raise GeometryReferenceError("Geometry reference payload is malformed")
        if data["format"] != _FORMAT:
            raise UnsupportedCamSchemaError("Unsupported geometry reference format")
        if (
            type(data["format_version"]) is not int
            or data["format_version"] != _FORMAT_VERSION
        ):
            raise UnsupportedCamSchemaError("Unsupported geometry reference version")
        fallback = data["diagnostic_fallback"]
        if not isinstance(fallback, list) or any(
            not isinstance(item, dict) or set(item) != {"key", "value"}
            for item in fallback
        ):
            raise GeometryReferenceError("Diagnostic fallback payload is malformed")
        try:
            return cls(
                reference_id=GeometryReferenceId.parse(data["reference_id"]),
                scheme=data["scheme"],
                scheme_version=data["scheme_version"],
                source_id=UUID(data["source_id"]),
                kind=GeometryReferenceKind(data["reference_kind"]),
                geometry_kind=GeometryRepresentationKind(data["geometry_kind"]),
                occurrence_path=data["occurrence_path"],
                subshape_selector=data["subshape_selector"],
                expected_geometry_fingerprint=GeometryFingerprint.from_dict(
                    data["expected_geometry_fingerprint"]
                ),
                expected_source_revision=Revision.from_dict(
                    data["expected_source_revision"]
                ),
                hint=data["hint"],
                diagnostic_fallback=tuple(
                    (item["key"], item["value"]) for item in fallback
                ),
            )
        except UnsupportedCamSchemaError:
            raise
        except (
            AttributeError,
            CamValidationError,
            KeyError,
            TypeError,
            ValueError,
        ) as error:
            raise GeometryReferenceError("Geometry reference payload is invalid") from error


@dataclass(frozen=True, slots=True)
class GeometryResolutionEvidence:
    """Native-free facts reported by a future resolver adapter."""

    source_id: UUID
    source_revision: Revision
    geometry_fingerprint: GeometryFingerprint | None
    match_count: int
    scheme_supported: bool = True
    version_supported: bool = True
    topology_changed: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.source_id, UUID):
            raise GeometryReferenceError("Resolution source_id must be UUID")
        if not isinstance(self.source_revision, Revision):
            raise GeometryReferenceError("Resolution revision is invalid")
        if self.geometry_fingerprint is not None and not isinstance(
            self.geometry_fingerprint, GeometryFingerprint
        ):
            raise GeometryReferenceError("Resolution fingerprint is invalid")
        if type(self.match_count) is not int or self.match_count < 0:
            raise GeometryReferenceError("Resolution match count is invalid")
        if type(self.scheme_supported) is not bool:
            raise GeometryReferenceError("scheme_supported must be boolean")
        if type(self.version_supported) is not bool:
            raise GeometryReferenceError("version_supported must be boolean")
        if type(self.topology_changed) is not bool:
            raise GeometryReferenceError("topology_changed must be boolean")


@dataclass(frozen=True, slots=True)
class GeometryResolutionResult:
    """Public resolution outcome containing no native geometry handle."""

    reference_id: GeometryReferenceId
    status: GeometryResolutionStatus
    diagnostics: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.reference_id, GeometryReferenceId):
            raise GeometryReferenceError("Resolution reference_id is invalid")
        if not isinstance(self.status, GeometryResolutionStatus):
            raise GeometryReferenceError("Resolution status is invalid")
        if not isinstance(self.diagnostics, tuple) or any(
            not isinstance(item, str) or not item.strip() for item in self.diagnostics
        ):
            raise GeometryReferenceError("Resolution diagnostics are invalid")


class GeometryReferenceResolver(Protocol):
    """Port implemented later by a CAD-specific application adapter."""

    def resolve(self, reference: GeometryReference) -> GeometryResolutionResult:
        """Resolve without exposing native geometry through the public result."""
        ...


def assess_geometry_resolution(
    reference: GeometryReference,
    evidence: GeometryResolutionEvidence,
) -> GeometryResolutionResult:
    """Apply fail-closed stale and ambiguity rules to resolver evidence."""
    if not evidence.scheme_supported:
        status = GeometryResolutionStatus.UNSUPPORTED_SCHEME
    elif not evidence.version_supported:
        status = GeometryResolutionStatus.UNSUPPORTED_VERSION
    elif evidence.source_id != reference.source_id:
        status = GeometryResolutionStatus.SOURCE_MISMATCH
    elif evidence.match_count == 0:
        status = GeometryResolutionStatus.MISSING
    elif evidence.match_count > 1:
        status = GeometryResolutionStatus.AMBIGUOUS
    elif evidence.topology_changed:
        status = GeometryResolutionStatus.TOPOLOGY_CHANGED
    elif evidence.geometry_fingerprint != reference.expected_geometry_fingerprint:
        status = GeometryResolutionStatus.TOPOLOGY_CHANGED
    elif evidence.source_revision != reference.expected_source_revision:
        status = GeometryResolutionStatus.STALE
    else:
        status = GeometryResolutionStatus.RESOLVED
    return GeometryResolutionResult(reference.reference_id, status)
