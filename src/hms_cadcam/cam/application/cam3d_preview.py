"""Qt-free CAM 3D preview execution, latest-wins publication and cache.

The module is the application boundary for WP3-B.  It accepts only the
immutable WP3-A request contract and publishes only immutable Python data.
Geometry resolution is injected so this module never imports Qt, OCP,
filesystem or project persistence code.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import CancelledError, Future, ThreadPoolExecutor
from dataclasses import dataclass, replace
from enum import StrEnum
import logging
import math
from threading import Event, RLock
from typing import Protocol

from hms_cadcam.cam.application.cam3d_request import (
    Cam3DCacheRecordIdentity,
    Cam3DCalculationJobId,
    Cam3DCalculationOwnershipKey,
    Cam3DCalculationRequestContract,
    Cam3DCalculationSession,
    Cam3DPreviewCacheKey,
    Cam3DResultIdentity,
    Cam3DSessionDecision,
)
from hms_cadcam.cam.cam3d.mesh import (
    Cam3DCancelledError,
    Cam3DMeshError,
)
from hms_cadcam.cam.domain.errors import CamValidationError

logger = logging.getLogger(__name__)

PreviewPoint = tuple[float, float, float]
PreviewTriangle = tuple[int, int, int]
PreviewBounds = tuple[float, float, float, float, float, float]


class Cam3DPreviewCompletionState(StrEnum):
    """Terminal state of one preview execution."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Cam3DPreviewSource(StrEnum):
    """Whether a successful preview came from work or the derived cache."""

    WORKER = "worker"
    CACHE = "cache"


class Cam3DPreviewDiagnosticCode(StrEnum):
    """Localization-neutral failure classification for preview delivery."""

    INVALID_REQUEST = "cam3d.preview.invalid_request"
    GEOMETRY_UNAVAILABLE = "cam3d.preview.geometry_unavailable"
    MESH_INVALID = "cam3d.preview.mesh_invalid"
    TESSELLATION_FAILED = "cam3d.preview.tessellation_failed"
    CANCELLED = "cam3d.preview.cancelled"
    SHUTDOWN = "cam3d.preview.shutdown"
    INTERNAL_ERROR = "cam3d.preview.internal_error"


@dataclass(frozen=True, slots=True)
class Cam3DPreviewDiagnostic:
    """Typed diagnostic with no localized text or exception object."""

    code: Cam3DPreviewDiagnosticCode
    details: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.code, Cam3DPreviewDiagnosticCode):
            raise TypeError("CAM 3D preview diagnostic code is invalid")
        if not isinstance(self.details, tuple) or any(
            not isinstance(item, tuple)
            or len(item) != 2
            or not all(isinstance(value, str) for value in item)
            for item in self.details
        ):
            raise TypeError("CAM 3D preview diagnostic details are invalid")
        ordered = tuple(sorted(self.details))
        if len({key for key, _value in ordered}) != len(ordered):
            raise ValueError("CAM 3D preview diagnostic keys are duplicated")
        object.__setattr__(self, "details", ordered)


def _finite_float(value: object, name: str) -> float:
    if type(value) is not float or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite float")
    return value


@dataclass(frozen=True, slots=True)
class Cam3DPreviewMesh:
    """Immutable native-free preview mesh payload."""

    vertices: tuple[PreviewPoint, ...]
    triangles: tuple[PreviewTriangle, ...]
    triangle_normals: tuple[PreviewPoint, ...]
    bounds: PreviewBounds

    def __post_init__(self) -> None:
        if not isinstance(self.vertices, tuple) or not self.vertices:
            raise ValueError("CAM 3D preview vertices must be non-empty")
        if not isinstance(self.triangles, tuple) or not self.triangles:
            raise ValueError("CAM 3D preview triangles must be non-empty")
        if not isinstance(self.triangle_normals, tuple) or len(
            self.triangle_normals
        ) != len(self.triangles):
            raise ValueError("CAM 3D preview triangle normals are incomplete")
        for point in self.vertices:
            if not isinstance(point, tuple) or len(point) != 3:
                raise ValueError("CAM 3D preview vertex shape is invalid")
            for value in point:
                _finite_float(value, "CAM 3D preview vertex")
        for triangle in self.triangles:
            if (
                not isinstance(triangle, tuple)
                or len(triangle) != 3
                or len(set(triangle)) != 3
                or any(type(index) is not int for index in triangle)
                or any(index < 0 or index >= len(self.vertices) for index in triangle)
            ):
                raise ValueError("CAM 3D preview triangle index is invalid")
        for normal in self.triangle_normals:
            if not isinstance(normal, tuple) or len(normal) != 3:
                raise ValueError("CAM 3D preview normal shape is invalid")
            values = tuple(_finite_float(value, "CAM 3D preview normal") for value in normal)
            if not math.isclose(
                math.sqrt(sum(value * value for value in values)),
                1.0,
                rel_tol=0.0,
                abs_tol=1.0e-6,
            ):
                raise ValueError("CAM 3D preview normal must be unit length")
        if not isinstance(self.bounds, tuple) or len(self.bounds) != 6:
            raise ValueError("CAM 3D preview bounds are invalid")
        bounds = tuple(_finite_float(value, "CAM 3D preview bound") for value in self.bounds)
        expected = (
            min(item[0] for item in self.vertices),
            min(item[1] for item in self.vertices),
            min(item[2] for item in self.vertices),
            max(item[0] for item in self.vertices),
            max(item[1] for item in self.vertices),
            max(item[2] for item in self.vertices),
        )
        if bounds != expected:
            raise ValueError("CAM 3D preview bounds do not match vertices")

    @property
    def vertex_count(self) -> int:
        return len(self.vertices)

    @property
    def triangle_count(self) -> int:
        return len(self.triangles)


@dataclass(frozen=True, slots=True)
class Cam3DPreviewResult:
    """Immutable result that is safe to cross a Qt signal boundary."""

    identity: Cam3DResultIdentity
    cache_key: Cam3DPreviewCacheKey
    state: Cam3DPreviewCompletionState
    source: Cam3DPreviewSource
    mesh: Cam3DPreviewMesh | None = None
    diagnostic: Cam3DPreviewDiagnostic | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.identity, Cam3DResultIdentity):
            raise TypeError("CAM 3D preview result identity is invalid")
        if not isinstance(self.cache_key, Cam3DPreviewCacheKey):
            raise TypeError("CAM 3D preview result cache key is invalid")
        if not isinstance(self.state, Cam3DPreviewCompletionState):
            raise TypeError("CAM 3D preview result state is invalid")
        if not isinstance(self.source, Cam3DPreviewSource):
            raise TypeError("CAM 3D preview result source is invalid")
        if self.state is Cam3DPreviewCompletionState.SUCCEEDED:
            if not isinstance(self.mesh, Cam3DPreviewMesh) or self.diagnostic is not None:
                raise ValueError("Successful CAM 3D preview must contain mesh only")
        elif self.mesh is not None or not isinstance(
            self.diagnostic, Cam3DPreviewDiagnostic
        ):
            raise ValueError("Failed CAM 3D preview must contain diagnostic only")

    @property
    def vertex_count(self) -> int:
        return self.mesh.vertex_count if self.mesh is not None else 0

    @property
    def triangle_count(self) -> int:
        return self.mesh.triangle_count if self.mesh is not None else 0

    @classmethod
    def success(
        cls,
        request: Cam3DCalculationRequestContract,
        mesh: Cam3DPreviewMesh,
        *,
        source: Cam3DPreviewSource,
    ) -> "Cam3DPreviewResult":
        return cls(
            Cam3DResultIdentity.from_request(request),
            request.cache_key,
            Cam3DPreviewCompletionState.SUCCEEDED,
            source,
            mesh,
        )

    @classmethod
    def failure(
        cls,
        request: Cam3DCalculationRequestContract,
        diagnostic: Cam3DPreviewDiagnostic,
    ) -> "Cam3DPreviewResult":
        return cls(
            Cam3DResultIdentity.from_request(request),
            request.cache_key,
            Cam3DPreviewCompletionState.FAILED,
            Cam3DPreviewSource.WORKER,
            diagnostic=diagnostic,
        )

    @classmethod
    def cancelled(cls, request: Cam3DCalculationRequestContract) -> "Cam3DPreviewResult":
        return cls(
            Cam3DResultIdentity.from_request(request),
            request.cache_key,
            Cam3DPreviewCompletionState.CANCELLED,
            Cam3DPreviewSource.WORKER,
            diagnostic=Cam3DPreviewDiagnostic(Cam3DPreviewDiagnosticCode.CANCELLED),
        )


class Cam3DPreviewTessellator(Protocol):
    """Injected native geometry resolution/tessellation boundary."""

    def tessellate(
        self,
        request: Cam3DCalculationRequestContract,
        cancellation: Callable[[], bool],
    ) -> Cam3DPreviewMesh:
        """Resolve request geometry and return an immutable preview mesh."""


class Cam3DPreviewCancellationToken:
    """Thread-safe cooperative cancellation state."""

    def __init__(self) -> None:
        self._event = Event()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> None:
        self._event.set()


@dataclass(frozen=True, slots=True)
class Cam3DPreviewCacheEntry:
    """Immutable cache value keyed only by WP3-A semantic identity."""

    identity: Cam3DCacheRecordIdentity
    mesh: Cam3DPreviewMesh

    def __post_init__(self) -> None:
        if not isinstance(self.identity, Cam3DCacheRecordIdentity):
            raise TypeError("CAM 3D preview cache identity is invalid")
        if not isinstance(self.mesh, Cam3DPreviewMesh):
            raise TypeError("CAM 3D preview cache mesh is invalid")


class Cam3DInMemoryPreviewCache:
    """Bounded deterministic FIFO cache; never writes persistence."""

    def __init__(self, max_entries: int = 32) -> None:
        if type(max_entries) is not int or max_entries <= 0:
            raise ValueError("CAM 3D preview cache size must be a positive integer")
        self._max_entries = max_entries
        self._lock = RLock()
        self._entries: OrderedDict[tuple[object, ...], Cam3DPreviewCacheEntry] = (
            OrderedDict()
        )

    @property
    def max_entries(self) -> int:
        return self._max_entries

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._entries)

    def get(self, request: Cam3DCalculationRequestContract) -> Cam3DPreviewMesh | None:
        if not isinstance(request, Cam3DCalculationRequestContract):
            raise TypeError("CAM 3D cache request is invalid")
        record = Cam3DCacheRecordIdentity.from_request(request)
        key = self._key(record)
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            if entry.identity != record:
                self._entries.pop(key, None)
                return None
            return entry.mesh

    def put(
        self,
        request: Cam3DCalculationRequestContract,
        mesh: Cam3DPreviewMesh,
    ) -> Cam3DPreviewCacheEntry:
        if not isinstance(request, Cam3DCalculationRequestContract):
            raise TypeError("CAM 3D cache request is invalid")
        if not isinstance(mesh, Cam3DPreviewMesh):
            raise TypeError("CAM 3D cache mesh is invalid")
        entry = Cam3DPreviewCacheEntry(Cam3DCacheRecordIdentity.from_request(request), mesh)
        key = self._key(entry.identity)
        with self._lock:
            self._entries.pop(key, None)
            self._entries[key] = entry
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)
        return entry

    def invalidate_for_request(self, request: Cam3DCalculationRequestContract) -> int:
        """Remove same-owner entries that fail the WP3-A reuse decision."""
        if not isinstance(request, Cam3DCalculationRequestContract):
            raise TypeError("CAM 3D cache request is invalid")
        record = Cam3DCacheRecordIdentity.from_request(request)
        with self._lock:
            keys = tuple(
                key
                for key, entry in self._entries.items()
                if entry.identity.ownership == record.ownership
                and (
                    entry.identity.project_generation != record.project_generation
                    or entry.identity.cache_key != record.cache_key
                )
            )
            for key in keys:
                self._entries.pop(key, None)
            return len(keys)

    def invalidate_ownership(self, ownership: Cam3DCalculationOwnershipKey) -> int:
        if not isinstance(ownership, Cam3DCalculationOwnershipKey):
            raise TypeError("CAM 3D cache ownership is invalid")
        with self._lock:
            keys = tuple(
                key for key, entry in self._entries.items() if entry.identity.ownership == ownership
            )
            for key in keys:
                self._entries.pop(key, None)
            return len(keys)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    @staticmethod
    def _key(record: Cam3DCacheRecordIdentity) -> tuple[object, ...]:
        return (record.ownership, record.project_generation, record.cache_key)


class Cam3DSubmissionDecision(StrEnum):
    ACCEPTED = "accepted"
    CACHE_HIT = "cache_hit"
    INVALID_REQUEST = "invalid_request"
    DUPLICATE_REQUEST = "duplicate_request"
    CLOSED = "closed"
    OWNERSHIP_MISMATCH = "ownership_mismatch"
    STALE_GENERATION = "stale_generation"


@dataclass(frozen=True, slots=True)
class Cam3DSubmissionReceipt:
    """Deterministic result of request admission, before asynchronous finish."""

    job_id: Cam3DCalculationJobId | None
    accepted: bool
    decision: Cam3DSubmissionDecision
    scheduled: bool

    def __post_init__(self) -> None:
        if self.job_id is not None and not isinstance(self.job_id, Cam3DCalculationJobId):
            raise TypeError("CAM 3D submission job identity is invalid")
        if type(self.accepted) is not bool or type(self.scheduled) is not bool:
            raise TypeError("CAM 3D submission flags are invalid")
        if not isinstance(self.decision, Cam3DSubmissionDecision):
            raise TypeError("CAM 3D submission decision is invalid")


class Cam3DJobExecutionState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    CANCELLING = "cancelling"
    COMPLETED = "completed"
    DROPPED = "dropped"


class Cam3DCancelDecision(StrEnum):
    REQUESTED = "requested"
    ALREADY_CANCELLED = "already_cancelled"
    ALREADY_COMPLETED = "already_completed"
    NOT_FOUND = "not_found"
    NOT_LATEST = "not_latest"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class Cam3DJobRecord:
    """Immutable observable execution record without a Future or callback."""

    identity: Cam3DResultIdentity
    state: Cam3DJobExecutionState
    publication_decision: Cam3DSessionDecision | None = None
    callback_invoked: bool = False


class _PendingJob:
    __slots__ = ("request", "token", "callback", "future")

    def __init__(
        self,
        request: Cam3DCalculationRequestContract,
        token: Cam3DPreviewCancellationToken,
        callback: Callable[[Cam3DPreviewResult], None] | None,
    ) -> None:
        self.request = request
        self.token = token
        self.callback = callback
        self.future: Future[Cam3DPreviewResult] | None = None


class Cam3DPreviewCoordinator:
    """Application-owned worker/session coordinator for WP3-B."""

    def __init__(
        self,
        tessellator: Cam3DPreviewTessellator,
        *,
        max_workers: int = 1,
        cache: Cam3DInMemoryPreviewCache | None = None,
        executor: ThreadPoolExecutor | None = None,
    ) -> None:
        if not callable(getattr(tessellator, "tessellate", None)):
            raise TypeError("CAM 3D preview tessellator is invalid")
        if type(max_workers) is not int or max_workers <= 0:
            raise ValueError("CAM 3D preview worker count must be positive")
        if executor is not None and not callable(getattr(executor, "submit", None)):
            raise TypeError("CAM 3D preview executor is invalid")
        self._tessellator = tessellator
        self._executor = executor or ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="hms-cam3d-preview",
        )
        self._cache = cache or Cam3DInMemoryPreviewCache()
        self._lock = RLock()
        self._closed = False
        self._sessions: dict[Cam3DCalculationOwnershipKey, Cam3DCalculationSession] = {}
        self._pending: dict[Cam3DCalculationJobId, _PendingJob] = {}
        self._records: dict[Cam3DCalculationJobId, Cam3DJobRecord] = {}

    @property
    def cache(self) -> Cam3DInMemoryPreviewCache:
        return self._cache

    def bind_session(
        self,
        ownership: Cam3DCalculationOwnershipKey,
        project_generation: int,
    ) -> None:
        """Bind or rebind one ownership after an explicit project/setup switch."""
        if not isinstance(ownership, Cam3DCalculationOwnershipKey):
            raise TypeError("CAM 3D preview ownership is invalid")
        if type(project_generation) is not int or project_generation <= 0:
            raise ValueError("CAM 3D preview generation is invalid")
        with self._lock:
            existing = self._sessions.get(ownership)
            if existing is not None and existing.live and existing.project_generation == project_generation:
                return
        if existing is not None:
            self.close_ownership(ownership)
        with self._lock:
            if self._closed:
                return
            self._sessions[ownership] = Cam3DCalculationSession(ownership, project_generation)

    def switch_ownership(
        self,
        previous: Cam3DCalculationOwnershipKey,
        current: Cam3DCalculationOwnershipKey,
        project_generation: int,
    ) -> None:
        """Close old ownership before binding the new project/setup."""
        if previous != current:
            self.close_ownership(previous)
        self.bind_session(current, project_generation)

    def submit(
        self,
        request: object,
        *,
        callback: Callable[[Cam3DPreviewResult], None] | None = None,
    ) -> Cam3DSubmissionReceipt:
        """Admit one immutable request and schedule it without reading live UI state."""
        if callback is not None and not callable(callback):
            raise TypeError("CAM 3D preview callback is invalid")
        if not isinstance(request, Cam3DCalculationRequestContract):
            return Cam3DSubmissionReceipt(
                None, False, Cam3DSubmissionDecision.INVALID_REQUEST, False
            )
        identity = Cam3DResultIdentity.from_request(request)
        token = Cam3DPreviewCancellationToken()
        previous_pending: _PendingJob | None = None
        with self._lock:
            if self._closed:
                return Cam3DSubmissionReceipt(
                    request.job_id, False, Cam3DSubmissionDecision.CLOSED, False
                )
            session = self._sessions.get(request.ownership)
            if session is None:
                session = Cam3DCalculationSession(
                    request.ownership, request.project_generation
                )
            previous_job_id = session.latest_job_id
            update = session.register(request)
            if not update.accepted:
                decision = {
                    Cam3DSessionDecision.DUPLICATE_REQUEST: Cam3DSubmissionDecision.DUPLICATE_REQUEST,
                    Cam3DSessionDecision.CLOSED: Cam3DSubmissionDecision.CLOSED,
                    Cam3DSessionDecision.OWNERSHIP_MISMATCH: Cam3DSubmissionDecision.OWNERSHIP_MISMATCH,
                    Cam3DSessionDecision.STALE_GENERATION: Cam3DSubmissionDecision.STALE_GENERATION,
                }.get(update.decision, Cam3DSubmissionDecision.INVALID_REQUEST)
                return Cam3DSubmissionReceipt(request.job_id, False, decision, False)
            self._sessions[request.ownership] = update.session
            if previous_job_id is not None:
                previous_pending = self._pending.get(previous_job_id)
                if previous_pending is not None:
                    previous_pending.token.cancel()
                    previous_record = self._records.get(previous_job_id)
                    if previous_record is not None and previous_record.state in {
                        Cam3DJobExecutionState.QUEUED,
                        Cam3DJobExecutionState.RUNNING,
                    }:
                        self._records[previous_job_id] = replace(
                            previous_record, state=Cam3DJobExecutionState.CANCELLING
                        )
            pending = _PendingJob(request, token, callback)
            self._pending[request.job_id] = pending
            self._records[request.job_id] = Cam3DJobRecord(
                identity, Cam3DJobExecutionState.QUEUED
            )

        self._cache.invalidate_for_request(request)
        cached = self._cache.get(request)
        if cached is not None and not token.cancelled:
            result = Cam3DPreviewResult.success(
                request, cached, source=Cam3DPreviewSource.CACHE
            )
            self._finish_result(request, result)
            return Cam3DSubmissionReceipt(request.job_id, True, Cam3DSubmissionDecision.CACHE_HIT, False)
        if token.cancelled:
            self._finish_result(request, Cam3DPreviewResult.cancelled(request))
            return Cam3DSubmissionReceipt(request.job_id, True, Cam3DSubmissionDecision.ACCEPTED, False)
        try:
            future = self._executor.submit(self._run_job, request, token)
        except RuntimeError:
            self._finish_result(
                request,
                Cam3DPreviewResult.failure(
                    request,
                    Cam3DPreviewDiagnostic(Cam3DPreviewDiagnosticCode.SHUTDOWN),
                ),
            )
            return Cam3DSubmissionReceipt(request.job_id, True, Cam3DSubmissionDecision.ACCEPTED, False)
        with self._lock:
            current = self._pending.get(request.job_id)
            if current is not pending or self._closed or token.cancelled:
                future.cancel()
            else:
                pending.future = future
        future.add_done_callback(
            lambda completed, request=request: self._finish_future(request, completed)
        )
        return Cam3DSubmissionReceipt(request.job_id, True, Cam3DSubmissionDecision.ACCEPTED, True)

    def cancel(self, job_id: Cam3DCalculationJobId) -> Cam3DCancelDecision:
        if not isinstance(job_id, Cam3DCalculationJobId):
            return Cam3DCancelDecision.NOT_FOUND
        future: Future[Cam3DPreviewResult] | None = None
        with self._lock:
            record = self._records.get(job_id)
            pending = self._pending.get(job_id)
            if record is None:
                return Cam3DCancelDecision.NOT_FOUND
            if record.state in {
                Cam3DJobExecutionState.COMPLETED,
                Cam3DJobExecutionState.DROPPED,
            }:
                return Cam3DCancelDecision.ALREADY_COMPLETED
            if pending is None:
                return Cam3DCancelDecision.CLOSED
            if pending.token.cancelled:
                return Cam3DCancelDecision.ALREADY_CANCELLED
            session = self._sessions.get(pending.request.ownership)
            if session is None or not session.live:
                pending.token.cancel()
                return Cam3DCancelDecision.CLOSED
            update = session.request_cancellation(job_id)
            if update.decision is Cam3DSessionDecision.NOT_LATEST:
                pending.token.cancel()
                return Cam3DCancelDecision.NOT_LATEST
            self._sessions[pending.request.ownership] = update.session
            pending.token.cancel()
            self._records[job_id] = replace(
                record, state=Cam3DJobExecutionState.CANCELLING
            )
            future = pending.future
        if future is not None:
            future.cancel()
        return Cam3DCancelDecision.REQUESTED

    def close_ownership(self, ownership: Cam3DCalculationOwnershipKey) -> None:
        futures: list[Future[Cam3DPreviewResult]] = []
        with self._lock:
            session = self._sessions.get(ownership)
            if session is not None:
                self._sessions[ownership] = session.close()
            for job_id, pending in tuple(self._pending.items()):
                if pending.request.ownership != ownership:
                    continue
                pending.token.cancel()
                if pending.future is not None:
                    futures.append(pending.future)
                record = self._records.get(job_id)
                if record is not None and record.state in {
                    Cam3DJobExecutionState.QUEUED,
                    Cam3DJobExecutionState.RUNNING,
                }:
                    self._records[job_id] = replace(
                        record, state=Cam3DJobExecutionState.CANCELLING
                    )
        for future in futures:
            future.cancel()
        self._cache.invalidate_ownership(ownership)

    def job_record(self, job_id: Cam3DCalculationJobId) -> Cam3DJobRecord | None:
        with self._lock:
            return self._records.get(job_id)

    def delivery_authorized(self, result: Cam3DPreviewResult) -> bool:
        """Recheck queued UI delivery after a signal crosses the Qt boundary."""
        if not isinstance(result, Cam3DPreviewResult):
            return False
        with self._lock:
            session = self._sessions.get(result.identity.ownership)
            return bool(
                session is not None
                and session.live
                and session.latest_job_id == result.identity.job_id
                and session.latest_fingerprint == result.identity.fingerprint
                and session.published_job_id == result.identity.job_id
                and result.identity.job_id not in session.cancelled_jobs
            )

    def clear_cache(self) -> None:
        self._cache.clear()

    def shutdown(self, *, wait: bool = True) -> None:
        futures: list[Future[Cam3DPreviewResult]] = []
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._sessions = {
                ownership: session.close()
                for ownership, session in self._sessions.items()
            }
            for pending in self._pending.values():
                pending.token.cancel()
                if pending.future is not None:
                    futures.append(pending.future)
        for future in futures:
            future.cancel()
        self._cache.clear()
        self._executor.shutdown(wait=wait, cancel_futures=True)

    def _run_job(
        self,
        request: Cam3DCalculationRequestContract,
        token: Cam3DPreviewCancellationToken,
    ) -> Cam3DPreviewResult:
        self._mark_running(request.job_id)
        if token.cancelled:
            return Cam3DPreviewResult.cancelled(request)
        try:
            mesh = self._tessellator.tessellate(request, lambda: token.cancelled)
            if token.cancelled:
                return Cam3DPreviewResult.cancelled(request)
            if not isinstance(mesh, Cam3DPreviewMesh):
                raise CamValidationError("CAM 3D preview tessellator returned invalid mesh")
            return Cam3DPreviewResult.success(
                request, mesh, source=Cam3DPreviewSource.WORKER
            )
        except Cam3DCancelledError:
            return Cam3DPreviewResult.cancelled(request)
        except Cam3DMeshError as error:
            return Cam3DPreviewResult.failure(request, _mesh_diagnostic(error))
        except CamValidationError:
            return Cam3DPreviewResult.failure(
                request,
                Cam3DPreviewDiagnostic(Cam3DPreviewDiagnosticCode.INVALID_REQUEST),
            )
        except (TypeError, ValueError):
            return Cam3DPreviewResult.failure(
                request,
                Cam3DPreviewDiagnostic(Cam3DPreviewDiagnosticCode.MESH_INVALID),
            )
        except Exception:
            logger.exception("Unexpected CAM 3D preview worker failure")
            return Cam3DPreviewResult.failure(
                request,
                Cam3DPreviewDiagnostic(Cam3DPreviewDiagnosticCode.INTERNAL_ERROR),
            )

    def _finish_future(
        self,
        request: Cam3DCalculationRequestContract,
        future: Future[Cam3DPreviewResult],
    ) -> None:
        try:
            result = future.result()
        except CancelledError:
            result = Cam3DPreviewResult.cancelled(request)
        except Exception:
            logger.exception("Unexpected CAM 3D preview future failure")
            result = Cam3DPreviewResult.failure(
                request,
                Cam3DPreviewDiagnostic(Cam3DPreviewDiagnosticCode.INTERNAL_ERROR),
            )
        self._finish_result(request, result)

    def _finish_result(
        self,
        request: Cam3DCalculationRequestContract,
        result: Cam3DPreviewResult,
    ) -> None:
        callback: Callable[[Cam3DPreviewResult], None] | None = None
        accepted = False
        with self._lock:
            pending = self._pending.get(request.job_id)
            record = self._records.get(request.job_id)
            if pending is None or record is None or record.state in {
                Cam3DJobExecutionState.COMPLETED,
                Cam3DJobExecutionState.DROPPED,
            }:
                return
            session = self._sessions.get(request.ownership)
            if session is None:
                decision = Cam3DSessionDecision.CLOSED
                update_session = None
            else:
                update = session.accept_result(result.identity)
                decision = update.decision
                update_session = update.session
                if update.accepted:
                    accepted = True
                    callback = pending.callback
            if update_session is not None:
                self._sessions[request.ownership] = update_session
            self._pending.pop(request.job_id, None)
            self._records[request.job_id] = replace(
                record,
                state=(
                    Cam3DJobExecutionState.COMPLETED
                    if accepted
                    else Cam3DJobExecutionState.DROPPED
                ),
                publication_decision=decision,
            )
        if accepted and result.state is Cam3DPreviewCompletionState.SUCCEEDED:
            self._cache.put(request, result.mesh)  # type: ignore[arg-type]
        if not accepted or callback is None:
            return
        try:
            callback(result)
        except Exception:
            logger.exception("CAM 3D preview result callback failed")
        with self._lock:
            current = self._records.get(request.job_id)
            if current is not None:
                self._records[request.job_id] = replace(current, callback_invoked=True)

    def _mark_running(self, job_id: Cam3DCalculationJobId) -> None:
        with self._lock:
            record = self._records.get(job_id)
            if record is not None and record.state is Cam3DJobExecutionState.QUEUED:
                self._records[job_id] = replace(
                    record, state=Cam3DJobExecutionState.RUNNING
                )


def _mesh_diagnostic(error: Cam3DMeshError) -> Cam3DPreviewDiagnostic:
    diagnostic = getattr(error, "diagnostic", None)
    native_code = getattr(getattr(diagnostic, "code", None), "value", "unknown")
    if isinstance(error, Cam3DCancelledError):
        code = Cam3DPreviewDiagnosticCode.CANCELLED
    elif native_code in {"cam3d.surface_missing", "cam3d.surface_stale"}:
        code = Cam3DPreviewDiagnosticCode.GEOMETRY_UNAVAILABLE
    elif native_code.startswith("cam3d.mesh_"):
        code = Cam3DPreviewDiagnosticCode.MESH_INVALID
    else:
        code = Cam3DPreviewDiagnosticCode.TESSELLATION_FAILED
    return Cam3DPreviewDiagnostic(code, (("native_code", native_code),))


__all__ = [
    "Cam3DInMemoryPreviewCache",
    "Cam3DJobExecutionState",
    "Cam3DJobRecord",
    "Cam3DPreviewCacheEntry",
    "Cam3DPreviewCancellationToken",
    "Cam3DPreviewCompletionState",
    "Cam3DPreviewDiagnostic",
    "Cam3DPreviewDiagnosticCode",
    "Cam3DPreviewMesh",
    "Cam3DPreviewResult",
    "Cam3DPreviewSource",
    "Cam3DPreviewTessellator",
    "Cam3DPreviewCoordinator",
    "Cam3DCancelDecision",
    "Cam3DSubmissionDecision",
    "Cam3DSubmissionReceipt",
]
