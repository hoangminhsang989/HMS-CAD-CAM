"""Immutable Toolpath IR artifact, statistics and diagnostics."""

from __future__ import annotations

import math
from dataclasses import InitVar, dataclass
from enum import StrEnum
from typing import Any, ClassVar

from hms_cadcam.cam.domain.errors import CamInvariantError, CamUnitError, CamValidationError
from hms_cadcam.cam.domain.ids import (
    MachineDefinitionId,
    OperationId,
    SetupId,
    ToolAssemblyId,
    ToolpathArtifactId,
)
from hms_cadcam.cam.domain.operation import ComputationToken, DiagnosticSeverity
from hms_cadcam.cam.domain.revision import ContentFingerprint, DependencyFingerprint, Revision
from hms_cadcam.cam.domain.units import FeedUnit, LengthUnit
from hms_cadcam.cam.toolpath.events import (
    AnyToolpathEvent,
    ArcMove,
    DwellEvent,
    FeedMode,
    FeedModeEvent,
    LinearMove,
    MotionClass,
    RapidMove,
    SpindleState,
    SpindleStateEvent,
    ToolpathEventKind,
)
from hms_cadcam.cam.toolpath.geometry import Bounds3, CoordinateSpace, Pose, same_pose

TOOLPATH_FORMAT = "HMS_CAM_TOOLPATH_ARTIFACT"
TOOLPATH_VERSION = 1


class ToolpathCompletionStatus(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"


class ToolpathDiagnosticCode(StrEnum):
    MALFORMED_EVENT = "malformed_event"
    DISCONTINUITY = "discontinuity"
    INVALID_FEED = "invalid_feed"
    INVALID_POSE = "invalid_pose"
    INVALID_ARC = "invalid_arc"
    INVALID_PROCESS_STATE = "invalid_process_state"
    MISSING_INITIAL_POSITION = "missing_initial_position"
    DUPLICATE_EVENT_ID = "duplicate_event_id"
    NON_MONOTONIC_SEQUENCE = "non_monotonic_sequence"
    BOUNDS_MISMATCH = "bounds_mismatch"
    UNSUPPORTED_COORDINATE_SPACE = "unsupported_coordinate_space"
    UNSUPPORTED_VERSION = "unsupported_version"
    STALE_PROVENANCE = "stale_provenance"
    STALE_INPUT = "stale_input"
    EVENT_LIMIT_EXCEEDED = "event_limit_exceeded"


@dataclass(frozen=True, slots=True)
class ToolpathDiagnostic:
    severity: DiagnosticSeverity
    code: ToolpathDiagnosticCode
    message: str
    context: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.severity, DiagnosticSeverity) or not isinstance(self.code, ToolpathDiagnosticCode):
            raise CamValidationError("Toolpath diagnostic enum is invalid")
        if not isinstance(self.message, str) or not self.message.strip():
            raise CamValidationError("Toolpath diagnostic message is invalid")
        if not isinstance(self.context, tuple) or any(not isinstance(item, tuple) or len(item) != 2 or
                not all(isinstance(value, str) and value for value in item) for item in self.context):
            raise CamValidationError("Toolpath diagnostic context is invalid")
        normalized = tuple(sorted(self.context))
        if len({key for key, _ in normalized}) != len(normalized):
            raise CamInvariantError("Toolpath diagnostic context keys must be unique")
        object.__setattr__(self, "context", normalized)


@dataclass(frozen=True, slots=True)
class ToolpathStatistics:
    total_rapid_length: float
    total_cutting_length: float
    total_link_length: float
    total_retract_length: float
    total_arc_length: float
    estimated_duration_seconds: float
    duration_is_partial: bool
    event_counts: tuple[tuple[ToolpathEventKind, int], ...]

    def __post_init__(self) -> None:
        lengths = (self.total_rapid_length, self.total_cutting_length, self.total_link_length,
                   self.total_retract_length, self.total_arc_length, self.estimated_duration_seconds)
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0.0 for value in lengths):
            raise CamValidationError("Toolpath statistics values must be finite and non-negative")
        if type(self.duration_is_partial) is not bool:
            raise CamValidationError("Duration partial flag is invalid")
        if not isinstance(self.event_counts, tuple) or any(not isinstance(kind, ToolpathEventKind) or
                type(count) is not int or count < 0 for kind, count in self.event_counts):
            raise CamValidationError("Toolpath event counts are invalid")
        normalized = tuple(sorted(self.event_counts, key=lambda item: item[0].value))
        if len({kind for kind, _ in normalized}) != len(normalized):
            raise CamInvariantError("Toolpath event count kinds must be unique")
        object.__setattr__(self, "event_counts", normalized)

    @classmethod
    def calculate(cls, events: tuple[AnyToolpathEvent, ...], unit: LengthUnit) -> "ToolpathStatistics":
        rapid = cutting = link = retract = arc = duration = 0.0
        partial = False
        counts: dict[ToolpathEventKind, int] = {}
        expected_feed_unit = (
            FeedUnit.MM_PER_MINUTE
            if unit is LengthUnit.MM
            else FeedUnit.INCH_PER_MINUTE
        )
        expected_revolution_unit = (
            FeedUnit.MM_PER_REVOLUTION
            if unit is LengthUnit.MM
            else FeedUnit.INCH_PER_REVOLUTION
        )
        spindle_speed = None
        for event in events:
            counts[event.kind] = counts.get(event.kind, 0) + 1
            if isinstance(event, SpindleStateEvent):
                spindle_speed = (
                    None if event.state is SpindleState.OFF else event.speed
                )
            elif isinstance(event, RapidMove):
                rapid += event.length
                if event.rapid_rate is None:
                    partial = True
                else:
                    duration += event.length / event.rapid_rate.to(expected_feed_unit).value * 60.0
            elif isinstance(event, (LinearMove, ArcMove)):
                length = event.length
                if event.motion_class is MotionClass.CUTTING:
                    cutting += length
                elif event.motion_class is MotionClass.LINK:
                    link += length
                elif event.motion_class is MotionClass.RETRACT:
                    retract += length
                if isinstance(event, ArcMove):
                    arc += length
                if event.feed_rate.unit in {
                    FeedUnit.MM_PER_REVOLUTION,
                    FeedUnit.INCH_PER_REVOLUTION,
                }:
                    if spindle_speed is None:
                        partial = True
                    else:
                        feed_per_revolution = event.feed_rate.to(
                            expected_revolution_unit
                        ).value
                        duration += (
                            length / feed_per_revolution
                            / spindle_speed.value * 60.0
                        )
                else:
                    duration += (
                        length / event.feed_rate.to(expected_feed_unit).value * 60.0
                    )
            elif isinstance(event, DwellEvent):
                duration += event.duration_seconds
        return cls(rapid, cutting, link, retract, arc, duration, partial,
                   tuple(counts.items()))


@dataclass(frozen=True, slots=True)
class ToolpathArtifact:
    """Published, immutable controller-neutral event stream candidate."""

    artifact_id: ToolpathArtifactId
    source_operation_id: OperationId
    operation_revision: Revision
    computation_token: ComputationToken
    input_fingerprint: DependencyFingerprint
    coordinate_space: CoordinateSpace
    unit: LengthUnit
    setup_id: SetupId
    setup_revision: Revision
    wcs_fingerprint: ContentFingerprint
    tool_assembly_id: ToolAssemblyId
    tool_assembly_fingerprint: ContentFingerprint
    machine_id: MachineDefinitionId | None
    machine_fingerprint: ContentFingerprint | None
    initial_pose: Pose
    events: tuple[AnyToolpathEvent, ...]
    bounds: Bounds3
    statistics: ToolpathStatistics
    diagnostics: tuple[ToolpathDiagnostic, ...]
    completion_status: ToolpathCompletionStatus
    artifact_fingerprint: ContentFingerprint | None
    created_at: str | None = None
    schema_version: int = TOOLPATH_VERSION
    verify_derived: InitVar[bool] = True
    SERIALIZATION_VERSION: ClassVar[int] = TOOLPATH_VERSION

    def __post_init__(self, verify_derived: bool) -> None:
        if type(verify_derived) is not bool:
            raise CamValidationError("Toolpath derived verification flag is invalid")
        if type(self.schema_version) is not int or self.schema_version != TOOLPATH_VERSION:
            from hms_cadcam.cam.domain.errors import UnsupportedCamSchemaError
            raise UnsupportedCamSchemaError("Unsupported toolpath artifact version")
        if not isinstance(self.artifact_id, ToolpathArtifactId) or not isinstance(self.source_operation_id, OperationId):
            raise CamValidationError("Toolpath artifact identity is invalid")
        if not isinstance(self.operation_revision, Revision) or not isinstance(self.computation_token, ComputationToken):
            raise CamValidationError("Toolpath computation provenance is invalid")
        if not isinstance(self.input_fingerprint, DependencyFingerprint) or not isinstance(self.coordinate_space, CoordinateSpace):
            raise CamValidationError("Toolpath input or coordinate space is invalid")
        if self.coordinate_space is not CoordinateSpace.SETUP_WCS:
            raise CamInvariantError("IR v1 movement must be stored in SETUP_WCS")
        if not isinstance(self.unit, LengthUnit) or self.unit is LengthUnit.UNKNOWN:
            raise CamUnitError("Toolpath artifact requires a known unit")
        if not isinstance(self.setup_id, SetupId) or not isinstance(self.setup_revision, Revision) or not isinstance(self.wcs_fingerprint, ContentFingerprint):
            raise CamValidationError("Toolpath setup provenance is invalid")
        if not isinstance(self.tool_assembly_id, ToolAssemblyId) or not isinstance(self.tool_assembly_fingerprint, ContentFingerprint):
            raise CamValidationError("Toolpath tool provenance is invalid")
        if (self.machine_id is None) != (self.machine_fingerprint is None):
            raise CamInvariantError("Machine ID and fingerprint must be both present or absent")
        if self.machine_id is not None and (not isinstance(self.machine_id, MachineDefinitionId) or not isinstance(self.machine_fingerprint, ContentFingerprint)):
            raise CamValidationError("Toolpath machine provenance is invalid")
        if not isinstance(self.initial_pose, Pose) or self.initial_pose.position.unit is not self.unit:
            raise CamUnitError("Toolpath initial pose unit is invalid")
        if not isinstance(self.events, tuple) or any(not _is_event(item) for item in self.events):
            raise CamValidationError("Toolpath events must be an immutable tuple")
        if not isinstance(self.bounds, Bounds3) or self.bounds.minimum.unit is not self.unit:
            raise CamValidationError("Toolpath bounds are invalid")
        if not isinstance(self.statistics, ToolpathStatistics):
            raise CamValidationError("Toolpath statistics are invalid")
        if not isinstance(self.diagnostics, tuple) or any(not isinstance(item, ToolpathDiagnostic) for item in self.diagnostics):
            raise CamValidationError("Toolpath diagnostics are invalid")
        if not isinstance(self.completion_status, ToolpathCompletionStatus):
            raise CamValidationError("Toolpath completion status is invalid")
        if self.artifact_fingerprint is not None and not isinstance(self.artifact_fingerprint, ContentFingerprint):
            raise CamValidationError("Toolpath fingerprint is invalid")
        if self.created_at is not None and (not isinstance(self.created_at, str) or not self.created_at.strip()):
            raise CamValidationError("Toolpath timestamp metadata is invalid")
        self._validate_stream()
        if verify_derived:
            calculated_bounds = calculate_bounds(self.initial_pose, self.events)
            if calculated_bounds != self.bounds:
                raise CamInvariantError("Toolpath bounds do not match event geometry")
            calculated_statistics = ToolpathStatistics.calculate(self.events, self.unit)
            if calculated_statistics != self.statistics:
                raise CamInvariantError("Toolpath statistics do not match event stream")
        from hms_cadcam.cam.toolpath.fingerprint import compute_toolpath_fingerprint
        calculated_fingerprint = compute_toolpath_fingerprint(self)
        if self.artifact_fingerprint is None:
            object.__setattr__(self, "artifact_fingerprint", calculated_fingerprint)
        elif calculated_fingerprint != self.artifact_fingerprint:
            raise CamInvariantError("Toolpath artifact fingerprint verification failed")

    def _validate_stream(self) -> None:
        ids = tuple(item.event_id for item in self.events)
        if len(set(ids)) != len(ids):
            raise CamInvariantError("Toolpath event IDs must be unique")
        current = self.initial_pose
        feed_mode = None
        for index, event in enumerate(self.events):
            if event.sequence_index != index:
                raise CamInvariantError("Toolpath sequence must be contiguous and monotonic")
            if event.source_operation_id != self.source_operation_id:
                raise CamInvariantError("Toolpath event belongs to another operation")
            if isinstance(event, (RapidMove, LinearMove, ArcMove)):
                if not same_pose(current, event.start):
                    raise CamInvariantError("Toolpath movement is discontinuous")
                if event.start.position.unit is not self.unit or event.end.position.unit is not self.unit:
                    raise CamUnitError("Toolpath movement unit differs from artifact")
                if isinstance(event, (LinearMove, ArcMove)):
                    per_revolution = event.feed_rate.unit in {
                        FeedUnit.MM_PER_REVOLUTION,
                        FeedUnit.INCH_PER_REVOLUTION,
                    }
                    expected_mode = (
                        FeedMode.UNITS_PER_REVOLUTION
                        if per_revolution
                        else FeedMode.UNITS_PER_MINUTE
                    )
                    if (
                        (per_revolution and feed_mode is not expected_mode)
                        or (
                            not per_revolution
                            and feed_mode is FeedMode.UNITS_PER_REVOLUTION
                        )
                    ):
                        raise CamInvariantError(
                            "Toolpath feed unit conflicts with active feed mode"
                        )
                current = event.end
            elif isinstance(event, FeedModeEvent):
                feed_mode = event.mode

    def to_dict(self) -> dict[str, Any]:
        """Serialize with the strict versioned Toolpath IR codec."""
        from hms_cadcam.cam.toolpath.codec import artifact_to_dict
        return artifact_to_dict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, max_events: int | None = None) -> "ToolpathArtifact":
        """Deserialize and verify bounds, statistics and content fingerprint."""
        from hms_cadcam.cam.toolpath.codec import artifact_from_dict
        return artifact_from_dict(data, max_events=max_events)

    @classmethod
    def create(cls, *, artifact_id: ToolpathArtifactId, source_operation_id: OperationId,
               operation_revision: Revision, computation_token: ComputationToken,
               input_fingerprint: DependencyFingerprint, coordinate_space: CoordinateSpace,
               unit: LengthUnit, setup_id: SetupId, setup_revision: Revision,
               wcs_fingerprint: ContentFingerprint, tool_assembly_id: ToolAssemblyId,
               tool_assembly_fingerprint: ContentFingerprint,
               machine_id: MachineDefinitionId | None, machine_fingerprint: ContentFingerprint | None,
               initial_pose: Pose, events: tuple[AnyToolpathEvent, ...],
               diagnostics: tuple[ToolpathDiagnostic, ...] = (),
               completion_status: ToolpathCompletionStatus = ToolpathCompletionStatus.COMPLETE,
               created_at: str | None = None) -> "ToolpathArtifact":
        bounds = calculate_bounds(initial_pose, events)
        statistics = ToolpathStatistics.calculate(events, unit)
        return cls(artifact_id, source_operation_id, operation_revision, computation_token,
            input_fingerprint, coordinate_space, unit, setup_id, setup_revision, wcs_fingerprint,
            tool_assembly_id, tool_assembly_fingerprint, machine_id, machine_fingerprint,
            initial_pose, events, bounds, statistics, diagnostics, completion_status, None,
            created_at, TOOLPATH_VERSION, False)


def _is_event(value: object) -> bool:
    from hms_cadcam.cam.toolpath.events import ToolpathEvent
    return isinstance(value, ToolpathEvent)


def calculate_bounds(initial_pose: Pose, events: tuple[AnyToolpathEvent, ...]) -> Bounds3:
    values = [Bounds3.from_points((initial_pose.position,))]
    values.extend(event.bounds for event in events if event.bounds is not None)
    return Bounds3.union(tuple(values))
