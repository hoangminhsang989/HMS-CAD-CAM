"""Immutable, deterministic simulation contracts for CAM Phase 7C.1.

The model deliberately contains no Open CASCADE, Qt, viewer, thread or clock
objects.  It is safe to serialize and to use as an input to a headless runner.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping

from hms_cadcam.cam.domain.errors import CamValidationError, UnsupportedCamSchemaError
from hms_cadcam.cam.domain.ids import (
    FixtureInstanceId,
    MachineDefinitionId,
    OperationId,
    SetupId,
    SimulationRequestId,
    SimulationResultId,
    ToolAssemblyId,
    ToolDefinitionId,
    HolderDefinitionId,
    ToolpathArtifactId,
)
from hms_cadcam.cam.domain.operation import DiagnosticSeverity
from hms_cadcam.cam.domain.revision import ContentFingerprint, DependencyFingerprint, Revision
from hms_cadcam.cam.domain.spatial import Point3, WcsFrame
from hms_cadcam.cam.domain.units import LengthUnit
from hms_cadcam.cam.toolpath.geometry import Bounds3

SIMULATION_FORMAT = "HMS_CAM_SIMULATION"
SIMULATION_VERSION = 1
SIMULATION_ALGORITHM_VERSION = 1
_MAX_SAMPLES = 1_000_000
_MAX_ISSUES = 10_000


class SimulationStatus(StrEnum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


class SimulationIssueCategory(StrEnum):
    COLLISION = "collision"
    GOUGE = "gouge"
    CLEARANCE_WARNING = "clearance_warning"
    INVALID_ARTIFACT = "invalid_artifact"
    UNSUPPORTED_GEOMETRY = "unsupported_geometry"


class SimulationIssueCode(StrEnum):
    INVALID_REQUEST = "sim.invalid_request"
    SOURCE_MISSING = "sim.source_missing"
    SOURCE_STALE = "sim.source_stale"
    SOURCE_UNSUPPORTED = "sim.source_unsupported"
    STOCK_MISSING = "sim.stock_missing"
    STOCK_STALE = "sim.stock_stale"
    FIXTURE_STALE = "sim.fixture_stale"
    TOOL_MISSING = "sim.tool_missing"
    TOOL_STALE = "sim.tool_stale"
    MACHINE_STALE = "sim.machine_stale"
    UNIT_MISMATCH = "sim.unit_mismatch"
    SAMPLE_LIMIT = "sim.sample_limit"
    INVALID_MOTION = "sim.invalid_motion"
    RAPID_BELOW_SAFE = "sim.rapid_below_safe"
    TOOL_FIXTURE_COLLISION = "sim.tool_fixture_collision"
    SHANK_STOCK_COLLISION = "sim.shank_stock_collision"
    SHANK_FIXTURE_COLLISION = "sim.shank_fixture_collision"
    HOLDER_STOCK_COLLISION = "sim.holder_stock_collision"
    HOLDER_FIXTURE_COLLISION = "sim.holder_fixture_collision"
    GOUGE_DETECTED = "sim.gouge_detected"
    UNSUPPORTED_GEOMETRY = "sim.unsupported_geometry"
    CANCELLED = "sim.cancelled"
    STALE_RESULT = "sim.stale_result"
    FAILED = "sim.failed"


def _finite(value: float, name: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CamValidationError(f"{name} must be a real number")
    normalized = float(value)
    if not math.isfinite(normalized) or (positive and normalized <= 0.0):
        raise CamValidationError(f"{name} must be finite and {'positive' if positive else 'valid'}")
    return normalized


def _text(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CamValidationError(f"{name} must be a non-empty string")
    return value.strip()


def _fingerprint(value: ContentFingerprint | DependencyFingerprint, name: str) -> None:
    if not isinstance(value, ContentFingerprint):
        raise CamValidationError(f"{name} fingerprint is invalid")


@dataclass(frozen=True, slots=True)
class SimulationSamplingPolicy:
    """Bounded deterministic sampling controls."""

    max_linear_step: float = 1.0
    chord_tolerance: float = 0.01
    max_arc_angle: float = math.pi / 8.0
    geometric_tolerance: float = 1.0e-8
    maximum_samples: int = 250_000
    chunk_size: int = 2_048
    cancellation_check_interval: int = 256
    maximum_issues: int = 10_000
    memory_budget_bytes: int = 256 * 1024 * 1024
    schema_version: int = SIMULATION_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SIMULATION_VERSION:
            raise UnsupportedCamSchemaError("Unsupported simulation sampling policy version")
        for value, name in (
            (self.max_linear_step, "max_linear_step"),
            (self.chord_tolerance, "chord_tolerance"),
            (self.max_arc_angle, "max_arc_angle"),
            (self.geometric_tolerance, "geometric_tolerance"),
        ):
            object.__setattr__(self, name, _finite(value, name, positive=True))
        if self.max_arc_angle > math.tau:
            raise CamValidationError("max_arc_angle exceeds one turn")
        if type(self.maximum_samples) is not int or not 1 <= self.maximum_samples <= _MAX_SAMPLES:
            raise CamValidationError("maximum_samples is outside the hard bound")
        if type(self.chunk_size) is not int or not 1 <= self.chunk_size <= 65_536:
            raise CamValidationError("chunk_size is invalid")
        if type(self.cancellation_check_interval) is not int or not 1 <= self.cancellation_check_interval <= 256:
            raise CamValidationError("cancellation_check_interval is invalid")
        if type(self.maximum_issues) is not int or not 1 <= self.maximum_issues <= _MAX_ISSUES:
            raise CamValidationError("maximum_issues is outside the hard bound")
        if type(self.memory_budget_bytes) is not int or not 65_536 <= self.memory_budget_bytes <= 2 * 1024**3:
            raise CamValidationError("memory_budget_bytes is invalid")

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": SIMULATION_FORMAT,
            "format_version": self.schema_version,
            "max_linear_step": self.max_linear_step,
            "chord_tolerance": self.chord_tolerance,
            "max_arc_angle": self.max_arc_angle,
            "geometric_tolerance": self.geometric_tolerance,
            "maximum_samples": self.maximum_samples,
            "chunk_size": self.chunk_size,
            "cancellation_check_interval": self.cancellation_check_interval,
            "maximum_issues": self.maximum_issues,
            "memory_budget_bytes": self.memory_budget_bytes,
        }


@dataclass(frozen=True, slots=True)
class SimulationIssue:
    severity: DiagnosticSeverity
    category: SimulationIssueCategory
    code: SimulationIssueCode
    message_key: str
    operation_id: OperationId
    artifact_id: ToolpathArtifactId
    segment_index: int | None = None
    event_index: int | None = None
    sample_index: int | None = None
    world_point: Point3 | None = None
    bounds: Bounds3 | None = None
    involved_entities: tuple[str, ...] = ()
    evidence: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.severity, DiagnosticSeverity) or not isinstance(self.category, SimulationIssueCategory):
            raise CamValidationError("Simulation issue severity/category is invalid")
        if not isinstance(self.code, SimulationIssueCode):
            raise CamValidationError("Simulation issue code is invalid")
        _text(self.message_key, "message_key")
        if not isinstance(self.operation_id, OperationId) or not isinstance(self.artifact_id, ToolpathArtifactId):
            raise CamValidationError("Simulation issue source identity is invalid")
        for value, name in ((self.segment_index, "segment_index"), (self.event_index, "event_index"), (self.sample_index, "sample_index")):
            if value is not None and (type(value) is not int or value < 0):
                raise CamValidationError(f"{name} is invalid")
        if self.world_point is not None and not isinstance(self.world_point, Point3):
            raise CamValidationError("Simulation issue point is invalid")
        if self.bounds is not None and not isinstance(self.bounds, Bounds3):
            raise CamValidationError("Simulation issue bounds are invalid")
        if not isinstance(self.involved_entities, tuple) or any(not isinstance(item, str) or not item for item in self.involved_entities):
            raise CamValidationError("Simulation issue entities are invalid")
        if not isinstance(self.evidence, tuple) or any(not isinstance(item, tuple) or len(item) != 2 or not all(isinstance(v, str) and v for v in item) for item in self.evidence):
            raise CamValidationError("Simulation issue evidence is invalid")
        object.__setattr__(self, "involved_entities", tuple(sorted(set(self.involved_entities))))
        normalized_evidence = tuple(sorted(self.evidence))
        if len({key for key, _ in normalized_evidence}) != len(normalized_evidence):
            raise CamValidationError("Simulation issue evidence keys must be unique")
        object.__setattr__(self, "evidence", normalized_evidence)

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity.value,
            "category": self.category.value,
            "code": self.code.value,
            "message_key": self.message_key,
            "operation_id": str(self.operation_id),
            "artifact_id": str(self.artifact_id),
            "segment_index": self.segment_index,
            "event_index": self.event_index,
            "sample_index": self.sample_index,
            "world_point": self.world_point.to_dict() if self.world_point else None,
            "bounds": self.bounds.to_dict() if self.bounds else None,
            "involved_entities": list(self.involved_entities),
            "evidence": [[key, value] for key, value in self.evidence],
        }


@dataclass(frozen=True, slots=True)
class SimulationStatistics:
    sampled_point_count: int
    sampled_segment_count: int
    collision_count: int
    warning_count: int
    error_count: int
    bounds: Bounds3

    def __post_init__(self) -> None:
        if any(type(value) is not int or value < 0 for value in (self.sampled_point_count, self.sampled_segment_count, self.collision_count, self.warning_count, self.error_count)):
            raise CamValidationError("Simulation statistics counts are invalid")
        if not isinstance(self.bounds, Bounds3):
            raise CamValidationError("Simulation statistics bounds are invalid")

    def to_dict(self) -> dict[str, Any]:
        return {"format": SIMULATION_FORMAT, "format_version": SIMULATION_VERSION, "sampled_point_count": self.sampled_point_count, "sampled_segment_count": self.sampled_segment_count, "collision_count": self.collision_count, "warning_count": self.warning_count, "error_count": self.error_count, "bounds": self.bounds.to_dict()}


@dataclass(frozen=True, slots=True)
class SimulationRequest:
    request_id: SimulationRequestId
    operation_id: OperationId
    operation_revision: Revision
    artifact_id: ToolpathArtifactId
    artifact_fingerprint: ContentFingerprint
    input_fingerprint: DependencyFingerprint
    setup_id: SetupId
    setup_revision: Revision
    wcs_fingerprint: ContentFingerprint
    stock_fingerprint: ContentFingerprint
    fixture_fingerprints: tuple[tuple[FixtureInstanceId, ContentFingerprint], ...]
    tool_assembly_id: ToolAssemblyId
    tool_assembly_fingerprint: ContentFingerprint
    tool_id: ToolDefinitionId
    tool_fingerprint: ContentFingerprint
    holder_id: HolderDefinitionId | None
    holder_fingerprint: ContentFingerprint | None
    machine_id: MachineDefinitionId | None
    machine_fingerprint: ContentFingerprint | None
    unit: LengthUnit
    sampling_policy: SimulationSamplingPolicy = SimulationSamplingPolicy()
    safe_height: float | None = None
    algorithm_version: int = SIMULATION_ALGORITHM_VERSION

    def __post_init__(self) -> None:
        if self.algorithm_version != SIMULATION_ALGORITHM_VERSION:
            raise UnsupportedCamSchemaError("Unsupported simulation algorithm version")
        for value, typ in ((self.request_id, SimulationRequestId), (self.operation_id, OperationId), (self.artifact_id, ToolpathArtifactId), (self.setup_id, SetupId), (self.tool_assembly_id, ToolAssemblyId), (self.tool_id, ToolDefinitionId)):
            if not isinstance(value, typ):
                raise CamValidationError("Simulation request identity is invalid")
        for value, name in ((self.operation_revision, "operation_revision"), (self.setup_revision, "setup_revision")):
            if not isinstance(value, Revision):
                raise CamValidationError(f"{name} is invalid")
        for value, name in ((self.artifact_fingerprint, "artifact"), (self.input_fingerprint, "input"), (self.wcs_fingerprint, "wcs"), (self.stock_fingerprint, "stock"), (self.tool_assembly_fingerprint, "tool assembly"), (self.tool_fingerprint, "tool")):
            _fingerprint(value, name)
        if (self.holder_id is None) != (self.holder_fingerprint is None) or (self.machine_id is None) != (self.machine_fingerprint is None):
            raise CamValidationError("Optional simulation provenance must be paired")
        if self.holder_id is not None and not isinstance(self.holder_id, HolderDefinitionId):
            raise CamValidationError("holder_id is invalid")
        if self.holder_fingerprint is not None:
            _fingerprint(self.holder_fingerprint, "holder")
        if self.machine_id is not None and not isinstance(self.machine_id, MachineDefinitionId):
            raise CamValidationError("machine_id is invalid")
        if self.machine_fingerprint is not None:
            _fingerprint(self.machine_fingerprint, "machine")
        if not isinstance(self.unit, LengthUnit) or self.unit is LengthUnit.UNKNOWN:
            raise CamValidationError("Simulation request unit is invalid")
        if not isinstance(self.sampling_policy, SimulationSamplingPolicy):
            raise CamValidationError("Simulation sampling policy is invalid")
        if self.safe_height is not None:
            object.__setattr__(self, "safe_height", _finite(self.safe_height, "safe_height"))
        if not isinstance(self.fixture_fingerprints, tuple) or any(not isinstance(item, tuple) or len(item) != 2 or not isinstance(item[0], FixtureInstanceId) or not isinstance(item[1], ContentFingerprint) for item in self.fixture_fingerprints):
            raise CamValidationError("Fixture provenance is invalid")
        object.__setattr__(self, "fixture_fingerprints", tuple(sorted(self.fixture_fingerprints, key=lambda item: str(item[0]))))
        if len({item for item, _ in self.fixture_fingerprints}) != len(self.fixture_fingerprints):
            raise CamValidationError("Fixture provenance IDs must be unique")

    def identity_payload(self) -> dict[str, Any]:
        """Return the deterministic input payload (request UUID is excluded)."""
        return {
            "algorithm_version": self.algorithm_version,
            "operation_id": str(self.operation_id), "operation_revision": self.operation_revision.value,
            "artifact_id": str(self.artifact_id), "artifact_fingerprint": self.artifact_fingerprint.to_dict(),
            "input_fingerprint": self.input_fingerprint.to_dict(), "setup_id": str(self.setup_id), "setup_revision": self.setup_revision.value,
            "wcs_fingerprint": self.wcs_fingerprint.to_dict(), "stock_fingerprint": self.stock_fingerprint.to_dict(),
            "fixtures": [[str(item), fp.to_dict()] for item, fp in self.fixture_fingerprints],
            "tool_assembly_id": str(self.tool_assembly_id), "tool_assembly_fingerprint": self.tool_assembly_fingerprint.to_dict(),
            "tool_id": str(self.tool_id), "tool_fingerprint": self.tool_fingerprint.to_dict(),
            "holder_id": str(self.holder_id) if self.holder_id else None, "holder_fingerprint": self.holder_fingerprint.to_dict() if self.holder_fingerprint else None,
            "machine_id": str(self.machine_id) if self.machine_id else None, "machine_fingerprint": self.machine_fingerprint.to_dict() if self.machine_fingerprint else None,
            "unit": self.unit.value, "sampling_policy": self.sampling_policy.to_dict(), "safe_height": self.safe_height,
        }

    def to_dict(self) -> dict[str, Any]:
        return {"format": SIMULATION_FORMAT, "format_version": SIMULATION_VERSION, "request_id": str(self.request_id), **self.identity_payload()}


@dataclass(frozen=True, slots=True)
class SimulationResult:
    result_id: SimulationResultId
    request_id: SimulationRequestId
    operation_id: OperationId
    artifact_id: ToolpathArtifactId
    artifact_fingerprint: ContentFingerprint
    input_fingerprint: DependencyFingerprint
    sampling_policy: SimulationSamplingPolicy
    status: SimulationStatus
    issues: tuple[SimulationIssue, ...]
    statistics: SimulationStatistics
    result_fingerprint: ContentFingerprint
    algorithm_version: int = SIMULATION_ALGORITHM_VERSION

    def __post_init__(self) -> None:
        if self.algorithm_version != SIMULATION_ALGORITHM_VERSION:
            raise UnsupportedCamSchemaError("Unsupported simulation result version")
        if not isinstance(self.result_id, SimulationResultId) or not isinstance(self.request_id, SimulationRequestId) or not isinstance(self.operation_id, OperationId) or not isinstance(self.artifact_id, ToolpathArtifactId):
            raise CamValidationError("Simulation result identity is invalid")
        _fingerprint(self.artifact_fingerprint, "artifact")
        if not isinstance(self.input_fingerprint, DependencyFingerprint) or not isinstance(self.sampling_policy, SimulationSamplingPolicy):
            raise CamValidationError("Simulation result provenance is invalid")
        if not isinstance(self.status, SimulationStatus) or not isinstance(self.statistics, SimulationStatistics):
            raise CamValidationError("Simulation result state is invalid")
        if not isinstance(self.issues, tuple) or len(self.issues) > _MAX_ISSUES or any(not isinstance(item, SimulationIssue) for item in self.issues):
            raise CamValidationError("Simulation result issues are invalid")
        ordered = tuple(sorted(self.issues, key=issue_sort_key))
        if ordered != self.issues:
            raise CamValidationError("Simulation issues must be deterministically ordered")
        warning_count = sum(item.severity is DiagnosticSeverity.WARNING for item in self.issues)
        error_count = sum(item.severity is DiagnosticSeverity.ERROR for item in self.issues)
        collision_count = sum(item.category in {SimulationIssueCategory.COLLISION, SimulationIssueCategory.GOUGE} for item in self.issues)
        if (warning_count, error_count, collision_count) != (self.statistics.warning_count, self.statistics.error_count, self.statistics.collision_count):
            raise CamValidationError("Simulation statistics do not match issues")
        expected_status = SimulationStatus.FAIL if error_count or collision_count else (SimulationStatus.WARN if self.issues else SimulationStatus.PASS)
        if self.status is not expected_status:
            raise CamValidationError("Simulation status does not match issues")
        _fingerprint(self.result_fingerprint, "result")
        expected = ContentFingerprint.from_payload(_result_identity_payload(
            algorithm_version=self.algorithm_version,
            operation_id=self.operation_id,
            artifact_id=self.artifact_id,
            artifact_fingerprint=self.artifact_fingerprint,
            input_fingerprint=self.input_fingerprint,
            sampling_policy=self.sampling_policy,
            status=self.status,
            issues=self.issues,
            statistics=self.statistics,
        ))
        if expected != self.result_fingerprint:
            raise CamValidationError("Simulation result fingerprint verification failed")

    @classmethod
    def create(cls, *, result_id: SimulationResultId, request: SimulationRequest, status: SimulationStatus, issues: tuple[SimulationIssue, ...], statistics: SimulationStatistics) -> "SimulationResult":
        ordered = tuple(sorted(issues, key=issue_sort_key))
        payload = _result_identity_payload(algorithm_version=SIMULATION_ALGORITHM_VERSION, operation_id=request.operation_id, artifact_id=request.artifact_id, artifact_fingerprint=request.artifact_fingerprint, input_fingerprint=request.input_fingerprint, sampling_policy=request.sampling_policy, status=status, issues=ordered, statistics=statistics)
        fingerprint = ContentFingerprint.from_payload(payload)
        return cls(result_id, request.request_id, request.operation_id, request.artifact_id, request.artifact_fingerprint, request.input_fingerprint, request.sampling_policy, status, ordered, statistics, fingerprint)

    def to_dict(self) -> dict[str, Any]:
        return {"format": SIMULATION_FORMAT, "format_version": SIMULATION_VERSION, "algorithm_version": self.algorithm_version, "result_id": str(self.result_id), "request_id": str(self.request_id), "operation_id": str(self.operation_id), "artifact_id": str(self.artifact_id), "artifact_fingerprint": self.artifact_fingerprint.to_dict(), "input_fingerprint": self.input_fingerprint.to_dict(), "sampling_policy": self.sampling_policy.to_dict(), "status": self.status.value, "issues": [item.to_dict() for item in self.issues], "statistics": self.statistics.to_dict(), "result_fingerprint": self.result_fingerprint.to_dict()}


def issue_sort_key(issue: SimulationIssue) -> tuple[Any, ...]:
    severity = {DiagnosticSeverity.ERROR: 0, DiagnosticSeverity.WARNING: 1, DiagnosticSeverity.INFO: 2}[issue.severity]
    return (severity, issue.category.value, issue.event_index if issue.event_index is not None else -1, issue.segment_index if issue.segment_index is not None else -1, issue.sample_index if issue.sample_index is not None else -1, issue.involved_entities, issue.evidence, issue.code.value)


def _result_identity_payload(*, algorithm_version: int, operation_id: OperationId, artifact_id: ToolpathArtifactId, artifact_fingerprint: ContentFingerprint, input_fingerprint: DependencyFingerprint, sampling_policy: SimulationSamplingPolicy, status: SimulationStatus, issues: tuple[SimulationIssue, ...], statistics: SimulationStatistics) -> dict[str, Any]:
    return {"algorithm_version": algorithm_version, "operation_id": str(operation_id), "artifact_id": str(artifact_id), "artifact_fingerprint": artifact_fingerprint.to_dict(), "input_fingerprint": input_fingerprint.to_dict(), "sampling_policy": sampling_policy.to_dict(), "status": status.value, "issues": [item.to_dict() for item in issues], "statistics": statistics.to_dict()}


def canonical_json(value: Any) -> str:
    """Canonical JSON helper used by deterministic codecs and fingerprints."""
    return json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":"))
