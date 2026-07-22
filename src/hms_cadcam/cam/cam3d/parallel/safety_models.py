"""Native-free safety contracts for Parallel Finishing Stage 8A.2.2."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any

from hms_cadcam.cam.domain.errors import CamValidationError
from hms_cadcam.cam.domain.ids import GeometryReferenceId, OperationId
from hms_cadcam.cam.domain.operation import (
    DiagnosticCode,
    DiagnosticSeverity,
    ValidationDiagnostic,
)
from hms_cadcam.cam.domain.revision import ContentFingerprint
from hms_cadcam.cam.domain.spatial import Point3
from hms_cadcam.cam.domain.units import LengthUnit


class ParallelSafetyStatus(StrEnum):
    """Five-state fail-closed result used throughout safety validation."""

    SAFE = "safe"
    UNSAFE = "unsafe"
    UNKNOWN = "unknown"
    CANCELLED = "cancelled"
    FAILED = "failed"


class ParallelToolComponent(StrEnum):
    CUTTER = "cutter"
    SHANK = "shank"
    HOLDER = "holder"


class ParallelGeometrySource(StrEnum):
    MACHINING_FACE = "machining_face"
    PROTECTED_PART = "protected_part"
    CHECK_SURFACE = "check_surface"
    FIXTURE = "fixture"
    STOCK = "stock"
    UNKNOWN = "unknown"


class ParallelSafetyMotion(StrEnum):
    CUT = "cut"
    APPROACH = "approach"
    RETRACT = "retract"
    RAPID = "rapid"
    LINK = "link"
    CLEARANCE = "clearance"
    REGION_TRANSITION = "region_transition"


@dataclass(frozen=True, slots=True)
class ParallelSafetyPolicy:
    """Separated collision meanings and deterministic workload guardrails."""

    numeric_epsilon_mm: float
    contact_tolerance_mm: float
    gouge_tolerance_mm: float
    shank_clearance_mm: float
    holder_clearance_mm: float
    rapid_clearance_mm: float
    boundary_clearance_mm: float
    maximum_validation_step_mm: float
    maximum_protected_triangles: int = 500_000
    maximum_collision_candidates: int = 2_000_000
    maximum_narrow_phase_checks: int = 2_000_000
    maximum_swept_subdivisions: int = 128
    maximum_checks_per_motion: int = 250_000
    maximum_total_checks: int = 4_000_000
    maximum_report_items: int = 256
    cancellation_cadence: int = 128
    operational_clearance_source: str = "stage_8a2_2_internal_minimum"
    machine_ready_clearance_verified: bool = False

    def __post_init__(self) -> None:
        float_fields = (
            "numeric_epsilon_mm",
            "contact_tolerance_mm",
            "gouge_tolerance_mm",
            "shank_clearance_mm",
            "holder_clearance_mm",
            "rapid_clearance_mm",
            "boundary_clearance_mm",
            "maximum_validation_step_mm",
        )
        for name in float_fields:
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0.0
            ):
                raise CamValidationError(f"Parallel safety {name} is invalid")
            object.__setattr__(self, name, float(value))
        if self.numeric_epsilon_mm <= 0.0 or self.maximum_validation_step_mm <= 0.0:
            raise CamValidationError("Parallel numeric epsilon/validation step must be positive")
        int_fields = (
            "maximum_protected_triangles",
            "maximum_collision_candidates",
            "maximum_narrow_phase_checks",
            "maximum_swept_subdivisions",
            "maximum_checks_per_motion",
            "maximum_total_checks",
            "maximum_report_items",
            "cancellation_cadence",
        )
        if any(type(getattr(self, name)) is not int or getattr(self, name) <= 0 for name in int_fields):
            raise CamValidationError("Parallel safety guardrails must be positive integers")
        if self.operational_clearance_source not in {
            "stage_8a2_2_internal_minimum",
            "domain_configured",
        }:
            raise CamValidationError("Parallel operational clearance source is invalid")
        if type(self.machine_ready_clearance_verified) is not bool:
            raise CamValidationError("Parallel machine-ready clearance flag is invalid")
        if (
            self.operational_clearance_source == "stage_8a2_2_internal_minimum"
            and self.machine_ready_clearance_verified
        ):
            raise CamValidationError(
                "Internal minimum clearances are not machine-ready verification"
            )

    def to_dict(self) -> dict[str, int | float | str | bool]:
        values: dict[str, int | float | str | bool] = {
            name: getattr(self, name)
            for name in (
                "numeric_epsilon_mm",
                "contact_tolerance_mm",
                "gouge_tolerance_mm",
                "shank_clearance_mm",
                "holder_clearance_mm",
                "rapid_clearance_mm",
                "boundary_clearance_mm",
                "maximum_validation_step_mm",
                "maximum_protected_triangles",
                "maximum_collision_candidates",
                "maximum_narrow_phase_checks",
                "maximum_swept_subdivisions",
                "maximum_checks_per_motion",
                "maximum_total_checks",
                "maximum_report_items",
                "cancellation_cadence",
            )
        }
        values["operational_clearance_source"] = self.operational_clearance_source
        values["machine_ready_clearance_verified"] = (
            self.machine_ready_clearance_verified
        )
        return values


@dataclass(frozen=True, slots=True)
class ParallelSafetyDiagnostic:
    """One reproducible safety finding with motion and geometry provenance."""

    status: ParallelSafetyStatus
    code: DiagnosticCode
    severity: DiagnosticSeverity
    message: str
    operation_id: OperationId
    pass_index: int | None = None
    segment_index: int | None = None
    motion_index: int | None = None
    tool_component: ParallelToolComponent | None = None
    geometry_source: ParallelGeometrySource = ParallelGeometrySource.UNKNOWN
    face_id: GeometryReferenceId | None = None
    protected_region_id: str | None = None
    closest_distance_mm: float | None = None
    penetration_depth_mm: float | None = None
    tolerance_mm: float | None = None
    contact_point: Point3 | None = None
    tool_position: Point3 | None = None
    debug_metadata: tuple[tuple[str, str], ...] = ()
    occurrence_count: int = 1
    first_sample_index: int | None = None
    last_sample_index: int | None = None
    minimum_clearance_mm: float | None = None
    maximum_penetration_mm: float | None = None
    required_clearance_mm: float | None = None
    swept_interval_start: float | None = None
    swept_interval_end: float | None = None

    def __post_init__(self) -> None:
        if self.status is ParallelSafetyStatus.SAFE:
            raise CamValidationError("SAFE is represented by an empty finding set")
        if not isinstance(self.status, ParallelSafetyStatus):
            raise CamValidationError("Parallel safety status is invalid")
        if not isinstance(self.code, DiagnosticCode) or not self.code.value.startswith(
            "parallel.safety."
        ):
            raise CamValidationError("Parallel safety diagnostic code is invalid")
        if not isinstance(self.severity, DiagnosticSeverity):
            raise CamValidationError("Parallel safety severity is invalid")
        if not isinstance(self.message, str) or not self.message.strip():
            raise CamValidationError("Parallel safety message must not be empty")
        if not isinstance(self.operation_id, OperationId):
            raise CamValidationError("Parallel safety operation ID is invalid")
        for name in ("pass_index", "segment_index", "motion_index"):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value < 0):
                raise CamValidationError(f"Parallel safety {name} is invalid")
        if type(self.occurrence_count) is not int or self.occurrence_count <= 0:
            raise CamValidationError("Parallel safety occurrence count is invalid")
        for name in ("first_sample_index", "last_sample_index"):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value < 0):
                raise CamValidationError(f"Parallel safety {name} is invalid")
        if (
            self.first_sample_index is not None
            and self.last_sample_index is not None
            and self.first_sample_index > self.last_sample_index
        ):
            raise CamValidationError("Parallel safety sample interval is invalid")
        if self.tool_component is not None and not isinstance(
            self.tool_component, ParallelToolComponent
        ):
            raise CamValidationError("Parallel safety tool component is invalid")
        if not isinstance(self.geometry_source, ParallelGeometrySource):
            raise CamValidationError("Parallel safety geometry source is invalid")
        if self.face_id is not None and not isinstance(self.face_id, GeometryReferenceId):
            raise CamValidationError("Parallel safety face ID is invalid")
        for name in (
            "closest_distance_mm",
            "penetration_depth_mm",
            "tolerance_mm",
            "maximum_penetration_mm",
            "required_clearance_mm",
        ):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0.0
            ):
                raise CamValidationError(f"Parallel safety {name} is invalid")
            if value is not None:
                object.__setattr__(self, name, float(value))
        clearance = self.minimum_clearance_mm
        if clearance is not None and (
            isinstance(clearance, bool)
            or not isinstance(clearance, (int, float))
            or not math.isfinite(clearance)
        ):
            raise CamValidationError("Parallel safety minimum clearance is invalid")
        if clearance is not None:
            object.__setattr__(self, "minimum_clearance_mm", float(clearance))
        for name in ("swept_interval_start", "swept_interval_end"):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or not 0.0 <= value <= 1.0
            ):
                raise CamValidationError(f"Parallel safety {name} is invalid")
            if value is not None:
                object.__setattr__(self, name, float(value))
        if (
            self.swept_interval_start is not None
            and self.swept_interval_end is not None
            and self.swept_interval_start > self.swept_interval_end
        ):
            raise CamValidationError("Parallel safety swept interval is invalid")
        for name in ("contact_point", "tool_position"):
            point = getattr(self, name)
            if point is not None and (
                not isinstance(point, Point3) or point.unit is not LengthUnit.MM
            ):
                raise CamValidationError(f"Parallel safety {name} is invalid")
        try:
            metadata = tuple(sorted(self.debug_metadata))
        except TypeError as error:
            raise CamValidationError("Parallel safety debug metadata is invalid") from error
        if any(
            not isinstance(item, tuple)
            or len(item) != 2
            or not all(isinstance(value, str) and value for value in item)
            for item in metadata
        ) or len({key for key, _value in metadata}) != len(metadata):
            raise CamValidationError("Parallel safety debug metadata is invalid")
        object.__setattr__(self, "debug_metadata", metadata)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "code": self.code.value,
            "severity": self.severity.value,
            "message": self.message.strip(),
            "operation_id": str(self.operation_id),
            "pass_index": self.pass_index,
            "segment_index": self.segment_index,
            "motion_index": self.motion_index,
            "tool_component": (
                self.tool_component.value if self.tool_component is not None else None
            ),
            "geometry_source": self.geometry_source.value,
            "face_id": str(self.face_id) if self.face_id is not None else None,
            "protected_region_id": self.protected_region_id,
            "closest_distance_mm": self.closest_distance_mm,
            "penetration_depth_mm": self.penetration_depth_mm,
            "tolerance_mm": self.tolerance_mm,
            "contact_point": (
                self.contact_point.to_dict() if self.contact_point is not None else None
            ),
            "tool_position": (
                self.tool_position.to_dict() if self.tool_position is not None else None
            ),
            "debug_metadata": [
                {"key": key, "value": value} for key, value in self.debug_metadata
            ],
            "occurrence_count": self.occurrence_count,
            "first_sample_index": self.first_sample_index,
            "last_sample_index": self.last_sample_index,
            "minimum_clearance_mm": self.minimum_clearance_mm,
            "maximum_penetration_mm": self.maximum_penetration_mm,
            "required_clearance_mm": self.required_clearance_mm,
            "swept_interval": (
                [self.swept_interval_start, self.swept_interval_end]
                if self.swept_interval_start is not None
                and self.swept_interval_end is not None
                else None
            ),
        }

    def to_validation_diagnostic(self) -> ValidationDiagnostic:
        context: list[tuple[str, str]] = [
            ("safety_status", self.status.value),
            ("geometry_source", self.geometry_source.value),
        ]
        optional = (
            ("pass_index", self.pass_index),
            ("segment_index", self.segment_index),
            ("motion_index", self.motion_index),
            ("tool_component", self.tool_component.value if self.tool_component else None),
            ("face_id", str(self.face_id) if self.face_id else None),
            ("closest_distance_mm", self.closest_distance_mm),
            ("penetration_depth_mm", self.penetration_depth_mm),
            ("tolerance_mm", self.tolerance_mm),
            ("occurrence_count", self.occurrence_count),
            ("first_sample_index", self.first_sample_index),
            ("last_sample_index", self.last_sample_index),
            ("minimum_clearance_mm", self.minimum_clearance_mm),
            ("maximum_penetration_mm", self.maximum_penetration_mm),
            ("required_clearance_mm", self.required_clearance_mm),
        )
        context.extend((key, str(value)) for key, value in optional if value is not None)
        context.extend(self.debug_metadata)
        return ValidationDiagnostic(self.severity, self.code, self.message, tuple(context))


@dataclass(frozen=True, slots=True)
class ParallelSafetyStatistics:
    motion_count: int = 0
    protected_triangle_count: int = 0
    broad_phase_candidate_count: int = 0
    narrow_phase_check_count: int = 0
    swept_subdivision_count: int = 0

    def __post_init__(self) -> None:
        if any(type(value) is not int or value < 0 for value in self.to_dict().values()):
            raise CamValidationError("Parallel safety statistics are invalid")

    def to_dict(self) -> dict[str, int]:
        return {
            "motion_count": self.motion_count,
            "protected_triangle_count": self.protected_triangle_count,
            "broad_phase_candidate_count": self.broad_phase_candidate_count,
            "narrow_phase_check_count": self.narrow_phase_check_count,
            "swept_subdivision_count": self.swept_subdivision_count,
        }


@dataclass(frozen=True, slots=True)
class ParallelSafetyReport:
    """Complete result bound to one calculation and algorithm revision."""

    status: ParallelSafetyStatus
    operation_id: OperationId
    calculation_id: str
    algorithm_version: int
    policy: ParallelSafetyPolicy
    diagnostics: tuple[ParallelSafetyDiagnostic, ...]
    statistics: ParallelSafetyStatistics
    checked_components: tuple[ParallelToolComponent, ...]
    unverified_components: tuple[ParallelToolComponent, ...]
    holder_state: str
    tool_assembly_fingerprint: ContentFingerprint
    safety_scope: str
    capabilities: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.status, ParallelSafetyStatus):
            raise CamValidationError("Parallel safety report status is invalid")
        if not isinstance(self.operation_id, OperationId):
            raise CamValidationError("Parallel safety report operation is invalid")
        if not isinstance(self.calculation_id, str) or not self.calculation_id.strip():
            raise CamValidationError("Parallel safety calculation ID is invalid")
        if type(self.algorithm_version) is not int or self.algorithm_version <= 0:
            raise CamValidationError("Parallel safety algorithm version is invalid")
        if not isinstance(self.policy, ParallelSafetyPolicy) or not isinstance(
            self.statistics, ParallelSafetyStatistics
        ):
            raise CamValidationError("Parallel safety report policy/statistics are invalid")
        if any(not isinstance(item, ParallelSafetyDiagnostic) for item in self.diagnostics):
            raise CamValidationError("Parallel safety report diagnostics are invalid")
        expected = _aggregate_status(self.diagnostics)
        if self.status is not expected:
            raise CamValidationError("Parallel safety report status does not match findings")
        checked = tuple(sorted(self.checked_components, key=lambda item: item.value))
        unverified = tuple(sorted(self.unverified_components, key=lambda item: item.value))
        if (
            len(set(checked)) != len(checked)
            or len(set(unverified)) != len(unverified)
            or set(checked) & set(unverified)
        ):
            raise CamValidationError("Parallel safety component scope is invalid")
        if self.holder_state not in {
            "geometry_faithful",
            "declared_absent",
            "missing",
            "reference_invalid",
        }:
            raise CamValidationError("Parallel safety holder state is invalid")
        if self.safety_scope not in {
            "declared_assembly_holder_verified",
            "declared_assembly_holder_absent",
            "incomplete_tool_assembly",
        }:
            raise CamValidationError("Parallel safety scope is invalid")
        if not isinstance(self.tool_assembly_fingerprint, ContentFingerprint):
            raise CamValidationError("Parallel safety assembly fingerprint is invalid")
        object.__setattr__(self, "checked_components", checked)
        object.__setattr__(self, "unverified_components", unverified)
        capabilities = tuple(sorted(self.capabilities))
        if any(
            not isinstance(item, tuple)
            or len(item) != 2
            or not all(isinstance(value, str) and value for value in item)
            for item in capabilities
        ) or len({key for key, _value in capabilities}) != len(capabilities):
            raise CamValidationError("Parallel safety capabilities are invalid")
        object.__setattr__(self, "capabilities", capabilities)

    @property
    def fingerprint(self) -> ContentFingerprint:
        return ContentFingerprint.from_payload(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": "HMS_PARALLEL_SAFETY_REPORT",
            "format_version": 1,
            "status": self.status.value,
            "operation_id": str(self.operation_id),
            "calculation_id": self.calculation_id,
            "algorithm_version": self.algorithm_version,
            "policy": self.policy.to_dict(),
            "diagnostics": [item.to_dict() for item in self.diagnostics],
            "statistics": self.statistics.to_dict(),
            "checked_components": [item.value for item in self.checked_components],
            "unverified_components": [item.value for item in self.unverified_components],
            "holder_state": self.holder_state,
            "tool_assembly_fingerprint": self.tool_assembly_fingerprint.to_dict(),
            "safety_scope": self.safety_scope,
            "capabilities": [
                {"key": key, "value": value} for key, value in self.capabilities
            ],
        }


def parallel_clearance_is_satisfied(
    actual_clearance_mm: float,
    required_clearance_mm: float,
) -> bool:
    """Apply the operational boundary without substituting numeric epsilon."""
    for name, value in (
        ("actual clearance", actual_clearance_mm),
        ("required clearance", required_clearance_mm),
    ):
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise CamValidationError(f"Parallel {name} is invalid")
    if required_clearance_mm < 0.0:
        raise CamValidationError("Parallel required clearance is invalid")
    return float(actual_clearance_mm) > float(required_clearance_mm)


def aggregate_parallel_safety_diagnostics(
    calculation_id: str,
    diagnostics: tuple[ParallelSafetyDiagnostic, ...],
) -> tuple[ParallelSafetyDiagnostic, ...]:
    """Aggregate repeated samples while preserving distinct collision identities."""
    if not isinstance(calculation_id, str) or not calculation_id.strip():
        raise CamValidationError("Parallel safety calculation ID is invalid")
    values: dict[tuple[object, ...], ParallelSafetyDiagnostic] = {}
    for diagnostic in diagnostics:
        if not isinstance(diagnostic, ParallelSafetyDiagnostic):
            raise CamValidationError("Parallel safety diagnostic is invalid")
        key = _aggregation_key(calculation_id, diagnostic)
        existing = values.get(key)
        values[key] = (
            diagnostic if existing is None else _merge_diagnostics(existing, diagnostic)
        )
    return tuple(
        sorted(
            values.values(),
            key=lambda item: (
                item.motion_index if item.motion_index is not None else -1,
                item.pass_index if item.pass_index is not None else -1,
                item.segment_index if item.segment_index is not None else -1,
                item.tool_component.value if item.tool_component else "",
                item.geometry_source.value,
                str(item.face_id) if item.face_id else item.protected_region_id or "",
                item.code.value,
            ),
        )
    )


def _aggregation_key(
    calculation_id: str,
    diagnostic: ParallelSafetyDiagnostic,
) -> tuple[object, ...]:
    return (
        calculation_id,
        diagnostic.operation_id,
        diagnostic.pass_index,
        diagnostic.segment_index,
        diagnostic.motion_index,
        diagnostic.tool_component,
        diagnostic.geometry_source,
        diagnostic.face_id,
        diagnostic.protected_region_id,
        diagnostic.code,
    )


def _merge_diagnostics(
    first: ParallelSafetyDiagnostic,
    second: ParallelSafetyDiagnostic,
) -> ParallelSafetyDiagnostic:
    first_penetration = first.maximum_penetration_mm or first.penetration_depth_mm or 0.0
    second_penetration = second.maximum_penetration_mm or second.penetration_depth_mm or 0.0
    first_clearance = (
        first.minimum_clearance_mm
        if first.minimum_clearance_mm is not None
        else math.inf
    )
    second_clearance = (
        second.minimum_clearance_mm
        if second.minimum_clearance_mm is not None
        else math.inf
    )
    primary = (
        second
        if (second_penetration, -second_clearance)
        > (first_penetration, -first_clearance)
        else first
    )
    sample_indices = tuple(
        value
        for value in (
            first.first_sample_index,
            first.last_sample_index,
            second.first_sample_index,
            second.last_sample_index,
        )
        if value is not None
    )
    interval_starts = tuple(
        value
        for value in (first.swept_interval_start, second.swept_interval_start)
        if value is not None
    )
    interval_ends = tuple(
        value
        for value in (first.swept_interval_end, second.swept_interval_end)
        if value is not None
    )
    clearances = tuple(
        value
        for value in (first.minimum_clearance_mm, second.minimum_clearance_mm)
        if value is not None
    )
    return replace(
        primary,
        occurrence_count=first.occurrence_count + second.occurrence_count,
        first_sample_index=min(sample_indices) if sample_indices else None,
        last_sample_index=max(sample_indices) if sample_indices else None,
        minimum_clearance_mm=min(clearances) if clearances else None,
        maximum_penetration_mm=max(first_penetration, second_penetration),
        swept_interval_start=min(interval_starts) if interval_starts else None,
        swept_interval_end=max(interval_ends) if interval_ends else None,
    )


def _aggregate_status(
    diagnostics: tuple[ParallelSafetyDiagnostic, ...],
) -> ParallelSafetyStatus:
    if not diagnostics:
        return ParallelSafetyStatus.SAFE
    precedence = (
        ParallelSafetyStatus.FAILED,
        ParallelSafetyStatus.CANCELLED,
        ParallelSafetyStatus.UNSAFE,
        ParallelSafetyStatus.UNKNOWN,
    )
    values = {item.status for item in diagnostics}
    return next(status for status in precedence if status in values)
