"""Native-free SimulationResult presentation and lifecycle registry.

The simulation result deliberately stores collision evidence, not a duplicate of
the sampled path.  This module deterministically re-samples the current,
fingerprinted toolpath in the current WCS and verifies the result statistics
before any viewer backend is allowed to build native objects.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from uuid import UUID

from hms_cadcam.cam.domain import (
    ContentFingerprint,
    DependencyFingerprint,
    DiagnosticSeverity,
    OperationId,
    Revision,
    SimulationRequestId,
    SimulationResultId,
    ToolpathArtifactId,
    WcsFrame,
)
from hms_cadcam.cam.domain.errors import CamValidationError
from hms_cadcam.cam.domain.spatial import Point3
from hms_cadcam.cam.simulation.codec import result_from_dict
from hms_cadcam.cam.simulation.model import (
    SimulationIssue,
    SimulationIssueCategory,
    SimulationIssueCode,
    SimulationResult,
    SimulationStatistics,
    SimulationStatus,
)
from hms_cadcam.cam.simulation.sampling import (
    SampledSegment,
    SamplingOutput,
    SimulationSamplingError,
    sample_toolpath,
)
from hms_cadcam.cam.toolpath import (
    MotionClass,
    ToolpathArtifact,
    ToolpathCompletionStatus,
)
from hms_cadcam.cam.toolpath.geometry import Bounds3


class SimulationPathSemantic(StrEnum):
    """Controller-neutral display semantics for sampled motion."""

    RAPID = "rapid"
    CUTTING = "cutting"
    LINK = "link"
    RETRACT = "retract"
    APPROACH = "approach"


class SimulationMarkerKind(StrEnum):
    """Stable marker identities; labels/localization remain outside the model."""

    TOOL_FIXTURE_COLLISION = "tool_fixture_collision"
    SHANK_STOCK_COLLISION = "shank_stock_collision"
    SHANK_FIXTURE_COLLISION = "shank_fixture_collision"
    HOLDER_STOCK_COLLISION = "holder_stock_collision"
    HOLDER_FIXTURE_COLLISION = "holder_fixture_collision"
    GOUGE = "gouge_detected"
    RAPID_BELOW_SAFE = "rapid_below_safe"
    CLEARANCE_WARNING = "clearance_warning"
    INVALID = "invalid"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True, slots=True)
class SimulationDisplayPolicy:
    """Session-only display bounds which never alter the simulation result."""

    maximum_path_points: int = 50_000
    maximum_markers: int = 1_000

    def __post_init__(self) -> None:
        if type(self.maximum_path_points) is not int or self.maximum_path_points < 2:
            raise CamValidationError("Simulation display path cap is invalid")
        if type(self.maximum_markers) is not int or self.maximum_markers < 1:
            raise CamValidationError("Simulation display marker cap is invalid")


@dataclass(frozen=True, slots=True)
class SimulationPresentationKey:
    project_id: UUID
    operation_id: OperationId
    result_id: SimulationResultId

    def __post_init__(self) -> None:
        if not isinstance(self.project_id, UUID) or self.project_id.int == 0:
            raise CamValidationError("Simulation presentation project ID is invalid")
        if not isinstance(self.operation_id, OperationId):
            raise CamValidationError("Simulation presentation operation ID is invalid")
        if not isinstance(self.result_id, SimulationResultId):
            raise CamValidationError("Simulation presentation result ID is invalid")


@dataclass(frozen=True, slots=True)
class SimulationPathSegment:
    """One sampled event polyline; adjacent events are never joined implicitly."""

    segment_index: int
    event_index: int
    event_id: str
    event_kind: str
    semantic: SimulationPathSemantic
    sample_indices: tuple[int, ...]
    points: tuple[Point3, ...]

    def __post_init__(self) -> None:
        if type(self.segment_index) is not int or self.segment_index < 0:
            raise CamValidationError("Simulation path segment index is invalid")
        if type(self.event_index) is not int or self.event_index < 0:
            raise CamValidationError("Simulation path event index is invalid")
        if not isinstance(self.event_id, str) or not self.event_id:
            raise CamValidationError("Simulation path event ID is invalid")
        if not isinstance(self.event_kind, str) or not self.event_kind:
            raise CamValidationError("Simulation path event kind is invalid")
        if not isinstance(self.semantic, SimulationPathSemantic):
            raise CamValidationError("Simulation path semantic is invalid")
        if (
            not isinstance(self.sample_indices, tuple)
            or len(self.sample_indices) < 2
            or any(type(value) is not int or value < 0 for value in self.sample_indices)
            or tuple(sorted(set(self.sample_indices))) != self.sample_indices
        ):
            raise CamValidationError("Simulation path sample provenance is invalid")
        if (
            not isinstance(self.points, tuple)
            or len(self.points) != len(self.sample_indices)
            or any(not isinstance(point, Point3) for point in self.points)
        ):
            raise CamValidationError("Simulation path points are invalid")


@dataclass(frozen=True, slots=True)
class SimulationIssueMarker:
    """Native-free marker metadata suitable for a future issue panel/controller."""

    marker_id: str
    kind: SimulationMarkerKind
    category: SimulationIssueCategory
    severity: DiagnosticSeverity
    code: SimulationIssueCode
    operation_id: OperationId
    result_id: SimulationResultId
    artifact_id: ToolpathArtifactId
    issue_index: int
    segment_index: int | None
    event_index: int | None
    sample_index: int | None
    world_point: Point3 | None
    bounds: Bounds3 | None
    entity_ids: tuple[str, ...]
    evidence: tuple[tuple[str, str], ...]
    evidence_fingerprint: ContentFingerprint

    def __post_init__(self) -> None:
        if (
            not isinstance(self.marker_id, str)
            or len(self.marker_id) != 64
            or any(character not in "0123456789abcdef" for character in self.marker_id)
        ):
            raise CamValidationError("Simulation marker ID is invalid")
        if not isinstance(self.kind, SimulationMarkerKind):
            raise CamValidationError("Simulation marker kind is invalid")
        if not isinstance(self.category, SimulationIssueCategory):
            raise CamValidationError("Simulation marker category is invalid")
        if not isinstance(self.severity, DiagnosticSeverity):
            raise CamValidationError("Simulation marker severity is invalid")
        if not isinstance(self.code, SimulationIssueCode):
            raise CamValidationError("Simulation marker code is invalid")
        if not isinstance(self.operation_id, OperationId):
            raise CamValidationError("Simulation marker operation ID is invalid")
        if not isinstance(self.result_id, SimulationResultId):
            raise CamValidationError("Simulation marker result ID is invalid")
        if not isinstance(self.artifact_id, ToolpathArtifactId):
            raise CamValidationError("Simulation marker artifact ID is invalid")
        if type(self.issue_index) is not int or self.issue_index < 0:
            raise CamValidationError("Simulation marker issue index is invalid")
        for value in (self.segment_index, self.event_index, self.sample_index):
            if value is not None and (type(value) is not int or value < 0):
                raise CamValidationError("Simulation marker provenance is invalid")
        if self.world_point is not None and not isinstance(self.world_point, Point3):
            raise CamValidationError("Simulation marker point is invalid")
        if self.bounds is not None and not isinstance(self.bounds, Bounds3):
            raise CamValidationError("Simulation marker bounds are invalid")
        if not isinstance(self.entity_ids, tuple) or any(
            not isinstance(value, str) or not value for value in self.entity_ids
        ):
            raise CamValidationError("Simulation marker entity IDs are invalid")
        if not isinstance(self.evidence, tuple) or any(
            not isinstance(item, tuple)
            or len(item) != 2
            or not all(isinstance(value, str) and value for value in item)
            for item in self.evidence
        ):
            raise CamValidationError("Simulation marker evidence is invalid")
        if not isinstance(self.evidence_fingerprint, ContentFingerprint):
            raise CamValidationError("Simulation marker evidence fingerprint is invalid")

    @property
    def anchor_point(self) -> Point3 | None:
        """Return a render anchor without inventing geometry for metadata-only issues."""
        if self.world_point is not None:
            return self.world_point
        if self.bounds is None:
            return None
        minimum, maximum = self.bounds.minimum, self.bounds.maximum
        return Point3(
            (minimum.x + maximum.x) / 2.0,
            (minimum.y + maximum.y) / 2.0,
            (minimum.z + maximum.z) / 2.0,
            minimum.unit,
        )


@dataclass(frozen=True, slots=True)
class SimulationIssueEvidenceSummary:
    severity: DiagnosticSeverity
    category: SimulationIssueCategory
    code: SimulationIssueCode
    count: int
    evidence_fingerprints: tuple[ContentFingerprint, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.severity, DiagnosticSeverity):
            raise CamValidationError("Simulation issue summary severity is invalid")
        if not isinstance(self.category, SimulationIssueCategory):
            raise CamValidationError("Simulation issue summary category is invalid")
        if not isinstance(self.code, SimulationIssueCode):
            raise CamValidationError("Simulation issue summary code is invalid")
        if type(self.count) is not int or self.count <= 0:
            raise CamValidationError("Simulation issue summary count is invalid")
        if (
            not isinstance(self.evidence_fingerprints, tuple)
            or len(self.evidence_fingerprints) != self.count
            or any(
                not isinstance(value, ContentFingerprint)
                for value in self.evidence_fingerprints
            )
        ):
            raise CamValidationError("Simulation issue summary evidence is invalid")


@dataclass(frozen=True, slots=True)
class SimulationDisplayContext:
    """Snapshot of all mutable state used by stale-result guards."""

    project_id: UUID
    project_generation: int
    operation_id: OperationId
    operation_revision: Revision
    operation_exists: bool
    operation_enabled: bool
    artifact_id: ToolpathArtifactId
    artifact_fingerprint: ContentFingerprint
    simulation_input_fingerprint: DependencyFingerprint
    current_result_id: SimulationResultId
    current_result_fingerprint: ContentFingerprint

    def __post_init__(self) -> None:
        if not isinstance(self.project_id, UUID) or self.project_id.int == 0:
            raise CamValidationError("Simulation display project ID is invalid")
        if type(self.project_generation) is not int or self.project_generation <= 0:
            raise CamValidationError("Simulation display project generation is invalid")
        if not isinstance(self.operation_id, OperationId):
            raise CamValidationError("Simulation display operation ID is invalid")
        if not isinstance(self.operation_revision, Revision):
            raise CamValidationError("Simulation display operation revision is invalid")
        if type(self.operation_exists) is not bool or type(self.operation_enabled) is not bool:
            raise CamValidationError("Simulation display operation state is invalid")
        if not isinstance(self.artifact_id, ToolpathArtifactId):
            raise CamValidationError("Simulation display artifact ID is invalid")
        if not isinstance(self.artifact_fingerprint, ContentFingerprint):
            raise CamValidationError("Simulation display artifact fingerprint is invalid")
        if not isinstance(self.simulation_input_fingerprint, DependencyFingerprint):
            raise CamValidationError("Simulation display input fingerprint is invalid")
        if not isinstance(self.current_result_id, SimulationResultId):
            raise CamValidationError("Simulation display current result ID is invalid")
        if not isinstance(self.current_result_fingerprint, ContentFingerprint):
            raise CamValidationError("Simulation display current result fingerprint is invalid")


@dataclass(frozen=True, slots=True)
class SimulationDisplayRequest:
    operation_id: OperationId
    project_id: UUID
    project_generation: int
    sequence: int


@dataclass(frozen=True, slots=True)
class SimulationPresentation:
    """Complete controller-neutral representation of one current result."""

    key: SimulationPresentationKey
    request_id: SimulationRequestId
    artifact_id: ToolpathArtifactId
    artifact_fingerprint: ContentFingerprint
    input_fingerprint: DependencyFingerprint
    result_fingerprint: ContentFingerprint
    operation_revision: Revision
    project_generation: int
    status: SimulationStatus
    path_segments: tuple[SimulationPathSegment, ...]
    markers: tuple[SimulationIssueMarker, ...]
    issue_evidence: tuple[SimulationIssueEvidenceSummary, ...]
    statistics: SimulationStatistics
    displayed_path_point_count: int
    total_path_point_count: int
    displayed_marker_count: int
    total_marker_count: int
    path_cap_overflow: bool = False
    marker_cap_overflow: bool = False
    visible: bool = True

    def __post_init__(self) -> None:
        _validate_presentation(self)

    @classmethod
    def from_result(
        cls,
        *,
        result: SimulationResult,
        artifact: ToolpathArtifact,
        wcs: WcsFrame,
        context: SimulationDisplayContext,
        policy: SimulationDisplayPolicy | None = None,
    ) -> "SimulationPresentation":
        """Validate current provenance, re-sample, decimate, and build metadata."""
        if type(result) is not SimulationResult:
            raise CamValidationError("Simulation result type is invalid")
        if type(artifact) is not ToolpathArtifact:
            raise CamValidationError("Simulation source artifact type is invalid")
        if type(wcs) is not WcsFrame:
            raise CamValidationError("Simulation display WCS type is invalid")
        if type(context) is not SimulationDisplayContext:
            raise CamValidationError("Simulation display context type is invalid")
        display_policy = policy or SimulationDisplayPolicy()
        if type(display_policy) is not SimulationDisplayPolicy:
            raise CamValidationError("Simulation display policy type is invalid")
        # Strict codec reconstruction catches future/malformed models and verifies the
        # result fingerprint without relying on object identity.
        if result_from_dict(result.to_dict()) != result:
            raise CamValidationError("Simulation result codec round-trip is unstable")
        _validate_source(result, artifact, wcs, context)
        try:
            sampling = sample_toolpath(
                artifact=artifact,
                wcs=wcs,
                policy=result.sampling_policy,
            )
        except SimulationSamplingError as error:
            raise CamValidationError(
                f"Simulation display sampling failed: {error}"
            ) from error
        _validate_sampling(result, sampling)
        _validate_issues(result, sampling)
        selected_samples, path_overflow = _display_sample_indices(
            sampling,
            result.issues,
            display_policy.maximum_path_points,
        )
        path_segments = _path_segments(artifact, sampling, selected_samples)
        all_markers = tuple(
            _marker(result, issue, issue_index)
            for issue_index, issue in enumerate(result.issues)
        )
        marker_indices, marker_overflow = _display_marker_indices(
            result.issues,
            display_policy.maximum_markers,
        )
        markers = tuple(all_markers[index] for index in marker_indices)
        return cls(
            key=SimulationPresentationKey(
                context.project_id,
                result.operation_id,
                result.result_id,
            ),
            request_id=result.request_id,
            artifact_id=result.artifact_id,
            artifact_fingerprint=result.artifact_fingerprint,
            input_fingerprint=result.input_fingerprint,
            result_fingerprint=result.result_fingerprint,
            operation_revision=context.operation_revision,
            project_generation=context.project_generation,
            status=result.status,
            path_segments=path_segments,
            markers=markers,
            issue_evidence=_issue_summaries(all_markers),
            statistics=result.statistics,
            displayed_path_point_count=len(selected_samples),
            total_path_point_count=len(sampling.samples),
            displayed_marker_count=len(markers),
            total_marker_count=len(all_markers),
            path_cap_overflow=path_overflow,
            marker_cap_overflow=marker_overflow,
        )


class SimulationPresentationRegistry:
    """Project-scoped current-result registry, independent of CAD selection."""

    def __init__(self) -> None:
        self._project_id: UUID | None = None
        self._generation: int | None = None
        self._items: dict[OperationId, SimulationPresentation] = {}
        self._requests: dict[OperationId, int] = {}
        self._sequence = 0

    @property
    def project_id(self) -> UUID | None:
        return self._project_id

    @property
    def generation(self) -> int | None:
        return self._generation

    @property
    def presentations(self) -> tuple[SimulationPresentation, ...]:
        return tuple(self._items[key] for key in sorted(self._items, key=str))

    def bind_project(self, project_id: UUID | None, generation: int | None) -> None:
        if (project_id is None) != (generation is None):
            raise CamValidationError("Simulation project binding must be paired")
        if project_id is not None and (
            not isinstance(project_id, UUID)
            or project_id.int == 0
            or type(generation) is not int
            or generation <= 0
        ):
            raise CamValidationError("Simulation project binding is invalid")
        if project_id != self._project_id or generation != self._generation:
            self.clear()
            self._project_id = project_id
            self._generation = generation

    def request_display(
        self,
        operation_id: OperationId,
        *,
        generation: int,
    ) -> SimulationDisplayRequest | None:
        if (
            not isinstance(operation_id, OperationId)
            or self._project_id is None
            or generation != self._generation
        ):
            return None
        self._sequence += 1
        self._requests[operation_id] = self._sequence
        return SimulationDisplayRequest(
            operation_id,
            self._project_id,
            generation,
            self._sequence,
        )

    def prepare(
        self,
        *,
        result: SimulationResult,
        artifact: ToolpathArtifact,
        wcs: WcsFrame,
        context: SimulationDisplayContext,
        request: SimulationDisplayRequest | None = None,
        policy: SimulationDisplayPolicy | None = None,
    ) -> SimulationPresentation | None:
        if not self._is_current_context(context, request):
            return None
        candidate = SimulationPresentation.from_result(
            result=result,
            artifact=artifact,
            wcs=wcs,
            context=context,
            policy=policy,
        )
        previous = self._items.get(result.operation_id)
        if previous is not None:
            candidate = replace(candidate, visible=previous.visible)
        return candidate

    def commit(
        self,
        candidate: SimulationPresentation,
        *,
        request: SimulationDisplayRequest | None = None,
    ) -> bool:
        if type(candidate) is not SimulationPresentation:
            raise CamValidationError("Simulation presentation candidate is invalid")
        context_matches = (
            candidate.key.project_id == self._project_id
            and candidate.project_generation == self._generation
        )
        request_matches = request is None or (
            request.operation_id == candidate.key.operation_id
            and request.project_id == candidate.key.project_id
            and request.project_generation == candidate.project_generation
            and self._requests.get(candidate.key.operation_id) == request.sequence
        )
        if not context_matches or not request_matches:
            return False
        if request is None:
            self._sequence += 1
            self._requests[candidate.key.operation_id] = self._sequence
        self._items[candidate.key.operation_id] = candidate
        return True

    def display(
        self,
        *,
        result: SimulationResult,
        artifact: ToolpathArtifact,
        wcs: WcsFrame,
        context: SimulationDisplayContext,
        request: SimulationDisplayRequest | None = None,
        policy: SimulationDisplayPolicy | None = None,
    ) -> bool:
        candidate = self.prepare(
            result=result,
            artifact=artifact,
            wcs=wcs,
            context=context,
            request=request,
            policy=policy,
        )
        return candidate is not None and self.commit(candidate, request=request)

    def current(self, operation_id: OperationId) -> SimulationPresentation | None:
        return self._items.get(operation_id)

    def restore(
        self,
        operation_id: OperationId,
        previous: SimulationPresentation | None,
    ) -> None:
        """Restore metadata after a native swap/remove failure."""
        if previous is None:
            self._items.pop(operation_id, None)
        else:
            self._items[operation_id] = previous

    def set_visible(self, operation_id: OperationId, visible: bool) -> None:
        if type(visible) is not bool:
            raise CamValidationError("Simulation visibility must be bool")
        self._items[operation_id] = replace(self._items[operation_id], visible=visible)

    def lookup_issue(
        self,
        *,
        project_id: UUID,
        operation_id: OperationId,
        result_id: SimulationResultId,
        marker_id: str,
    ) -> SimulationIssueMarker | None:
        if project_id != self._project_id:
            return None
        presentation = self._items.get(operation_id)
        if presentation is None or presentation.key.result_id != result_id:
            return None
        return next(
            (marker for marker in presentation.markers if marker.marker_id == marker_id),
            None,
        )

    def remove(self, operation_id: OperationId) -> None:
        self._sequence += 1
        self._requests[operation_id] = self._sequence
        self._items.pop(operation_id, None)

    def clear(self) -> None:
        self._sequence += 1
        self._items.clear()
        self._requests.clear()

    def _is_current_context(
        self,
        context: SimulationDisplayContext,
        request: SimulationDisplayRequest | None,
    ) -> bool:
        if (
            context.project_id != self._project_id
            or context.project_generation != self._generation
            or not context.operation_exists
            or not context.operation_enabled
        ):
            return False
        if request is None:
            return True
        return (
            request.operation_id == context.operation_id
            and request.project_id == context.project_id
            and request.project_generation == context.project_generation
            and self._requests.get(context.operation_id) == request.sequence
        )


def _validate_source(
    result: SimulationResult,
    artifact: ToolpathArtifact,
    wcs: WcsFrame,
    context: SimulationDisplayContext,
) -> None:
    fingerprint = artifact.artifact_fingerprint
    if (
        not context.operation_exists
        or not context.operation_enabled
        or result.operation_id != context.operation_id
        or artifact.source_operation_id != context.operation_id
        or result.artifact_id != artifact.artifact_id
        or result.artifact_id != context.artifact_id
        or fingerprint is None
        or result.artifact_fingerprint != fingerprint
        or context.artifact_fingerprint != fingerprint
        or result.input_fingerprint != context.simulation_input_fingerprint
        or result.result_id != context.current_result_id
        or result.result_fingerprint != context.current_result_fingerprint
        or artifact.operation_revision != context.operation_revision
        or artifact.completion_status is not ToolpathCompletionStatus.COMPLETE
        or artifact.wcs_fingerprint != ContentFingerprint.from_payload(wcs.to_dict())
        or artifact.unit is not wcs.origin.unit
    ):
        raise CamValidationError("Simulation result/source is stale or mismatched")
    if result.status not in {
        SimulationStatus.PASS,
        SimulationStatus.WARN,
        SimulationStatus.FAIL,
    }:
        raise CamValidationError("Simulation result status is unsupported")
    if any(
        issue.operation_id != result.operation_id
        or issue.artifact_id != result.artifact_id
        for issue in result.issues
    ):
        raise CamValidationError("Simulation issue belongs to another source")


def _validate_sampling(result: SimulationResult, sampling: SamplingOutput) -> None:
    points = tuple(sample.world_pose.position for sample in sampling.samples)
    if not points:
        raise CamValidationError("Simulation display sampling is empty")
    if (
        result.statistics.sampled_point_count != len(sampling.samples)
        or result.statistics.sampled_segment_count != len(sampling.segments)
        or result.statistics.bounds != Bounds3.from_points(points)
    ):
        raise CamValidationError("Simulation result statistics do not match re-sampling")


def _validate_issues(result: SimulationResult, sampling: SamplingOutput) -> None:
    for issue in result.issues:
        _validate_issue_shape(issue)
        segment: SampledSegment | None = None
        if issue.segment_index is not None:
            if issue.segment_index >= len(sampling.segments):
                raise CamValidationError("Simulation issue segment index is invalid")
            segment = sampling.segments[issue.segment_index]
        if issue.event_index is not None:
            matching = tuple(
                item for item in sampling.segments
                if item.event_index == issue.event_index
            )
            if not matching or (segment is not None and segment not in matching):
                raise CamValidationError("Simulation issue event index is invalid")
            if segment is None:
                segment = matching[0]
        if issue.sample_index is not None:
            if issue.sample_index >= len(sampling.samples):
                raise CamValidationError("Simulation issue sample index is invalid")
            if segment is not None and issue.sample_index not in segment.sample_indices:
                raise CamValidationError("Simulation issue sample provenance is malformed")
            point = sampling.samples[issue.sample_index].world_pose.position
            if issue.world_point is not None and issue.world_point != point:
                raise CamValidationError("Simulation issue world point is inconsistent")
        for point in (issue.world_point,):
            if point is not None and point.unit is not result.statistics.bounds.minimum.unit:
                raise CamValidationError("Simulation issue point unit is inconsistent")
        if (
            issue.bounds is not None
            and issue.bounds.minimum.unit is not result.statistics.bounds.minimum.unit
        ):
            raise CamValidationError("Simulation issue bounds unit is inconsistent")
        _marker_kind(issue)


def _validate_issue_shape(issue: SimulationIssue) -> None:
    """Reject semantically mixed issue payloads before marker conversion."""
    collision_codes = {
        SimulationIssueCode.TOOL_FIXTURE_COLLISION,
        SimulationIssueCode.SHANK_STOCK_COLLISION,
        SimulationIssueCode.SHANK_FIXTURE_COLLISION,
        SimulationIssueCode.HOLDER_STOCK_COLLISION,
        SimulationIssueCode.HOLDER_FIXTURE_COLLISION,
    }
    if issue.code in collision_codes and (
        issue.category is not SimulationIssueCategory.COLLISION
        or issue.severity is not DiagnosticSeverity.ERROR
    ):
        raise CamValidationError("Simulation collision issue category/severity is malformed")
    if issue.code is SimulationIssueCode.GOUGE_DETECTED and (
        issue.category not in {
            SimulationIssueCategory.COLLISION,
            SimulationIssueCategory.GOUGE,
        }
        or issue.severity is not DiagnosticSeverity.ERROR
    ):
        raise CamValidationError("Simulation gouge issue category/severity is malformed")
    if issue.code in {
        SimulationIssueCode.RAPID_BELOW_SAFE,
        SimulationIssueCode.FAILED,
    } and (
        issue.category is not SimulationIssueCategory.CLEARANCE_WARNING
        or issue.severity is not DiagnosticSeverity.WARNING
    ):
        raise CamValidationError("Simulation clearance issue category/severity is malformed")
    if issue.code is SimulationIssueCode.UNSUPPORTED_GEOMETRY and (
        issue.category is not SimulationIssueCategory.UNSUPPORTED_GEOMETRY
        or issue.severity not in {
            DiagnosticSeverity.WARNING,
            DiagnosticSeverity.ERROR,
        }
    ):
        raise CamValidationError("Simulation unsupported issue category/severity is malformed")
    if issue.category is SimulationIssueCategory.INVALID_ARTIFACT and issue.severity not in {
        DiagnosticSeverity.WARNING,
        DiagnosticSeverity.ERROR,
    }:
        raise CamValidationError("Simulation invalid issue severity is malformed")


def _display_sample_indices(
    sampling: SamplingOutput,
    issues: tuple[SimulationIssue, ...],
    cap: int,
) -> tuple[tuple[int, ...], bool]:
    mandatory: set[int] = set()
    for segment in sampling.segments:
        mandatory.update((segment.sample_indices[0], segment.sample_indices[-1]))
    for issue in issues:
        if issue.sample_index is None:
            continue
        mandatory.add(issue.sample_index)
        for segment in sampling.segments:
            if issue.sample_index not in segment.sample_indices:
                continue
            local = segment.sample_indices.index(issue.sample_index)
            if local > 0:
                mandatory.add(segment.sample_indices[local - 1])
            if local + 1 < len(segment.sample_indices):
                mandatory.add(segment.sample_indices[local + 1])
    total_indices = tuple(range(len(sampling.samples)))
    if len(total_indices) <= cap:
        return total_indices, False
    # The cap is a target: mandatory endpoints/evidence neighbours are never lost.
    selected = set(mandatory)
    available = tuple(index for index in total_indices if index not in selected)
    remaining = max(0, cap - len(selected))
    selected.update(_evenly_spaced(available, remaining))
    return tuple(sorted(selected)), len(selected) > cap


def _evenly_spaced(values: tuple[int, ...], count: int) -> tuple[int, ...]:
    if count <= 0 or not values:
        return ()
    if count >= len(values):
        return values
    if count == 1:
        return (values[len(values) // 2],)
    positions = {
        round(index * (len(values) - 1) / (count - 1))
        for index in range(count)
    }
    return tuple(values[position] for position in sorted(positions))


def _path_segments(
    artifact: ToolpathArtifact,
    sampling: SamplingOutput,
    selected: tuple[int, ...],
) -> tuple[SimulationPathSegment, ...]:
    selected_set = set(selected)
    result: list[SimulationPathSegment] = []
    for segment in sampling.segments:
        indices = tuple(
            index for index in segment.sample_indices if index in selected_set
        )
        if len(indices) < 2:
            raise CamValidationError("Simulation display decimation removed an endpoint")
        event = artifact.events[segment.event_index]
        result.append(
            SimulationPathSegment(
                segment_index=segment.segment_index,
                event_index=segment.event_index,
                event_id=segment.event_id,
                event_kind=segment.event_kind,
                semantic=_path_semantic(segment.motion_class, event.provenance),
                sample_indices=indices,
                points=tuple(sampling.samples[index].world_pose.position for index in indices),
            )
        )
    return tuple(result)


def _path_semantic(
    motion_class: MotionClass,
    provenance: str,
) -> SimulationPathSemantic:
    if provenance.endswith(".approach") or ".approach." in provenance:
        return SimulationPathSemantic.APPROACH
    if motion_class is MotionClass.CUTTING:
        return SimulationPathSemantic.CUTTING
    if motion_class is MotionClass.LINK:
        return SimulationPathSemantic.LINK
    if motion_class is MotionClass.RETRACT:
        return SimulationPathSemantic.RETRACT
    return SimulationPathSemantic.RAPID


def _display_marker_indices(
    issues: tuple[SimulationIssue, ...],
    cap: int,
) -> tuple[tuple[int, ...], bool]:
    if len(issues) <= cap:
        return tuple(range(len(issues))), False
    errors = tuple(
        index for index, issue in enumerate(issues)
        if issue.severity is DiagnosticSeverity.ERROR
    )
    remaining = max(0, cap - len(errors))
    lower = tuple(
        index for index, issue in enumerate(issues)
        if issue.severity is not DiagnosticSeverity.ERROR
    )
    selected = tuple(sorted((*errors, *lower[:remaining])))
    return selected, len(selected) > cap


def _marker(
    result: SimulationResult,
    issue: SimulationIssue,
    issue_index: int,
) -> SimulationIssueMarker:
    evidence_payload = {
        "operation_id": str(result.operation_id),
        "result_id": str(result.result_id),
        "artifact_id": str(result.artifact_id),
        "issue_index": issue_index,
        "category": issue.category.value,
        "severity": issue.severity.value,
        "code": issue.code.value,
        "segment_index": issue.segment_index,
        "event_index": issue.event_index,
        "sample_index": issue.sample_index,
        "world_point": issue.world_point.to_dict() if issue.world_point else None,
        "bounds": issue.bounds.to_dict() if issue.bounds else None,
        "entity_ids": list(issue.involved_entities),
        "evidence": [list(item) for item in issue.evidence],
    }
    evidence_fingerprint = ContentFingerprint.from_payload(evidence_payload)
    return SimulationIssueMarker(
        marker_id=evidence_fingerprint.digest,
        kind=_marker_kind(issue),
        category=issue.category,
        severity=issue.severity,
        code=issue.code,
        operation_id=result.operation_id,
        result_id=result.result_id,
        artifact_id=result.artifact_id,
        issue_index=issue_index,
        segment_index=issue.segment_index,
        event_index=issue.event_index,
        sample_index=issue.sample_index,
        world_point=issue.world_point,
        bounds=issue.bounds,
        entity_ids=issue.involved_entities,
        evidence=issue.evidence,
        evidence_fingerprint=evidence_fingerprint,
    )


def _marker_kind(issue: SimulationIssue) -> SimulationMarkerKind:
    direct = {
        SimulationIssueCode.TOOL_FIXTURE_COLLISION:
            SimulationMarkerKind.TOOL_FIXTURE_COLLISION,
        SimulationIssueCode.SHANK_STOCK_COLLISION:
            SimulationMarkerKind.SHANK_STOCK_COLLISION,
        SimulationIssueCode.SHANK_FIXTURE_COLLISION:
            SimulationMarkerKind.SHANK_FIXTURE_COLLISION,
        SimulationIssueCode.HOLDER_STOCK_COLLISION:
            SimulationMarkerKind.HOLDER_STOCK_COLLISION,
        SimulationIssueCode.HOLDER_FIXTURE_COLLISION:
            SimulationMarkerKind.HOLDER_FIXTURE_COLLISION,
        SimulationIssueCode.GOUGE_DETECTED: SimulationMarkerKind.GOUGE,
        SimulationIssueCode.RAPID_BELOW_SAFE:
            SimulationMarkerKind.RAPID_BELOW_SAFE,
    }
    if issue.code in direct:
        return direct[issue.code]
    if issue.category is SimulationIssueCategory.CLEARANCE_WARNING:
        return SimulationMarkerKind.CLEARANCE_WARNING
    if issue.category is SimulationIssueCategory.INVALID_ARTIFACT:
        return SimulationMarkerKind.INVALID
    if issue.category is SimulationIssueCategory.UNSUPPORTED_GEOMETRY:
        return SimulationMarkerKind.UNSUPPORTED
    raise CamValidationError("Simulation issue category/code cannot be presented")


def _issue_summaries(
    markers: tuple[SimulationIssueMarker, ...],
) -> tuple[SimulationIssueEvidenceSummary, ...]:
    groups: dict[
        tuple[DiagnosticSeverity, SimulationIssueCategory, SimulationIssueCode],
        list[ContentFingerprint],
    ] = {}
    for marker in markers:
        key = (marker.severity, marker.category, marker.code)
        groups.setdefault(key, []).append(marker.evidence_fingerprint)
    severity_rank = {
        DiagnosticSeverity.ERROR: 0,
        DiagnosticSeverity.WARNING: 1,
        DiagnosticSeverity.INFO: 2,
    }
    return tuple(
        SimulationIssueEvidenceSummary(
            severity,
            category,
            code,
            len(groups[(severity, category, code)]),
            tuple(groups[(severity, category, code)]),
        )
        for severity, category, code in sorted(
            groups,
            key=lambda value: (
                severity_rank[value[0]],
                value[1].value,
                value[2].value,
            ),
        )
    )


def _validate_presentation(value: SimulationPresentation) -> None:
    if not isinstance(value.key, SimulationPresentationKey):
        raise CamValidationError("Simulation presentation key is invalid")
    if not isinstance(value.request_id, SimulationRequestId):
        raise CamValidationError("Simulation presentation request ID is invalid")
    if not isinstance(value.artifact_id, ToolpathArtifactId):
        raise CamValidationError("Simulation presentation artifact ID is invalid")
    if not isinstance(value.artifact_fingerprint, ContentFingerprint):
        raise CamValidationError("Simulation presentation artifact fingerprint is invalid")
    if not isinstance(value.input_fingerprint, DependencyFingerprint):
        raise CamValidationError("Simulation presentation input fingerprint is invalid")
    if not isinstance(value.result_fingerprint, ContentFingerprint):
        raise CamValidationError("Simulation presentation result fingerprint is invalid")
    if not isinstance(value.operation_revision, Revision):
        raise CamValidationError("Simulation presentation operation revision is invalid")
    if type(value.project_generation) is not int or value.project_generation <= 0:
        raise CamValidationError("Simulation presentation project generation is invalid")
    if not isinstance(value.status, SimulationStatus):
        raise CamValidationError("Simulation presentation status is invalid")
    if not isinstance(value.path_segments, tuple) or any(
        not isinstance(item, SimulationPathSegment) for item in value.path_segments
    ):
        raise CamValidationError("Simulation presentation path is invalid")
    if tuple(item.segment_index for item in value.path_segments) != tuple(
        range(len(value.path_segments))
    ):
        raise CamValidationError("Simulation presentation path order is invalid")
    if not isinstance(value.markers, tuple) or any(
        not isinstance(item, SimulationIssueMarker) for item in value.markers
    ):
        raise CamValidationError("Simulation presentation markers are invalid")
    if len({item.marker_id for item in value.markers}) != len(value.markers):
        raise CamValidationError("Simulation presentation marker IDs are not unique")
    if any(
        item.operation_id != value.key.operation_id
        or item.result_id != value.key.result_id
        or item.artifact_id != value.artifact_id
        for item in value.markers
    ):
        raise CamValidationError("Simulation presentation marker source is invalid")
    if not isinstance(value.issue_evidence, tuple) or any(
        not isinstance(item, SimulationIssueEvidenceSummary)
        for item in value.issue_evidence
    ):
        raise CamValidationError("Simulation presentation evidence is invalid")
    if not isinstance(value.statistics, SimulationStatistics):
        raise CamValidationError("Simulation presentation statistics are invalid")
    for displayed, total, name in (
        (value.displayed_path_point_count, value.total_path_point_count, "path"),
        (value.displayed_marker_count, value.total_marker_count, "marker"),
    ):
        if (
            type(displayed) is not int
            or type(total) is not int
            or displayed < 0
            or total < displayed
        ):
            raise CamValidationError(f"Simulation presentation {name} counts are invalid")
    unique_indices = {
        index for segment in value.path_segments for index in segment.sample_indices
    }
    # A valid artifact may contain only a dwell/marker event.  Its initial sample
    # has no motion segment, so it is intentionally represented only by the
    # displayed count/status presentation rather than fabricated geometry.
    if len(unique_indices) > value.displayed_path_point_count:
        raise CamValidationError("Simulation presentation displayed point count is invalid")
    if len(value.markers) != value.displayed_marker_count:
        raise CamValidationError("Simulation presentation displayed marker count is invalid")
    if any(
        type(flag) is not bool
        for flag in (value.path_cap_overflow, value.marker_cap_overflow, value.visible)
    ):
        raise CamValidationError("Simulation presentation flags are invalid")
