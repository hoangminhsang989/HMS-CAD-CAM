"""Qt bridge, UI coordinator and viewport mapper for Lathe Preview V1-V3."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import logging
from threading import RLock
from typing import Protocol
import weakref

from PySide6.QtCore import QObject, Qt, Signal, Slot
from shiboken6 import isValid

from hms_cadcam.cam.lathe.application import LatheOperationService
from hms_cadcam.cam.lathe.domain import LatheOwnershipKey
from hms_cadcam.cam.lathe.presenter import LatheOperationSnapshot
from hms_cadcam.cam.lathe.toolpath.model import (
    LathePathSegment,
    LatheToolpathDiagnostic,
    LatheToolpathDiagnosticCode,
    LatheToolpathJobId,
    LatheToolpathResult,
    LatheToolpathResultSource,
    LatheToolpathResultState,
)
from hms_cadcam.cam.lathe.toolpath.request import (
    LatheToolpathRequestBuilder,
    LatheToolpathRequestV1,
)
from hms_cadcam.cam.lathe.toolpath.runtime import (
    LatheCancelDecision,
    LatheSubmissionReceipt,
    LatheToolpathCoordinator,
)
from hms_cadcam.cam.lathe.toolpath.stock import LatheStockSnapshotV1
from hms_cadcam.viewer.lathe import (
    LathePreviewActorIdentity,
    LathePreviewOwnership,
    LathePreviewPublication,
    LathePreviewPublicationResult,
    LathePreviewPublicationSource,
    LathePreviewSegmentData,
)
from hms_cadcam.viewer.widget import CadViewportWidget

logger = logging.getLogger(__name__)


class LatheToolpathUiStateCode(StrEnum):
    READY = "ready"
    CALCULATING = "calculating"
    CANCELLING = "cancelling"
    PREVIEW_READY = "preview_ready"
    CACHE_HIT = "cache_hit"
    CANCELLED = "cancelled"
    UNSUPPORTED_STRATEGY = "unsupported_strategy"
    THREAD_UNSUPPORTED_V2 = "stage12_2_thread_unsupported"
    INVALID_REQUEST = "invalid_request"
    GENERATION_FAILED = "generation_failed"
    PUBLICATION_FAILED = "publication_failed"
    STALE_RESULT_DROPPED = "stale_result_dropped"


@dataclass(frozen=True, slots=True)
class LatheToolpathUiState:
    code: LatheToolpathUiStateCode
    ownership: LatheOwnershipKey | None = None
    job_id: LatheToolpathJobId | None = None
    diagnostic: LatheToolpathDiagnostic | None = None
    diagnostics: tuple[LatheToolpathDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.code, LatheToolpathUiStateCode):
            raise TypeError("Lathe toolpath UI state code is invalid")
        if self.ownership is not None and not isinstance(
            self.ownership, LatheOwnershipKey
        ):
            raise TypeError("Lathe toolpath UI ownership is invalid")
        if self.job_id is not None and not isinstance(
            self.job_id, LatheToolpathJobId
        ):
            raise TypeError("Lathe toolpath UI job identity is invalid")
        if self.diagnostic is not None and not isinstance(
            self.diagnostic, LatheToolpathDiagnostic
        ):
            raise TypeError("Lathe toolpath UI diagnostic is invalid")
        if not isinstance(self.diagnostics, tuple) or any(
            not isinstance(item, LatheToolpathDiagnostic)
            for item in self.diagnostics
        ):
            raise TypeError("Lathe toolpath UI diagnostics are invalid")
        if self.diagnostic is not None and not self.diagnostics:
            object.__setattr__(self, "diagnostics", (self.diagnostic,))
        elif (
            self.diagnostic is not None
            and self.diagnostics
            and self.diagnostic not in self.diagnostics
        ):
            raise ValueError("Lathe toolpath primary diagnostic is not in its set")


class LatheToolpathQtBridge(QObject):
    """Queue coordinator callbacks onto the QObject owner thread."""

    result_ready = Signal(object)
    _queued_result = Signal(object)

    def __init__(
        self,
        coordinator: LatheToolpathCoordinator,
        parent: QObject | None = None,
    ) -> None:
        if not isinstance(coordinator, LatheToolpathCoordinator):
            raise TypeError("Lathe Qt bridge coordinator is invalid")
        super().__init__(parent)
        self.setObjectName("LatheToolpathQtBridge")
        self._coordinator = coordinator
        self._state_lock = RLock()
        self._closed = False
        self._receiver: weakref.ReferenceType[QObject] | None = None
        self._receiver_method: str | None = None
        self._queued_result.connect(
            self._dispatch,
            Qt.ConnectionType.QueuedConnection,
        )
        self.destroyed.connect(self._on_destroyed)

    def set_receiver(
        self,
        receiver: QObject | None,
        method_name: str = "handle_lathe_toolpath_result",
    ) -> None:
        if receiver is None:
            with self._state_lock:
                self._receiver = None
                self._receiver_method = None
            return
        if not isinstance(receiver, QObject) or not isValid(receiver):
            raise TypeError("Lathe Qt bridge receiver is invalid")
        if not isinstance(method_name, str) or not method_name.strip():
            raise ValueError("Lathe Qt bridge receiver method is invalid")
        normalized = method_name.strip()
        if not callable(getattr(receiver, normalized, None)):
            raise TypeError("Lathe Qt bridge receiver method is not callable")
        with self._state_lock:
            current = self._receiver() if self._receiver is not None else None
            if current is receiver and self._receiver_method == normalized:
                return
            self._receiver = weakref.ref(receiver)
            self._receiver_method = normalized

    def submit(
        self, request: LatheToolpathRequestV1
    ) -> LatheSubmissionReceipt:
        bridge_ref = weakref.ref(self)

        def enqueue(result: LatheToolpathResult) -> None:
            bridge = bridge_ref()
            if bridge is not None:
                bridge._enqueue(result)

        return self._coordinator.submit(request, callback=enqueue)

    def cancel(self, job_id: LatheToolpathJobId) -> LatheCancelDecision:
        return self._coordinator.cancel(job_id)

    def close_ownership(self, ownership: LatheOwnershipKey) -> None:
        self._coordinator.close_ownership(ownership)

    def shutdown(self, *, wait: bool = False) -> None:
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            self._receiver = None
            self._receiver_method = None
        self._coordinator.shutdown(wait=wait)

    def _enqueue(self, result: LatheToolpathResult) -> None:
        if not isinstance(result, LatheToolpathResult):
            return
        with self._state_lock:
            if self._closed:
                return
        try:
            self._queued_result.emit(result)
        except RuntimeError:
            return

    @Slot(object)
    def _dispatch(self, payload: object) -> None:
        if not isinstance(payload, LatheToolpathResult):
            return
        with self._state_lock:
            if self._closed:
                return
            receiver = self._receiver() if self._receiver is not None else None
            method_name = self._receiver_method
        if not self._coordinator.delivery_authorized(payload):
            return
        self.result_ready.emit(payload)
        if receiver is None or method_name is None or not isValid(receiver):
            return
        callback = getattr(receiver, method_name, None)
        if not callable(callback):
            return
        try:
            callback(payload)
        except RuntimeError:
            return
        except Exception:
            logger.exception("Lathe Qt bridge receiver callback failed")

    @Slot()
    def _on_destroyed(self) -> None:
        self.shutdown(wait=False)


def lathe_preview_ownership(
    ownership: LatheOwnershipKey,
) -> LathePreviewOwnership:
    if not isinstance(ownership, LatheOwnershipKey):
        raise TypeError("Lathe viewport ownership is invalid")
    return LathePreviewOwnership(
        ownership.project_id,
        ownership.document_id,
        ownership.source_id,
        ownership.generation,
        ownership.setup_id,
        ownership.operation_id,
    )


def lathe_preview_publication_from_result(
    result: LatheToolpathResult,
) -> LathePreviewPublication:
    """Map domain diameter X to viewport radius X/2 on the XZ plane."""

    if not isinstance(result, LatheToolpathResult):
        raise TypeError("Lathe viewport result is invalid")
    if not result.succeeded:
        raise ValueError("Only a successful Lathe result can be published")
    segments = tuple(
        LathePreviewSegmentData(
            item.sequence_index,
            item.motion_class,
            (
                float(item.start.x_diameter_mm / 2.0),
                0.0,
                float(item.start.z_mm),
            ),
            (
                float(item.end.x_diameter_mm / 2.0),
                0.0,
                float(item.end.z_mm),
            ),
            item.semantic_source,
        )
        for item in result.motions
        if isinstance(item, LathePathSegment)
    )
    if not segments:
        raise ValueError("Lathe result has no publishable segments")
    identity = result.identity
    return LathePreviewPublication(
        LathePreviewActorIdentity(
            lathe_preview_ownership(identity.ownership),
            str(identity.job_id),
            identity.request_sequence,
            identity.fingerprint.digest,
            result.cache_key.digest,
            LathePreviewPublicationSource(result.source.value),
        ),
        segments,
    )


class LathePreviewSink(Protocol):
    def publish(self, result: LatheToolpathResult) -> bool: ...

    def clear(self, ownership: LatheOwnershipKey) -> bool: ...


class LatheViewportPreviewSink:
    """Publish only accepted immutable Lathe paths to the product viewport."""

    def __init__(self, viewport: CadViewportWidget) -> None:
        if not isinstance(viewport, CadViewportWidget):
            raise TypeError("Lathe viewport sink requires CadViewportWidget")
        self._viewport = viewport

    def publish(self, result: LatheToolpathResult) -> bool:
        try:
            publication = lathe_preview_publication_from_result(result)
        except (TypeError, ValueError):
            return False
        outcome = self._viewport.publish_lathe_preview(publication)
        if not isinstance(outcome, LathePreviewPublicationResult):
            logger.error("Lathe viewport returned an invalid publication outcome")
            return False
        if not outcome.succeeded:
            logger.warning(
                "Lathe viewport publication rejected: %s", outcome.code.value
            )
        return outcome.succeeded

    def clear(self, ownership: LatheOwnershipKey) -> bool:
        try:
            viewer_ownership = lathe_preview_ownership(ownership)
        except TypeError:
            return False
        outcome = self._viewport.clear_lathe_preview(viewer_ownership)
        if not outcome.succeeded:
            logger.warning("Lathe viewport clear rejected: %s", outcome.code.value)
        return outcome.succeeded


class LatheToolpathUiController(QObject):
    """Own one request/worker/bridge/publication flow for a live Lathe session."""

    state_changed = Signal(object)

    def __init__(
        self,
        service: LatheOperationService,
        stock: LatheStockSnapshotV1 | None,
        sink: LathePreviewSink,
        *,
        coordinator: LatheToolpathCoordinator | None = None,
        parent: QObject | None = None,
    ) -> None:
        if not isinstance(service, LatheOperationService):
            raise TypeError("Lathe toolpath UI service is invalid")
        if stock is not None and not isinstance(stock, LatheStockSnapshotV1):
            raise TypeError("Lathe toolpath UI stock is invalid")
        if not callable(getattr(sink, "publish", None)) or not callable(
            getattr(sink, "clear", None)
        ):
            raise TypeError("Lathe toolpath UI sink is invalid")
        if coordinator is not None and not isinstance(
            coordinator, LatheToolpathCoordinator
        ):
            raise TypeError("Lathe toolpath UI coordinator is invalid")
        super().__init__(parent)
        self.setObjectName("LatheToolpathUiController")
        self._service = service
        self._stock = stock
        self._sink = sink
        self._coordinator = coordinator or LatheToolpathCoordinator()
        self._bridge = LatheToolpathQtBridge(self._coordinator, self)
        self._bridge.set_receiver(self)
        self._builder = LatheToolpathRequestBuilder()
        self._sequence = 0
        self._active_job_id: LatheToolpathJobId | None = None
        self._active_ownership: LatheOwnershipKey | None = None
        self._published_ownership: LatheOwnershipKey | None = None
        self._known_ownerships: set[LatheOwnershipKey] = set()
        self._closed = False
        self._state = LatheToolpathUiState(LatheToolpathUiStateCode.READY)

    @property
    def state(self) -> LatheToolpathUiState:
        return self._state

    @property
    def coordinator(self) -> LatheToolpathCoordinator:
        return self._coordinator

    @property
    def bridge(self) -> LatheToolpathQtBridge:
        return self._bridge

    @property
    def active_job_id(self) -> LatheToolpathJobId | None:
        return self._active_job_id

    def preview(self, operation: LatheOperationSnapshot) -> LatheSubmissionReceipt | None:
        """Build and submit only from an explicit UI action."""

        if not isinstance(operation, LatheOperationSnapshot):
            raise TypeError("Lathe preview operation snapshot is invalid")
        if self._closed:
            self._set_state(
                LatheToolpathUiState(
                    LatheToolpathUiStateCode.INVALID_REQUEST,
                    operation.ownership,
                    diagnostic=LatheToolpathDiagnostic(
                        LatheToolpathDiagnosticCode.CLOSED
                    ),
                )
            )
            return None
        self._sequence += 1
        job_id = LatheToolpathJobId.new()
        built = self._builder.build(
            service=self._service,
            operation_id=operation.ownership.operation_id,
            expected_revision=operation.revision,
            stock=self._stock,
            job_id=job_id,
            request_sequence=self._sequence,
        )
        if not built.accepted or built.request is None:
            diagnostic = built.diagnostics[0]
            if diagnostic.code is (
                LatheToolpathDiagnosticCode.THREAD_TOOLPATH_NOT_IMPLEMENTED_V2
            ):
                code = LatheToolpathUiStateCode.THREAD_UNSUPPORTED_V2
            elif diagnostic.code is (
                LatheToolpathDiagnosticCode.TOOLPATH_NOT_IMPLEMENTED_V1
            ):
                code = LatheToolpathUiStateCode.UNSUPPORTED_STRATEGY
            else:
                code = LatheToolpathUiStateCode.INVALID_REQUEST
            self._set_state(
                LatheToolpathUiState(
                    code,
                    operation.ownership,
                    job_id,
                    diagnostic,
                )
            )
            return None
        request = built.request
        self._known_ownerships.add(request.ownership)
        self._coordinator.bind_ownership(request.ownership)
        self._active_job_id = request.job_id
        self._active_ownership = request.ownership
        self._set_state(
            LatheToolpathUiState(
                LatheToolpathUiStateCode.CALCULATING,
                request.ownership,
                request.job_id,
            )
        )
        receipt = self._bridge.submit(request)
        if not receipt.accepted:
            self._active_job_id = None
            self._set_state(
                LatheToolpathUiState(
                    LatheToolpathUiStateCode.INVALID_REQUEST,
                    request.ownership,
                    request.job_id,
                    LatheToolpathDiagnostic(
                        LatheToolpathDiagnosticCode.INVALID_REQUEST,
                        details=(("decision", receipt.decision.value),),
                    ),
                )
            )
        return receipt

    def cancel(self) -> LatheCancelDecision:
        job_id = self._active_job_id
        if job_id is None:
            return LatheCancelDecision.NOT_FOUND
        ownership = self._active_ownership
        self._set_state(
            LatheToolpathUiState(
                LatheToolpathUiStateCode.CANCELLING,
                ownership,
                job_id,
            )
        )
        decision = self._bridge.cancel(job_id)
        if decision in {
            LatheCancelDecision.REQUESTED,
            LatheCancelDecision.ALREADY_CANCELLED,
        }:
            self._active_job_id = None
            self._set_state(
                LatheToolpathUiState(
                    LatheToolpathUiStateCode.CANCELLED,
                    ownership,
                    job_id,
                    LatheToolpathDiagnostic(
                        LatheToolpathDiagnosticCode.CANCELLED
                    ),
                )
            )
        return decision

    def update_stock(self, stock: LatheStockSnapshotV1 | None) -> None:
        if stock is not None and not isinstance(stock, LatheStockSnapshotV1):
            raise TypeError("Lathe toolpath stock transition is invalid")
        if stock == self._stock:
            return
        self.cancel()
        self.clear_published()
        self._stock = stock

    def invalidate_ownership(self, ownership: LatheOwnershipKey) -> None:
        if not isinstance(ownership, LatheOwnershipKey):
            raise TypeError("Lathe toolpath invalidation ownership is invalid")
        if self._active_ownership == ownership:
            self.cancel()
        if self._published_ownership == ownership:
            self.clear_published()
        self._bridge.close_ownership(ownership)

    def transition(self, stock: LatheStockSnapshotV1 | None) -> None:
        """Cancel and clear exact owned state on source/setup/read-only changes."""

        self.cancel()
        self.clear_published()
        for ownership in tuple(self._known_ownerships):
            self._bridge.close_ownership(ownership)
        self._known_ownerships.clear()
        self._active_ownership = None
        self._stock = stock
        self._set_state(LatheToolpathUiState(LatheToolpathUiStateCode.READY))

    def invalidate_after_edit(self) -> None:
        """Cancel and clear derived preview after an accepted semantic edit."""

        if self._closed:
            return
        self.cancel()
        self.clear_published()
        self._active_job_id = None
        self._active_ownership = None
        self._set_state(LatheToolpathUiState(LatheToolpathUiStateCode.READY))

    def clear_published(self) -> bool:
        ownership = self._published_ownership
        if ownership is None:
            return True
        cleared = bool(self._sink.clear(ownership))
        if cleared:
            self._published_ownership = None
        return cleared

    @Slot(object)
    def handle_lathe_toolpath_result(self, payload: object) -> None:
        if self._closed or not isinstance(payload, LatheToolpathResult):
            return
        ownership = payload.identity.ownership
        if payload.state is LatheToolpathResultState.CANCELLED:
            self._active_job_id = None
            self._set_state(
                LatheToolpathUiState(
                    LatheToolpathUiStateCode.CANCELLED,
                    ownership,
                    payload.identity.job_id,
                    payload.diagnostics[0],
                )
            )
            return
        if not payload.succeeded:
            self._active_job_id = None
            self._set_state(
                LatheToolpathUiState(
                    LatheToolpathUiStateCode.GENERATION_FAILED,
                    ownership,
                    payload.identity.job_id,
                    payload.diagnostics[0],
                )
            )
            return
        if not self._sink.publish(payload):
            self._active_job_id = None
            self._set_state(
                LatheToolpathUiState(
                    LatheToolpathUiStateCode.PUBLICATION_FAILED,
                    ownership,
                    payload.identity.job_id,
                    LatheToolpathDiagnostic(
                        LatheToolpathDiagnosticCode.PUBLICATION_FAILED
                    ),
                )
            )
            return
        self._published_ownership = ownership
        self._active_job_id = None
        primary_diagnostic = next(
            (
                item
                for item in payload.diagnostics
                if item.code
                is LatheToolpathDiagnosticCode.PHASE_NEUTRAL_SYNCHRONIZED_CENTERLINE_PREVIEW
            ),
            payload.diagnostics[0] if payload.diagnostics else None,
        )
        self._set_state(
            LatheToolpathUiState(
                (
                    LatheToolpathUiStateCode.CACHE_HIT
                    if payload.source is LatheToolpathResultSource.CACHE
                    else LatheToolpathUiStateCode.PREVIEW_READY
                ),
                ownership,
                payload.identity.job_id,
                primary_diagnostic,
                payload.diagnostics,
            )
        )

    def shutdown(self, *, wait: bool = True) -> None:
        if self._closed:
            return
        self._closed = True
        self.cancel()
        self.clear_published()
        self._bridge.set_receiver(None)
        self._bridge.shutdown(wait=wait)

    def _set_state(self, state: LatheToolpathUiState) -> None:
        self._state = state
        self.state_changed.emit(state)


__all__ = [
    "LathePreviewSink",
    "LatheToolpathQtBridge",
    "LatheToolpathUiController",
    "LatheToolpathUiState",
    "LatheToolpathUiStateCode",
    "LatheViewportPreviewSink",
    "lathe_preview_ownership",
    "lathe_preview_publication_from_result",
]
