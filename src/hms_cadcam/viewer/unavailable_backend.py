"""Controlled viewport fallback used when OCP rendering cannot load."""

from __future__ import annotations

from uuid import UUID

from hms_cadcam.cad.models import CadDocumentId, CadObjectId
from hms_cadcam.cam.domain import OperationId
from hms_cadcam.cam.domain.ids import SimulationResultId
from hms_cadcam.cam.domain.spatial import WcsFrame
from hms_cadcam.cam.simulation.model import SimulationResult
from hms_cadcam.cam.toolpath import ToolpathArtifact
from hms_cadcam.viewer.backend import SelectionCallback
from hms_cadcam.viewer.models import (
    DisplayMode,
    KeyboardModifier,
    MouseButton,
    ObjectColor,
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

    def display_toolpath(self, artifact: ToolpathArtifact) -> None:
        del artifact

    def get_toolpath_presentations(self) -> tuple[ToolpathPresentation, ...]:
        return ()

    def clear_toolpaths(self) -> None:
        return None

    def remove_toolpath(self, operation_id: OperationId) -> None:
        del operation_id

    def set_toolpath_visibility(self, operation_id: OperationId, visible: bool) -> None:
        del operation_id, visible

    def bind_simulation_project(
        self,
        project_id: UUID | None,
        generation: int | None,
    ) -> None:
        del project_id, generation

    def request_simulation_display(
        self,
        operation_id: OperationId,
        *,
        generation: int,
    ) -> SimulationDisplayRequest | None:
        del operation_id, generation
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
        del result, artifact, wcs, context, request, policy
        return False

    def get_simulation_presentations(self) -> tuple[SimulationPresentation, ...]:
        return ()

    def set_simulation_visibility(
        self,
        operation_id: OperationId,
        visible: bool,
    ) -> None:
        del operation_id, visible

    def lookup_simulation_issue(
        self,
        *,
        project_id: UUID,
        operation_id: OperationId,
        result_id: SimulationResultId,
        marker_id: str,
    ) -> SimulationIssueMarker | None:
        del project_id, operation_id, result_id, marker_id
        return None

    def remove_simulation(self, operation_id: OperationId) -> None:
        del operation_id

    def clear_simulations(self) -> None:
        return None

    def fit_all(self) -> None:
        return None

    def set_view_direction(self, direction: ViewDirection) -> None:
        del direction

    def set_display_mode(self, mode: DisplayMode) -> None:
        del mode

    def set_selection_mode(self, mode: SelectionMode) -> None:
        del mode

    def select_objects(
        self,
        document_id: CadDocumentId,
        object_ids: tuple[CadObjectId, ...],
    ) -> None:
        del document_id, object_ids

    def set_object_visibility(
        self,
        document_id: CadDocumentId,
        object_id: CadObjectId,
        visible: bool,
    ) -> None:
        del document_id, object_id, visible

    def isolate_object(
        self,
        document_id: CadDocumentId,
        object_id: CadObjectId,
    ) -> None:
        del document_id, object_id

    def reset_isolate(self, document_id: CadDocumentId) -> None:
        del document_id

    def set_object_color(
        self,
        document_id: CadDocumentId,
        object_id: CadObjectId,
        color: ObjectColor,
    ) -> None:
        del document_id, object_id, color

    def set_object_transparency(
        self,
        document_id: CadDocumentId,
        object_id: CadObjectId,
        transparency: float,
    ) -> None:
        del document_id, object_id, transparency

    def reset_object_appearance(
        self,
        document_id: CadDocumentId,
        object_id: CadObjectId,
    ) -> None:
        del document_id, object_id

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
