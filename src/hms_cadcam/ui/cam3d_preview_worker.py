"""Thin Qt queued-delivery bridge for the Qt-free CAM 3D preview worker."""

from __future__ import annotations

import logging
from threading import RLock
import weakref

from PySide6.QtCore import QObject, Qt, Signal, Slot
from shiboken6 import isValid

from hms_cadcam.cam.application.cam3d_preview import (
    Cam3DCancelDecision,
    Cam3DPreviewCoordinator,
    Cam3DPreviewResult,
    Cam3DSubmissionReceipt,
)
from hms_cadcam.cam.application.cam3d_request import (
    Cam3DCalculationJobId,
    Cam3DCalculationOwnershipKey,
    Cam3DCalculationRequestContract,
)

logger = logging.getLogger(__name__)


class Cam3DQtWorkerBridge(QObject):
    """Schedule through the coordinator and deliver only on the Qt owner thread."""

    result_ready = Signal(object)
    _queued_result = Signal(object)

    def __init__(
        self,
        coordinator: Cam3DPreviewCoordinator,
        parent: QObject | None = None,
    ) -> None:
        if not isinstance(coordinator, Cam3DPreviewCoordinator):
            raise TypeError("CAM 3D Qt bridge coordinator is invalid")
        super().__init__(parent)
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
        method_name: str = "handle_cam3d_preview",
    ) -> None:
        """Weakly bind one QObject receiver; repeated binding is idempotent."""
        if receiver is None:
            with self._state_lock:
                self._receiver = None
                self._receiver_method = None
            return
        if not isinstance(receiver, QObject) or not isValid(receiver):
            raise TypeError("CAM 3D Qt preview receiver is invalid")
        if not isinstance(method_name, str) or not method_name.strip():
            raise ValueError("CAM 3D Qt preview receiver method is invalid")
        normalized = method_name.strip()
        if not callable(getattr(receiver, normalized, None)):
            raise TypeError("CAM 3D Qt preview receiver method is not callable")
        with self._state_lock:
            current = self._receiver() if self._receiver is not None else None
            if current is receiver and self._receiver_method == normalized:
                return
            self._receiver = weakref.ref(receiver)
            self._receiver_method = normalized

    def submit(
        self,
        request: Cam3DCalculationRequestContract,
    ) -> Cam3DSubmissionReceipt:
        """Forward explicit submission; no field change can call this implicitly."""
        bridge_ref = weakref.ref(self)

        def enqueue(result: Cam3DPreviewResult) -> None:
            bridge = bridge_ref()
            if bridge is not None:
                bridge._enqueue(result)

        return self._coordinator.submit(request, callback=enqueue)

    def cancel(self, job_id: Cam3DCalculationJobId) -> Cam3DCancelDecision:
        return self._coordinator.cancel(job_id)

    def close_ownership(self, ownership: Cam3DCalculationOwnershipKey) -> None:
        self._coordinator.close_ownership(ownership)

    def switch_ownership(
        self,
        previous: Cam3DCalculationOwnershipKey,
        current: Cam3DCalculationOwnershipKey,
        project_generation: int,
    ) -> None:
        self._coordinator.switch_ownership(previous, current, project_generation)

    def shutdown(self, *, wait: bool = False) -> None:
        """Suppress late delivery and cooperatively tear down worker ownership."""
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            self._receiver = None
            self._receiver_method = None
        self._coordinator.shutdown(wait=wait)

    def _enqueue(self, result: Cam3DPreviewResult) -> None:
        if not isinstance(result, Cam3DPreviewResult):
            return
        with self._state_lock:
            if self._closed:
                return
        try:
            self._queued_result.emit(result)
        except RuntimeError:
            # The QObject can be deleted between the weak-reference check and
            # emit.  The coordinator has already retained no GUI object.
            return

    @Slot(object)
    def _dispatch(self, payload: object) -> None:
        if not isinstance(payload, Cam3DPreviewResult):
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
            # Deleted C++ receivers are fail-closed even if their Python wrapper
            # survives until the queued event is processed.
            return
        except Exception:
            logger.exception("CAM 3D Qt preview receiver callback failed")

    @Slot()
    def _on_destroyed(self) -> None:
        self.shutdown(wait=False)


__all__ = ["Cam3DQtWorkerBridge"]
