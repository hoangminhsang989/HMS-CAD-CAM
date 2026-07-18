"""Product CadViewportWidget and backend tests for Stage 4D."""

from __future__ import annotations

import os
from dataclasses import fields, is_dataclass
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from OCP.BRepMesh import BRepMesh_IncrementalMesh  # noqa: E402
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox  # noqa: E402
from OCP.StlAPI import StlAPI_Writer  # noqa: E402
from OCP.TopAbs import TopAbs_ShapeEnum  # noqa: E402
from OCP.TopExp import TopExp  # noqa: E402
from OCP.TopTools import TopTools_IndexedMapOfShape  # noqa: E402
from OCP.TopoDS import TopoDS_Shape  # noqa: E402
from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from hms_cadcam.cad.models import BoundingBox, CadDocumentId  # noqa: E402
from hms_cadcam.cad.ocp import OcpCadKernel  # noqa: E402
from hms_cadcam.viewer.backend import CadViewportBackend  # noqa: E402
from hms_cadcam.viewer import backend as backend_module  # noqa: E402
from hms_cadcam.viewer import models as models_module  # noqa: E402
from hms_cadcam.viewer.factory import CadViewportBackendFactory  # noqa: E402
from hms_cadcam.viewer.models import (  # noqa: E402
    DisplayMode,
    KeyboardModifier,
    MouseButton,
    SelectionMetadata,
    SelectionMode,
    ViewDirection,
    ViewportStatus,
)
from hms_cadcam.viewer.ocp.backend import (  # noqa: E402
    OcpCadViewportBackend,
    _VIEW_DIRECTIONS,
)
from hms_cadcam.viewer.ocp.lifecycle import OcpViewportLifecycle  # noqa: E402
from hms_cadcam.viewer.ocp.selection import OcpSelectionController  # noqa: E402
from hms_cadcam.viewer.unavailable_backend import (  # noqa: E402
    UnavailableCadViewportBackend,
)
from hms_cadcam.viewer.widget import CadViewportWidget  # noqa: E402


class MockViewportBackend:
    """Offscreen-safe backend recording the widget/backend contract."""

    def __init__(self, fail_operation: str | None = None) -> None:
        self.fail_operation = fail_operation
        self.callback = lambda _items: None
        self.initialize_count = 0
        self.close_count = 0
        self.resize_calls: list[tuple[int, int]] = []
        self.displayed_document: CadDocumentId | None = None
        self.display_history: list[CadDocumentId] = []
        self.clear_count = 0
        self.closed = False

    def get_status(self) -> ViewportStatus:
        return ViewportStatus(True, self.initialize_count > 0, "mock")

    def set_selection_callback(self, callback) -> None:
        self.callback = callback

    def initialize(self, native_window_id: int) -> None:
        assert native_window_id > 0
        if self.initialize_count == 0:
            self.initialize_count = 1

    def display_document(self, document_id: CadDocumentId) -> None:
        if self.fail_operation == "display":
            raise RuntimeError("simulated display failure")
        self.displayed_document = document_id
        self.display_history.append(document_id)

    def clear(self) -> None:
        self.displayed_document = None
        self.clear_count += 1
        self.callback(())

    def fit_all(self) -> None:
        return None

    def set_view_direction(self, direction: ViewDirection) -> None:
        del direction

    def set_display_mode(self, mode: DisplayMode) -> None:
        del mode

    def set_selection_mode(self, mode: SelectionMode) -> None:
        del mode

    def resize(self, width: int, height: int) -> None:
        self.resize_calls.append((width, height))

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
        if not self.closed:
            self.closed = True
            self.close_count += 1

    def emit_selection(self, items: tuple[SelectionMetadata, ...]) -> None:
        self.callback(items)


class FakeView:
    def __init__(self) -> None:
        self.projection = None

    def SetProj(self, projection) -> None:  # noqa: N802 - OCP-compatible fake
        self.projection = projection


class FakeLifecycle:
    def __init__(self) -> None:
        self.initialized = True
        self.view = FakeView()
        self.presentation = None
        self.replaced = 0
        self.removed = 0
        self.display_modes: list[DisplayMode] = []
        self.resize_calls: list[tuple[int, int]] = []
        self.fit_count = 0
        self.closed = False

    def replace_shape(self, shape, mode: DisplayMode):
        assert not shape.IsNull()
        if self.presentation is not None:
            self.removed += 1
        self.presentation = object()
        self.replaced += 1
        self.display_modes.append(mode)
        return self.presentation

    def replace_triangulation(self, triangulation, mode: DisplayMode):
        assert triangulation.NbNodes() > 0
        assert triangulation.NbTriangles() > 0
        if self.presentation is not None:
            self.removed += 1
        self.presentation = object()
        self.replaced += 1
        self.display_modes.append(mode)
        return self.presentation

    def apply_display_mode(self, presentation, mode: DisplayMode) -> None:
        assert presentation is self.presentation
        self.display_modes.append(mode)

    def clear(self) -> None:
        if self.presentation is not None:
            self.removed += 1
        self.presentation = None

    def fit_all(self) -> None:
        self.fit_count += 1

    def resize(self, width: int, height: int) -> None:
        self.resize_calls.append((width, height))

    def close(self) -> None:
        self.closed = True
        self.presentation = None


class FakeSelection:
    def __init__(self) -> None:
        self.document_id = None
        self.mode = SelectionMode.SOLID
        self.clear_count = 0

    def bind_document(self, document_id, shape, presentation) -> None:
        assert not shape.IsNull()
        assert presentation is not None
        self.document_id = document_id

    def set_mode(self, mode: SelectionMode) -> None:
        self.mode = mode

    def clear_document(self) -> None:
        self.document_id = None
        self.clear_count += 1


class FakeInput:
    def __init__(self) -> None:
        self.reset_count = 0

    def reset(self) -> None:
        self.reset_count += 1


class FakeSelectionContext:
    def __init__(self) -> None:
        self.selected_shape = TopoDS_Shape()
        self.has_current = False

    def ClearSelected(self, update: bool) -> None:  # noqa: N802
        del update
        self.has_current = False

    def Deactivate(self, presentation) -> None:  # noqa: N802
        del presentation

    def Activate(self, presentation, mode: int) -> None:  # noqa: N802
        del presentation, mode

    def InitSelected(self) -> None:  # noqa: N802
        return None

    def MoreSelected(self) -> bool:  # noqa: N802
        return self.has_current

    def SelectedShape(self):  # noqa: N802
        return self.selected_shape

    def NextSelected(self) -> None:  # noqa: N802
        self.has_current = False


def _assert_no_topods(value: object) -> None:
    assert not isinstance(value, TopoDS_Shape)
    if is_dataclass(value) and not isinstance(value, type):
        for field in fields(value):
            _assert_no_topods(getattr(value, field.name))
    elif isinstance(value, (tuple, list, set, frozenset, dict)):
        values = value.values() if isinstance(value, dict) else value
        for item in values:
            _assert_no_topods(item)


def _application() -> QApplication:
    return QApplication.instance() or QApplication([])


def _write_stl(path: Path) -> None:
    shape = BRepPrimAPI_MakeBox(4.0, 5.0, 6.0).Shape()
    BRepMesh_IncrementalMesh(shape, 0.1)
    writer = StlAPI_Writer()
    writer.ASCIIMode = True
    assert writer.Write(shape, str(path))


def test_viewer_models_and_protocol_are_ocp_free() -> None:
    metadata = SelectionMetadata(
        document_id=CadDocumentId("doc:1"),
        selection_id="doc:1:face:1",
        topology=SelectionMode.FACE,
        bounding_box=BoundingBox(0.0, 0.0, 0.0, 1.0, 2.0, 3.0),
    )
    _assert_no_topods(metadata)
    assert "OCP" not in type(metadata).__module__
    for module in (models_module, backend_module):
        for value in vars(module).values():
            origin = getattr(value, "__module__", "")
            assert not origin.startswith(("OCP", "PySide6"))


def test_factory_creates_real_backend_when_ocp_is_available() -> None:
    backend = CadViewportBackendFactory.create(OcpCadKernel())
    assert isinstance(backend, OcpCadViewportBackend)
    assert isinstance(backend, CadViewportBackend)
    assert backend.get_status().available


def test_factory_falls_back_when_ocp_backend_import_fails(monkeypatch) -> None:
    def fail_load():
        raise ImportError("simulated viewer DLL failure")

    monkeypatch.setattr(
        CadViewportBackendFactory,
        "_load_ocp_backend",
        staticmethod(fail_load),
    )
    backend = CadViewportBackendFactory.create(OcpCadKernel())
    assert isinstance(backend, UnavailableCadViewportBackend)
    assert "simulated viewer DLL failure" in (backend.get_status().error or "")
    backend.initialize(1)
    backend.resize(640, 480)
    backend.set_view_direction(ViewDirection.ISOMETRIC)
    backend.set_display_mode(DisplayMode.SHADED)
    backend.set_selection_mode(SelectionMode.SOLID)
    backend.clear()
    with pytest.raises(RuntimeError, match="unavailable"):
        backend.display_document(CadDocumentId("missing"))
    backend.close()
    backend.close()


def test_widget_initializes_once_resizes_and_closes_safely() -> None:
    application = _application()
    backend = MockViewportBackend()
    widget = CadViewportWidget(OcpCadKernel(), backend)
    widget.resize(720, 480)
    application.processEvents()
    assert backend.initialize_count == 0
    widget.show()
    application.processEvents()
    widget.initialize_viewport()
    assert backend.initialize_count == 1
    assert backend.resize_calls
    widget.resize(900, 600)
    application.processEvents()
    assert (900, 600) in backend.resize_calls
    widget.shutdown()
    widget.shutdown()
    assert backend.close_count == 1


def test_widget_displays_replaces_clears_and_ignores_signal_after_close() -> None:
    application = _application()
    kernel = OcpCadKernel()
    first = kernel.create_box(1.0, 2.0, 3.0)
    second = kernel.create_box(4.0, 5.0, 6.0)
    backend = MockViewportBackend()
    widget = CadViewportWidget(kernel, backend)
    received: list[tuple[SelectionMetadata, ...]] = []
    widget.selection_changed.connect(received.append)
    widget.display_document(first)
    widget.display_document(second)
    assert backend.display_history == [first, second]
    assert backend.displayed_document == second
    item = SelectionMetadata(
        second,
        f"{second}:solid:1",
        SelectionMode.SOLID,
        BoundingBox(0.0, 0.0, 0.0, 4.0, 5.0, 6.0),
    )
    backend.emit_selection((item,))
    assert received == [(item,)]
    widget.clear()
    assert backend.displayed_document is None
    assert received[-1] == ()
    widget.shutdown()
    QTimer.singleShot(0, lambda: backend.emit_selection((item,)))
    application.processEvents()
    assert received[-1] == ()


def test_backend_error_updates_status_without_crashing_widget() -> None:
    application = _application()
    backend = MockViewportBackend(fail_operation="display")
    kernel = OcpCadKernel()
    widget = CadViewportWidget(kernel, backend)
    widget.show()
    application.processEvents()
    document_id = kernel.create_box(1.0, 1.0, 1.0)
    widget.display_document(document_id)
    application.processEvents()
    assert widget.viewport_status.available
    assert "simulated display failure" in (widget.viewport_status.error or "")
    backend.fail_operation = None
    widget.display_document(document_id)
    assert widget.viewport_status.error is None
    assert backend.displayed_document == document_id
    widget.shutdown()


def test_ocp_backend_replaces_documents_preserves_old_on_lookup_error_and_clears() -> None:
    kernel = OcpCadKernel()
    first = kernel.create_box(1.0, 2.0, 3.0)
    second = kernel.create_box(4.0, 5.0, 6.0)
    backend = OcpCadViewportBackend(kernel)
    lifecycle = FakeLifecycle()
    selection = FakeSelection()
    input_controller = FakeInput()
    backend._lifecycle = lifecycle
    backend._selection = selection
    backend._input = input_controller

    backend.display_document(first)
    backend.display_document(second)
    assert lifecycle.replaced == 2
    assert lifecycle.removed == 1
    assert selection.document_id == second
    with pytest.raises(KeyError):
        backend.display_document(CadDocumentId("missing"))
    assert lifecycle.presentation is not None
    assert selection.document_id == second
    assert input_controller.reset_count == 2
    backend.display_document(first)
    assert lifecycle.replaced == 3
    assert selection.document_id == first
    assert input_controller.reset_count == 3
    backend.clear()
    assert lifecycle.presentation is None
    assert selection.document_id is None
    assert input_controller.reset_count == 4


def test_ocp_backend_switches_brep_mesh_and_brep_safely(tmp_path: Path) -> None:
    source = tmp_path / "box.stl"
    _write_stl(source)
    kernel = OcpCadKernel()
    brep = kernel.create_box(1.0, 2.0, 3.0)
    mesh_result = kernel.import_stl(source)
    assert mesh_result.document_id is not None
    backend = OcpCadViewportBackend(kernel)
    lifecycle = FakeLifecycle()
    selection = FakeSelection()
    input_controller = FakeInput()
    backend._lifecycle = lifecycle
    backend._selection = selection
    backend._input = input_controller

    backend.display_document(brep)
    assert selection.document_id == brep
    backend.display_document(mesh_result.document_id)
    assert selection.document_id is None
    backend.display_document(brep)
    assert selection.document_id == brep
    assert lifecycle.replaced == 3
    assert lifecycle.removed == 2
    assert input_controller.reset_count == 3


def test_ocp_backend_camera_display_selection_and_resize_mapping() -> None:
    backend = OcpCadViewportBackend(OcpCadKernel())
    lifecycle = FakeLifecycle()
    selection = FakeSelection()
    backend._lifecycle = lifecycle
    backend._selection = selection
    backend.resize(640, 480)
    for direction in ViewDirection:
        backend.set_view_direction(direction)
        assert lifecycle.view.projection == _VIEW_DIRECTIONS[direction]
    for mode in DisplayMode:
        lifecycle.presentation = object()
        backend.set_display_mode(mode)
        assert lifecycle.display_modes[-1] is mode
    for mode in SelectionMode:
        backend.set_selection_mode(mode)
        assert selection.mode is mode
    assert lifecycle.resize_calls[-1] == (640, 480)


@pytest.mark.parametrize(
    ("mode", "shape_type"),
    (
        (SelectionMode.SOLID, TopAbs_ShapeEnum.TopAbs_SOLID),
        (SelectionMode.FACE, TopAbs_ShapeEnum.TopAbs_FACE),
        (SelectionMode.EDGE, TopAbs_ShapeEnum.TopAbs_EDGE),
    ),
)
def test_selection_metadata_is_stable_and_contains_no_topods(
    mode: SelectionMode,
    shape_type: TopAbs_ShapeEnum,
) -> None:
    document_shape = BRepPrimAPI_MakeBox(4.0, 5.0, 6.0).Shape()
    shapes = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(document_shape, shape_type, shapes)
    context = FakeSelectionContext()
    controller = OcpSelectionController(context)
    document_id = CadDocumentId("ocp:test-document")
    controller.bind_document(document_id, document_shape, object())
    controller.set_mode(mode)
    context.selected_shape = shapes.FindKey(1)
    context.has_current = True
    first = controller.current_metadata()
    context.has_current = True
    second = controller.current_metadata()
    assert first == second
    assert len(first) == 1
    assert first[0].topology is mode
    assert first[0].document_id == document_id
    _assert_no_topods(first)
    controller.clear_document()
    assert controller.current_metadata() == ()


def test_ocp_lifecycle_close_is_idempotent_before_initialize() -> None:
    lifecycle = OcpViewportLifecycle()
    lifecycle.resize(320, 240)
    lifecycle.close()
    lifecycle.close()
    assert not lifecycle.initialized
