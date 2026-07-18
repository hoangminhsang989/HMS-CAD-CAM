"""Open CASCADE implementation of the product viewport backend."""

from __future__ import annotations

import logging

from OCP.V3d import V3d_TypeOfOrientation

from hms_cadcam.cad.kernel import CadKernel
from hms_cadcam.cad.models import CadDocumentId, CadGeometryKind
from hms_cadcam.cad.ocp import OcpCadKernel
from hms_cadcam.viewer.backend import SelectionCallback
from hms_cadcam.viewer.models import (
    DisplayMode,
    KeyboardModifier,
    MouseButton,
    SelectionMetadata,
    SelectionMode,
    ViewDirection,
    ViewportStatus,
)
from hms_cadcam.viewer.ocp.input_controller import OcpInputController
from hms_cadcam.viewer.ocp.lifecycle import OcpViewportLifecycle
from hms_cadcam.viewer.ocp.selection import OcpSelectionController

_VIEW_DIRECTIONS = {
    ViewDirection.TOP: V3d_TypeOfOrientation.V3d_TypeOfOrientation_Zup_Top,
    ViewDirection.BOTTOM: V3d_TypeOfOrientation.V3d_TypeOfOrientation_Zup_Bottom,
    ViewDirection.FRONT: V3d_TypeOfOrientation.V3d_TypeOfOrientation_Zup_Front,
    ViewDirection.BACK: V3d_TypeOfOrientation.V3d_TypeOfOrientation_Zup_Back,
    ViewDirection.LEFT: V3d_TypeOfOrientation.V3d_TypeOfOrientation_Zup_Left,
    ViewDirection.RIGHT: V3d_TypeOfOrientation.V3d_TypeOfOrientation_Zup_Right,
    ViewDirection.ISOMETRIC: (
        V3d_TypeOfOrientation.V3d_TypeOfOrientation_Zup_AxoRight
    ),
}


class OcpCadViewportBackend:
    """Render OCP kernel documents without exposing native objects to Qt UI."""

    def __init__(self, kernel: CadKernel) -> None:
        if not isinstance(kernel, OcpCadKernel):
            raise TypeError("OCP viewport requires OcpCadKernel")
        self._kernel = kernel
        self._lifecycle = OcpViewportLifecycle()
        self._selection: OcpSelectionController | None = None
        self._input: OcpInputController | None = None
        self._selection_callback: SelectionCallback = lambda _items: None
        self._document_id: CadDocumentId | None = None
        self._display_mode = DisplayMode.SHADED_WITH_EDGES
        self._selection_mode = SelectionMode.SOLID
        self._view_direction = ViewDirection.ISOMETRIC
        self._pending_size = (0, 0)
        self._closed = False

    def get_status(self) -> ViewportStatus:
        return ViewportStatus(
            available=True,
            initialized=self._lifecycle.initialized,
            backend="OCP",
        )

    def set_selection_callback(self, callback: SelectionCallback) -> None:
        self._selection_callback = callback

    def initialize(self, native_window_id: int) -> None:
        if self._lifecycle.initialized:
            return
        if self._closed:
            raise RuntimeError("OCP viewport backend is already closed")
        self._lifecycle.initialize(native_window_id)
        self._selection = OcpSelectionController(self._lifecycle.context)
        self._selection.set_mode(self._selection_mode)
        self._input = OcpInputController(self._lifecycle.view, self._selection)
        self.set_view_direction(self._view_direction)
        self.resize(*self._pending_size)

    def display_document(self, document_id: CadDocumentId) -> None:
        self._require_initialized()
        old_document_id = self._document_id
        old_presentation = self._lifecycle.presentation
        metadata = self._kernel.get_document_metadata(document_id)
        shape = None
        if metadata.geometry_kind is CadGeometryKind.BREP:
            shape = self._kernel._resolve_shape(document_id)
            presentation = self._lifecycle.prepare_shape(shape, self._display_mode)
        else:
            triangulation = self._kernel._resolve_triangulation(document_id)
            presentation = self._lifecycle.prepare_triangulation(
                triangulation,
                self._display_mode,
            )
        if self._input is not None:
            self._input.reset()
        try:
            selection = self._require_selection()
            if shape is None:
                selection.clear_document()
            else:
                selection.bind_document(document_id, shape, presentation)
                selection.set_mode(self._selection_mode)
            self._lifecycle.commit_presentation(presentation)
        except Exception:
            self._lifecycle.discard_presentation(presentation)
            self._restore_selection(old_document_id, old_presentation)
            self._emit_selection(())
            raise
        self._document_id = document_id
        self._emit_selection(())
        self.fit_all()

    def clear(self) -> None:
        if self._selection is not None:
            self._selection.clear_document()
        if self._input is not None:
            self._input.reset()
        self._lifecycle.clear()
        self._document_id = None
        self._emit_selection(())

    def fit_all(self) -> None:
        self._lifecycle.fit_all()

    def set_view_direction(self, direction: ViewDirection) -> None:
        self._view_direction = direction
        if self._lifecycle.initialized:
            self._lifecycle.view.SetProj(_VIEW_DIRECTIONS[direction])
            self.fit_all()

    def set_display_mode(self, mode: DisplayMode) -> None:
        self._display_mode = mode
        presentation = self._lifecycle.presentation
        if presentation is not None:
            self._lifecycle.apply_display_mode(presentation, mode)

    def set_selection_mode(self, mode: SelectionMode) -> None:
        self._selection_mode = mode
        if self._selection is not None:
            self._selection.set_mode(mode)
            self._emit_selection(())

    def resize(self, width: int, height: int) -> None:
        if width < 0 or height < 0:
            raise ValueError("Viewport size must not be negative")
        self._pending_size = (width, height)
        self._lifecycle.resize(width, height)

    def handle_mouse_press(
        self,
        x: int,
        y: int,
        button: MouseButton,
        modifiers: KeyboardModifier,
    ) -> None:
        if self._input is not None:
            self._input.press(x, y, button, modifiers)

    def handle_mouse_move(
        self,
        x: int,
        y: int,
        buttons: frozenset[MouseButton],
        modifiers: KeyboardModifier,
    ) -> None:
        if self._input is not None:
            self._input.move(x, y, buttons, modifiers)

    def handle_mouse_release(
        self,
        x: int,
        y: int,
        button: MouseButton,
        modifiers: KeyboardModifier,
    ) -> None:
        if self._input is None:
            return
        selection = self._input.release(x, y, button, modifiers)
        if selection is not None:
            self._emit_selection(selection)

    def handle_wheel(
        self,
        x: int,
        y: int,
        delta: int,
        modifiers: KeyboardModifier,
    ) -> None:
        del x, y
        if self._input is not None:
            self._input.wheel(delta, modifiers)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._selection is not None:
            self._selection.clear_document()
        if self._input is not None:
            self._input.reset()
        self._selection_callback = lambda _items: None
        self._input = None
        self._selection = None
        self._document_id = None
        self._lifecycle.close()

    def _require_initialized(self) -> None:
        if not self._lifecycle.initialized:
            raise RuntimeError("OCP viewport is not initialized")

    def _require_selection(self) -> OcpSelectionController:
        if self._selection is None:
            raise RuntimeError("OCP selection is not initialized")
        return self._selection

    def _restore_selection(self, document_id, presentation) -> None:
        selection = self._require_selection()
        if document_id is None or presentation is None:
            selection.clear_document()
            return
        metadata = self._kernel.get_document_metadata(document_id)
        if metadata.geometry_kind is CadGeometryKind.BREP:
            shape = self._kernel._resolve_shape(document_id)
            selection.bind_document(document_id, shape, presentation)
            selection.set_mode(self._selection_mode)
        else:
            selection.clear_document()

    def _emit_selection(self, items: tuple[SelectionMetadata, ...]) -> None:
        if self._closed:
            return
        try:
            self._selection_callback(items)
        except Exception:
            logging.getLogger(__name__).exception(
                "CAD viewport selection callback failed"
            )
