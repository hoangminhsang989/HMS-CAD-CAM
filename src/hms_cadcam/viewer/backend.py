"""Toolkit- and OCP-independent CAD viewport backend protocol."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, runtime_checkable

from hms_cadcam.cad.models import CadDocumentId, CadObjectId
from hms_cadcam.cam.domain import OperationId
from hms_cadcam.cam.toolpath import ToolpathArtifact
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

SelectionCallback = Callable[[tuple[SelectionMetadata, ...]], None]


@runtime_checkable
class CadViewportBackend(Protocol):
    """Rendering API consumed by the product Qt viewport widget."""

    def get_status(self) -> ViewportStatus: ...

    def set_selection_callback(self, callback: SelectionCallback) -> None: ...

    def initialize(self, native_window_id: int) -> None: ...

    def display_document(self, document_id: CadDocumentId) -> None: ...

    def clear(self) -> None: ...

    def display_toolpath(self, artifact: ToolpathArtifact) -> None: ...

    def get_toolpath_presentations(self) -> tuple[ToolpathPresentation, ...]: ...

    def clear_toolpaths(self) -> None: ...

    def remove_toolpath(self, operation_id: OperationId) -> None: ...

    def set_toolpath_visibility(self, operation_id: OperationId, visible: bool) -> None: ...

    def fit_all(self) -> None: ...

    def set_view_direction(self, direction: ViewDirection) -> None: ...

    def set_display_mode(self, mode: DisplayMode) -> None: ...

    def set_selection_mode(self, mode: SelectionMode) -> None: ...

    def select_objects(
        self,
        document_id: CadDocumentId,
        object_ids: tuple[CadObjectId, ...],
    ) -> None: ...

    def set_object_visibility(
        self,
        document_id: CadDocumentId,
        object_id: CadObjectId,
        visible: bool,
    ) -> None: ...

    def isolate_object(
        self,
        document_id: CadDocumentId,
        object_id: CadObjectId,
    ) -> None: ...

    def reset_isolate(self, document_id: CadDocumentId) -> None: ...

    def set_object_color(
        self,
        document_id: CadDocumentId,
        object_id: CadObjectId,
        color: ObjectColor,
    ) -> None: ...

    def set_object_transparency(
        self,
        document_id: CadDocumentId,
        object_id: CadObjectId,
        transparency: float,
    ) -> None: ...

    def reset_object_appearance(
        self,
        document_id: CadDocumentId,
        object_id: CadObjectId,
    ) -> None: ...

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
