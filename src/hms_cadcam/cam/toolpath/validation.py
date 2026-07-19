"""Validation and 7A.4 publish contracts for Toolpath IR candidates."""

from __future__ import annotations

from dataclasses import dataclass, replace

from hms_cadcam.cam.domain.errors import CamValidationError
from hms_cadcam.cam.domain.operation import ArtifactStatus, ComputationToken, DirtyReason, Operation
from hms_cadcam.cam.domain.revision import DependencyFingerprint
from hms_cadcam.cam.toolpath.events import ArcMove, LinearMove, RapidMove
from hms_cadcam.cam.toolpath.fingerprint import compute_toolpath_fingerprint
from hms_cadcam.cam.toolpath.geometry import Pose, same_pose
from hms_cadcam.cam.toolpath.model import (
    ToolpathArtifact, ToolpathDiagnostic, ToolpathDiagnosticCode,
)
from hms_cadcam.cam.domain.operation import DiagnosticSeverity


def validate_event_stream(initial_pose: Pose | None, events: tuple[object, ...],
                          *, max_events: int | None = None) -> tuple[ToolpathDiagnostic, ...]:
    """Inspect stream structure without requiring construction of an artifact."""
    diagnostics: list[ToolpathDiagnostic] = []
    if initial_pose is None:
        diagnostics.append(ToolpathDiagnostic(DiagnosticSeverity.ERROR,
            ToolpathDiagnosticCode.MISSING_INITIAL_POSITION, "Initial tool position is missing"))
        return tuple(diagnostics)
    if max_events is not None and (type(max_events) is not int or max_events < 0 or len(events) > max_events):
        diagnostics.append(ToolpathDiagnostic(DiagnosticSeverity.ERROR,
            ToolpathDiagnosticCode.EVENT_LIMIT_EXCEEDED, "Toolpath event limit exceeded"))
    ids: set[object] = set()
    current = initial_pose
    for index, event in enumerate(events):
        if not hasattr(event, "event_id") or not hasattr(event, "sequence_index"):
            diagnostics.append(ToolpathDiagnostic(DiagnosticSeverity.ERROR,
                ToolpathDiagnosticCode.MALFORMED_EVENT, "Malformed toolpath event", (("index", str(index)),)))
            continue
        if event.event_id in ids:
            diagnostics.append(ToolpathDiagnostic(DiagnosticSeverity.ERROR,
                ToolpathDiagnosticCode.DUPLICATE_EVENT_ID, "Duplicate toolpath event ID", (("index", str(index)),)))
        ids.add(event.event_id)
        if event.sequence_index != index:
            diagnostics.append(ToolpathDiagnostic(DiagnosticSeverity.ERROR,
                ToolpathDiagnosticCode.NON_MONOTONIC_SEQUENCE, "Toolpath sequence is not contiguous", (("index", str(index)),)))
        if isinstance(event, (RapidMove, LinearMove, ArcMove)):
            if not same_pose(current, event.start):
                diagnostics.append(ToolpathDiagnostic(DiagnosticSeverity.ERROR,
                    ToolpathDiagnosticCode.DISCONTINUITY, "Movement start does not match current pose", (("index", str(index)),)))
            current = event.end
    return tuple(diagnostics)


@dataclass(frozen=True, slots=True)
class ToolpathPublishResult:
    operation: Operation
    artifact: ToolpathArtifact | None
    accepted: bool
    reason: str | None = None


def publish_toolpath(operation: Operation, candidate: ToolpathArtifact,
                     token: ComputationToken, current_input: DependencyFingerprint,
                     *, operation_exists: bool = True) -> ToolpathPublishResult:
    """Publish only a current, verified candidate; stale candidates never overwrite."""
    if not isinstance(operation, Operation) or not isinstance(candidate, ToolpathArtifact):
        raise CamValidationError("Toolpath publish inputs are invalid")
    if not isinstance(token, ComputationToken) or not isinstance(current_input, DependencyFingerprint):
        raise CamValidationError("Toolpath publish token or fingerprint is invalid")
    state = operation.artifact_state
    current_token = state.token
    if state.status is not ArtifactStatus.COMPUTING or token != current_token:
        return ToolpathPublishResult(operation, None, False, "stale_token")
    provenance_valid = (operation_exists and operation.enabled and
        candidate.source_operation_id == operation.operation_id and
        candidate.operation_revision == operation.revision and
        candidate.computation_token == token and
        candidate.input_fingerprint == current_input and
        candidate.artifact_fingerprint == compute_toolpath_fingerprint(candidate))
    if not provenance_valid:
        dirty = state.mark_dirty(DirtyReason.UPSTREAM_CHANGED)
        return ToolpathPublishResult(replace(operation, artifact_state=dirty), None, False, "stale_provenance")
    if candidate.artifact_fingerprint is None:
        raise CamValidationError("Toolpath candidate fingerprint is missing")
    published, accepted = state.publish(token, current_input, candidate.artifact_fingerprint, enabled=operation.enabled)
    if not accepted:
        return ToolpathPublishResult(replace(operation, artifact_state=published), None, False, "stale_input")
    return ToolpathPublishResult(replace(operation, artifact_state=published), candidate, True)
