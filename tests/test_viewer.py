"""Product CadViewportWidget and backend tests for Stage 4D."""

from __future__ import annotations

import os
from dataclasses import fields, is_dataclass
from pathlib import Path

import pytest

from OCP.BRep import BRep_Builder  # noqa: E402
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from OCP.BRepMesh import BRepMesh_IncrementalMesh  # noqa: E402
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox  # noqa: E402
from OCP.StlAPI import StlAPI_Writer  # noqa: E402
from OCP.TopAbs import TopAbs_ShapeEnum  # noqa: E402
from OCP.TopExp import TopExp  # noqa: E402
from OCP.TopTools import TopTools_IndexedMapOfShape  # noqa: E402
from OCP.TopoDS import TopoDS_Compound, TopoDS_Shape  # noqa: E402
from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from hms_cadcam.cad.models import (  # noqa: E402
    BoundingBox,
    CadDocumentId,
    CadDocumentTree,
    CadFormat,
    CadObjectId,
    CadObjectKind,
    CadObjectNode,
)
from hms_cadcam.cad.ocp import OcpCadKernel  # noqa: E402
from hms_cadcam.viewer.backend import CadViewportBackend  # noqa: E402
from hms_cadcam.viewer import backend as backend_module  # noqa: E402
from hms_cadcam.viewer import models as models_module  # noqa: E402
from hms_cadcam.viewer.factory import CadViewportBackendFactory  # noqa: E402
from hms_cadcam.viewer.models import (  # noqa: E402
    DisplayMode,
    DEFAULT_OBJECT_COLOR,
    KeyboardModifier,
    MouseButton,
    ObjectAppearance,
    ObjectColor,
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
from hms_cadcam.viewer.ocp.registry import OcpPresentationRegistry  # noqa: E402
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
        self.redraw_count = 0

    def SetProj(self, projection) -> None:  # noqa: N802 - OCP-compatible fake
        self.projection = projection

    def Redraw(self) -> None:  # noqa: N802 - OCP-compatible fake
        self.redraw_count += 1


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
        candidate = self.prepare_shape(shape, mode)
        self.commit_presentation(candidate)
        return candidate

    def prepare_shape(self, shape, mode: DisplayMode):
        assert not shape.IsNull()
        candidate = object()
        self.display_modes.append(mode)
        return candidate

    def replace_triangulation(self, triangulation, mode: DisplayMode):
        candidate = self.prepare_triangulation(triangulation, mode)
        self.commit_presentation(candidate)
        return candidate

    def prepare_triangulation(self, triangulation, mode: DisplayMode):
        assert triangulation.NbNodes() > 0
        assert triangulation.NbTriangles() > 0
        candidate = object()
        self.display_modes.append(mode)
        return candidate

    def commit_presentation(self, candidate) -> None:
        if self.presentation is not None:
            self.removed += 1
        self.presentation = candidate
        self.replaced += 1

    def discard_presentation(self, candidate) -> None:
        assert candidate is not self.presentation

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


class FailOnceSelection(FakeSelection):
    def __init__(self) -> None:
        super().__init__()
        self.fail_next_bind = False

    def bind_document(self, document_id, shape, presentation) -> None:
        if self.fail_next_bind:
            self.fail_next_bind = False
            raise RuntimeError("simulated selection bind failure")
        super().bind_document(document_id, shape, presentation)


class FakeManagedSelection(FakeSelection):
    def __init__(self) -> None:
        super().__init__()
        self.fail_next_bind = False
        self.clear_selection_count = 0
        self.selected_object_ids: tuple[CadObjectId, ...] = ()

    def bind_document(
        self,
        document_id,
        shape,
        presentation,
        object_shapes=None,
        presentations=None,
    ) -> None:
        del object_shapes, presentations
        if self.fail_next_bind:
            self.fail_next_bind = False
            raise RuntimeError("simulated managed bind failure")
        super().bind_document(document_id, shape, presentation)

    def select_objects(self, object_ids: tuple[CadObjectId, ...]) -> None:
        self.selected_object_ids = object_ids

    def clear_selection(self) -> None:
        self.clear_selection_count += 1
        self.selected_object_ids = ()


class FakeManagedLifecycle(FakeLifecycle):
    def __init__(self) -> None:
        super().__init__()
        self.registry = None
        self.context = FakeRegistryContext()
        self.discard_count = 0
        self.fail_commit = False
        self.fail_discard = False

    @property
    def presentations(self):
        return self.registry.presentations if self.registry is not None else {}

    def prepare_registry(self, tree, shapes, mode, triangulation=None):
        del shapes, mode, triangulation
        candidates = {node.object_id: object() for node in tree.presentation_nodes}
        self.context.displayed.update(candidates.values())
        return OcpPresentationRegistry(self.context, tree, candidates)

    def commit_registry(self, registry) -> None:
        if self.fail_commit:
            self.fail_commit = False
            raise RuntimeError("simulated managed commit failure")
        self.registry = registry
        self.presentation = next(iter(registry.presentations.values()))

    def discard_registry(self, registry) -> None:
        assert registry is not self.registry
        self.discard_count += 1
        if self.fail_discard:
            self.fail_discard = False
            raise RuntimeError("simulated managed discard failure")
        for presentation in registry.presentations.values():
            self.context.displayed.discard(presentation)

    def clear(self) -> None:
        if self.registry is not None:
            self.registry.clear_isolate()
        self.registry = None
        super().clear()


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


class FakeRegistryContext:
    def __init__(self) -> None:
        self.displayed: set[object] = set()
        self.erased: set[object] = set()
        self.colors: list[tuple[object, object]] = []
        self.transparencies: list[tuple[object, float]] = []
        self.current_colors: dict[object, tuple[float, float, float]] = {}
        self.current_transparencies: dict[object, float] = {}
        self.removed: set[object] = set()
        self.update_count = 0
        self.fail_operation: str | None = None
        self.fail_on_call = 0
        self._operation_calls = 0

    def fail_once(self, operation: str, call_number: int = 2) -> None:
        self.fail_operation = operation
        self.fail_on_call = call_number
        self._operation_calls = 0

    def _maybe_fail(self, operation: str) -> None:
        if self.fail_operation != operation:
            return
        self._operation_calls += 1
        if self._operation_calls == self.fail_on_call:
            self.fail_operation = None
            raise RuntimeError(f"simulated {operation} failure")

    def Display(self, presentation, update: bool) -> None:  # noqa: N802
        del update
        self._maybe_fail("display")
        self.displayed.add(presentation)
        self.erased.discard(presentation)

    def Erase(self, presentation, update: bool) -> None:  # noqa: N802
        del update
        self._maybe_fail("erase")
        self.erased.add(presentation)
        self.displayed.discard(presentation)

    def SetColor(self, presentation, color, update: bool) -> None:  # noqa: N802
        del update
        self._maybe_fail("color")
        self.colors.append((presentation, color))
        self.current_colors[presentation] = (color.Red(), color.Green(), color.Blue())

    def SetTransparency(  # noqa: N802
        self,
        presentation,
        value: float,
        update: bool,
    ) -> None:
        del update
        self._maybe_fail("transparency")
        self.transparencies.append((presentation, value))
        self.current_transparencies[presentation] = value

    def UpdateCurrentViewer(self) -> None:  # noqa: N802
        self.update_count += 1

    def Remove(self, presentation, update: bool) -> None:  # noqa: N802
        del update
        self._maybe_fail("remove")
        self.removed.add(presentation)
        self.displayed.discard(presentation)


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


def test_object_appearance_validation_and_public_schema() -> None:
    color = ObjectColor(0.1, 0.2, 0.3)
    appearance = ObjectAppearance(True, color, 0.45)
    assert color.to_hex() == "#1A334C"
    assert appearance.transparency == pytest.approx(0.45)
    _assert_no_topods(appearance)
    with pytest.raises(ValueError):
        ObjectColor(-0.1, 0.0, 0.0)
    with pytest.raises(ValueError):
        ObjectColor(0.0, float("nan"), 0.0)
    with pytest.raises(ValueError):
        ObjectAppearance(transparency=1.01)
    with pytest.raises(TypeError):
        ObjectAppearance(visible=1)


def test_registry_hide_show_isolate_and_reset_restore_visibility() -> None:
    document_id = CadDocumentId("doc:registry")
    first_id = CadObjectId("doc:registry:object:1")
    second_id = CadObjectId("doc:registry:object:2")
    bounds = BoundingBox(0.0, 0.0, 0.0, 1.0, 1.0, 1.0)
    first = CadObjectNode(
        document_id,
        first_id,
        CadObjectKind.SOLID,
        "Solid 1",
        bounds,
        has_presentation=True,
    )
    second = CadObjectNode(
        document_id,
        second_id,
        CadObjectKind.SOLID,
        "Solid 2",
        bounds,
        has_presentation=True,
    )
    root = CadObjectNode(
        document_id,
        CadObjectId("doc:registry:document"),
        CadObjectKind.DOCUMENT,
        "CAD document",
        bounds,
        (first, second),
    )
    context = FakeRegistryContext()
    first_presentation = object()
    second_presentation = object()
    registry = OcpPresentationRegistry(
        context,
        CadDocumentTree(document_id, root),
        {first_id: first_presentation, second_id: second_presentation},
    )

    registry.set_visibility(second_id, False)
    registry.isolate(first_id)
    registry.isolate(second_id)
    assert registry.appearances[first_id].visible is False
    assert registry.appearances[second_id].visible is True
    isolated_color = ObjectColor(0.2, 0.4, 0.6)
    registry.set_color(first_id, isolated_color)
    registry.reset_isolate()
    assert registry.appearances[first_id].visible is True
    assert registry.appearances[second_id].visible is False
    assert registry.appearances[first_id].color == isolated_color
    registry.set_visibility(second_id, True)
    registry.set_color(root.object_id, isolated_color)
    registry.set_transparency(first_id, 0.5)
    assert registry.appearances[second_id].visible is True
    assert registry.appearances[first_id].transparency == pytest.approx(0.5)
    assert len(context.colors) == 3
    registry.set_visibility(root.object_id, False)
    assert not registry.appearances[root.object_id].visible
    assert not registry.appearances[first_id].visible
    assert not registry.appearances[second_id].visible
    registry.set_visibility(first_id, True)
    assert registry.appearances[root.object_id].visible
    assert registry.appearances[first_id].visible
    assert not registry.appearances[second_id].visible


@pytest.mark.parametrize(
    "operation",
    ("visibility", "color", "transparency", "isolate", "reset_isolate"),
)
def test_registry_apply_failure_restores_previous_appearance(operation: str) -> None:
    document_id = CadDocumentId(f"doc:rollback:{operation}")
    first_id = CadObjectId(f"{document_id}:object:1")
    second_id = CadObjectId(f"{document_id}:object:2")
    bounds = BoundingBox(0.0, 0.0, 0.0, 1.0, 1.0, 1.0)
    children = tuple(
        CadObjectNode(
            document_id,
            object_id,
            CadObjectKind.SOLID,
            f"Solid {index}",
            bounds,
            has_presentation=True,
        )
        for index, object_id in enumerate((first_id, second_id), start=1)
    )
    root = CadObjectNode(
        document_id,
        CadObjectId(f"{document_id}:document"),
        CadObjectKind.DOCUMENT,
        "CAD document",
        bounds,
        children,
    )
    presentations = {first_id: object(), second_id: object()}
    context = FakeRegistryContext()
    context.displayed.update(presentations.values())
    registry = OcpPresentationRegistry(
        context,
        CadDocumentTree(document_id, root),
        presentations,
    )
    original = dict(registry.appearances)

    if operation == "visibility":
        context.fail_once("erase")
        action = lambda: registry.set_visibility(root.object_id, False)
    elif operation == "color":
        context.fail_once("color")
        action = lambda: registry.set_color(root.object_id, ObjectColor(0.1, 0.2, 0.3))
    elif operation == "transparency":
        context.fail_once("transparency")
        action = lambda: registry.set_transparency(root.object_id, 0.6)
    elif operation == "isolate":
        registry.set_visibility(second_id, False)
        original = dict(registry.appearances)
        context.fail_once("display", 1)
        action = lambda: registry.isolate(second_id)
    else:
        registry.set_visibility(second_id, False)
        registry.isolate(second_id)
        original = dict(registry.appearances)
        context.fail_once("display", 1)
        action = registry.reset_isolate

    with pytest.raises(RuntimeError, match="simulated"):
        action()

    assert registry.appearances == original
    assert registry.isolate_active is (operation == "reset_isolate")
    for object_id, presentation in presentations.items():
        expected = original[object_id]
        assert (presentation in context.displayed) is expected.visible
        if operation == "color":
            assert context.current_colors[presentation] == pytest.approx(
                (
                    DEFAULT_OBJECT_COLOR.red,
                    DEFAULT_OBJECT_COLOR.green,
                    DEFAULT_OBJECT_COLOR.blue,
                )
            )
        if operation == "transparency":
            assert context.current_transparencies[presentation] == pytest.approx(0.0)


def test_lifecycle_commit_failure_restores_old_registry_and_isolate_state() -> None:
    bounds = BoundingBox(0.0, 0.0, 0.0, 1.0, 1.0, 1.0)

    def make_registry(
        context: FakeRegistryContext,
        name: str,
        count: int,
    ) -> OcpPresentationRegistry:
        document_id = CadDocumentId(f"doc:{name}")
        children = tuple(
            CadObjectNode(
                document_id,
                CadObjectId(f"{document_id}:object:{index}"),
                CadObjectKind.SOLID,
                f"Solid {index}",
                bounds,
                has_presentation=True,
            )
            for index in range(1, count + 1)
        )
        root = CadObjectNode(
            document_id,
            CadObjectId(f"{document_id}:document"),
            CadObjectKind.DOCUMENT,
            "CAD document",
            bounds,
            children,
        )
        presentations = {node.object_id: object() for node in children}
        context.displayed.update(presentations.values())
        return OcpPresentationRegistry(
            context,
            CadDocumentTree(document_id, root),
            presentations,
        )

    context = FakeRegistryContext()
    old_registry = make_registry(context, "old", 2)
    old_target = old_registry.tree.presentation_nodes[0].object_id
    old_registry.isolate(old_target)
    candidate = make_registry(context, "candidate", 1)
    lifecycle = OcpViewportLifecycle()
    lifecycle._context = context
    lifecycle._view = FakeView()
    lifecycle._registry = old_registry
    context.fail_once("remove", 2)

    with pytest.raises(RuntimeError, match="simulated remove"):
        lifecycle.commit_registry(candidate)

    assert lifecycle.registry is old_registry
    assert old_registry.isolate_active
    for object_id, presentation in old_registry.presentations.items():
        assert (presentation in context.displayed) is old_registry.appearances[
            object_id
        ].visible
    assert set(candidate.presentations.values()).issubset(context.displayed)


def test_lifecycle_discard_retries_and_removes_every_candidate() -> None:
    document_id = CadDocumentId("doc:discard")
    bounds = BoundingBox(0.0, 0.0, 0.0, 1.0, 1.0, 1.0)
    children = tuple(
        CadObjectNode(
            document_id,
            CadObjectId(f"{document_id}:object:{index}"),
            CadObjectKind.SOLID,
            f"Solid {index}",
            bounds,
            has_presentation=True,
        )
        for index in (1, 2)
    )
    root = CadObjectNode(
        document_id,
        CadObjectId(f"{document_id}:document"),
        CadObjectKind.DOCUMENT,
        "CAD document",
        bounds,
        children,
    )
    presentations = {node.object_id: object() for node in children}
    context = FakeRegistryContext()
    context.displayed.update(presentations.values())
    candidate = OcpPresentationRegistry(
        context,
        CadDocumentTree(document_id, root),
        presentations,
    )
    lifecycle = OcpViewportLifecycle()
    lifecycle._context = context
    lifecycle._view = FakeView()
    context.fail_once("remove", 2)

    lifecycle.discard_registry(candidate)

    assert not set(presentations.values()).intersection(context.displayed)
    assert set(presentations.values()).issubset(context.removed)


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
    contextual_received: list[
        tuple[CadDocumentId | None, tuple[SelectionMetadata, ...]]
    ] = []
    widget.selection_changed.connect(received.append)
    widget.selection_context_changed.connect(
        lambda document_id, items: contextual_received.append((document_id, items))
    )
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
    assert contextual_received == [(second, (item,))]
    widget.clear()
    assert backend.displayed_document is None
    assert received[-1] == ()
    assert contextual_received[-1] == (None, ())
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


def test_ocp_backend_preserves_old_document_when_selection_bind_fails() -> None:
    kernel = OcpCadKernel()
    first = kernel.create_box(1.0, 2.0, 3.0)
    second = kernel.create_box(4.0, 5.0, 6.0)
    backend = OcpCadViewportBackend(kernel)
    lifecycle = FakeLifecycle()
    selection = FailOnceSelection()
    backend._lifecycle = lifecycle
    backend._selection = selection
    backend._input = FakeInput()

    backend.display_document(first)
    old_presentation = lifecycle.presentation
    selection.fail_next_bind = True
    with pytest.raises(RuntimeError, match="selection bind"):
        backend.display_document(second)

    assert backend._document_id == first
    assert lifecycle.presentation is old_presentation
    assert selection.document_id == first


@pytest.mark.parametrize("failure", ("bind", "commit"))
def test_managed_registry_failure_discards_candidate_and_preserves_old(
    failure: str,
) -> None:
    kernel = OcpCadKernel()
    first = kernel.create_box(1.0, 2.0, 3.0)
    compound = TopoDS_Compound()
    builder = BRep_Builder()
    builder.MakeCompound(compound)
    builder.Add(compound, BRepPrimAPI_MakeBox(4.0, 5.0, 6.0).Shape())
    builder.Add(compound, BRepPrimAPI_MakeBox(7.0, 8.0, 9.0).Shape())
    second = kernel._documents.add_brep(compound, CadFormat.GENERATED).document_id
    backend = OcpCadViewportBackend(kernel)
    lifecycle = FakeManagedLifecycle()
    selection = FakeManagedSelection()
    backend._lifecycle = lifecycle
    backend._selection = selection
    backend._input = FakeInput()

    backend.display_document(first)
    old_registry = lifecycle.registry
    old_presentation = lifecycle.presentation
    if failure == "bind":
        selection.fail_next_bind = True
    else:
        lifecycle.fail_commit = True

    with pytest.raises(RuntimeError, match=f"managed {failure}"):
        backend.display_document(second)

    assert backend._document_id == first
    assert lifecycle.registry is old_registry
    assert lifecycle.presentation is old_presentation
    assert len(lifecycle.presentations) == 1
    assert selection.document_id == first
    assert lifecycle.discard_count == 1


def test_discard_failure_still_restores_old_document_selection() -> None:
    kernel = OcpCadKernel()
    first = kernel.create_box(1.0, 2.0, 3.0)
    second = kernel.create_box(4.0, 5.0, 6.0)
    backend = OcpCadViewportBackend(kernel)
    lifecycle = FakeManagedLifecycle()
    selection = FakeManagedSelection()
    backend._lifecycle = lifecycle
    backend._selection = selection
    backend._input = FakeInput()
    backend.display_document(first)
    old_registry = lifecycle.registry
    lifecycle.fail_commit = True
    lifecycle.fail_discard = True

    with pytest.raises(RuntimeError, match="discard"):
        backend.display_document(second)

    assert backend._document_id == first
    assert lifecycle.registry is old_registry
    assert selection.document_id == first


def test_visibility_apply_failure_preserves_selection_and_registry_state() -> None:
    kernel = OcpCadKernel()
    document_id = kernel.create_box(1.0, 2.0, 3.0)
    backend = OcpCadViewportBackend(kernel)
    lifecycle = FakeManagedLifecycle()
    selection = FakeManagedSelection()
    backend._lifecycle = lifecycle
    backend._selection = selection
    backend._input = FakeInput()
    backend.display_document(document_id)
    object_id = kernel.get_document_tree(document_id).presentation_nodes[0].object_id
    backend.select_objects(document_id, (object_id,))
    original = dict(lifecycle.registry.appearances)
    lifecycle.context.fail_once("erase", 1)

    with pytest.raises(RuntimeError, match="simulated erase"):
        backend.set_object_visibility(document_id, object_id, False)

    assert lifecycle.registry.appearances == original
    assert selection.selected_object_ids == (object_id,)
    assert selection.clear_selection_count == 0
    assert backend._selected_object_ids == (object_id,)


def test_clear_drops_managed_registry_selection_and_isolate_snapshot() -> None:
    kernel = OcpCadKernel()
    document_id = kernel.create_box(1.0, 2.0, 3.0)
    backend = OcpCadViewportBackend(kernel)
    lifecycle = FakeManagedLifecycle()
    selection = FakeManagedSelection()
    backend._lifecycle = lifecycle
    backend._selection = selection
    backend._input = FakeInput()
    backend.display_document(document_id)
    object_id = kernel.get_document_tree(document_id).presentation_nodes[0].object_id
    backend.select_objects(document_id, (object_id,))
    backend.isolate_object(document_id, object_id)
    old_registry = lifecycle.registry
    assert old_registry.isolate_active

    backend.clear()

    assert backend._document_id is None
    assert backend._tree is None
    assert backend._selected_object_ids == ()
    assert lifecycle.registry is None
    assert not old_registry.isolate_active
    assert selection.document_id is None


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
