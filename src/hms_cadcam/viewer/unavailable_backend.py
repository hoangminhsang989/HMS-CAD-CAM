"""Controlled viewport fallback used when OCP rendering cannot load."""

from __future__ import annotations

from hms_cadcam.cad.models import CadDocumentId
from hms_cadcam.viewer.backend import SelectionCallback
from hms_cadcam.viewer.models import (
    DisplayMode,
    KeyboardModifier,
    MouseButton,
    SelectionMode,
    ViewDirection,
    ViewportStatus,
)


class UnavailableCadViewportBackend:
    """Keep QWidget lifecycle safe while clearly reporting renderer failure."""

    def __init__(self, error: BaseException | str) -> None:
        self._error = str(error) or type(error).__name__
        self._initialized = False
        self._closed = False
        self._selection_callback: SelectionCallback = lambda _items: None

    def get_status(self) -> ViewportStatus:
        return ViewportStatus(
            available=False,
            initialized=self._initialized and not self._closed,
            backend="unavailable",
            error=self._error,
        )

    def set_selection_callback(self, callback: SelectionCallback) -> None:
        self._selection_callback = callback

    def initialize(self, native_window_id: int) -> None:
        del native_window_id
        if not self._closed:
            self._initialized = True

    def display_document(self, document_id: CadDocumentId) -> None:
        del document_id
        raise RuntimeError(f"CAD viewport is unavailable: {self._error}")

    def clear(self) -> None:
        if not self._closed:
            self._selection_callback(())

    def fit_all(self) -> None:
        return None

    def set_view_direction(self, direction: ViewDirection) -> None:
        del direction

    def set_display_mode(self, mode: DisplayMode) -> None:
        del mode

    def set_selection_mode(self, mode: SelectionMode) -> None:
        del mode

    def resize(self, width: int, height: int) -> None:
        del width, height

    def handle_mouse_press(
        self,
        x: int,
        y: int,
        button: MouseButton,
        modifiers: KeyboardModifier,
    ) -> None:
        del x, y, button, modifiers

    def handle_mouse_move(
        self,
        x: int,
        y: int,
        buttons: frozenset[MouseButton],
        modifiers: KeyboardModifier,
    ) -> None:
        del x, y, buttons, modifiers

    def handle_mouse_release(
        self,
        x: int,
        y: int,
        button: MouseButton,
        modifiers: KeyboardModifier,
    ) -> None:
        del x, y, button, modifiers

    def handle_wheel(
        self,
        x: int,
        y: int,
        delta: int,
        modifiers: KeyboardModifier,
    ) -> None:
        del x, y, delta, modifiers

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._selection_callback = lambda _items: None
