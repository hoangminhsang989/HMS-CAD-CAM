"""Durable R274 boundary for the sealed R273 Rest Finishing candidate.

The R273 core deliberately stops at an in-memory candidate.  This module is
the only layer that turns that candidate into files, and deliberately leaves
SQLite metadata staging to :class:`CamApplicationService`'s transaction.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Callable

from hms_cadcam.cam.application.rest_finishing_toolpath import (
    RestFinishingCandidate, require_rest_finishing_candidate,
)
from hms_cadcam.cam.application.rest_finishing_lifecycle import (
    RestFinishingLifecyclePreparation,
    RestFinishingLifecycleResult,
    RestFinishingLifecycleStatus,
)
from hms_cadcam.cam.domain import Operation
from hms_cadcam.cam.domain.rest_finishing import (
    RestFinishingDiagnosticCode, RestFinishingValidationError,
)
from hms_cadcam.cam.material_state import (
    MaterialState, MaterialStateLoadStatus, MaterialStateStore,
)
from hms_cadcam.cam.material_state.core import MaterialStateVerificationOrigin
from hms_cadcam.cam.persistence.artifact_store import ToolpathArtifactStore
from hms_cadcam.cam.persistence.errors import ToolpathArtifactStoreError
from hms_cadcam.cam.persistence.models import ToolpathArtifactMetadata
from hms_cadcam.cam.toolpath import ToolpathArtifact, publish_toolpath


@dataclass(frozen=True, slots=True)
class RestFinishingPublication:
    """Read-back verified files plus the completed editable operation."""

    operation: Operation
    artifact_metadata: ToolpathArtifactMetadata
    artifact: ToolpathArtifact
    successor_state: MaterialState


class RestFinishingApplicationStatus(StrEnum):
    """Stable R274 result taxonomy at the project/application boundary."""

    SUCCESS = "SUCCESS"
    NO_WORK = "NO_WORK"
    CANCELLED = "CANCELLED"
    FAILURE = "FAILURE"


class RestFinishingPreparationStatus(StrEnum):
    """Stable public taxonomy for R274 preparation-only requests."""

    PREPARED = "PREPARED"
    NO_WORK = "NO_WORK"
    CANCELLED = "CANCELLED"
    FAILURE = "FAILURE"


@dataclass(frozen=True, slots=True)
class RestFinishingApplicationResult:
    """R274 outcome without collapsing no-work, cancellation, or failure."""

    status: RestFinishingApplicationStatus
    preparation: RestFinishingLifecyclePreparation | None = None
    candidate: RestFinishingCandidate | None = None
    publication: RestFinishingPublication | None = None
    diagnostic_code: RestFinishingDiagnosticCode | None = None
    message: str = ""

    def __post_init__(self) -> None:
        if self.status is RestFinishingApplicationStatus.SUCCESS:
            valid = (
                isinstance(self.candidate, RestFinishingCandidate)
                and isinstance(self.publication, RestFinishingPublication)
                and self.diagnostic_code is None
                and not self.message
            )
        elif self.status is RestFinishingApplicationStatus.NO_WORK:
            valid = (
                self.candidate is None
                and self.publication is None
                and self.diagnostic_code is None
                and not self.message
            )
        elif self.status is RestFinishingApplicationStatus.CANCELLED:
            valid = (
                self.candidate is None
                and self.publication is None
                and self.diagnostic_code is RestFinishingDiagnosticCode.CANCELLED
                and bool(self.message)
            )
        else:
            valid = (
                self.status is RestFinishingApplicationStatus.FAILURE
                and self.candidate is None
                and self.publication is None
                and isinstance(self.diagnostic_code, RestFinishingDiagnosticCode)
                and self.diagnostic_code is not RestFinishingDiagnosticCode.CANCELLED
                and bool(self.message)
            )
        if not valid:
            raise ValueError("Rest Finishing application result is inconsistent")


def rest_finishing_application_failure(
    diagnostic_code: RestFinishingDiagnosticCode,
    message: str,
    *,
    preparation: RestFinishingLifecyclePreparation | None = None,
) -> RestFinishingApplicationResult:
    """Map a typed R273 diagnostic into the stable R274 taxonomy."""
    status = (
        RestFinishingApplicationStatus.CANCELLED
        if diagnostic_code is RestFinishingDiagnosticCode.CANCELLED
        else RestFinishingApplicationStatus.FAILURE
    )
    return RestFinishingApplicationResult(
        status,
        preparation=preparation,
        diagnostic_code=diagnostic_code,
        message=message,
    )


def rest_finishing_application_result(
    core_result: RestFinishingLifecycleResult,
    *,
    publication: RestFinishingPublication | None = None,
) -> RestFinishingApplicationResult:
    """Translate one sealed core result without weakening its authority."""
    if not isinstance(core_result, RestFinishingLifecycleResult):
        raise TypeError("Rest Finishing core result is invalid")
    if core_result.status is RestFinishingLifecycleStatus.SUCCESS:
        return RestFinishingApplicationResult(
            RestFinishingApplicationStatus.SUCCESS,
            core_result.preparation,
            core_result.candidate,
            publication,
        )
    if core_result.status is RestFinishingLifecycleStatus.NO_REST_FINISHING_MATERIAL:
        return RestFinishingApplicationResult(
            RestFinishingApplicationStatus.NO_WORK,
            preparation=core_result.preparation,
        )
    assert core_result.diagnostic_code is not None
    return rest_finishing_application_failure(
        core_result.diagnostic_code,
        core_result.message,
        preparation=core_result.preparation,
    )


def publish_rest_finishing_candidate(
    candidate: RestFinishingCandidate,
    *,
    project_root: Path,
    artifact_store: ToolpathArtifactStore,
    material_state_store: MaterialStateStore,
    cancellation: Callable[[], bool] | None = None,
) -> RestFinishingPublication:
    """Write immutable bytes first, after the last cancellation checkpoint.

    Orphan bytes left by a later SQLite failure are intentionally harmless:
    project authority is granted only when the caller records returned metadata
    in its SQLite transaction.
    """
    if not isinstance(project_root, Path):
        raise TypeError("Rest Finishing project root is invalid")
    if not isinstance(artifact_store, ToolpathArtifactStore) or not isinstance(material_state_store, MaterialStateStore):
        raise TypeError("Rest Finishing durable stores are invalid")
    try:
        require_rest_finishing_candidate(candidate, cancellation=cancellation)
        if cancellation is not None and cancellation():
            raise RestFinishingValidationError(
                RestFinishingDiagnosticCode.CANCELLED,
                "Rest Finishing was cancelled before durable publication",
            )
        memory = publish_toolpath(
            candidate.prepared.computing_operation,
            candidate.artifact,
            candidate.prepared.computation_token,
            candidate.prepared.input_fingerprint,
        )
        if not memory.accepted or memory.artifact is None:
            raise RestFinishingValidationError(
                RestFinishingDiagnosticCode.SUCCESSOR_INVALID,
                "Rest Finishing in-memory publication was rejected",
            )
        if cancellation is not None and cancellation():
            raise RestFinishingValidationError(
                RestFinishingDiagnosticCode.CANCELLED,
                "Rest Finishing was cancelled before immutable file publication",
            )
        metadata = artifact_store.publish(project_root, candidate.artifact)
        artifact = artifact_store.load(project_root, metadata)
        if artifact != candidate.artifact:
            raise RestFinishingValidationError(
                RestFinishingDiagnosticCode.SUCCESSOR_INVALID,
                "Rest Finishing artifact readback differs from its candidate",
            )
        material_state_store.write(project_root, candidate.successor_state)
        state_readback = material_state_store.load(
            project_root, candidate.successor_state.fingerprint,
        )
        state = state_readback.state
        if (
            state_readback.status is not MaterialStateLoadStatus.VALID
            or state is None
            or state.verification_origin is not MaterialStateVerificationOrigin.TRUSTED_PERSISTED
            or state != candidate.successor_state
            or state.content_integrity_fingerprint
            != candidate.successor_state.content_integrity_fingerprint
        ):
            raise RestFinishingValidationError(
                RestFinishingDiagnosticCode.SUCCESSOR_INVALID,
                "Rest Finishing successor material-state readback is invalid",
            )
        return RestFinishingPublication(memory.operation, metadata, artifact, state)
    except RestFinishingValidationError:
        raise
    except (OSError, ToolpathArtifactStoreError, TypeError, ValueError) as error:
        raise RestFinishingValidationError(
            RestFinishingDiagnosticCode.SUCCESSOR_INVALID,
            f"Rest Finishing durable publication failed: {error}",
        ) from error


__all__ = [
    "RestFinishingApplicationResult",
    "RestFinishingApplicationStatus",
    "RestFinishingPreparationStatus",
    "RestFinishingPublication",
    "publish_rest_finishing_candidate",
    "rest_finishing_application_failure",
    "rest_finishing_application_result",
]
