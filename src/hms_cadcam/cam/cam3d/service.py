"""Latest-wins CAM 3D geometry snapshot and calculation service."""

from __future__ import annotations

import threading
import logging
from dataclasses import dataclass
from typing import Callable
from uuid import UUID, uuid4

from hms_cadcam.cam.cam3d.context import (
    Cam3DCalculationContext,
    Cam3DCalculationState,
)
from hms_cadcam.cam.cam3d.mesh import (
    Cam3DCancelledError,
    Cam3DMeshError,
    Cam3DSurfaceMesher,
    build_calculation_mesh,
)
from hms_cadcam.cam.cam3d.models import (
    Cam3DDiagnostic,
    Cam3DDiagnosticCode,
    Cam3DDiagnosticSeverity,
    Cam3DGeometrySnapshot,
    Cam3DSafeMotionPolicy,
    MachiningZone3D,
)
from hms_cadcam.cam.domain.errors import CamValidationError
from hms_cadcam.cam.domain.ids import (
    Cam3DCalculationContextId,
    Cam3DGeometrySnapshotId,
    CamJobId,
    SetupId,
)
from hms_cadcam.cam.domain.revision import ContentFingerprint, DependencyFingerprint

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Cam3DCalculationRequest:
    """Immutable request envelope safe to pass to a worker thread."""

    request_token: UUID
    project_id: UUID
    project_generation: int
    job_id: CamJobId
    setup_id: SetupId
    zone: MachiningZone3D
    tool_assembly_fingerprint: ContentFingerprint | DependencyFingerprint
    tool_definition_fingerprint: ContentFingerprint
    safe_motion_policy: Cam3DSafeMotionPolicy
    algorithm: str = "hms_cam3d_geometry_foundation"
    algorithm_version: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.request_token, UUID) or self.request_token.int == 0:
            raise CamValidationError("CAM 3D request token is invalid")
        if not isinstance(self.project_id, UUID) or self.project_id.int == 0:
            raise CamValidationError("CAM 3D request project ID is invalid")
        if type(self.project_generation) is not int or self.project_generation < 0:
            raise CamValidationError("CAM 3D request generation is invalid")
        if not isinstance(self.job_id, CamJobId) or not isinstance(self.setup_id, SetupId):
            raise CamValidationError("CAM 3D request Job/Setup is invalid")
        if not isinstance(self.zone, MachiningZone3D):
            raise CamValidationError("CAM 3D request machining zone is invalid")
        if self.zone.project_id != self.project_id or self.zone.job_id != self.job_id or self.zone.setup_id != self.setup_id:
            raise CamValidationError("CAM 3D request identities do not match its zone")
        if not isinstance(
            self.tool_assembly_fingerprint, (ContentFingerprint, DependencyFingerprint)
        ) or not isinstance(self.tool_definition_fingerprint, ContentFingerprint):
            raise CamValidationError("CAM 3D request tool fingerprints are invalid")
        if not isinstance(self.safe_motion_policy, Cam3DSafeMotionPolicy):
            raise CamValidationError("CAM 3D request safe-motion policy is invalid")
        if not isinstance(self.algorithm, str) or not self.algorithm.strip():
            raise CamValidationError("CAM 3D request algorithm is invalid")
        object.__setattr__(self, "algorithm", self.algorithm.strip())
        if type(self.algorithm_version) is not int or self.algorithm_version <= 0:
            raise CamValidationError("CAM 3D request algorithm version is invalid")

    @classmethod
    def create(
        cls,
        *,
        project_id: UUID,
        project_generation: int,
        job_id: CamJobId,
        setup_id: SetupId,
        zone: MachiningZone3D,
        tool_assembly_fingerprint: ContentFingerprint | DependencyFingerprint,
        tool_definition_fingerprint: ContentFingerprint,
        safe_motion_policy: Cam3DSafeMotionPolicy,
        algorithm: str = "hms_cam3d_geometry_foundation",
        algorithm_version: int = 1,
    ) -> "Cam3DCalculationRequest":
        """Create a request with a fresh latest-wins token."""
        return cls(
            uuid4(),
            project_id,
            project_generation,
            job_id,
            setup_id,
            zone,
            tool_assembly_fingerprint,
            tool_definition_fingerprint,
            safe_motion_policy,
            algorithm,
            algorithm_version,
        )

    @property
    def fingerprint(self) -> DependencyFingerprint:
        """Fingerprint every latest-wins input except the request token."""
        return DependencyFingerprint.from_payload(
            {
                "project_id": str(self.project_id),
                "project_generation": self.project_generation,
                "job_id": str(self.job_id),
                "setup_id": str(self.setup_id),
                "zone": self.zone.to_dict(),
                "tool_assembly_fingerprint": self.tool_assembly_fingerprint.to_dict(),
                "tool_definition_fingerprint": self.tool_definition_fingerprint.to_dict(),
                "safe_motion_policy": self.safe_motion_policy.to_dict(),
                "algorithm": self.algorithm,
                "algorithm_version": self.algorithm_version,
            }
        )


@dataclass(frozen=True, slots=True)
class Cam3DCalculationExecution:
    """Atomic service outcome; candidate data is never exposed before publish."""

    state: Cam3DCalculationState
    published: bool
    context: Cam3DCalculationContext | None
    diagnostics: tuple[Cam3DDiagnostic, ...] = ()
    previous_valid_retained: bool = False


class Cam3DGeometryService:
    """Project-scoped synchronous core suitable for execution in a worker."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._project_id: UUID | None = None
        self._project_generation: int | None = None
        self._closed = True
        self._active_token: UUID | None = None
        self._active_fingerprint: DependencyFingerprint | None = None
        self._state = Cam3DCalculationState.MISSING
        self._current: Cam3DCalculationContext | None = None
        self._current_request_fingerprint: DependencyFingerprint | None = None

    @property
    def state(self) -> Cam3DCalculationState:
        with self._lock:
            return self._state

    @property
    def current_context(self) -> Cam3DCalculationContext | None:
        """Return the last fully published context, including retained stale evidence."""
        with self._lock:
            return self._current

    def bind_project(self, project_id: UUID, generation: int) -> None:
        """Bind a live project and invalidate every request from another binding."""
        if not isinstance(project_id, UUID) or project_id.int == 0:
            raise CamValidationError("CAM 3D service project ID is invalid")
        if type(generation) is not int or generation < 0:
            raise CamValidationError("CAM 3D service project generation is invalid")
        with self._lock:
            changed = self._project_id != project_id or self._project_generation != generation
            self._project_id = project_id
            self._project_generation = generation
            self._closed = False
            self._active_token = None
            self._active_fingerprint = None
            if changed and self._current is not None:
                self._state = Cam3DCalculationState.STALE
            elif self._current is None:
                self._state = Cam3DCalculationState.MISSING

    def close_project(self) -> None:
        """Invalidate active callbacks and prohibit publishing after close."""
        with self._lock:
            self._closed = True
            self._active_token = None
            self._active_fingerprint = None
            self._state = (
                Cam3DCalculationState.STALE
                if self._current is not None
                else Cam3DCalculationState.MISSING
            )

    def invalidate(self) -> None:
        """Cancel active work after geometry, Setup, tool or policy changes."""
        with self._lock:
            self._active_token = None
            self._active_fingerprint = None
            self._state = (
                Cam3DCalculationState.STALE
                if self._current is not None
                else Cam3DCalculationState.MISSING
            )

    def calculate(
        self,
        request: Cam3DCalculationRequest,
        mesher: Cam3DSurfaceMesher,
        *,
        cancellation: Callable[[], bool] | None = None,
        current_request: Callable[[], Cam3DCalculationRequest] | None = None,
    ) -> Cam3DCalculationExecution:
        """Capture, validate, tessellate, stale-check and atomically publish."""
        if not isinstance(request, Cam3DCalculationRequest):
            raise CamValidationError("CAM 3D calculation request is invalid")
        retained_before = self.current_context
        try:
            self._begin(request)
            snapshot = Cam3DGeometrySnapshot(
                Cam3DGeometrySnapshotId.new(),
                request.project_id,
                request.project_generation,
                request.zone.setup_revision,
                request.zone.geometry_revision,
                request.zone.geometry_fingerprint,
                request.zone,
            )
            self._transition(request, Cam3DCalculationState.TESSELLATING)
            fragments = []
            for surface in request.zone.all_surfaces():
                self._checkpoint(request, cancellation)
                fragments.append(
                    mesher.tessellate(
                        surface,
                        request.zone.tolerance,
                        lambda: self._cancelled(request, cancellation),
                    )
                )
            self._transition(request, Cam3DCalculationState.VALIDATING_MESH)
            mesh = build_calculation_mesh(
                tuple(fragments),
                request.zone.tolerance,
                request.zone.geometry_fingerprint,
                cancellation=lambda: self._cancelled(request, cancellation),
            )
            candidate = Cam3DCalculationContext(
                Cam3DCalculationContextId.new(),
                request.request_token,
                request.project_id,
                request.project_generation,
                request.job_id,
                request.setup_id,
                snapshot,
                request.zone,
                mesh,
                request.tool_assembly_fingerprint,
                request.tool_definition_fingerprint,
                request.zone.tolerance,
                request.zone.allowance,
                request.safe_motion_policy,
                request.algorithm,
                request.algorithm_version,
            )
            if current_request is not None:
                current = current_request()
                if (
                    not isinstance(current, Cam3DCalculationRequest)
                    or current.request_token != request.request_token
                    or current.fingerprint != request.fingerprint
                ):
                    return self._stale(request, retained_before, "CAM 3D inputs changed during calculation")
            return self._publish(request, candidate, retained_before)
        except Cam3DCancelledError as error:
            return self._failure(
                request,
                Cam3DCalculationState.CANCELLED,
                error.diagnostic,
                retained_before,
            )
        except Cam3DMeshError as error:
            return self._failure(
                request,
                Cam3DCalculationState.FAILED,
                error.diagnostic,
                retained_before,
            )
        except CamValidationError as error:
            diagnostic = Cam3DDiagnostic(
                Cam3DDiagnosticCode.INVALID_REQUEST,
                Cam3DDiagnosticSeverity.ERROR,
                str(error),
                setup_id=request.setup_id,
            )
            return self._failure(
                request,
                Cam3DCalculationState.FAILED,
                diagnostic,
                retained_before,
            )
        except Exception as error:
            logger.exception("Unexpected CAM 3D calculation failure")
            diagnostic = Cam3DDiagnostic(
                Cam3DDiagnosticCode.FAILED,
                Cam3DDiagnosticSeverity.ERROR,
                str(error) or "CAM 3D calculation failed",
                setup_id=request.setup_id,
            )
            return self._failure(
                request,
                Cam3DCalculationState.FAILED,
                diagnostic,
                retained_before,
            )

    def _begin(self, request: Cam3DCalculationRequest) -> None:
        with self._lock:
            if self._closed or self._project_id != request.project_id:
                raise CamValidationError("CAM 3D request belongs to a closed or different project")
            if self._project_generation != request.project_generation:
                raise CamValidationError("CAM 3D project generation changed")
            self._active_token = request.request_token
            self._active_fingerprint = request.fingerprint
            self._state = Cam3DCalculationState.VALIDATING

    def _transition(
        self, request: Cam3DCalculationRequest, state: Cam3DCalculationState
    ) -> None:
        with self._lock:
            if not self._is_active(request):
                raise Cam3DCancelledError(
                    Cam3DDiagnostic(
                        Cam3DDiagnosticCode.STALE,
                        Cam3DDiagnosticSeverity.WARNING,
                        "CAM 3D request became stale",
                        setup_id=request.setup_id,
                    )
                )
            self._state = state

    def _checkpoint(
        self,
        request: Cam3DCalculationRequest,
        cancellation: Callable[[], bool] | None,
    ) -> None:
        if self._cancelled(request, cancellation):
            raise Cam3DCancelledError(
                Cam3DDiagnostic(
                    Cam3DDiagnosticCode.CANCELLED,
                    Cam3DDiagnosticSeverity.WARNING,
                    "CAM 3D calculation was cancelled or invalidated",
                    setup_id=request.setup_id,
                )
            )

    def _cancelled(
        self,
        request: Cam3DCalculationRequest,
        cancellation: Callable[[], bool] | None,
    ) -> bool:
        if cancellation is not None and cancellation():
            return True
        with self._lock:
            return not self._is_active(request)

    def _is_active(self, request: Cam3DCalculationRequest) -> bool:
        return (
            not self._closed
            and self._project_id == request.project_id
            and self._project_generation == request.project_generation
            and self._active_token == request.request_token
            and self._active_fingerprint == request.fingerprint
        )

    def _publish(
        self,
        request: Cam3DCalculationRequest,
        candidate: Cam3DCalculationContext,
        retained_before: Cam3DCalculationContext | None,
    ) -> Cam3DCalculationExecution:
        with self._lock:
            if not self._is_active(request):
                return self._stale_locked(
                    request, retained_before, "CAM 3D request became stale before publish"
                )
            self._current = candidate
            self._current_request_fingerprint = request.fingerprint
            self._state = Cam3DCalculationState.CURRENT
            self._active_token = None
            self._active_fingerprint = None
            return Cam3DCalculationExecution(
                Cam3DCalculationState.CURRENT, True, candidate
            )

    def _stale(
        self,
        request: Cam3DCalculationRequest,
        retained: Cam3DCalculationContext | None,
        message: str,
    ) -> Cam3DCalculationExecution:
        with self._lock:
            return self._stale_locked(request, retained, message)

    def _stale_locked(
        self,
        request: Cam3DCalculationRequest,
        retained: Cam3DCalculationContext | None,
        message: str,
    ) -> Cam3DCalculationExecution:
        diagnostic = Cam3DDiagnostic(
            Cam3DDiagnosticCode.STALE,
            Cam3DDiagnosticSeverity.WARNING,
            message,
            setup_id=request.setup_id,
        )
        if self._active_token == request.request_token:
            self._active_token = None
            self._active_fingerprint = None
        self._state = Cam3DCalculationState.STALE
        return Cam3DCalculationExecution(
            Cam3DCalculationState.STALE,
            False,
            retained,
            (diagnostic,),
            retained is not None,
        )

    def _failure(
        self,
        request: Cam3DCalculationRequest,
        state: Cam3DCalculationState,
        diagnostic: Cam3DDiagnostic,
        retained: Cam3DCalculationContext | None,
    ) -> Cam3DCalculationExecution:
        with self._lock:
            retained_is_current = (
                retained is not None
                and self._current_request_fingerprint == request.fingerprint
            )
            if retained is not None and not retained_is_current:
                state = Cam3DCalculationState.STALE
            if self._active_token == request.request_token:
                self._active_token = None
                self._active_fingerprint = None
                self._state = state
            elif self._closed or self._project_id != request.project_id:
                state = Cam3DCalculationState.STALE
                diagnostic = Cam3DDiagnostic(
                    Cam3DDiagnosticCode.STALE,
                    Cam3DDiagnosticSeverity.WARNING,
                    "CAM 3D callback was discarded after project switch or close",
                    setup_id=request.setup_id,
                )
            return Cam3DCalculationExecution(
                state,
                False,
                retained,
                (diagnostic,),
                retained_is_current,
            )
