"""Z-Level v2 safety adapter built on the shared Stage 8A.2.2 engine.

This module deliberately contains provenance, scope, hash and gate policy only.
Collision semantics (tool assembly, swept broad/narrow phase, cancellation and
diagnostic aggregation) remain in ``cam3d.parallel.safety``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Callable

from hms_cadcam.cam.cam3d.context import Cam3DCalculationContext
from hms_cadcam.cam.cam3d.parallel.safety import (
    validate_parallel_candidate_safety,
)
from hms_cadcam.cam.cam3d.parallel.safety_models import (
    ParallelGeometrySource,
    ParallelSafetyDiagnostic,
    ParallelSafetyPolicy,
    ParallelSafetyReport,
    ParallelSafetyStatistics,
    ParallelSafetyStatus,
    ParallelToolComponent,
)
from hms_cadcam.cam.cam3d.zlevel.models import (
    ZLevelContour,
    ZLevelFinishingError,
    ZLevelMachiningFrame,
    ZLevelPreview,
    Z_LEVEL_FINISHING_ALGORITHM_VERSION,
    Z_LEVEL_FINISHING_STRATEGY_KEY,
    Z_LEVEL_FINISHING_STRATEGY_VERSION,
)
from hms_cadcam.cam.domain.ids import GeometryReferenceId
from hms_cadcam.cam.domain.operation import (
    DiagnosticCode,
    DiagnosticSeverity,
    Operation,
    ValidationDiagnostic,
)
from hms_cadcam.cam.domain.revision import ContentFingerprint
from hms_cadcam.cam.domain.spatial import Point3, Vector3, WcsFrame
from hms_cadcam.cam.domain.tooling import HolderDefinition, ToolAssembly, ToolDefinition
from hms_cadcam.cam.toolpath.events import LinearMove, MotionClass, RapidMove
from hms_cadcam.cam.toolpath.model import ToolpathArtifact


class ZLevelScopeStatus(StrEnum):
    CHECKED = "CHECKED"
    NOT_PRESENT = "NOT_PRESENT"
    NOT_PROVIDED = "NOT_PROVIDED"
    INVALID = "INVALID"
    UNSUPPORTED = "UNSUPPORTED"
    UNVERIFIED = "UNVERIFIED"


@dataclass(frozen=True, slots=True)
class ZLevelSafetyScopeEntry:
    """One explicit safety-scope item; absence is never reported as SAFE."""

    name: str
    status: ZLevelScopeStatus
    fingerprint: str | None = None
    detail: str = ""

    def __post_init__(self) -> None:
        if not self.name or not isinstance(self.status, ZLevelScopeStatus):
            raise ValueError("Z-Level safety scope entry is invalid")
        if self.fingerprint is not None and not _is_sha256(self.fingerprint):
            raise ValueError("Z-Level safety scope fingerprint is invalid")

    def to_dict(self) -> dict[str, str | None]:
        return {
            "name": self.name,
            "status": self.status.value,
            "fingerprint": self.fingerprint,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class ZLevelSafetyDiagnostic:
    """Strategy-provenanced diagnostic that may retain a legacy shared code."""

    status: ParallelSafetyStatus
    code: DiagnosticCode
    message: str
    pass_index: int | None = None
    segment_index: int | None = None
    motion_index: int | None = None
    component: ParallelToolComponent | None = None
    geometry_source: ParallelGeometrySource | None = None
    candidate_geometry: str | None = None
    classification: str = "conservative"
    debug_metadata: tuple[tuple[str, str], ...] = ()
    path_parameter: float | None = None
    broad_phase_result: str = "not_tested"
    narrow_phase_result: str = "not_tested"
    closest_distance_mm: float | None = None
    penetration_depth_mm: float | None = None
    occurrence_count: int = 1
    first_sample_index: int | None = None
    last_sample_index: int | None = None
    minimum_clearance_mm: float | None = None
    maximum_penetration_mm: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, ParallelSafetyStatus):
            raise ValueError("Z-Level diagnostic status is invalid")
        if not isinstance(self.code, DiagnosticCode):
            raise ValueError("Z-Level diagnostic code is invalid")
        if not self.message.strip():
            raise ValueError("Z-Level diagnostic message is empty")
        for value in (self.pass_index, self.segment_index, self.motion_index):
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError("Z-Level diagnostic provenance is invalid")
        if self.component is not None and not isinstance(self.component, ParallelToolComponent):
            raise ValueError("Z-Level diagnostic component is invalid")
        if self.geometry_source is not None and not isinstance(
            self.geometry_source, ParallelGeometrySource
        ):
            raise ValueError("Z-Level diagnostic geometry source is invalid")
        if self.classification not in {"exact", "conservative", "ambiguous"}:
            raise ValueError("Z-Level diagnostic classification is invalid")
        if self.path_parameter is not None and not 0.0 <= self.path_parameter <= 1.0:
            raise ValueError("Z-Level diagnostic path parameter is invalid")
        if self.broad_phase_result not in {"overlap", "not_tested"}:
            raise ValueError("Z-Level diagnostic broad-phase result is invalid")
        if self.narrow_phase_result not in {"exact", "conservative", "ambiguous", "not_tested"}:
            raise ValueError("Z-Level diagnostic narrow-phase result is invalid")
        if type(self.occurrence_count) is not int or self.occurrence_count <= 0:
            raise ValueError("Z-Level diagnostic occurrence count is invalid")
        object.__setattr__(self, "debug_metadata", tuple(sorted(self.debug_metadata)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "code": self.code.value,
            "message": self.message,
            "pass_index": self.pass_index,
            "segment_index": self.segment_index,
            "motion_index": self.motion_index,
            "component": self.component.value if self.component else None,
            "geometry_source": self.geometry_source.value if self.geometry_source else None,
            "candidate_geometry": self.candidate_geometry,
            "classification": self.classification,
            "path_parameter": self.path_parameter,
            "broad_phase_result": self.broad_phase_result,
            "narrow_phase_result": self.narrow_phase_result,
            "closest_distance_mm": self.closest_distance_mm,
            "penetration_depth_mm": self.penetration_depth_mm,
            "occurrence_count": self.occurrence_count,
            "first_sample_index": self.first_sample_index,
            "last_sample_index": self.last_sample_index,
            "minimum_clearance_mm": self.minimum_clearance_mm,
            "maximum_penetration_mm": self.maximum_penetration_mm,
            "debug_metadata": [{"key": key, "value": value} for key, value in self.debug_metadata],
        }

    def to_validation_diagnostic(self) -> ValidationDiagnostic:
        context = [
            ("safety_status", self.status.value),
            ("classification", self.classification),
        ]
        optional = (
            ("pass_index", self.pass_index),
            ("segment_index", self.segment_index),
            ("motion_index", self.motion_index),
            ("component", self.component.value if self.component else None),
            ("geometry_source", self.geometry_source.value if self.geometry_source else None),
            ("candidate_geometry", self.candidate_geometry),
            ("path_parameter", self.path_parameter),
            ("broad_phase_result", self.broad_phase_result),
            ("narrow_phase_result", self.narrow_phase_result),
            ("closest_distance_mm", self.closest_distance_mm),
            ("penetration_depth_mm", self.penetration_depth_mm),
            ("occurrence_count", self.occurrence_count),
        )
        context.extend((key, str(value)) for key, value in optional if value is not None)
        context.extend(self.debug_metadata)
        return ValidationDiagnostic(
            DiagnosticSeverity.ERROR,
            self.code,
            self.message,
            tuple(context),
        )


@dataclass(frozen=True, slots=True)
class ZLevelSafetyStatistics:
    """Deterministic counters exposed by the Z-Level safety contract."""

    motion_count: int
    candidate_geometry_count: int
    cutter_checks: int
    shank_checks: int
    holder_checks: int
    swept_subdivisions: int
    exact_checks: int
    conservative_checks: int
    collision_occurrences: int
    aggregated_diagnostics: int
    rejected_motions: int
    broad_phase_candidate_count: int = 0
    narrow_phase_check_count: int = 0

    def __post_init__(self) -> None:
        if any(type(value) is not int or value < 0 for value in self.to_dict().values()):
            raise ValueError("Z-Level safety counters are invalid")

    @property
    def swept_subdivision_count(self) -> int:
        return self.swept_subdivisions

    def to_dict(self) -> dict[str, int]:
        return {
            "motion_count": self.motion_count,
            "candidate_geometry_count": self.candidate_geometry_count,
            "cutter_checks": self.cutter_checks,
            "shank_checks": self.shank_checks,
            "holder_checks": self.holder_checks,
            "swept_subdivisions": self.swept_subdivisions,
            "swept_subdivision_count": self.swept_subdivision_count,
            "exact_checks": self.exact_checks,
            "conservative_checks": self.conservative_checks,
            "collision_occurrences": self.collision_occurrences,
            "aggregated_diagnostics": self.aggregated_diagnostics,
            "rejected_motions": self.rejected_motions,
            "broad_phase_candidate_count": self.broad_phase_candidate_count,
            "narrow_phase_check_count": self.narrow_phase_check_count,
        }


@dataclass(frozen=True, slots=True)
class ZLevelSafetyReport:
    """Complete Z-Level v2 report with a shared-engine provenance envelope."""

    status: ParallelSafetyStatus
    operation_id: Any
    calculation_id: str
    strategy: str
    algorithm_version: int
    payload_version: int
    shared_report: ParallelSafetyReport
    diagnostics: tuple[ZLevelSafetyDiagnostic, ...]
    safety_scope: tuple[ZLevelSafetyScopeEntry, ...]
    statistics: ZLevelSafetyStatistics
    tool_assembly_fingerprint: ContentFingerprint
    holder_state: str
    linking_decision: str = "retract_clearance"
    machine_ready_clearance_verified: bool = False

    def __post_init__(self) -> None:
        if self.strategy != Z_LEVEL_FINISHING_STRATEGY_KEY:
            raise ValueError("Z-Level safety strategy is invalid")
        if self.algorithm_version != Z_LEVEL_FINISHING_ALGORITHM_VERSION:
            raise ValueError("Z-Level safety algorithm version is invalid")
        if self.payload_version != Z_LEVEL_FINISHING_STRATEGY_VERSION:
            raise ValueError("Z-Level safety payload version is invalid")
        if not isinstance(self.shared_report, ParallelSafetyReport):
            raise ValueError("Shared safety report is invalid")
        if not isinstance(self.tool_assembly_fingerprint, ContentFingerprint):
            raise ValueError("Z-Level assembly fingerprint is invalid")
        if self.linking_decision not in {"retract_clearance", "direct_safe", "direct_rejected_fallback"}:
            raise ValueError("Z-Level linking decision is invalid")
        if self.machine_ready_clearance_verified:
            raise ValueError("Machine-ready clearance must remain unverified")
        expected = _aggregate_status(item.status for item in self.diagnostics)
        if self.status is not expected:
            raise ValueError("Z-Level safety report status does not match diagnostics")

    @property
    def fingerprint(self) -> ContentFingerprint:
        payload = self.to_dict()
        # Calculation IDs are runtime correlation tokens.  They remain in the
        # serialized report for audit, but must not invalidate an otherwise
        # identical safety/artifact contract across deterministic recomputes.
        payload.pop("calculation_id", None)
        shared_report = dict(payload["shared_report"])
        shared_report.pop("calculation_id", None)
        payload["shared_report"] = shared_report
        return ContentFingerprint.from_payload(payload)

    @property
    def checked_components(self) -> tuple[ParallelToolComponent, ...]:
        return self.shared_report.checked_components

    @property
    def unverified_components(self) -> tuple[ParallelToolComponent, ...]:
        return self.shared_report.unverified_components

    @property
    def capabilities(self) -> tuple[tuple[str, str], ...]:
        return self.shared_report.capabilities

    @property
    def scope_fingerprint(self) -> ContentFingerprint:
        return ContentFingerprint.from_payload(
            [entry.to_dict() for entry in self.safety_scope]
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": "HMS_Z_LEVEL_SAFETY_REPORT",
            "format_version": 1,
            "strategy": self.strategy,
            "algorithm_version": self.algorithm_version,
            "payload_version": self.payload_version,
            "status": self.status.value,
            "operation_id": str(self.operation_id),
            "calculation_id": self.calculation_id,
            "shared_report": self.shared_report.to_dict(),
            "diagnostics": [item.to_dict() for item in self.diagnostics],
            "safety_scope": [item.to_dict() for item in self.safety_scope],
            "statistics": self.statistics.to_dict(),
            "tool_assembly_fingerprint": self.tool_assembly_fingerprint.to_dict(),
            "holder_state": self.holder_state,
            "linking_decision": self.linking_decision,
            "machine_ready_clearance_verified": self.machine_ready_clearance_verified,
        }


def build_z_level_safety_policy(
    context: Cam3DCalculationContext,
    *,
    tool_radius_mm: float,
) -> ParallelSafetyPolicy:
    """Use the shared policy with a shorter deterministic motion guardrail."""

    from hms_cadcam.cam.cam3d.parallel.safety import build_parallel_safety_policy

    return build_parallel_safety_policy(context, tool_radius_mm=tool_radius_mm)


def validate_z_level_candidate_safety(
    *,
    operation: Operation,
    context: Cam3DCalculationContext,
    tool: ToolDefinition,
    assembly: ToolAssembly,
    holder: HolderDefinition | None,
    artifact: ToolpathArtifact,
    preview: ZLevelPreview,
    cancellation: Callable[[], bool] | None = None,
    policy: ParallelSafetyPolicy | None = None,
    linking_decision: str = "retract_clearance",
    protected_geometry_required: bool = False,
) -> ZLevelSafetyReport:
    """Validate a Z-Level candidate through the shared safety pipeline."""

    _checkpoint(cancellation)
    local = _validate_preview_contract(
        operation=operation,
        context=context,
        preview=preview,
        cancellation=cancellation,
    )
    shared = validate_parallel_candidate_safety(
        operation=operation,
        context=context,
        tool=tool,
        assembly=assembly,
        holder=holder,
        artifact=artifact,
        preview=preview,  # Z-Level preview intentionally implements the shared shape
        cancellation=cancellation,
        policy=policy,
    )
    _checkpoint(cancellation)
    translated = tuple(
        _translate_shared(item, holder_state=shared.holder_state)
        for item in shared.diagnostics
    )
    boundary = _boundary_findings(
        operation=operation,
        context=context,
        preview=preview,
        artifact=artifact,
        cancellation=cancellation,
    )
    required_scope_findings: tuple[ZLevelSafetyDiagnostic, ...] = ()
    if protected_geometry_required and context.machining_zone.check_surfaces is None:
        required_scope_findings = (
            _local_diagnostic(
                ParallelSafetyStatus.UNKNOWN,
                DiagnosticCode.Z_LEVEL_SAFETY_MISSING_PROTECTED_GEOMETRY,
                "Safety scope yêu cầu protected geometry nhưng geometry chưa được cung cấp.",
            ),
        )
    diagnostics = (*local, *translated, *boundary, *required_scope_findings)
    status = _aggregate_status(item.status for item in diagnostics)
    scope = _build_scope(context, artifact, shared, holder)
    stats = _statistics(shared, diagnostics)
    return ZLevelSafetyReport(
        status,
        operation.operation_id,
        str(artifact.computation_token.value),
        Z_LEVEL_FINISHING_STRATEGY_KEY,
        Z_LEVEL_FINISHING_ALGORITHM_VERSION,
        Z_LEVEL_FINISHING_STRATEGY_VERSION,
        shared,
        diagnostics,
        scope,
        stats,
        shared.tool_assembly_fingerprint,
        shared.holder_state,
        linking_decision,
        False,
    )


def z_level_artifact_contract_hash(
    *,
    operation: Operation,
    context: Cam3DCalculationContext,
    parameters: Any,
    tool: ToolDefinition,
    assembly: ToolAssembly,
    holder: HolderDefinition | None,
    candidate_artifact: ToolpathArtifact,
    safety_report: ZLevelSafetyReport,
    algorithm_version: int = Z_LEVEL_FINISHING_ALGORITHM_VERSION,
    machine_ready_clearance_verified: bool = False,
) -> ContentFingerprint:
    """Hash all safety-sensitive inputs without a self-referential marker."""

    if type(algorithm_version) is not int or algorithm_version <= 0:
        raise ValueError("Z-Level artifact algorithm version is invalid")
    if type(machine_ready_clearance_verified) is not bool:
        raise ValueError("Z-Level machine-ready clearance state is invalid")
    zone = context.machining_zone
    frame = parameters.machining_frame or ZLevelMachiningFrame(
        zone.wcs.origin,
        zone.wcs.x_axis,
        zone.wcs.y_axis,
        zone.wcs.z_axis,
    )
    selected = tuple(
        sorted(
            (item.fingerprint.digest, item.fingerprint.to_dict())
            for item in zone.part_surfaces.selection.surfaces
        )
    )
    protected = tuple(
        sorted(
            (item.fingerprint.digest, item.fingerprint.to_dict())
            for item in zone.all_surfaces()
        )
    )
    payload = {
        "strategy": Z_LEVEL_FINISHING_STRATEGY_KEY,
        "algorithm_version": algorithm_version,
        "payload_version": Z_LEVEL_FINISHING_STRATEGY_VERSION,
        "operation_revision": operation.revision.to_dict(),
        "selected_face_fingerprints": selected,
        "machining_frame": frame.to_dict(),
        "effective_parameters": parameters.to_operation_parameters().to_dict(),
        "effective_parameter_hash": parameters.fingerprint.to_dict(),
        "tool_fingerprint": tool.content_fingerprint.to_dict(),
        "shank_fingerprint": ContentFingerprint.from_payload(tool.shank.to_dict()).to_dict(),
        "holder_fingerprint": (
            holder.content_fingerprint.to_dict() if holder is not None else None
        ),
        "holder_state": safety_report.holder_state,
        "assembly_fingerprint": ContentFingerprint.from_payload(assembly.to_dict()).to_dict(),
        "safety_scope": [entry.to_dict() for entry in safety_report.safety_scope],
        "safety_scope_hash": safety_report.scope_fingerprint.to_dict(),
        "protected_geometry_fingerprints": protected,
        "stock_fingerprint": None,
        "fixture_fingerprints": tuple(
            item.fingerprint.to_dict()
            for item in zone.fixture_surfaces.selection.surfaces
        )
        if zone.fixture_surfaces is not None
        else (),
        "toolpath_ir_hash": candidate_artifact.artifact_fingerprint.to_dict(),
        "safety_report_hash": safety_report.fingerprint.to_dict(),
        "machine_ready_clearance_verified": machine_ready_clearance_verified,
    }
    return ContentFingerprint.from_payload(payload)


def _validate_preview_contract(
    *,
    operation: Operation,
    context: Cam3DCalculationContext,
    preview: ZLevelPreview,
    cancellation: Callable[[], bool] | None,
) -> tuple[ZLevelSafetyDiagnostic, ...]:
    """Check contact/level/provenance again immediately before collision work."""

    values: list[ZLevelSafetyDiagnostic] = []
    selected = {
        item.geometry.reference_id
        for item in context.machining_zone.part_surfaces.selection.surfaces
    }
    previous_level: float | None = None
    seen_levels: set[float] = set()
    for level_pass in preview.passes:
        _checkpoint(cancellation)
        if previous_level is not None and level_pass.level > previous_level:
            values.append(
                _local_diagnostic(
                    ParallelSafetyStatus.UNKNOWN,
                    DiagnosticCode.Z_LEVEL_UNSUPPORTED_TOPOLOGY,
                    "Thứ tự level Z-Level không đơn điệu top-to-bottom.",
                    pass_index=level_pass.pass_index,
                )
            )
        if level_pass.level in seen_levels:
            values.append(
                _local_diagnostic(
                    ParallelSafetyStatus.UNKNOWN,
                    DiagnosticCode.Z_LEVEL_UNSUPPORTED_TOPOLOGY,
                    "Z-Level có level trùng lặp.",
                    pass_index=level_pass.pass_index,
                )
            )
        seen_levels.add(level_pass.level)
        previous_level = level_pass.level
        for contour in level_pass.segments:
            for point_index, point in enumerate(contour.points):
                if not point.source_surface_ids or not set(point.source_surface_ids) <= selected:
                    values.append(
                        _local_diagnostic(
                            ParallelSafetyStatus.UNKNOWN,
                            DiagnosticCode.Z_LEVEL_INVALID_CONTACT,
                            "Contact Z-Level thiếu face provenance thuộc selected scope.",
                            pass_index=contour.pass_index,
                            segment_index=contour.segment_index,
                        )
                    )
                if not _finite_point(point.contact_point) or not _finite_point(
                    point.tool_center_point
                ):
                    values.append(
                        _local_diagnostic(
                            ParallelSafetyStatus.UNKNOWN,
                            DiagnosticCode.Z_LEVEL_INVALID_CONTACT,
                            "Contact/tool-center Z-Level không hữu hạn.",
                            pass_index=contour.pass_index,
                            segment_index=contour.segment_index,
                        )
                    )
                if not math.isclose(
                    preview.frame.coordinates(point.tool_center_point)[2],
                    level_pass.level,
                    rel_tol=0.0,
                    abs_tol=context.tolerance_policy.contact_tolerance,
                ):
                    values.append(
                        _local_diagnostic(
                            ParallelSafetyStatus.UNKNOWN,
                            DiagnosticCode.Z_LEVEL_INVALID_CONTACT,
                            "Tool-center W height không đúng requested level.",
                            pass_index=contour.pass_index,
                            segment_index=contour.segment_index,
                        )
                    )
                if point.boundary_classification.value == "ambiguous":
                    values.append(
                        _local_diagnostic(
                            ParallelSafetyStatus.UNKNOWN,
                            DiagnosticCode.Z_LEVEL_SAFETY_AMBIGUOUS_CONTACT,
                            "Contact gần trim/seam có phân loại ambiguous.",
                            pass_index=contour.pass_index,
                            segment_index=contour.segment_index,
                            classification="ambiguous",
                        )
                    )
                if point.surface_normal.magnitude <= 1.0e-9 or not all(
                    math.isfinite(value)
                    for value in (
                        point.surface_normal.x,
                        point.surface_normal.y,
                        point.surface_normal.z,
                    )
                ):
                    values.append(
                        _local_diagnostic(
                            ParallelSafetyStatus.UNKNOWN,
                            DiagnosticCode.Z_LEVEL_INVALID_NORMAL,
                            "Differential normal Z-Level suy biến hoặc không hữu hạn.",
                            pass_index=contour.pass_index,
                            segment_index=contour.segment_index,
                        )
                    )
                if point.allowance_deviation_mm > context.tolerance_policy.contact_tolerance:
                    values.append(
                        _local_diagnostic(
                            ParallelSafetyStatus.UNKNOWN,
                            DiagnosticCode.Z_LEVEL_ALLOWANCE_DEVIATION,
                            "Allowance Z-Level vượt tolerance.",
                            pass_index=contour.pass_index,
                            segment_index=contour.segment_index,
                            debug=(("point_index", str(point_index)),),
                        )
                    )
    return _dedupe_local(values)


def _boundary_findings(
    *,
    operation: Operation,
    context: Cam3DCalculationContext,
    preview: ZLevelPreview,
    artifact: ToolpathArtifact,
    cancellation: Callable[[], bool] | None,
) -> tuple[ZLevelSafetyDiagnostic, ...]:
    """Conservatively classify outer trim and inner-loop direct links."""

    findings: list[ZLevelSafetyDiagnostic] = []
    boundary = context.machining_zone.boundary
    outer = (
        tuple(preview.frame.coordinates(point)[:2] for point in boundary.points[:-1])
        if boundary is not None and boundary.points
        else ()
    )
    inner_by_level: dict[float, tuple[tuple[float, float], ...]] = {}
    contour_regions = {
        (contour.pass_index, contour.segment_index): contour.region_id
        for level_pass in preview.passes
        for contour in level_pass.segments
    }
    for level_pass in preview.passes:
        for contour in level_pass.segments:
            if contour.loop_type.value == "inner" and contour.closed:
                inner_by_level[level_pass.level] = tuple(
                    preview.frame.coordinates(point.contact_point)[:2]
                    for point in contour.points
                )
    for event in artifact.events:
        _checkpoint(cancellation)
        if not isinstance(event, (RapidMove, LinearMove)):
            continue
        provenance_parts = event.provenance.split(".")
        action = (
            ".".join(provenance_parts[5:])
            if len(provenance_parts) >= 6 and provenance_parts[0] == "z_level"
            else event.provenance.rsplit(".", 1)[-1]
        )
        motion_xy = (
            preview.frame.coordinates(
                _world_point(event.start.position, context.machining_zone.wcs)
            )[:2],
            preview.frame.coordinates(
                _world_point(event.end.position, context.machining_zone.wcs)
            )[:2],
        )
        if outer and action not in {"retract", "clearance"}:
            samples = max(
                2,
                min(
                    64,
                    int(
                        math.ceil(
                            _distance_2d(*motion_xy)
                            / max(context.tolerance_policy.contact_tolerance, 1.0e-6)
                        )
                    ),
                ),
            )
            if any(
                not _inside_or_boundary(
                    _lerp_2d(motion_xy[0], motion_xy[1], index / (samples - 1)),
                    outer,
                    context.tolerance_policy.contact_tolerance,
                )
                for index in range(samples)
            ):
                findings.append(
                    _local_diagnostic(
                        ParallelSafetyStatus.UNSAFE,
                        DiagnosticCode.Z_LEVEL_SAFETY_BOUNDARY_ESCAPE,
                        "Quỹ đạo Z-Level vượt outer trim boundary.",
                        classification="exact",
                    )
                )
        if action in {"direct", "link"} or action.startswith("link.direct"):
            for polygon in inner_by_level.values():
                if _segment_crosses_polygon(
                    motion_xy[0],
                    motion_xy[1],
                    polygon,
                    context.tolerance_policy.contact_tolerance,
                ):
                    findings.append(
                        _local_diagnostic(
                            ParallelSafetyStatus.UNSAFE,
                            DiagnosticCode.Z_LEVEL_SAFETY_HOLE_CROSSING,
                            "Direct link Z-Level cắt qua inner hole.",
                            classification="exact",
                        )
                    )
                    break
        if action.startswith("link.direct"):
            if len(provenance_parts) >= 8:
                current_key = (
                    int(provenance_parts[2]),
                    int(provenance_parts[4]),
                )
                previous_key = (
                    int(provenance_parts[-2]),
                    int(provenance_parts[-1]),
                )
                if contour_regions.get(current_key) != contour_regions.get(previous_key):
                    findings.append(
                        _local_diagnostic(
                            ParallelSafetyStatus.UNSAFE,
                            DiagnosticCode.Z_LEVEL_SAFETY_LINK_COLLISION,
                            "Direct link không được nối qua disconnected region.",
                            classification="conservative",
                        )
                    )
    return _dedupe_local(findings)


def _build_scope(
    context: Cam3DCalculationContext,
    artifact: ToolpathArtifact,
    shared: ParallelSafetyReport,
    holder: HolderDefinition | None,
) -> tuple[ZLevelSafetyScopeEntry, ...]:
    zone = context.machining_zone
    mesh_sources = set(context.calculation_mesh.triangle_sources)

    def coverage(items: tuple[Any, ...] | None, name: str) -> ZLevelSafetyScopeEntry:
        if items is None:
            return ZLevelSafetyScopeEntry(name, ZLevelScopeStatus.NOT_PROVIDED)
        fingerprints = tuple(sorted(item.fingerprint.digest for item in items))
        status = (
            ZLevelScopeStatus.CHECKED
            if all(item.geometry.reference_id in mesh_sources for item in items)
            else ZLevelScopeStatus.UNVERIFIED
        )
        return ZLevelSafetyScopeEntry(
            name,
            status,
            ContentFingerprint.from_payload(fingerprints).digest,
            f"{len(items)} face reference(s)",
        )

    entries = [
        coverage(
            zone.part_surfaces.selection.surfaces,
            "selected_machining_faces",
        ),
        coverage(
            zone.part_surfaces.selection.surfaces,
            "neighboring_selected_faces",
        ),
        coverage(
            zone.check_surfaces.selection.surfaces if zone.check_surfaces else None,
            "protected_model_faces",
        ),
        ZLevelSafetyScopeEntry("stock_geometry", ZLevelScopeStatus.NOT_PROVIDED),
        coverage(
            zone.fixture_surfaces.selection.surfaces if zone.fixture_surfaces else None,
            "fixture_geometry",
        ),
    ]
    for component in ParallelToolComponent:
        if component is ParallelToolComponent.HOLDER:
            status = (
                ZLevelScopeStatus.NOT_PRESENT
                if shared.holder_state == "declared_absent"
                else ZLevelScopeStatus.CHECKED
                if shared.holder_state == "geometry_faithful"
                else ZLevelScopeStatus.INVALID
                if shared.holder_state == "reference_invalid"
                else ZLevelScopeStatus.UNVERIFIED
            )
        elif component in shared.checked_components:
            status = ZLevelScopeStatus.CHECKED
        else:
            status = ZLevelScopeStatus.UNVERIFIED
        entries.append(ZLevelSafetyScopeEntry(component.value, status))

    motion_names = {
        "cut_motions": "cut",
        "direct_links": "direct",
        "approach": "approach",
        "retract": "retract",
        "rapid": "rapid",
    }
    for name, token in motion_names.items():
        present = any(
            isinstance(event, (RapidMove, LinearMove))
            and token in event.provenance
            for event in artifact.events
        )
        entries.append(
            ZLevelSafetyScopeEntry(
                name,
                ZLevelScopeStatus.CHECKED if present and shared.status is ParallelSafetyStatus.SAFE else
                ZLevelScopeStatus.UNVERIFIED if present else ZLevelScopeStatus.NOT_PRESENT,
            )
        )
    return tuple(entries)


def _statistics(
    shared: ParallelSafetyReport,
    diagnostics: tuple[ZLevelSafetyDiagnostic, ...],
) -> ZLevelSafetyStatistics:
    base: ParallelSafetyStatistics = shared.statistics
    collisions = sum(item.occurrence_count for item in shared.diagnostics)
    checked = base.narrow_phase_check_count
    # The shared validator evaluates each primitive in deterministic component
    # order; derive per-component counts from the stable primitive names in the
    # report rather than maintaining a second solver counter.
    cutter = checked if ParallelToolComponent.CUTTER in shared.checked_components else 0
    shank = checked if ParallelToolComponent.SHANK in shared.checked_components else 0
    holder = checked if ParallelToolComponent.HOLDER in shared.checked_components else 0
    return ZLevelSafetyStatistics(
        base.motion_count,
        base.broad_phase_candidate_count,
        cutter,
        shank,
        holder,
        base.swept_subdivision_count,
        checked,
        base.swept_subdivision_count,
        collisions,
        len(diagnostics),
        len(
            {
                (item.motion_index, item.pass_index, item.segment_index)
                for item in diagnostics
                if item.status is not ParallelSafetyStatus.SAFE
            }
        ),
        base.broad_phase_candidate_count,
        base.narrow_phase_check_count,
    )


def _translate_shared(
    item: ParallelSafetyDiagnostic,
    *,
    holder_state: str | None = None,
) -> ZLevelSafetyDiagnostic:
    mapping = {
        "parallel.safety.cutter_gouge": (
            DiagnosticCode.Z_LEVEL_SAFETY_CUTTER_GOUGE,
            "Phát hiện cutter gouge trong swept volume Z-Level.",
        ),
        "parallel.safety.shank_collision": (
            DiagnosticCode.Z_LEVEL_SAFETY_SHANK_COLLISION,
            "Phát hiện va chạm shank trong chuyển động Z-Level.",
        ),
        "parallel.safety.holder_collision": (
            DiagnosticCode.Z_LEVEL_SAFETY_HOLDER_COLLISION,
            "Phát hiện va chạm Holder trong chuyển động Z-Level.",
        ),
        "parallel.safety.link_collision": (
            DiagnosticCode.Z_LEVEL_SAFETY_LINK_COLLISION,
            "Direct/link Z-Level không chứng minh được an toàn.",
        ),
        "parallel.safety.rapid_collision": (
            DiagnosticCode.Z_LEVEL_SAFETY_RAPID_COLLISION,
            "Rapid Z-Level va chạm geometry.",
        ),
        "parallel.safety.approach_collision": (
            DiagnosticCode.Z_LEVEL_SAFETY_APPROACH_COLLISION,
            "Approach Z-Level va chạm geometry.",
        ),
        "parallel.safety.retract_collision": (
            DiagnosticCode.Z_LEVEL_SAFETY_RETRACT_COLLISION,
            "Retract Z-Level va chạm geometry.",
        ),
        "parallel.safety.protected_face_collision": (
            DiagnosticCode.Z_LEVEL_SAFETY_PROTECTED_FACE_COLLISION,
            "Cutter/shank/Holder chạm protected geometry.",
        ),
        "parallel.safety.limit_exceeded": (
            DiagnosticCode.Z_LEVEL_SAFETY_EXCESSIVE_CHECKS,
            "Safety Z-Level vượt deterministic guardrail.",
        ),
        "parallel.safety.missing_holder_geometry": (
            (
                DiagnosticCode.Z_LEVEL_SAFETY_INVALID_HOLDER
                if holder_state == "reference_invalid"
                else DiagnosticCode.Z_LEVEL_SAFETY_HOLDER_NOT_PROVIDED
            ),
            (
                "Holder reference không khớp assembly hoặc fingerprint."
                if holder_state == "reference_invalid"
                else "Holder reference thiếu hoặc chưa được cung cấp."
            ),
        ),
        "parallel.safety.unknown": (
            DiagnosticCode.Z_LEVEL_SAFETY_UNKNOWN_V2,
            "Safety Z-Level chưa chứng minh được SAFE.",
        ),
        "parallel.safety.cancelled": (
            DiagnosticCode.Z_LEVEL_SAFETY_CANCELLED,
            "Tính toán safety Z-Level đã bị hủy.",
        ),
        "parallel.safety.failed": (
            DiagnosticCode.Z_LEVEL_SAFETY_UNKNOWN_V2,
            "Safety Z-Level thất bại tại ranh giới nội bộ.",
        ),
    }
    code, message = mapping.get(
        item.code.value,
        (item.code, item.message),
    )
    metadata = list(item.debug_metadata)
    metadata.append(("shared_diagnostic_code", item.code.value))
    metadata.append(("safety_contract", "stage_8a2_2_shared"))
    classification = (
        "exact"
        if any(key == "primitive_support" and value == "exact" for key, value in metadata)
        else "conservative"
    )
    return ZLevelSafetyDiagnostic(
        item.status,
        code,
        message,
        item.pass_index,
        item.segment_index,
        item.motion_index,
        item.tool_component,
        item.geometry_source,
        str(item.face_id) if item.face_id else item.protected_region_id,
        classification,
        tuple(metadata),
        item.swept_interval_start,
        "overlap",
        classification,
        item.closest_distance_mm,
        item.penetration_depth_mm,
        item.occurrence_count,
        item.first_sample_index,
        item.last_sample_index,
        item.minimum_clearance_mm,
        item.maximum_penetration_mm,
    )


def _local_diagnostic(
    status: ParallelSafetyStatus,
    code: DiagnosticCode,
    message: str,
    *,
    pass_index: int | None = None,
    segment_index: int | None = None,
    motion_index: int | None = None,
    component: ParallelToolComponent | None = None,
    classification: str = "conservative",
    debug: tuple[tuple[str, str], ...] = (),
) -> ZLevelSafetyDiagnostic:
    return ZLevelSafetyDiagnostic(
        status,
        code,
        message,
        pass_index,
        segment_index,
        motion_index,
        component,
        None,
        None,
        classification,
        debug,
    )


def _dedupe_local(
    values: list[ZLevelSafetyDiagnostic],
) -> tuple[ZLevelSafetyDiagnostic, ...]:
    unique: dict[tuple[Any, ...], ZLevelSafetyDiagnostic] = {}
    for item in values:
        key = (
            item.status,
            item.code,
            item.pass_index,
            item.segment_index,
            item.motion_index,
            item.component,
            item.candidate_geometry,
        )
        unique.setdefault(key, item)
    return tuple(
        sorted(
            unique.values(),
            key=lambda item: (
                item.motion_index if item.motion_index is not None else -1,
                item.pass_index if item.pass_index is not None else -1,
                item.segment_index if item.segment_index is not None else -1,
                item.code.value,
            ),
        )
    )


def _aggregate_status(statuses: Any) -> ParallelSafetyStatus:
    values = set(statuses)
    for status in (
        ParallelSafetyStatus.FAILED,
        ParallelSafetyStatus.CANCELLED,
        ParallelSafetyStatus.UNSAFE,
        ParallelSafetyStatus.UNKNOWN,
    ):
        if status in values:
            return status
    return ParallelSafetyStatus.SAFE


def _build_boundary_polygon(
    points: tuple[Point3, ...],
    frame: WcsFrame,
) -> tuple[tuple[float, float], ...]:
    return tuple(
        (
            Vector3(
                point.x - frame.origin.x,
                point.y - frame.origin.y,
                point.z - frame.origin.z,
            ).dot(frame.x_axis),
            Vector3(
                point.x - frame.origin.x,
                point.y - frame.origin.y,
                point.z - frame.origin.z,
            ).dot(frame.y_axis),
        )
        for point in points
    )


def _inside_or_boundary(
    point: tuple[float, float],
    polygon: tuple[tuple[float, float], ...],
    tolerance: float,
) -> bool:
    if any(_distance_2d(point, vertex) <= tolerance for vertex in polygon):
        return True
    for first, second in zip(polygon, polygon[1:] + polygon[:1], strict=True):
        if _distance_to_segment_2d(point, first, second) <= tolerance:
            return True
    crossings = 0
    for first, second in zip(polygon, polygon[1:] + polygon[:1], strict=True):
        if (first[1] > point[1]) != (second[1] > point[1]):
            x = (second[0] - first[0]) * (point[1] - first[1]) / (
                second[1] - first[1]
            ) + first[0]
            if point[0] < x:
                crossings += 1
    return crossings % 2 == 1


def _segment_crosses_polygon(
    first: tuple[float, float],
    second: tuple[float, float],
    polygon: tuple[tuple[float, float], ...],
    tolerance: float,
) -> bool:
    # A direct link ending exactly on a hole boundary is already unsafe.  The
    # previous implementation only rejected links whose two endpoints were
    # both inside/on the polygon, allowing a link to terminate on a hole rim
    # without reporting the crossing.
    if _inside_or_boundary(first, polygon, tolerance) or _inside_or_boundary(
        second, polygon, tolerance
    ):
        return True
    for left, right in zip(polygon, polygon[1:] + polygon[:1], strict=True):
        if _segments_intersect((first, second), (left, right), tolerance):
            return True
    return False


def _segments_intersect(
    first: tuple[tuple[float, float], tuple[float, float]],
    second: tuple[tuple[float, float], tuple[float, float]],
    tolerance: float,
) -> bool:
    def orient(
        origin: tuple[float, float],
        left: tuple[float, float],
        right: tuple[float, float],
    ) -> float:
        return (left[0] - origin[0]) * (right[1] - origin[1]) - (
            left[1] - origin[1]
        ) * (right[0] - origin[0])

    a, b = first
    c, d = second
    values = (orient(a, b, c), orient(a, b, d), orient(c, d, a), orient(c, d, b))
    return (
        ((values[0] > tolerance and values[1] < -tolerance) or
         (values[0] < -tolerance and values[1] > tolerance))
        and ((values[2] > tolerance and values[3] < -tolerance) or
             (values[2] < -tolerance and values[3] > tolerance))
    )


def _distance_to_segment_2d(
    point: tuple[float, float],
    first: tuple[float, float],
    second: tuple[float, float],
) -> float:
    dx, dy = second[0] - first[0], second[1] - first[1]
    denominator = dx * dx + dy * dy
    if denominator <= 1.0e-24:
        return _distance_2d(point, first)
    ratio = max(
        0.0,
        min(
            1.0,
            ((point[0] - first[0]) * dx + (point[1] - first[1]) * dy)
            / denominator,
        ),
    )
    return _distance_2d(point, (first[0] + ratio * dx, first[1] + ratio * dy))


def _lerp_2d(
    first: tuple[float, float],
    second: tuple[float, float],
    ratio: float,
) -> tuple[float, float]:
    return (
        first[0] + (second[0] - first[0]) * ratio,
        first[1] + (second[1] - first[1]) * ratio,
    )


def _distance_2d(first: tuple[float, float], second: tuple[float, float]) -> float:
    return math.dist(first, second)


def _finite_point(point: Point3) -> bool:
    return all(math.isfinite(value) for value in (point.x, point.y, point.z))


def _world_point(point: Point3, wcs: WcsFrame) -> Point3:
    return Point3(
        wcs.origin.x
        + point.x * wcs.x_axis.x
        + point.y * wcs.y_axis.x
        + point.z * wcs.z_axis.x,
        wcs.origin.y
        + point.x * wcs.x_axis.y
        + point.y * wcs.y_axis.y
        + point.z * wcs.z_axis.y,
        wcs.origin.z
        + point.x * wcs.x_axis.z
        + point.y * wcs.y_axis.z
        + point.z * wcs.z_axis.z,
        point.unit,
    )


def _is_sha256(value: str | None) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


def _checkpoint(cancellation: Callable[[], bool] | None) -> None:
    if cancellation is not None and cancellation():
        raise ZLevelFinishingError(
            DiagnosticCode.Z_LEVEL_CANCELLED,
            "Tính toán safety Z-Level đã bị hủy.",
        )


__all__ = [
    "ZLevelScopeStatus",
    "ZLevelSafetyScopeEntry",
    "ZLevelSafetyDiagnostic",
    "ZLevelSafetyStatistics",
    "ZLevelSafetyReport",
    "build_z_level_safety_policy",
    "validate_z_level_candidate_safety",
    "z_level_artifact_contract_hash",
]
