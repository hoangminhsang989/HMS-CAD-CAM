"""Operation records, validation diagnostics and recompute state."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any, ClassVar, TypeAlias
from uuid import UUID, uuid4

from hms_cadcam.cam.domain.errors import (
    CamInvariantError,
    CamUnitError,
    CamValidationError,
    UnsupportedCamSchemaError,
)
from hms_cadcam.cam.domain.geometry_reference import (
    GeometryReference,
    GeometryReferenceKind,
    GeometryResolutionResult,
    GeometryResolutionStatus,
)
from hms_cadcam.cam.domain.ids import (
    CamNodeId,
    GeometryInputId,
    MachineDefinitionId,
    OperationId,
    SetupId,
    ToolAssemblyId,
)
from hms_cadcam.cam.domain.machine import MachineCompatibilityStatus, MachineRequirement, OperationCapability
from hms_cadcam.cam.domain.revision import ContentFingerprint, DependencyFingerprint, Revision
from hms_cadcam.cam.domain.tooling import ToolAssembly
from hms_cadcam.cam.domain.units import LengthUnit

_PARAMETER_FORMAT = "HMS_CAM_OPERATION_PARAMETERS"
_GEOMETRY_INPUT_FORMAT = "HMS_CAM_GEOMETRY_INPUT"
_TOOL_REFERENCE_FORMAT = "HMS_CAM_TOOL_ASSEMBLY_REFERENCE"
_MACHINE_REQUIREMENT_FORMAT = "HMS_CAM_MACHINE_REQUIREMENT"
_DIAGNOSTIC_FORMAT = "HMS_CAM_VALIDATION_DIAGNOSTIC"
_ARTIFACT_FORMAT = "HMS_CAM_ARTIFACT_STATE"
_OPERATION_FORMAT = "HMS_CAM_OPERATION"
_VERSION = 1
_KEY = re.compile(r"[a-z][a-z0-9_.-]{1,127}")
_PARAMETER_NAME = re.compile(r"[a-z][a-z0-9_]{0,63}")


def _payload(data: Any, format_name: str, fields: set[str]) -> None:
    if not isinstance(data, dict) or set(data) != {"format", "format_version", *fields}:
        raise CamValidationError(f"{format_name} payload is malformed")
    if data["format"] != format_name:
        raise UnsupportedCamSchemaError(f"Unsupported {format_name} format")
    if type(data["format_version"]) is not int or data["format_version"] != _VERSION:
        raise UnsupportedCamSchemaError(f"Unsupported {format_name} version")


class OperationFamily(StrEnum):
    MILLING = "milling"
    DRILLING = "drilling"
    TURNING = "turning"
    PROBING = "probing"
    CUSTOM = "custom"


class GeometryInputRole(StrEnum):
    DRIVE_GEOMETRY = "drive_geometry"
    BOUNDARY = "boundary"
    CHECK_GEOMETRY = "check_geometry"
    START_POINT = "start_point"
    STOCK = "stock"
    FIXTURE = "fixture"
    AXIS = "axis"
    PROFILE = "profile"


class DiagnosticSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class DiagnosticCode(StrEnum):
    TREE_INVALID = "tree_invalid"
    GEOMETRY_UNRESOLVED = "geometry_unresolved"
    GEOMETRY_STALE = "geometry_stale"
    TOOL_MISSING = "tool_missing"
    TOOL_STALE = "tool_stale"
    TOOL_UNIT_MISMATCH = "tool_unit_mismatch"
    MACHINE_MISSING = "machine_missing"
    MACHINE_STALE = "machine_stale"
    MACHINE_CAPABILITY_MISMATCH = "machine_capability_mismatch"
    MACHINE_UNIT_MISMATCH = "machine_unit_mismatch"
    PARAMETERS_INVALID = "parameters_invalid"
    DEPENDENCY_CYCLE = "dependency_cycle"
    UPSTREAM_INVALID = "upstream_invalid"
    OPERATION_DISABLED = "operation_disabled"
    ARTIFACT_MISSING = "artifact_missing"
    ARTIFACT_CORRUPT = "artifact_corrupt"
    COMPUTATION_INTERRUPTED = "computation_interrupted"
    FACING_INVALID_PARAMETERS = "facing.invalid_parameters"
    FACING_UNSUPPORTED_STOCK = "facing.unsupported_stock"
    FACING_NON_PLANAR_FACE = "facing.non_planar_face"
    FACING_AXIS_MISMATCH = "facing.axis_mismatch"
    FACING_GEOMETRY_UNRESOLVED = "facing.geometry_unresolved"
    FACING_GEOMETRY_STALE = "facing.geometry_stale"
    FACING_TOOL_MISSING = "facing.tool_missing"
    FACING_TOOL_STALE = "facing.tool_stale"
    FACING_UNSUPPORTED_TOOL = "facing.unsupported_tool"
    FACING_MACHINE_INCOMPATIBLE = "facing.machine_incompatible"
    FACING_UNSAFE_CLEARANCE = "facing.unsafe_clearance"
    FACING_GENERATION_FAILED = "facing.generation_failed"
    FACING_STALE_RESULT = "facing.stale_result"
    FACING_FACE_REFERENCE_MISSING = "facing.face_reference_missing"
    FACING_FACE_REFERENCE_STALE = "facing.face_reference_stale"
    FACING_FACE_REFERENCE_AMBIGUOUS = "facing.face_reference_ambiguous"
    FACING_FACE_SOURCE_MISMATCH = "facing.face_source_mismatch"
    FACING_FACE_TOPOLOGY_CHANGED = "facing.face_topology_changed"
    FACING_INVALID_FACE_BOUNDARY = "facing.invalid_face_boundary"
    FACING_UNSUPPORTED_INNER_LOOPS = "facing.unsupported_inner_loops"
    FACING_UNSUPPORTED_FACE_SHAPE = "facing.unsupported_face_shape"
    FACING_TARGET_ABOVE_STOCK = "facing.target_above_stock"
    FACING_GEOMETRY_RESOLUTION_FAILED = "facing.geometry_resolution_failed"
    CONTOUR_INVALID_PARAMETERS = "contour.invalid_parameters"
    CONTOUR_PROFILE_MISSING = "contour.profile_missing"
    CONTOUR_PROFILE_STALE = "contour.profile_stale"
    CONTOUR_PROFILE_AMBIGUOUS = "contour.profile_ambiguous"
    CONTOUR_SOURCE_MISMATCH = "contour.source_mismatch"
    CONTOUR_TOPOLOGY_CHANGED = "contour.topology_changed"
    CONTOUR_OPEN_PROFILE = "contour.open_profile"
    CONTOUR_NON_PLANAR_PROFILE = "contour.non_planar_profile"
    CONTOUR_SELF_INTERSECTION = "contour.self_intersection"
    CONTOUR_UNSUPPORTED_CURVE = "contour.unsupported_curve"
    CONTOUR_UNSUPPORTED_INNER_LOOPS = "contour.unsupported_inner_loops"
    CONTOUR_OFFSET_FAILED = "contour.offset_failed"
    CONTOUR_OFFSET_COLLAPSED = "contour.offset_collapsed"
    CONTOUR_UNSAFE_LEAD = "contour.unsafe_lead"
    CONTOUR_TOOL_MISSING = "contour.tool_missing"
    CONTOUR_TOOL_STALE = "contour.tool_stale"
    CONTOUR_UNSUPPORTED_TOOL = "contour.unsupported_tool"
    CONTOUR_MACHINE_INCOMPATIBLE = "contour.machine_incompatible"
    CONTOUR_STALE_RESULT = "contour.stale_result"
    CONTOUR_GENERATION_FAILED = "contour.generation_failed"
    POCKET_PROFILE_MISSING = "pocket.profile_missing"
    POCKET_PROFILE_STALE = "pocket.profile_stale"
    POCKET_PROFILE_INVALID = "pocket.profile_invalid"
    POCKET_UNSUPPORTED_CURVE = "pocket.unsupported_curve"
    POCKET_SELF_INTERSECTION = "pocket.self_intersection"
    POCKET_INVALID_DEPTH = "pocket.invalid_depth"
    POCKET_UNIT_MISSING = "pocket.unit_missing"
    POCKET_OFFSET_FAILED = "pocket.offset_failed"
    POCKET_OFFSET_COLLAPSED = "pocket.offset_collapsed"
    POCKET_INVALID_STEPOVER = "pocket.invalid_stepover"
    POCKET_INVALID_STEPDOWN = "pocket.invalid_stepdown"
    POCKET_ENTRY_UNSAFE = "pocket.entry_unsafe"
    POCKET_TOOL_MISSING = "pocket.tool_missing"
    POCKET_TOOL_STALE = "pocket.tool_stale"
    POCKET_UNSUPPORTED_TOOL = "pocket.unsupported_tool"
    POCKET_MACHINE_INCOMPATIBLE = "pocket.machine_incompatible"
    POCKET_GENERATION_FAILED = "pocket.generation_failed"
    POCKET_STALE_RESULT = "pocket.stale_result"
    DRILL_GEOMETRY_MISSING = "drill.geometry_missing"
    DRILL_GEOMETRY_STALE = "drill.geometry_stale"
    DRILL_GEOMETRY_AMBIGUOUS = "drill.geometry_ambiguous"
    DRILL_SOURCE_MISMATCH = "drill.source_mismatch"
    DRILL_UNSUPPORTED_GEOMETRY = "drill.unsupported_geometry"
    DRILL_INVALID_DEPTH = "drill.invalid_depth"
    DRILL_UNIT_MISSING = "drill.unit_missing"
    DRILL_DUPLICATE_LOCATION = "drill.duplicate_location"


@dataclass(frozen=True, slots=True)
class ValidationDiagnostic:
    severity: DiagnosticSeverity
    code: DiagnosticCode
    message: str
    context: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.severity, DiagnosticSeverity) or not isinstance(self.code, DiagnosticCode):
            raise CamValidationError("Diagnostic enum is invalid")
        if not isinstance(self.message, str) or not self.message.strip():
            raise CamValidationError("Diagnostic message must not be empty")
        if not isinstance(self.context, tuple) or any(
            not isinstance(item, tuple) or len(item) != 2
            or not all(isinstance(value, str) and value for value in item)
            for item in self.context
        ):
            raise CamValidationError("Diagnostic context is invalid")
        normalized = tuple(sorted(self.context))
        if len({key for key, _ in normalized}) != len(normalized):
            raise CamInvariantError("Diagnostic context keys must be unique")
        object.__setattr__(self, "context", normalized)

    def to_dict(self) -> dict[str, Any]:
        return {"format": _DIAGNOSTIC_FORMAT, "format_version": _VERSION,
                "severity": self.severity.value, "code": self.code.value,
                "message": self.message,
                "context": [{"key": key, "value": value} for key, value in self.context]}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ValidationDiagnostic":
        _payload(data, _DIAGNOSTIC_FORMAT, {"severity", "code", "message", "context"})
        context = data["context"]
        if not isinstance(context, list) or any(not isinstance(item, dict) or set(item) != {"key", "value"} for item in context):
            raise CamValidationError("Diagnostic context payload is malformed")
        try:
            return cls(DiagnosticSeverity(data["severity"]), DiagnosticCode(data["code"]), data["message"],
                       tuple((item["key"], item["value"]) for item in context))
        except UnsupportedCamSchemaError:
            raise
        except (TypeError, ValueError) as error:
            raise CamValidationError("Diagnostic payload is invalid") from error


ParameterValue: TypeAlias = None | bool | int | float | str


def _parameter_value(value: object) -> ParameterValue:
    if value is None or type(value) in {bool, int, str}:
        if isinstance(value, str) and len(value) > 4096:
            raise CamValidationError("Parameter string is too long")
        if type(value) is int and not -(2**63) <= value < 2**63:
            raise CamValidationError("Parameter integer is out of range")
        return value
    if type(value) is float and math.isfinite(value):
        return value
    raise CamValidationError("Parameter value must be a bounded JSON primitive")


@dataclass(frozen=True, slots=True)
class OperationParameterSet:
    """Validated, versioned and canonically ordered strategy parameters."""

    strategy_key: str
    strategy_version: int
    values: tuple[tuple[str, ParameterValue], ...] = ()
    schema_version: int = 1
    SERIALIZATION_VERSION: ClassVar[int] = _VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.strategy_key, str) or not _KEY.fullmatch(self.strategy_key):
            raise CamValidationError("Strategy key is invalid")
        if type(self.strategy_version) is not int or self.strategy_version != 1:
            raise UnsupportedCamSchemaError("Unsupported strategy version")
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise UnsupportedCamSchemaError("Unsupported parameter schema version")
        if not isinstance(self.values, tuple):
            raise CamValidationError("Parameter values must be an immutable tuple")
        normalized: list[tuple[str, ParameterValue]] = []
        for item in self.values:
            if not isinstance(item, tuple) or len(item) != 2:
                raise CamValidationError("Parameter entry is malformed")
            name, value = item
            if not isinstance(name, str) or not _PARAMETER_NAME.fullmatch(name):
                raise CamValidationError("Parameter name is invalid")
            normalized.append((name, _parameter_value(value)))
        normalized.sort(key=lambda item: item[0])
        if len({name for name, _ in normalized}) != len(normalized):
            raise CamInvariantError("Parameter names must be unique")
        object.__setattr__(self, "values", tuple(normalized))

    @property
    def fingerprint(self) -> ContentFingerprint:
        return ContentFingerprint.from_payload({"strategy_key": self.strategy_key,
            "strategy_version": self.strategy_version, "schema_version": self.schema_version,
            "values": [{"name": name, "value": value} for name, value in self.values]})

    def to_dict(self) -> dict[str, Any]:
        return {"format": _PARAMETER_FORMAT, "format_version": _VERSION,
                "strategy_key": self.strategy_key, "strategy_version": self.strategy_version,
                "schema_version": self.schema_version,
                "values": [{"name": name, "value": value} for name, value in self.values]}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "OperationParameterSet":
        _payload(data, _PARAMETER_FORMAT, {"strategy_key", "strategy_version", "schema_version", "values"})
        values = data["values"]
        if not isinstance(values, list) or any(not isinstance(item, dict) or set(item) != {"name", "value"} for item in values):
            raise CamValidationError("Parameter values payload is malformed")
        return cls(data["strategy_key"], data["strategy_version"],
                   tuple((item["name"], item["value"]) for item in values), data["schema_version"])


@dataclass(frozen=True, slots=True)
class OperationGeometryInput:
    input_id: GeometryInputId
    role: GeometryInputRole
    reference: GeometryReference
    required: bool = True
    expected_kind: GeometryReferenceKind | None = None
    selection_order: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.input_id, GeometryInputId) or not isinstance(self.role, GeometryInputRole):
            raise CamValidationError("Geometry input identity or role is invalid")
        if not isinstance(self.reference, GeometryReference):
            raise CamValidationError("Geometry input reference is invalid")
        if type(self.required) is not bool:
            raise CamValidationError("Geometry input required flag must be boolean")
        if self.expected_kind is not None and not isinstance(self.expected_kind, GeometryReferenceKind):
            raise CamValidationError("Expected geometry kind is invalid")
        if self.expected_kind is not None and self.reference.kind is not self.expected_kind:
            raise CamInvariantError("Geometry reference does not match expected kind")
        if self.selection_order is not None and (type(self.selection_order) is not int or self.selection_order < 0):
            raise CamValidationError("Geometry selection order is invalid")

    def to_dict(self) -> dict[str, Any]:
        return {"format": _GEOMETRY_INPUT_FORMAT, "format_version": _VERSION,
                "input_id": str(self.input_id), "role": self.role.value,
                "reference": self.reference.to_dict(), "required": self.required,
                "expected_kind": self.expected_kind.value if self.expected_kind else None,
                "selection_order": self.selection_order}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "OperationGeometryInput":
        _payload(data, _GEOMETRY_INPUT_FORMAT, {"input_id", "role", "reference", "required", "expected_kind", "selection_order"})
        try:
            expected = GeometryReferenceKind(data["expected_kind"]) if data["expected_kind"] is not None else None
            return cls(GeometryInputId.parse(data["input_id"]), GeometryInputRole(data["role"]),
                       GeometryReference.from_dict(data["reference"]), data["required"], expected, data["selection_order"])
        except UnsupportedCamSchemaError:
            raise
        except (TypeError, ValueError) as error:
            raise CamValidationError("Geometry input payload is invalid") from error


class ToolReferenceStatus(StrEnum):
    VALID = "valid"
    MISSING = "missing"
    STALE = "stale"
    INCOMPATIBLE_UNIT = "incompatible_unit"


@dataclass(frozen=True, slots=True)
class ToolAssemblyReference:
    assembly_id: ToolAssemblyId
    expected_revision: Revision
    expected_fingerprint: ContentFingerprint
    unit: LengthUnit

    def __post_init__(self) -> None:
        if not isinstance(self.assembly_id, ToolAssemblyId) or not isinstance(self.expected_revision, Revision) or not isinstance(self.expected_fingerprint, ContentFingerprint):
            raise CamValidationError("Tool assembly reference is invalid")
        if not isinstance(self.unit, LengthUnit) or self.unit is LengthUnit.UNKNOWN:
            raise CamUnitError("Tool assembly reference requires a known unit")

    @classmethod
    def from_assembly(cls, assembly: ToolAssembly) -> "ToolAssemblyReference":
        if not isinstance(assembly, ToolAssembly):
            raise CamValidationError("Tool assembly is invalid")
        return cls(assembly.assembly_id, assembly.revision,
                   ContentFingerprint.from_payload(assembly.to_dict()), assembly.unit)

    def assess(self, assembly: ToolAssembly | None) -> ToolReferenceStatus:
        if assembly is None:
            return ToolReferenceStatus.MISSING
        if assembly.unit is not self.unit:
            return ToolReferenceStatus.INCOMPATIBLE_UNIT
        actual = ContentFingerprint.from_payload(assembly.to_dict())
        if assembly.revision != self.expected_revision or actual != self.expected_fingerprint:
            return ToolReferenceStatus.STALE
        return ToolReferenceStatus.VALID

    def to_dict(self) -> dict[str, Any]:
        return {"format": _TOOL_REFERENCE_FORMAT, "format_version": _VERSION,
                "assembly_id": str(self.assembly_id), "expected_revision": self.expected_revision.to_dict(),
                "expected_fingerprint": self.expected_fingerprint.to_dict(), "unit": self.unit.value}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ToolAssemblyReference":
        _payload(data, _TOOL_REFERENCE_FORMAT, {"assembly_id", "expected_revision", "expected_fingerprint", "unit"})
        try:
            return cls(ToolAssemblyId.parse(data["assembly_id"]), Revision.from_dict(data["expected_revision"]),
                       ContentFingerprint.from_dict(data["expected_fingerprint"]), LengthUnit(data["unit"]))
        except UnsupportedCamSchemaError:
            raise
        except (TypeError, ValueError) as error:
            raise CamValidationError("Tool reference payload is invalid") from error


def machine_requirement_to_dict(value: MachineRequirement) -> dict[str, Any]:
    return {"format": _MACHINE_REQUIREMENT_FORMAT, "format_version": _VERSION,
            "machine_id": str(value.machine_id), "expected_revision": value.expected_revision.to_dict(),
            "expected_fingerprint": value.expected_fingerprint.to_dict(), "unit": value.unit.value,
            "required_capabilities": [item.value for item in value.required_capabilities]}


def machine_requirement_from_dict(data: dict[str, Any]) -> MachineRequirement:
    _payload(data, _MACHINE_REQUIREMENT_FORMAT, {"machine_id", "expected_revision", "expected_fingerprint", "unit", "required_capabilities"})
    values = data["required_capabilities"]
    if not isinstance(values, list):
        raise CamValidationError("Machine capabilities payload must be a list")
    try:
        return MachineRequirement(MachineDefinitionId.parse(data["machine_id"]), Revision.from_dict(data["expected_revision"]),
            ContentFingerprint.from_dict(data["expected_fingerprint"]), LengthUnit(data["unit"]),
            tuple(OperationCapability(item) for item in values))
    except UnsupportedCamSchemaError:
        raise
    except (TypeError, ValueError) as error:
        raise CamValidationError("Machine requirement payload is invalid") from error


class ArtifactStatus(StrEnum):
    MISSING = "missing"
    DIRTY = "dirty"
    COMPUTING = "computing"
    VALID = "valid"
    FAILED = "failed"


class DirtyReason(StrEnum):
    GEOMETRY_CHANGED = "geometry_changed"
    WCS_CHANGED = "wcs_changed"
    STOCK_CHANGED = "stock_changed"
    FIXTURE_CHANGED = "fixture_changed"
    TOOL_CHANGED = "tool_changed"
    MACHINE_CHANGED = "machine_changed"
    PARAMETERS_CHANGED = "parameters_changed"
    UPSTREAM_CHANGED = "upstream_changed"
    ARTIFACT_MISSING = "artifact_missing"


@dataclass(frozen=True, slots=True)
class ComputationToken:
    value: UUID
    generation: int

    def __post_init__(self) -> None:
        if not isinstance(self.value, UUID) or self.value.int == 0 or type(self.generation) is not int or self.generation <= 0:
            raise CamValidationError("Computation token is invalid")


@dataclass(frozen=True, slots=True)
class ArtifactState:
    status: ArtifactStatus = ArtifactStatus.MISSING
    generation: int = 0
    token: ComputationToken | None = None
    input_fingerprint: DependencyFingerprint | None = None
    artifact_fingerprint: ContentFingerprint | None = None
    dirty_reasons: tuple[DirtyReason, ...] = (DirtyReason.ARTIFACT_MISSING,)
    diagnostics: tuple[ValidationDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.status, ArtifactStatus) or type(self.generation) is not int or self.generation < 0:
            raise CamValidationError("Artifact state is invalid")
        if self.token is not None and not isinstance(self.token, ComputationToken):
            raise CamValidationError("Artifact token is invalid")
        if (self.status is ArtifactStatus.COMPUTING) != (self.token is not None):
            raise CamInvariantError("Only COMPUTING state carries a token")
        if self.input_fingerprint is not None and not isinstance(self.input_fingerprint, DependencyFingerprint):
            raise CamValidationError("Artifact input fingerprint is invalid")
        if self.artifact_fingerprint is not None and not isinstance(self.artifact_fingerprint, ContentFingerprint):
            raise CamValidationError("Artifact fingerprint is invalid")
        if not isinstance(self.dirty_reasons, tuple) or any(not isinstance(item, DirtyReason) for item in self.dirty_reasons):
            raise CamValidationError("Dirty reasons are invalid")
        object.__setattr__(self, "dirty_reasons", tuple(sorted(set(self.dirty_reasons), key=lambda item: item.value)))
        if not isinstance(self.diagnostics, tuple) or any(not isinstance(item, ValidationDiagnostic) for item in self.diagnostics):
            raise CamValidationError("Artifact diagnostics are invalid")

    def mark_dirty(self, reason: DirtyReason) -> "ArtifactState":
        if not isinstance(reason, DirtyReason):
            raise CamValidationError("Dirty reason is invalid")
        return replace(self, status=ArtifactStatus.DIRTY, token=None, artifact_fingerprint=None,
                       dirty_reasons=(*self.dirty_reasons, reason))

    def begin(self, fingerprint: DependencyFingerprint) -> tuple["ArtifactState", ComputationToken]:
        if self.status not in {ArtifactStatus.MISSING, ArtifactStatus.DIRTY, ArtifactStatus.FAILED}:
            raise CamInvariantError(f"Cannot compute from {self.status.value}")
        if not isinstance(fingerprint, DependencyFingerprint):
            raise CamValidationError("Computation fingerprint is invalid")
        token = ComputationToken(uuid4(), self.generation + 1)
        return (replace(self, status=ArtifactStatus.COMPUTING, generation=token.generation,
                        token=token, input_fingerprint=fingerprint, diagnostics=()), token)

    def publish(self, token: ComputationToken, current_input: DependencyFingerprint,
                artifact: ContentFingerprint, *, enabled: bool = True) -> tuple["ArtifactState", bool]:
        if not isinstance(token, ComputationToken) or not isinstance(current_input, DependencyFingerprint):
            raise CamValidationError("Publish token or input fingerprint is invalid")
        if not isinstance(artifact, ContentFingerprint) or type(enabled) is not bool:
            raise CamValidationError("Publish artifact or enabled flag is invalid")
        if self.status is not ArtifactStatus.COMPUTING or token != self.token:
            return self, False
        if not enabled or current_input != self.input_fingerprint:
            reason = DirtyReason.PARAMETERS_CHANGED if current_input != self.input_fingerprint else DirtyReason.UPSTREAM_CHANGED
            return self.mark_dirty(reason), False
        return (replace(self, status=ArtifactStatus.VALID, token=None,
                        artifact_fingerprint=artifact, dirty_reasons=(), diagnostics=()), True)

    def fail(self, token: ComputationToken, diagnostics: tuple[ValidationDiagnostic, ...] = ()) -> tuple["ArtifactState", bool]:
        if self.status is not ArtifactStatus.COMPUTING or token != self.token:
            return self, False
        return replace(self, status=ArtifactStatus.FAILED, token=None, diagnostics=diagnostics), True

    def transition(self, status: ArtifactStatus) -> "ArtifactState":
        """Expose only policy-approved non-compute transitions for validation/tests."""
        if status is ArtifactStatus.DIRTY and self.status in {ArtifactStatus.VALID, ArtifactStatus.FAILED}:
            return self.mark_dirty(DirtyReason.UPSTREAM_CHANGED)
        raise CamInvariantError(f"Invalid artifact transition: {self.status.value} -> {status.value}")

    def to_dict(self) -> dict[str, Any]:
        return {"format": _ARTIFACT_FORMAT, "format_version": _VERSION, "status": self.status.value,
                "generation": self.generation,
                "token": ({"value": str(self.token.value), "generation": self.token.generation} if self.token else None),
                "input_fingerprint": self.input_fingerprint.to_dict() if self.input_fingerprint else None,
                "artifact_fingerprint": self.artifact_fingerprint.to_dict() if self.artifact_fingerprint else None,
                "dirty_reasons": [item.value for item in self.dirty_reasons],
                "diagnostics": [item.to_dict() for item in self.diagnostics]}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ArtifactState":
        _payload(data, _ARTIFACT_FORMAT, {"status", "generation", "token", "input_fingerprint", "artifact_fingerprint", "dirty_reasons", "diagnostics"})
        token = data["token"]
        if token is not None and (not isinstance(token, dict) or set(token) != {"value", "generation"}):
            raise CamValidationError("Computation token payload is malformed")
        try:
            return cls(ArtifactStatus(data["status"]), data["generation"],
                ComputationToken(UUID(token["value"]), token["generation"]) if token else None,
                DependencyFingerprint.from_dict(data["input_fingerprint"]) if data["input_fingerprint"] else None,
                ContentFingerprint.from_dict(data["artifact_fingerprint"]) if data["artifact_fingerprint"] else None,
                tuple(DirtyReason(item) for item in data["dirty_reasons"]),
                tuple(ValidationDiagnostic.from_dict(item) for item in data["diagnostics"]))
        except UnsupportedCamSchemaError:
            raise
        except (TypeError, ValueError) as error:
            raise CamValidationError("Artifact state payload is invalid") from error


@dataclass(frozen=True, slots=True)
class OperationInputSnapshot:
    strategy_key: str
    strategy_version: int
    parameter_fingerprint: ContentFingerprint
    geometry_fingerprints: tuple[tuple[str, ContentFingerprint], ...] = ()
    setup_dependencies: tuple[tuple[str, ContentFingerprint], ...] = ()
    tool_fingerprint: ContentFingerprint | None = None
    machine_fingerprint: ContentFingerprint | None = None
    upstream_artifacts: tuple[tuple[str, ContentFingerprint], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.strategy_key, str) or not _KEY.fullmatch(self.strategy_key) or type(self.strategy_version) is not int or self.strategy_version != 1:
            raise CamValidationError("Input snapshot strategy is invalid")
        if not isinstance(self.parameter_fingerprint, ContentFingerprint):
            raise CamValidationError("Input parameter fingerprint is invalid")
        for field_name in ("geometry_fingerprints", "setup_dependencies", "upstream_artifacts"):
            values = getattr(self, field_name)
            if not isinstance(values, tuple) or any(not isinstance(key, str) or not isinstance(fp, ContentFingerprint) for key, fp in values):
                raise CamValidationError(f"{field_name} is invalid")
            normalized = tuple(sorted(values, key=lambda item: item[0]))
            if len({key for key, _ in normalized}) != len(normalized):
                raise CamInvariantError(f"{field_name} keys must be unique")
            object.__setattr__(self, field_name, normalized)
        for field_name in ("tool_fingerprint", "machine_fingerprint"):
            value = getattr(self, field_name)
            if value is not None and not isinstance(value, ContentFingerprint):
                raise CamValidationError(f"{field_name} is invalid")

    @property
    def fingerprint(self) -> DependencyFingerprint:
        def encoded(values: tuple[tuple[str, ContentFingerprint], ...]) -> list[dict[str, Any]]:
            return [{"key": key, "fingerprint": fp.to_dict()} for key, fp in values]
        return DependencyFingerprint.from_payload({"strategy_key": self.strategy_key,
            "strategy_version": self.strategy_version, "parameters": self.parameter_fingerprint.to_dict(),
            "geometry": encoded(self.geometry_fingerprints), "setup": encoded(self.setup_dependencies),
            "tool": self.tool_fingerprint.to_dict() if self.tool_fingerprint else None,
            "machine": self.machine_fingerprint.to_dict() if self.machine_fingerprint else None,
            "upstream": encoded(self.upstream_artifacts)})


@dataclass(frozen=True, slots=True)
class Operation:
    operation_id: OperationId
    node_id: CamNodeId
    family: OperationFamily
    setup_id: SetupId
    tool_assembly: ToolAssemblyReference
    geometry_inputs: tuple[OperationGeometryInput, ...]
    parameters: OperationParameterSet
    machine_requirement: MachineRequirement | None = None
    enabled: bool = True
    revision: Revision = Revision(0)
    artifact_state: ArtifactState = ArtifactState()
    diagnostics: tuple[ValidationDiagnostic, ...] = ()
    SERIALIZATION_VERSION: ClassVar[int] = _VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.operation_id, OperationId) or not isinstance(self.node_id, CamNodeId) or not isinstance(self.setup_id, SetupId):
            raise CamValidationError("Operation identity is invalid")
        if not isinstance(self.family, OperationFamily) or not isinstance(self.tool_assembly, ToolAssemblyReference):
            raise CamValidationError("Operation family or tool reference is invalid")
        if not isinstance(self.geometry_inputs, tuple) or any(not isinstance(item, OperationGeometryInput) for item in self.geometry_inputs):
            raise CamValidationError("Operation geometry inputs must be an immutable tuple")
        ids = tuple(item.input_id for item in self.geometry_inputs)
        if len(set(ids)) != len(ids):
            raise CamInvariantError("Geometry input IDs must be unique")
        if not isinstance(self.parameters, OperationParameterSet) or self.parameters.strategy_key == "":
            raise CamValidationError("Operation parameters are invalid")
        if self.machine_requirement is not None and not isinstance(self.machine_requirement, MachineRequirement):
            raise CamValidationError("Operation machine requirement is invalid")
        if type(self.enabled) is not bool or not isinstance(self.revision, Revision) or not isinstance(self.artifact_state, ArtifactState):
            raise CamValidationError("Operation state is invalid")
        if not isinstance(self.diagnostics, tuple) or any(not isinstance(item, ValidationDiagnostic) for item in self.diagnostics):
            raise CamValidationError("Operation diagnostics are invalid")

    @property
    def strategy_key(self) -> str:
        return self.parameters.strategy_key

    @property
    def strategy_version(self) -> int:
        return self.parameters.strategy_version

    def with_enabled(self, enabled: bool) -> "Operation":
        if type(enabled) is not bool:
            raise CamValidationError("Operation enabled must be boolean")
        if enabled == self.enabled:
            return self
        state = self.artifact_state.mark_dirty(DirtyReason.UPSTREAM_CHANGED)
        return replace(self, enabled=enabled, revision=self.revision.next(), artifact_state=state)

    def to_dict(self) -> dict[str, Any]:
        return {"format": _OPERATION_FORMAT, "format_version": _VERSION,
                "operation_id": str(self.operation_id), "node_id": str(self.node_id), "family": self.family.value,
                "setup_id": str(self.setup_id), "tool_assembly": self.tool_assembly.to_dict(),
                "machine_requirement": machine_requirement_to_dict(self.machine_requirement) if self.machine_requirement else None,
                "geometry_inputs": [item.to_dict() for item in self.geometry_inputs], "parameters": self.parameters.to_dict(),
                "enabled": self.enabled, "revision": self.revision.to_dict(), "artifact_state": self.artifact_state.to_dict(),
                "diagnostics": [item.to_dict() for item in self.diagnostics]}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Operation":
        _payload(data, _OPERATION_FORMAT, {"operation_id", "node_id", "family", "setup_id", "tool_assembly", "machine_requirement", "geometry_inputs", "parameters", "enabled", "revision", "artifact_state", "diagnostics"})
        if not isinstance(data["geometry_inputs"], list) or not isinstance(data["diagnostics"], list):
            raise CamValidationError("Operation child payloads must be lists")
        try:
            return cls(OperationId.parse(data["operation_id"]), CamNodeId.parse(data["node_id"]), OperationFamily(data["family"]),
                SetupId.parse(data["setup_id"]), ToolAssemblyReference.from_dict(data["tool_assembly"]),
                tuple(OperationGeometryInput.from_dict(item) for item in data["geometry_inputs"]), OperationParameterSet.from_dict(data["parameters"]),
                machine_requirement_from_dict(data["machine_requirement"]) if data["machine_requirement"] is not None else None,
                data["enabled"], Revision.from_dict(data["revision"]), ArtifactState.from_dict(data["artifact_state"]),
                tuple(ValidationDiagnostic.from_dict(item) for item in data["diagnostics"]))
        except UnsupportedCamSchemaError:
            raise
        except (TypeError, ValueError) as error:
            raise CamValidationError("Operation payload is invalid") from error


def validate_operation(operation: Operation, *, geometry_results: tuple[GeometryResolutionResult, ...] = (),
                       tool_status: ToolReferenceStatus = ToolReferenceStatus.VALID,
                       machine_status: MachineCompatibilityStatus = MachineCompatibilityStatus.COMPATIBLE,
                       upstream_valid: bool = True) -> tuple[ValidationDiagnostic, ...]:
    """Build serializable diagnostics from adapter-supplied, native-free evidence."""
    diagnostics: list[ValidationDiagnostic] = []
    if not operation.enabled:
        diagnostics.append(ValidationDiagnostic(DiagnosticSeverity.INFO, DiagnosticCode.OPERATION_DISABLED, "Operation is disabled"))
    result_by_id = {item.reference_id: item for item in geometry_results}
    stale = {GeometryResolutionStatus.STALE, GeometryResolutionStatus.TOPOLOGY_CHANGED}
    for item in operation.geometry_inputs:
        result = result_by_id.get(item.reference.reference_id)
        if result is None and not item.required:
            continue
        if result is None or result.status is not GeometryResolutionStatus.RESOLVED:
            code = DiagnosticCode.GEOMETRY_STALE if result is not None and result.status in stale else DiagnosticCode.GEOMETRY_UNRESOLVED
            diagnostics.append(ValidationDiagnostic(DiagnosticSeverity.ERROR, code, "Geometry input is not resolved", (("input_id", str(item.input_id)),)))
    tool_map = {ToolReferenceStatus.MISSING: DiagnosticCode.TOOL_MISSING, ToolReferenceStatus.STALE: DiagnosticCode.TOOL_STALE,
                ToolReferenceStatus.INCOMPATIBLE_UNIT: DiagnosticCode.TOOL_UNIT_MISMATCH}
    if tool_status in tool_map:
        diagnostics.append(ValidationDiagnostic(DiagnosticSeverity.ERROR, tool_map[tool_status], "Tool assembly reference is not valid"))
    machine_map = {MachineCompatibilityStatus.MISSING_MACHINE: DiagnosticCode.MACHINE_MISSING,
        MachineCompatibilityStatus.REVISION_MISMATCH: DiagnosticCode.MACHINE_STALE,
        MachineCompatibilityStatus.CAPABILITY_MISMATCH: DiagnosticCode.MACHINE_CAPABILITY_MISMATCH,
        MachineCompatibilityStatus.INCOMPATIBLE_UNIT: DiagnosticCode.MACHINE_UNIT_MISMATCH}
    if operation.machine_requirement is not None and machine_status in machine_map:
        diagnostics.append(ValidationDiagnostic(DiagnosticSeverity.ERROR, machine_map[machine_status], "Machine requirement is not satisfied"))
    if not upstream_valid:
        diagnostics.append(ValidationDiagnostic(DiagnosticSeverity.ERROR, DiagnosticCode.UPSTREAM_INVALID, "Upstream operation is invalid"))
    return tuple(diagnostics)
