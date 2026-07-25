"""Qt widget adapting native events to the product viewport backend."""

from __future__ import annotations

from collections.abc import Callable
import logging
from uuid import UUID

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import (
    QCloseEvent,
    QMouseEvent,
    QPaintEngine,
    QPaintEvent,
    QResizeEvent,
    QShowEvent,
    QWheelEvent,
)
from PySide6.QtWidgets import QLabel, QSizePolicy, QWidget

from hms_cadcam.cad.kernel import CadKernel
from hms_cadcam.cad.models import CadDocumentId, CadObjectId
from hms_cadcam.cam.domain import OperationId, SimulationResultId, WcsFrame
from hms_cadcam.cam.simulation.model import SimulationResult
from hms_cadcam.cam.toolpath import ToolpathArtifact
from hms_cadcam.viewer.backend import CadViewportBackend
from hms_cadcam.viewer.factory import CadViewportBackendFactory
from hms_cadcam.viewer.models import (
    DisplayMode,
    KeyboardModifier,
    MouseButton,
    ObjectColor,
    SelectionMetadata,
    SelectionMode,
    ViewDirection,
    ViewportStatus,
)
from hms_cadcam.viewer.toolpath import ToolpathPresentation
from hms_cadcam.viewer.simulation import (
    SimulationDisplayContext,
    SimulationDisplayPolicy,
    SimulationDisplayRequest,
    SimulationIssueMarker,
    SimulationPresentation,
)

_QT_MOUSE_BUTTONS = {
    Qt.MouseButton.LeftButton: MouseButton.LEFT,
    Qt.MouseButton.MiddleButton: MouseButton.MIDDLE,
    Qt.MouseButton.RightButton: MouseButton.RIGHT,
}


class CadViewportWidget(QWidget):
    """Own backend lifecycle without importing or exposing native CAD objects."""

    selection_changed = Signal(object)
    selection_context_changed = Signal(object, object)
    status_changed = Signal(object)

    def __init__(
        self,
        kernel: CadKernel,
        backend: CadViewportBackend | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("CadViewportWidget")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(520, 360)
        self.setAttribute(Qt.WidgetAttribute.WA_NativeWindow)
        self.setAttribute(Qt.WidgetAttribute.WA_PaintOnScreen)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setMouseTracking(True)
        self._backend = backend or CadViewportBackendFactory.create(kernel)
        self._status = self._backend.get_status()
        self._native_painting = self._status.available
        self._initialized = False
        self._closed = False
        self._selection_document_id: CadDocumentId | None = None
        self._status_text_resolver: Callable[[str], str] = str
        self._status_label = QLabel(self)
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_label.setWordWrap(True)
        self._status_label.setStyleSheet(
            "background: #e7e8ea; color: #6f7780; padding: 16px;"
        )
        self._backend.set_selection_callback(self._receive_selection)
        self._refresh_status(self._status)

    def set_status_text_resolver(
        self,
        resolver: Callable[[str], str],
    ) -> None:
        """Install the UI text resolver without changing viewport state."""
        if not callable(resolver):
            raise TypeError("Viewport status text resolver must be callable")
        self._status_text_resolver = resolver
        self.retranslate_status()

    def retranslate_status(self) -> None:
        """Refresh only the rendered fallback text for the current status."""
        self._refresh_status(self._status, emit_status=False)

    @property
    def viewport_status(self) -> ViewportStatus:
        """Return the latest backend state exposed to product UI."""
        return self._status

    def initialize_viewport(self) -> None:
        """Initialize the native backend at most once for this HWND."""
        if self._initialized or self._closed:
            return
        try:
            self._backend.initialize(int(self.winId()))
            self._initialized = True
            self._backend.resize(self.width(), self.height())
            self._refresh_status(self._backend.get_status())
        except Exception as error:
            self._report_backend_error("initialize", error)

    def display_document(self, document_id: CadDocumentId) -> bool:
        """Display a kernel-owned document and report presentation success."""
        self.initialize_viewport()
        if self._initialized:
            previous_document_id = self._selection_document_id
            self._selection_document_id = document_id
            displayed = self._invoke(
                "display document",
                self._backend.display_document,
                document_id,
                clear_error=True,
            )
            if not displayed:
                self._selection_document_id = previous_document_id
            return displayed
        return False

    def clear(self) -> None:
        """Clear the presentation and all selection metadata."""
        previous_document_id = self._selection_document_id
        self._selection_document_id = None
        if not self._invoke("clear", self._backend.clear, clear_error=True):
            self._selection_document_id = previous_document_id

    def display_toolpath(self, artifact: ToolpathArtifact) -> bool:
        """Display derived CAM geometry without entering CAD selection state."""
        self.initialize_viewport()
        callback = getattr(self._backend, "display_toolpath", None)
        return bool(self._initialized and callable(callback) and self._invoke(
            "display toolpath", callback, artifact, clear_error=True))

    @property
    def toolpath_presentations(self) -> tuple[ToolpathPresentation, ...]:
        """Expose native-free CAM metadata for the current viewer session."""
        callback = getattr(self._backend, "get_toolpath_presentations", None)
        return callback() if callable(callback) else ()

    def clear_toolpaths(self) -> None:
        callback = getattr(self._backend, "clear_toolpaths", None)
        if callable(callback):
            self._invoke("clear toolpaths", callback, clear_error=True)

    def remove_toolpath(self, operation_id: OperationId) -> None:
        """Remove one CAM presentation without disturbing CAD or other operations."""
        callback = getattr(self._backend, "remove_toolpath", None)
        if callable(callback):
            self._invoke("remove toolpath", callback, operation_id, clear_error=True)

    def set_toolpath_visibility(self, operation_id: OperationId, visible: bool) -> bool:
        callback = getattr(self._backend, "set_toolpath_visibility", None)
        return bool(callable(callback) and self._invoke("toolpath visibility", callback,
                                                        operation_id, visible, clear_error=True))

    def bind_simulation_project(
        self,
        project_id: UUID | None,
        generation: int | None,
    ) -> bool:
        """Bind session-only overlays to the active project generation."""
        callback = getattr(self._backend, "bind_simulation_project", None)
        return bool(callable(callback) and self._invoke(
            "bind simulation project", callback, project_id, generation,
            clear_error=True,
        ))

    def request_simulation_display(
        self,
        operation_id: OperationId,
        *,
        generation: int,
    ) -> SimulationDisplayRequest | None:
        callback = getattr(self._backend, "request_simulation_display", None)
        if not callable(callback):
            return None
        try:
            return callback(operation_id, generation=generation)
        except Exception as error:
            self._report_backend_error("request simulation display", error)
            return None

    def display_simulation(
        self,
        result: SimulationResult,
        artifact: ToolpathArtifact,
        wcs: WcsFrame,
        context: SimulationDisplayContext,
        request: SimulationDisplayRequest | None = None,
        policy: SimulationDisplayPolicy | None = None,
    ) -> bool:
        """Display one published result without running simulation from UI."""
        self.initialize_viewport()
        callback = getattr(self._backend, "display_simulation", None)
        return bool(self._initialized and callable(callback) and self._invoke(
            "display simulation", callback, result, artifact, wcs, context,
            request, policy, clear_error=True,
        ))

    @property
    def simulation_presentations(self) -> tuple[SimulationPresentation, ...]:
        callback = getattr(self._backend, "get_simulation_presentations", None)
        return callback() if callable(callback) else ()

    def set_simulation_visibility(
        self,
        operation_id: OperationId,
        visible: bool,
    ) -> bool:
        callback = getattr(self._backend, "set_simulation_visibility", None)
        return bool(callable(callback) and self._invoke(
            "simulation visibility", callback, operation_id, visible,
            clear_error=True,
        ))

    def lookup_simulation_issue(
        self,
        *,
        project_id: UUID,
        operation_id: OperationId,
        result_id: SimulationResultId,
        marker_id: str,
    ) -> SimulationIssueMarker | None:
        callback = getattr(self._backend, "lookup_simulation_issue", None)
        if not callable(callback):
            return None
        try:
            return callback(
                project_id=project_id,
                operation_id=operation_id,
                result_id=result_id,
                marker_id=marker_id,
            )
        except Exception as error:
            self._report_backend_error("lookup simulation issue", error)
            return None

    def focus_simulation_issue(
        self,
        *,
        project_id: UUID,
        operation_id: OperationId,
        result_id: SimulationResultId,
        marker_id: str,
    ) -> bool:
        """Highlight one current simulation marker without CAD selection."""
        callback = getattr(self._backend, "focus_simulation_issue", None)
        if not callable(callback) or self._closed:
            return False
        try:
            return bool(callback(
                project_id=project_id,
                operation_id=operation_id,
                result_id=result_id,
                marker_id=marker_id,
            ))
        except Exception as error:
            self._report_backend_error("focus simulation issue", error)
            return False

    def clear_simulation_issue_focus(self) -> None:
        callback = getattr(self._backend, "clear_simulation_issue_focus", None)
        if callable(callback):
            self._invoke("clear simulation issue focus", callback, clear_error=True)

    def remove_simulation(self, operation_id: OperationId) -> None:
        callback = getattr(self._backend, "remove_simulation", None)
        if callable(callback):
            self._invoke("remove simulation", callback, operation_id, clear_error=True)

    def clear_simulations(self) -> None:
        callback = getattr(self._backend, "clear_simulations", None)
        if callable(callback):
            self._invoke("clear simulations", callback, clear_error=True)

    def fit_all(self) -> None:
        """Fit the displayed document into the current widget."""
        self._invoke("fit all", self._backend.fit_all, clear_error=True)

    def set_view_direction(self, direction: ViewDirection) -> bool:
        """Apply one standard camera direction."""
        return self._invoke(
            "set view direction",
            self._backend.set_view_direction,
            direction,
            clear_error=True,
        )

    def set_display_mode(self, mode: DisplayMode) -> bool:
        """Apply one product presentation style."""
        return self._invoke(
            "set display mode",
            self._backend.set_display_mode,
            mode,
            clear_error=True,
        )

    def set_selection_mode(self, mode: SelectionMode) -> None:
        """Activate one product topology selection level."""
        self._invoke(
            "set selection mode",
            self._backend.set_selection_mode,
            mode,
            clear_error=True,
        )

    def select_objects(
        self,
        document_id: CadDocumentId,
        object_ids: tuple[CadObjectId, ...],
    ) -> bool:
        """Highlight managed objects without synthesizing topology nodes."""
        return self._invoke(
            "select objects",
            self._backend.select_objects,
            document_id,
            object_ids,
            clear_error=True,
        )

    def set_object_visibility(
        self,
        document_id: CadDocumentId,
        object_id: CadObjectId,
        visible: bool,
    ) -> bool:
        return self._invoke(
            "set object visibility",
            self._backend.set_object_visibility,
            document_id,
            object_id,
            visible,
            clear_error=True,
        )

    def isolate_object(
        self,
        document_id: CadDocumentId,
        object_id: CadObjectId,
    ) -> bool:
        return self._invoke(
            "isolate object",
            self._backend.isolate_object,
            document_id,
            object_id,
            clear_error=True,
        )

    def reset_isolate(self, document_id: CadDocumentId) -> bool:
        return self._invoke(
            "reset isolate",
            self._backend.reset_isolate,
            document_id,
            clear_error=True,
        )

    def set_object_color(
        self,
        document_id: CadDocumentId,
        object_id: CadObjectId,
        color: ObjectColor,
    ) -> bool:
        return self._invoke(
            "set object color",
            self._backend.set_object_color,
            document_id,
            object_id,
            color,
            clear_error=True,
        )

    def set_object_transparency(
        self,
        document_id: CadDocumentId,
        object_id: CadObjectId,
        transparency: float,
    ) -> bool:
        return self._invoke(
            "set object transparency",
            self._backend.set_object_transparency,
            document_id,
            object_id,
            transparency,
            clear_error=True,
        )

    def reset_object_appearance(
        self,
        document_id: CadDocumentId,
        object_id: CadObjectId,
    ) -> bool:
        return self._invoke(
            "reset object appearance",
            self._backend.reset_object_appearance,
            document_id,
            object_id,
            clear_error=True,
        )

    def shutdown(self) -> None:
        """Close backend resources idempotently before HWND destruction."""
        if self._closed:
            return
        self._closed = True
        self._selection_document_id = None
        try:
            self._backend.close()
        except Exception:
            logging.getLogger(__name__).exception("CAD viewport backend close failed")
        self._initialized = False

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802 - Qt API name
        super().showEvent(event)
        self.initialize_viewport()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt API name
        super().resizeEvent(event)
        self._status_label.setGeometry(self.rect())
        if not self._closed:
            self._invoke(
                "resize",
                self._backend.resize,
                event.size().width(),
                event.size().height(),
            )

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        point = event.position().toPoint()
        self._invoke(
            "mouse press",
            self._backend.handle_mouse_press,
            point.x(),
            point.y(),
            _mouse_button(event.button()),
            _keyboard_modifiers(event.modifiers()),
        )
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        point = event.position().toPoint()
        self._invoke(
            "mouse move",
            self._backend.handle_mouse_move,
            point.x(),
            point.y(),
            _pressed_buttons(event.buttons()),
            _keyboard_modifiers(event.modifiers()),
        )
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        point = event.position().toPoint()
        self._invoke(
            "mouse release",
            self._backend.handle_mouse_release,
            point.x(),
            point.y(),
            _mouse_button(event.button()),
            _keyboard_modifiers(event.modifiers()),
        )
        event.accept()

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        point = event.position().toPoint()
        self._invoke(
            "mouse wheel",
            self._backend.handle_wheel,
            point.x(),
            point.y(),
            event.angleDelta().y(),
            _keyboard_modifiers(event.modifiers()),
        )
        event.accept()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - Qt API name
        if self._native_painting and self._initialized and not self._closed:
            self._invoke("redraw", self._backend.resize, self.width(), self.height())
            return
        super().paintEvent(event)

    def paintEngine(self) -> QPaintEngine | None:  # noqa: N802 - Qt API name
        if getattr(self, "_native_painting", False):
            return None
        return super().paintEngine()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt API name
        self.shutdown()
        super().closeEvent(event)

    def _receive_selection(self, items: tuple[SelectionMetadata, ...]) -> None:
        if not self._closed:
            self.selection_changed.emit(items)
            self.selection_context_changed.emit(
                self._selection_document_id,
                items,
            )

    def _invoke(
        self,
        label: str,
        operation,
        *args: object,
        clear_error: bool = False,
    ) -> bool:
        if self._closed:
            return False
        try:
            operation(*args)
        except Exception as error:
            self._report_backend_error(label, error)
            return False
        if clear_error:
            backend_status = self._backend.get_status()
            self._native_painting = backend_status.available
            if backend_status != self._status:
                self._refresh_status(backend_status)
        return True

    def _report_backend_error(self, operation: str, error: Exception) -> None:
        logging.getLogger(__name__).exception(
            "CAD viewport backend failed during %s",
            operation,
        )
        backend_status = self._backend.get_status()
        self._native_painting = backend_status.available
        self._refresh_status(
            ViewportStatus(
                available=backend_status.available,
                initialized=backend_status.initialized,
                backend=backend_status.backend,
                error=f"{operation}: {error}",
            )
        )

    def _refresh_status(
        self,
        status: ViewportStatus,
        *,
        emit_status: bool = True,
    ) -> None:
        self._status = status
        if status.available and status.error is None:
            self._status_label.hide()
        else:
            message_source = status.error or "CAD rendering backend is unavailable."
            message = self._status_text_resolver(message_source)
            title_source = (
                "CAD VIEWER ERROR"
                if status.available
                else "CAD VIEWER UNAVAILABLE"
            )
            title = self._status_text_resolver(title_source)
            self._status_label.setText(f"{title}\n{message}")
            self._status_label.setAccessibleName(title)
            self._status_label.setAccessibleDescription(message)
            self._status_label.show()
            self._status_label.raise_()
        if emit_status:
            self.status_changed.emit(status)


def _mouse_button(button: Qt.MouseButton) -> MouseButton:
    return _QT_MOUSE_BUTTONS.get(button, MouseButton.NONE)


def _pressed_buttons(buttons: Qt.MouseButton) -> frozenset[MouseButton]:
    return frozenset(
        model_button
        for qt_button, model_button in _QT_MOUSE_BUTTONS.items()
        if buttons & qt_button
    )


def _keyboard_modifiers(modifiers: Qt.KeyboardModifier) -> KeyboardModifier:
    result = KeyboardModifier.NONE
    if modifiers & Qt.KeyboardModifier.ShiftModifier:
        result |= KeyboardModifier.SHIFT
    if modifiers & Qt.KeyboardModifier.ControlModifier:
        result |= KeyboardModifier.CONTROL
    if modifiers & Qt.KeyboardModifier.AltModifier:
        result |= KeyboardModifier.ALT
    return result
