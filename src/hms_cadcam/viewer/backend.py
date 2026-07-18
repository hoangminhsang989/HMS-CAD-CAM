"""Toolkit- and OCP-independent CAD viewport backend protocol."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, runtime_checkable

from hms_cadcam.cad.models import CadDocumentId
from hms_cadcam.viewer.models import (
    DisplayMode,
    KeyboardModifier,
    MouseButton,
    SelectionMetadata,
    SelectionMode,
    ViewDirection,
    ViewportStatus,
)

SelectionCallback = Callable[[tuple[SelectionMetadata, ...]], None]


@runtime_checkable
class CadViewportBackend(Protocol):
    """Rendering API consumed by the product Qt viewport widget."""

    def get_status(self) -> ViewportStatus: ...

    def set_selection_callback(self, callback: SelectionCallback) -> None: ...

    def initialize(self, native_window_id: int) -> None: ...

    def display_document(self, document_id: CadDocumentId) -> None: ...

    def clear(self) -> None: ...

    def fit_all(self) -> None: ...

    def set_view_direction(self, direction: ViewDirection) -> None: ...

    def set_display_mode(self, mode: DisplayMode) -> None: ...

    def set_selection_mode(self, mode: SelectionMode) -> None: ...

    def resize(self, width: int, height: int) -> None: ...

    def handle_mouse_press(
        self,
        x: int,
        y: int,
        button: MouseButton,
        modifiers: KeyboardModifier,
    ) -> None: ...

    def handle_mouse_move(
        self,
        x: int,
        y: int,
        buttons: frozenset[MouseButton],
        modifiers: KeyboardModifier,
    ) -> None: ...

    def handle_mouse_release(
        self,
        x: int,
        y: int,
        button: MouseButton,
        modifiers: KeyboardModifier,
    ) -> None: ...

    def handle_wheel(
        self,
        x: int,
        y: int,
        delta: int,
        modifiers: KeyboardModifier,
    ) -> None: ...

    def close(self) -> None: ...
