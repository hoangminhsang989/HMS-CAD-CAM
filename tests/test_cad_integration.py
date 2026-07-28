"""Stage 4E integration tests for MainWindow, CAD import and project lifecycle."""

from __future__ import annotations

from contextlib import closing
import os
import sqlite3
import threading
import time
from pathlib import Path
from uuid import uuid4

import pytest

from OCP.BRep import BRep_Builder  # noqa: E402
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from OCP.BRepTools import BRepTools  # noqa: E402
from OCP.BRepMesh import BRepMesh_IncrementalMesh  # noqa: E402
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox  # noqa: E402
from OCP.IFSelect import IFSelect_ReturnStatus  # noqa: E402
from OCP.STEPControl import (  # noqa: E402
    STEPControl_StepModelType,
    STEPControl_Writer,
)
from OCP.StlAPI import StlAPI_Writer  # noqa: E402
from OCP.TopoDS import TopoDS_Compound  # noqa: E402
from PySide6.QtCore import QTimer, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QMenu, QToolBar, QToolButton  # noqa: E402

from hms_cadcam.cad.exceptions import CadDocumentNotFoundError  # noqa: E402
from hms_cadcam.cad.kernel import CadKernel  # noqa: E402
from hms_cadcam.cad.models import (  # noqa: E402
    BoundingBox,
    CadDocumentId,
    CadDocumentMetadata,
    CadFormat,
    CadGeometryKind,
    CadImportResult,
    CadKernelStatus,
    CadObjectId,
    CadObjectKind,
    TopologyCounts,
    XcafNodeRole,
)
from hms_cadcam.cad.ocp import OcpCadKernel  # noqa: E402
from hms_cadcam.cad.persistent_keys import (  # noqa: E402
    PersistentKeyScheme,
    PersistentXcafOccurrenceKey,
    XcafOccurrenceKeyVersion,
    XcafOccurrencePath,
    XcafProductIdentity,
)
from hms_cadcam.cad.ocp.measurement import OcpMeasurementService  # noqa: E402
from hms_cadcam.project.models import UnitSystem  # noqa: E402
from hms_cadcam.project.cad_state import (  # noqa: E402
    CadViewState,
    ObjectAppearanceOverride,
    PersistentObjectAppearance,
)
from hms_cadcam.project.service import ProjectService  # noqa: E402
from hms_cadcam.ui.cad_worker import CadImportTask  # noqa: E402
from hms_cadcam.ui.main_window import MainWindow  # noqa: E402
from hms_cadcam.viewer.models import (  # noqa: E402
    DisplayMode,
    KeyboardModifier,
    MouseButton,
    ObjectAppearance,
    ObjectColor,
    SelectionMetadata,
    SelectionMode,
    ViewDirection,
    ViewportStatus,
)
from spikes.xcaf_step.fixture import write_xcaf_step_fixture  # noqa: E402


class IntegrationViewportBackend:
    """Offscreen backend that records presentation and action routing."""

    def __init__(self) -> None:
        self.callback = lambda _items: None
        self.initialized = False
        self.closed = False
        self.fail_display = False
        self.fail_appearance: str | None = None
        self.current_document: CadDocumentId | None = None
        self.display_history: list[CadDocumentId] = []
        self.clear_count = 0
        self.view_directions: list[ViewDirection] = []
        self.display_modes: list[DisplayMode] = []
        self.selection_modes: list[SelectionMode] = []
        self.object_selections: list[tuple[CadDocumentId, tuple[CadObjectId, ...]]] = []
        self.visibility_changes: list[tuple[CadDocumentId, CadObjectId, bool]] = []
        self.isolate_changes: list[tuple[CadDocumentId, CadObjectId]] = []
        self.reset_isolate_documents: list[CadDocumentId] = []
        self.color_changes: list[tuple[CadDocumentId, CadObjectId, ObjectColor]] = []
        self.transparency_changes: list[tuple[CadDocumentId, CadObjectId, float]] = []
        self.reset_appearance_changes: list[tuple[CadDocumentId, CadObjectId]] = []

    def get_status(self) -> ViewportStatus:
        return ViewportStatus(True, self.initialized and not self.closed, "mock")

    def set_selection_callback(self, callback) -> None:
        self.callback = callback

    def initialize(self, native_window_id: int) -> None:
        assert native_window_id > 0
        self.initialized = True

    def display_document(self, document_id: CadDocumentId) -> None:
        if self.fail_display:
            raise RuntimeError("simulated managed registry display failure")
        self.current_document = document_id
        self.display_history.append(document_id)

    def clear(self) -> None:
        self.current_document = None
        self.clear_count += 1
        self.callback(())

    def fit_all(self) -> None:
        return None

    def set_view_direction(self, direction: ViewDirection) -> None:
        self.view_directions.append(direction)

    def set_display_mode(self, mode: DisplayMode) -> None:
        self.display_modes.append(mode)

    def set_selection_mode(self, mode: SelectionMode) -> None:
        self.selection_modes.append(mode)

    def select_objects(
        self,
        document_id: CadDocumentId,
        object_ids: tuple[CadObjectId, ...],
    ) -> None:
        self.object_selections.append((document_id, object_ids))

    def set_object_visibility(
        self,
        document_id: CadDocumentId,
        object_id: CadObjectId,
        visible: bool,
    ) -> None:
        if self.fail_appearance == "visibility":
            self.fail_appearance = None
            raise RuntimeError("simulated visibility apply failure")
        self.visibility_changes.append((document_id, object_id, visible))

    def isolate_object(
        self,
        document_id: CadDocumentId,
        object_id: CadObjectId,
    ) -> None:
        if self.fail_appearance == "isolate":
            self.fail_appearance = None
            raise RuntimeError("simulated isolate apply failure")
        self.isolate_changes.append((document_id, object_id))

    def reset_isolate(self, document_id: CadDocumentId) -> None:
        self.reset_isolate_documents.append(document_id)

    def reset_object_appearance(
        self,
        document_id: CadDocumentId,
        object_id: CadObjectId,
    ) -> None:
        self.reset_appearance_changes.append((document_id, object_id))

    def set_object_color(
        self,
        document_id: CadDocumentId,
        object_id: CadObjectId,
        color: ObjectColor,
    ) -> None:
        if self.fail_appearance == "color":
            self.fail_appearance = None
            raise RuntimeError("simulated color apply failure")
        self.color_changes.append((document_id, object_id, color))

    def set_object_transparency(
        self,
        document_id: CadDocumentId,
        object_id: CadObjectId,
        transparency: float,
    ) -> None:
        if self.fail_appearance == "transparency":
            self.fail_appearance = None
            raise RuntimeError("simulated transparency apply failure")
        self.transparency_changes.append((document_id, object_id, transparency))

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
        self.closed = True
        self.callback = lambda _items: None

    def emit_selection(self, items: tuple[SelectionMetadata, ...]) -> None:
        self.callback(items)


class BlockingCadKernel:
    """Delegate to OCP but pause the first import to exercise request generations."""

    def __init__(self, blocked_path: Path) -> None:
        self._kernel = OcpCadKernel()
        self._blocked_path = blocked_path.resolve()
        self.started = threading.Event()
        self.release = threading.Event()
        self.finished = threading.Event()
        self.first_result: CadImportResult | None = None
        self.released_document_ids: list[CadDocumentId] = []

    def is_available(self) -> bool:
        return True

    def get_status(self) -> CadKernelStatus:
        return self._kernel.get_status()

    def create_box(
        self, x_length: float, y_length: float, z_length: float
    ) -> CadDocumentId:
        return self._kernel.create_box(x_length, y_length, z_length)

    def import_step(self, path: str | Path) -> CadImportResult:
        source = Path(path).resolve()
        if source == self._blocked_path:
            self.started.set()
            self.release.wait(timeout=10)
        result = self._kernel.import_step(source)
        if source == self._blocked_path:
            self.first_result = result
            self.finished.set()
        return result

    def import_brep(self, path: str | Path) -> CadImportResult:
        return self._kernel.import_brep(path)

    def import_iges(self, path: str | Path) -> CadImportResult:
        return self._kernel.import_iges(path)

    def import_stl(self, path: str | Path) -> CadImportResult:
        return self._kernel.import_stl(path)

    def release_document(self, document_id: CadDocumentId) -> None:
        self.released_document_ids.append(document_id)
        self._kernel.release_document(document_id)

    def get_document_metadata(
        self, document_id: CadDocumentId
    ) -> CadDocumentMetadata:
        return self._kernel.get_document_metadata(document_id)

    def get_topology_counts(self, document_id: CadDocumentId) -> TopologyCounts:
        return self._kernel.get_topology_counts(document_id)

    def get_bounding_box(self, document_id: CadDocumentId) -> BoundingBox:
        return self._kernel.get_bounding_box(document_id)


def _application() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _qt_application() -> QApplication:
    """Keep one QApplication wrapper alive for every QWidget integration test."""
    return _application()


def _write_step(path: Path, size: float = 40.0) -> None:
    shape = BRepPrimAPI_MakeBox(size, 30.0, 20.0).Shape()
    writer = STEPControl_Writer()
    assert (
        writer.Transfer(shape, STEPControl_StepModelType.STEPControl_AsIs)
        == IFSelect_ReturnStatus.IFSelect_RetDone
    )
    assert writer.Write(str(path)) == IFSelect_ReturnStatus.IFSelect_RetDone


def _write_brep(path: Path, size: float = 25.0) -> None:
    shape = BRepPrimAPI_MakeBox(size, 15.0, 10.0).Shape()
    assert BRepTools.Write_s(shape, str(path))


def _write_multi_solid_brep(path: Path) -> None:
    compound = TopoDS_Compound()
    builder = BRep_Builder()
    builder.MakeCompound(compound)
    builder.Add(compound, BRepPrimAPI_MakeBox(10.0, 8.0, 6.0).Shape())
    builder.Add(compound, BRepPrimAPI_MakeBox(5.0, 4.0, 3.0).Shape())
    assert BRepTools.Write_s(compound, str(path))


def _write_stl(path: Path, *, ascii_mode: bool = True) -> None:
    shape = BRepPrimAPI_MakeBox(18.0, 12.0, 7.0).Shape()
    BRepMesh_IncrementalMesh(shape, 0.1)
    writer = StlAPI_Writer()
    writer.ASCIIMode = ascii_mode
    assert writer.Write(shape, str(path))


def _wait_until(application: QApplication, predicate, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        application.processEvents()
        time.sleep(0.005)
    application.processEvents()
    assert predicate()


def _window(
    tmp_path: Path,
    kernel: CadKernel | None = None,
) -> tuple[MainWindow, IntegrationViewportBackend, CadKernel]:
    selected_kernel = kernel or OcpCadKernel()
    backend = IntegrationViewportBackend()
    service = ProjectService.create_default(tmp_path / "config")
    return MainWindow(service, selected_kernel, backend), backend, selected_kernel


def test_main_window_uses_real_viewport_and_shared_cad_actions(tmp_path: Path) -> None:
    window, _backend, _kernel = _window(tmp_path)
    assert window.viewport.objectName() == "CadViewportWidget"
    actions = window.cad_controller.actions
    assert len({id(action) for action in actions.values()}) == len(actions)
    toolbar = window.findChild(QToolBar, "CadViewToolbar")
    assert toolbar is not None
    assert actions["open_step"] in toolbar.actions()
    assert actions["open_iges"] in toolbar.actions()
    assert actions["open_stl"] in toolbar.actions()
    assert actions["fit_all"] in toolbar.actions()
    menu_actions = {
        action
        for menu in window.menuBar().findChildren(QMenu)
        for action in menu.actions()
    }
    assert actions["open_step"] in menu_actions
    assert actions["open_iges"] in menu_actions
    assert actions["open_stl"] in menu_actions
    ribbon_actions = {
        button.defaultAction()
        for button in window.findChildren(QToolButton)
        if button.objectName() == "RibbonButton" and button.defaultAction() is not None
    }
    assert actions["open_step"] in ribbon_actions
    assert actions["open_iges"] in ribbon_actions
    assert actions["open_stl"] in ribbon_actions
    assert actions["fit_all"] in ribbon_actions
    window.close()


@pytest.mark.parametrize(
    ("file_name", "cad_format"),
    (("box.step", CadFormat.STEP), ("box.brep", CadFormat.BREP)),
)
def test_background_import_displays_step_and_brep(
    tmp_path: Path,
    file_name: str,
    cad_format: CadFormat,
) -> None:
    application = _application()
    source = tmp_path / file_name
    (_write_step if cad_format is CadFormat.STEP else _write_brep)(source)
    window, backend, _kernel = _window(tmp_path)
    window.cad_controller.start_import(source, cad_format)
    _wait_until(application, lambda: not window.cad_controller.is_busy)
    assert backend.current_document == window.cad_controller.active_document_id
    assert window.cad_controller.active_metadata is not None
    assert window.cad_controller.active_metadata.cad_format is cad_format
    assert window._import_status.text() == "CAD: Hoàn thành"
    window.close()


def test_xcaf_occurrence_tree_selection_properties_and_session_appearance(
    tmp_path: Path,
) -> None:
    application = _application()
    source = tmp_path / "nested-assembly.step"
    expected = write_xcaf_step_fixture(source)
    window, backend, _kernel = _window(tmp_path)
    window.cad_controller.start_import(source, CadFormat.STEP)
    _wait_until(application, lambda: not window.cad_controller.is_busy)
    tree = window.cad_controller.active_tree
    document_id = window.cad_controller.active_document_id
    assert tree is not None and document_id is not None
    assert window.cad_controller._persistent_map is None
    root = tree.root.children[0]
    repeated = [
        node
        for node in root.children
        if node.product_name == expected.repeated_product_name
    ]
    assert len(repeated) == 2
    first_item = window._ensure_object_item(repeated[0].object_id)
    second_item = window._ensure_object_item(repeated[1].object_id)
    assert first_item is not None and second_item is not None
    assert repeated[0].product_name in first_item.text(1)
    first_item.setSelected(True)
    application.processEvents()
    assert backend.object_selections[-1] == (document_id, (repeated[0].object_id,))

    selected = SelectionMetadata(
        document_id,
        f"{document_id}:solid:1",
        SelectionMode.SOLID,
        repeated[1].bounding_box,
        repeated[1].object_id,
    )
    backend.emit_selection((selected,))
    application.processEvents()
    assert second_item.isSelected()
    properties = {
        window._properties_table.item(row, 0).text(): (
            window._properties_table.item(row, 1).text()
        )
        for row in range(window._properties_table.rowCount())
    }
    assert properties["Product name"] == expected.repeated_product_name
    assert properties["Role"] == "PART"
    assert properties["Object ID"] == str(repeated[1].object_id)
    source_color = window.cad_controller._base_appearances[repeated[0].object_id].color
    override = ObjectColor(0.2, 0.4, 0.7)
    assert window.cad_controller.set_object_color(
        document_id, repeated[0].object_id, override
    )
    assert window.cad_controller.set_object_transparency(
        document_id, repeated[0].object_id, 0.35
    )
    assert window.cad_controller.reset_object_appearance(
        document_id, repeated[0].object_id
    )
    current = dict(window.cad_controller.appearances)[repeated[0].object_id]
    assert current.color == source_color
    assert current.transparency == 0.0
    window.close()


def test_mesh_disables_topology_selection_and_brep_restores_it(
    tmp_path: Path,
) -> None:
    application = _application()
    mesh = tmp_path / "box.stl"
    brep = tmp_path / "box.brep"
    _write_stl(mesh)
    _write_brep(brep)
    window, backend, _kernel = _window(tmp_path)
    selection_actions = [
        window.cad_controller.actions[f"selection_{mode.value}"]
        for mode in SelectionMode
    ]

    window.cad_controller.start_import(brep, CadFormat.BREP)
    _wait_until(application, lambda: not window.cad_controller.is_busy)
    window.cad_controller.actions["selection_face"].trigger()
    assert window.cad_controller.actions["selection_face"].isChecked()
    assert backend.selection_modes[-1] is SelectionMode.FACE

    window.cad_controller.start_import(mesh, CadFormat.STL)
    _wait_until(application, lambda: not window.cad_controller.is_busy)
    assert backend.current_document == window.cad_controller.active_document_id
    assert all(not action.isEnabled() for action in selection_actions)
    assert not window.cad_controller.actions["selection_vertex"].isEnabled()
    assert not window.cad_controller.actions["measurement"].isEnabled()
    properties = {
        window._properties_table.item(row, 0).text(): (
            window._properties_table.item(row, 1).text()
        )
        for row in range(window._properties_table.rowCount())
    }
    assert properties["Loại hình học"] == "TRIANGLE_MESH"
    assert "không xác định" in properties["Đơn vị"].lower()
    assert "Vertex / Triangle" in properties
    assert "Solid / Face / Edge" not in properties

    window.cad_controller.start_import(brep, CadFormat.BREP)
    _wait_until(application, lambda: not window.cad_controller.is_busy)
    assert all(action.isEnabled() for action in selection_actions)
    assert window.cad_controller.actions["measurement"].isEnabled()
    assert window.cad_controller.actions["selection_face"].isChecked()
    window.close()


def test_failed_import_keeps_current_document(tmp_path: Path) -> None:
    application = _application()
    valid = tmp_path / "box.brep"
    broken = tmp_path / "broken.step"
    _write_brep(valid)
    broken.write_text("not STEP", encoding="utf-8")
    window, backend, _kernel = _window(tmp_path)
    window.cad_controller.start_import(valid, CadFormat.BREP)
    _wait_until(application, lambda: not window.cad_controller.is_busy)
    current = window.cad_controller.active_document_id
    current_tree = window.cad_controller.active_tree
    window.cad_controller.start_import(broken, CadFormat.STEP)
    _wait_until(application, lambda: not window.cad_controller.is_busy)
    assert window.cad_controller.active_document_id == current
    assert window.cad_controller.active_tree is current_tree
    assert backend.current_document == current
    assert window._import_status.text() == "CAD: Lỗi"
    window.close()


def test_display_failure_keeps_document_tree_and_releases_candidate(
    tmp_path: Path,
) -> None:
    application = _application()
    first = tmp_path / "displayed.brep"
    second = tmp_path / "candidate.brep"
    _write_brep(first, 20.0)
    _write_brep(second, 30.0)
    window, backend, kernel = _window(tmp_path)
    window.cad_controller.start_import(first, CadFormat.BREP)
    _wait_until(application, lambda: not window.cad_controller.is_busy)
    old_document_id = window.cad_controller.active_document_id
    old_tree = window.cad_controller.active_tree
    backend.fail_display = True

    window.cad_controller.start_import(second, CadFormat.BREP)
    _wait_until(application, lambda: not window.cad_controller.is_busy)
    assert window.cad_controller.active_document_id == old_document_id
    assert window.cad_controller.active_tree is old_tree
    assert backend.current_document == old_document_id
    assert len(backend.display_history) == 1
    assert len(kernel._documents._records) == 1
    window.close()


def test_worker_exception_reports_error_without_sticking_busy_state(
    tmp_path: Path,
    monkeypatch,
) -> None:
    application = _application()
    kernel = OcpCadKernel()

    def fail_import(_path: str | Path) -> CadImportResult:
        raise RuntimeError("simulated worker failure")

    monkeypatch.setattr(kernel, "import_step", fail_import)
    window, backend, _selected_kernel = _window(tmp_path, kernel)
    window.cad_controller.start_import(tmp_path / "failure.step", CadFormat.STEP)
    _wait_until(application, lambda: not window.cad_controller.is_busy)
    assert window._import_status.text() == "CAD: Lỗi"
    assert window.cad_controller.active_document_id is None
    assert backend.current_document is None
    window.close()


def test_old_worker_result_cannot_replace_new_request(tmp_path: Path) -> None:
    application = _application()
    first = tmp_path / "first.step"
    second = tmp_path / "second.brep"
    _write_step(first, 40.0)
    _write_brep(second, 22.0)
    kernel = BlockingCadKernel(first)
    window, backend, _selected_kernel = _window(tmp_path, kernel)
    window.cad_controller.start_import(first, CadFormat.STEP)
    assert kernel.started.wait(timeout=5)
    window.cad_controller.start_import(second, CadFormat.BREP)
    kernel.release.set()
    _wait_until(application, lambda: not window.cad_controller.is_busy)
    assert window.cad_controller.active_metadata is not None
    assert window.cad_controller.active_metadata.cad_format is CadFormat.BREP
    assert backend.display_history == [window.cad_controller.active_document_id]
    assert kernel.first_result is not None
    assert kernel.first_result.document_id is not None
    with pytest.raises(CadDocumentNotFoundError):
        kernel.get_document_metadata(kernel.first_result.document_id)
    window.close()


def test_abandoned_queued_result_is_released_exactly_once(tmp_path: Path) -> None:
    source = tmp_path / "queued.brep"
    _write_brep(source)
    kernel = BlockingCadKernel(tmp_path / "not-blocked.step")
    window, _backend, _selected_kernel = _window(tmp_path, kernel)
    task = CadImportTask(kernel, 17, source, CadFormat.BREP)
    completed: list[tuple[int, CadImportResult]] = []
    task.signals.completed.connect(
        lambda request_id, result: completed.append((request_id, result))
    )
    task.run()
    assert len(completed) == 1
    request_id, result = completed[0]
    assert result.document_id is not None

    task.abandon()
    window.cad_controller._finish_import(request_id, result)

    assert kernel.released_document_ids.count(result.document_id) == 1
    window.close()


def test_document_and_selection_update_tree_and_properties(tmp_path: Path) -> None:
    application = _application()
    source = tmp_path / "box.brep"
    _write_brep(source)
    window, backend, _kernel = _window(tmp_path)
    window.cad_controller.start_import(source, CadFormat.BREP)
    _wait_until(application, lambda: not window.cad_controller.is_busy)
    tree_text = [
        item.text(column)
        for item in window._project_tree.findItems(
            "*", Qt.MatchFlag.MatchWildcard | Qt.MatchFlag.MatchRecursive,
            0,
        )
        for column in (0, 1)
    ]
    assert "Tài liệu CAD" in tree_text
    assert "Cấu trúc hình học" in tree_text
    assert "Hộp bao" in tree_text
    metadata = window.cad_controller.active_metadata
    assert metadata is not None
    document_properties = {
        window._properties_table.item(row, 0).text(): (
            window._properties_table.item(row, 1).text()
        )
        for row in range(window._properties_table.rowCount())
    }
    assert document_properties["Định dạng"] == "BREP"
    assert "Hộp bao" in document_properties
    item = SelectionMetadata(
        metadata.document_id,
        f"{metadata.document_id}:face:1",
        SelectionMode.FACE,
        metadata.bounding_box,
    )
    backend.emit_selection((item,))
    application.processEvents()
    properties = {
        window._properties_table.item(row, 0).text(): (
            window._properties_table.item(row, 1).text()
        )
        for row in range(window._properties_table.rowCount())
    }
    assert properties["Loại cấu trúc hình học"] == "FACE"
    assert properties["ID lựa chọn"] == item.selection_id
    window.close()


def test_topology_tree_selection_sync_is_loop_free_and_rejects_stale_ids(
    tmp_path: Path,
) -> None:
    application = _application()
    source = tmp_path / "two_solids.brep"
    _write_multi_solid_brep(source)
    window, backend, _kernel = _window(tmp_path)
    window.cad_controller.start_import(source, CadFormat.BREP)
    _wait_until(application, lambda: not window.cad_controller.is_busy)
    tree = window.cad_controller.active_tree
    assert tree is not None
    solids = tuple(
        node for node in tree.root.walk() if node.kind is CadObjectKind.SOLID
    )
    assert len(solids) == 2
    assert len(tree.presentation_nodes) == 2

    lazy_viewport_item = SelectionMetadata(
        tree.document_id,
        f"{tree.document_id}:face:1",
        SelectionMode.FACE,
        solids[1].bounding_box,
        solids[1].object_id,
    )
    backend.emit_selection((lazy_viewport_item,))
    application.processEvents()
    assert backend.object_selections == []
    assert window._selected_object_ids == (solids[1].object_id,)

    window._project_tree.expandAll()
    application.processEvents()
    solid_items = window._project_tree.findItems(
        "Solid *",
        Qt.MatchFlag.MatchWildcard | Qt.MatchFlag.MatchRecursive,
        0,
    )
    assert len(solid_items) == 2
    window._tree_sync_guard = True
    window._project_tree.clearSelection()
    window._tree_sync_guard = False
    solid_items[0].setSelected(True)
    application.processEvents()
    assert backend.object_selections[-1] == (
        tree.document_id,
        (solids[0].object_id,),
    )

    calls_before_viewport_event = len(backend.object_selections)
    viewport_item = SelectionMetadata(
        tree.document_id,
        f"{tree.document_id}:face:1",
        SelectionMode.FACE,
        solids[1].bounding_box,
        solids[1].object_id,
    )
    backend.emit_selection((viewport_item,))
    application.processEvents()
    assert len(backend.object_selections) == calls_before_viewport_event
    assert window._selected_object_ids == (solids[1].object_id,)

    window.cad_controller.select_tree_objects(
        tree.document_id,
        (CadObjectId(f"{tree.document_id}:object:missing"),),
    )
    window.cad_controller.select_tree_objects(
        CadDocumentId("stale:document"),
        (solids[0].object_id,),
    )
    assert len(backend.object_selections) == calls_before_viewport_event
    window.close()


def test_visibility_and_isolate_sync_clear_only_hidden_selection(
    tmp_path: Path,
) -> None:
    application = _application()
    source = tmp_path / "visibility.brep"
    _write_multi_solid_brep(source)
    window, backend, _kernel = _window(tmp_path)
    window.cad_controller.start_import(source, CadFormat.BREP)
    _wait_until(application, lambda: not window.cad_controller.is_busy)
    tree = window.cad_controller.active_tree
    assert tree is not None
    first, second = tree.presentation_nodes
    selected = SelectionMetadata(
        tree.document_id,
        f"{tree.document_id}:solid:1",
        SelectionMode.SOLID,
        first.bounding_box,
        first.object_id,
    )
    backend.emit_selection((selected,))
    application.processEvents()

    assert window.cad_controller.set_object_visibility(
        tree.document_id,
        first.object_id,
        False,
    )
    assert window._active_selection == ()
    assert backend.visibility_changes[-1] == (
        tree.document_id,
        first.object_id,
        False,
    )
    assert window.cad_controller.set_object_visibility(
        tree.document_id,
        first.object_id,
        True,
    )
    assert window._active_selection == ()

    assert window.cad_controller.set_object_visibility(
        tree.document_id,
        second.object_id,
        False,
    )
    assert window.cad_controller.isolate_object(tree.document_id, first.object_id)
    assert window.cad_controller.isolate_object(tree.document_id, second.object_id)
    assert window.cad_controller.reset_isolate(tree.document_id)
    appearance = dict(window.cad_controller.appearances)
    assert appearance[first.object_id].visible is True
    assert appearance[second.object_id].visible is False
    window.close()


def test_appearance_is_session_only_and_isolate_resets_on_document_change(
    tmp_path: Path,
) -> None:
    application = _application()
    first_source = tmp_path / "first.brep"
    second_source = tmp_path / "second.brep"
    _write_brep(first_source)
    _write_brep(second_source, 12.0)
    service = ProjectService.create_default(tmp_path / "config")
    session = service.new_project(tmp_path, "Appearance", UnitSystem.MILLIMETER)
    backend = IntegrationViewportBackend()
    window = MainWindow(service, OcpCadKernel(), backend)
    window.cad_controller.start_import(first_source, CadFormat.BREP)
    _wait_until(application, lambda: not window.cad_controller.is_busy)
    first_tree = window.cad_controller.active_tree
    assert first_tree is not None
    object_id = first_tree.presentation_nodes[0].object_id
    assert window.cad_controller.set_object_color(
        first_tree.document_id,
        object_id,
        ObjectColor(0.2, 0.3, 0.4),
    )
    assert window.cad_controller.set_object_transparency(
        first_tree.document_id,
        object_id,
        0.35,
    )
    assert window.cad_controller.isolate_object(first_tree.document_id, object_id)
    assert not session.is_dirty
    assert list((session.root_path / "autosave").iterdir()) == []

    window.cad_controller.start_import(second_source, CadFormat.BREP)
    _wait_until(application, lambda: not window.cad_controller.is_busy)
    second_tree = window.cad_controller.active_tree
    assert second_tree is not None and second_tree.document_id != first_tree.document_id
    assert not window.cad_controller.reset_isolate(second_tree.document_id)
    assert not window.cad_controller.set_object_visibility(
        first_tree.document_id,
        object_id,
        False,
    )
    assert not window.cad_controller.set_object_visibility(
        second_tree.document_id,
        object_id,
        False,
    )
    assert not session.is_dirty
    window.close()


def test_appearance_apply_error_preserves_controller_state_and_project(
    tmp_path: Path,
) -> None:
    application = _application()
    source = tmp_path / "appearance_failure.brep"
    _write_brep(source)
    service = ProjectService.create_default(tmp_path / "config")
    session = service.new_project(tmp_path, "Rollback", UnitSystem.MILLIMETER)
    backend = IntegrationViewportBackend()
    window = MainWindow(service, OcpCadKernel(), backend)
    window.cad_controller.start_import(source, CadFormat.BREP)
    _wait_until(application, lambda: not window.cad_controller.is_busy)
    tree = window.cad_controller.active_tree
    assert tree is not None
    object_id = tree.presentation_nodes[0].object_id
    actions = {
        "visibility": lambda: window.cad_controller.set_object_visibility(
            tree.document_id,
            object_id,
            False,
        ),
        "isolate": lambda: window.cad_controller.isolate_object(
            tree.document_id,
            object_id,
        ),
        "color": lambda: window.cad_controller.set_object_color(
            tree.document_id,
            object_id,
            ObjectColor(0.1, 0.2, 0.3),
        ),
        "transparency": lambda: window.cad_controller.set_object_transparency(
            tree.document_id,
            object_id,
            0.6,
        ),
    }

    for operation, action in actions.items():
        original = window.cad_controller.appearances
        backend.fail_appearance = operation
        assert not action()
        assert window.cad_controller.appearances == original

    assert not window.cad_controller.reset_isolate(tree.document_id)
    assert not session.is_dirty
    assert list((session.root_path / "autosave").iterdir()) == []
    window.close()


def test_vertex_pair_measurement_is_read_only_and_resets_at_boundaries(
    tmp_path: Path,
) -> None:
    application = _application()
    source = tmp_path / "box.brep"
    _write_brep(source)
    service = ProjectService.create_default(tmp_path / "config")
    session = service.new_project(tmp_path, "Measurement", UnitSystem.MILLIMETER)
    backend = IntegrationViewportBackend()
    window = MainWindow(service, OcpCadKernel(), backend)
    window.cad_controller.start_import(source, CadFormat.BREP)
    _wait_until(application, lambda: not window.cad_controller.is_busy)
    metadata = window.cad_controller.active_metadata
    assert metadata is not None
    first = SelectionMetadata(
        metadata.document_id,
        f"{metadata.document_id}:vertex:1",
        SelectionMode.VERTEX,
        metadata.bounding_box,
    )
    second = SelectionMetadata(
        metadata.document_id,
        f"{metadata.document_id}:vertex:2",
        SelectionMode.VERTEX,
        metadata.bounding_box,
    )

    backend.emit_selection((first, second))
    application.processEvents()
    properties = {
        window._properties_table.item(row, 0).text(): (
            window._properties_table.item(row, 1).text()
        )
        for row in range(window._properties_table.rowCount())
    }
    assert "Khoảng cách" in properties
    assert not properties["Khoảng cách"].startswith("-")
    assert len(window.cad_controller.vertex_pair) == 2
    assert not session.is_dirty
    assert list((session.root_path / "autosave").iterdir()) == []

    face = SelectionMetadata(
        metadata.document_id,
        f"{metadata.document_id}:face:1",
        SelectionMode.FACE,
        metadata.bounding_box,
    )
    backend.emit_selection((face,))
    application.processEvents()
    face_properties = {
        window._properties_table.item(row, 0).text(): (
            window._properties_table.item(row, 1).text()
        )
        for row in range(window._properties_table.rowCount())
    }
    assert not face_properties["Diện tích"].startswith("-")

    solid = SelectionMetadata(
        metadata.document_id,
        f"{metadata.document_id}:solid:1",
        SelectionMode.SOLID,
        metadata.bounding_box,
    )
    backend.emit_selection((solid,))
    application.processEvents()
    solid_properties = {
        window._properties_table.item(row, 0).text(): (
            window._properties_table.item(row, 1).text()
        )
        for row in range(window._properties_table.rowCount())
    }
    assert not solid_properties["Thể tích"].startswith("-")

    backend.emit_selection((first,))
    application.processEvents()
    assert window.cad_controller.vertex_pair == (first.selection_id,)
    window.cad_controller.actions["selection_face"].trigger()
    assert window.cad_controller.vertex_pair == ()
    backend.emit_selection((first, second))
    replacement = tmp_path / "replacement.brep"
    _write_brep(replacement, 12.0)
    window.cad_controller.start_import(replacement, CadFormat.BREP)
    _wait_until(application, lambda: not window.cad_controller.is_busy)
    assert window.cad_controller.vertex_pair == ()

    replacement_metadata = window.cad_controller.active_metadata
    assert replacement_metadata is not None
    replacement_first = SelectionMetadata(
        replacement_metadata.document_id,
        f"{replacement_metadata.document_id}:vertex:1",
        SelectionMode.VERTEX,
        replacement_metadata.bounding_box,
    )
    backend.emit_selection((replacement_first,))
    assert window.cad_controller.vertex_pair == (replacement_first.selection_id,)
    window.project_controller.close_project()
    application.processEvents()
    assert window.cad_controller.vertex_pair == ()
    assert window.cad_controller.active_document_id is None
    window.close()
    assert window.cad_controller.vertex_pair == ()


def test_measurement_error_keeps_document_and_selection(tmp_path: Path) -> None:
    application = _application()
    source = tmp_path / "box.brep"
    _write_brep(source)
    service = ProjectService.create_default(tmp_path / "config")
    session = service.new_project(tmp_path, "Error", UnitSystem.MILLIMETER)
    backend = IntegrationViewportBackend()
    window = MainWindow(service, OcpCadKernel(), backend)
    window.cad_controller.start_import(source, CadFormat.BREP)
    _wait_until(application, lambda: not window.cad_controller.is_busy)
    metadata = window.cad_controller.active_metadata
    document_id = window.cad_controller.active_document_id
    assert metadata is not None and document_id is not None

    class FailingMeasurementService:
        def measure_selection(self, document_id, selection_id):
            del document_id, selection_id
            raise ValueError("simulated measurement failure")

        def measure_distance(self, document_id, first_selection_id, second_selection_id):
            del document_id, first_selection_id, second_selection_id
            raise ValueError("simulated measurement failure")

        def measure_document(self, document_id):
            del document_id
            raise ValueError("simulated measurement failure")

    window.cad_controller._measurement_service = FailingMeasurementService()
    item = SelectionMetadata(
        document_id,
        f"{document_id}:face:1",
        SelectionMode.FACE,
        metadata.bounding_box,
    )
    backend.emit_selection((item,))
    application.processEvents()
    assert window.cad_controller.active_document_id == document_id
    assert window._active_selection == (item,)
    assert backend.current_document == document_id
    assert not session.is_dirty
    assert list((session.root_path / "autosave").iterdir()) == []
    window.close()


def test_old_selection_and_measurement_cannot_replace_new_document_properties(
    tmp_path: Path,
) -> None:
    application = _application()
    first_source = tmp_path / "first.brep"
    second_source = tmp_path / "second.brep"
    _write_brep(first_source, 10.0)
    _write_brep(second_source, 20.0)
    window, backend, kernel = _window(tmp_path)

    window.cad_controller.start_import(first_source, CadFormat.BREP)
    _wait_until(application, lambda: not window.cad_controller.is_busy)
    first_metadata = window.cad_controller.active_metadata
    assert first_metadata is not None
    old_item = SelectionMetadata(
        first_metadata.document_id,
        f"{first_metadata.document_id}:face:1",
        SelectionMode.FACE,
        first_metadata.bounding_box,
    )
    old_result = OcpMeasurementService(kernel).measure_selection(
        first_metadata.document_id,
        old_item.selection_id,
    )

    window.cad_controller.start_import(second_source, CadFormat.BREP)
    _wait_until(application, lambda: not window.cad_controller.is_busy)
    second_metadata = window.cad_controller.active_metadata
    assert second_metadata is not None
    current_item = SelectionMetadata(
        second_metadata.document_id,
        f"{second_metadata.document_id}:face:1",
        SelectionMode.FACE,
        second_metadata.bounding_box,
    )
    backend.emit_selection((current_item,))
    application.processEvents()
    expected_selection = window._active_selection
    expected_measurements = window._active_measurements
    expected_properties = {
        window._properties_table.item(row, 0).text(): (
            window._properties_table.item(row, 1).text()
        )
        for row in range(window._properties_table.rowCount())
    }

    QTimer.singleShot(
        0,
        lambda: window.cad_controller.handle_selection_event(
            first_metadata.document_id,
            (old_item,),
        ),
    )
    QTimer.singleShot(
        0,
        lambda: window.cad_controller.handle_selection_event(
            first_metadata.document_id,
            (),
        ),
    )
    QTimer.singleShot(
        0,
        lambda: window.cad_controller.measurement_context_changed.emit(
            first_metadata.document_id,
            (old_result,),
        ),
    )
    QTimer.singleShot(
        0,
        lambda: window.cad_controller.measurement_context_changed.emit(
            first_metadata.document_id,
            (),
        ),
    )
    application.processEvents()

    assert window._active_selection == expected_selection
    assert window._active_measurements == expected_measurements
    assert {
        window._properties_table.item(row, 0).text(): (
            window._properties_table.item(row, 1).text()
        )
        for row in range(window._properties_table.rowCount())
    } == expected_properties
    window.close()


def test_close_project_clears_and_releases_document(tmp_path: Path) -> None:
    application = _application()
    source = tmp_path / "box.brep"
    _write_brep(source)
    service = ProjectService.create_default(tmp_path / "config")
    service.new_project(tmp_path, "Demo", UnitSystem.MILLIMETER)
    kernel = OcpCadKernel()
    backend = IntegrationViewportBackend()
    window = MainWindow(service, kernel, backend)
    window.cad_controller.start_import(source, CadFormat.BREP)
    _wait_until(application, lambda: not window.cad_controller.is_busy)
    document_id = window.cad_controller.active_document_id
    assert document_id is not None
    window.project_controller.close_project()
    application.processEvents()
    assert window.cad_controller.active_document_id is None
    assert backend.current_document is None
    with pytest.raises(CadDocumentNotFoundError):
        kernel.get_document_metadata(document_id)
    window.close()


def test_project_source_is_loaded_without_modifying_source(tmp_path: Path) -> None:
    application = _application()
    source = tmp_path / "source.step"
    _write_step(source)
    original = source.read_bytes()
    service = ProjectService.create_default(tmp_path / "config")
    session = service.create_project_from_source(
        tmp_path,
        "SourceProject",
        source,
        UnitSystem.MILLIMETER,
    )
    kernel = OcpCadKernel()
    backend = IntegrationViewportBackend()
    window = MainWindow(service, kernel, backend)
    _wait_until(application, lambda: not window.cad_controller.is_busy)
    assert session.root_path == service.current_project.root_path
    assert window.cad_controller.active_metadata is not None
    assert source.read_bytes() == original
    window.close()


def test_closing_main_window_during_import_detaches_worker(tmp_path: Path) -> None:
    application = _application()
    source = tmp_path / "slow.step"
    _write_step(source)
    kernel = BlockingCadKernel(source)
    window, backend, _selected_kernel = _window(tmp_path, kernel)
    window.show()
    window.cad_controller.start_import(source, CadFormat.STEP)
    assert kernel.started.wait(timeout=5)
    assert window.close()
    kernel.release.set()
    _wait_until(application, kernel.finished.is_set)
    _wait_until(application, lambda: window.cad_controller.active_document_id is None)
    assert not window.isVisible()
    assert backend.current_document is None


def test_project_cad_view_state_save_open_round_trip(tmp_path: Path) -> None:
    application = _application()
    source = tmp_path / "persistent.brep"
    _write_brep(source)
    service = ProjectService.create_default(tmp_path / "config")
    session = service.create_project_from_source(tmp_path, "Persistent View", source)
    backend = IntegrationViewportBackend()
    window = MainWindow(service, OcpCadKernel(), backend)
    _wait_until(application, lambda: not window.cad_controller.is_busy)
    tree = window.cad_controller.active_tree
    assert tree is not None
    object_id = tree.presentation_nodes[0].object_id
    color = ObjectColor(0.15, 0.35, 0.55)

    assert window.cad_controller.set_object_visibility(tree.document_id, object_id, False)
    assert window.cad_controller.set_object_color(tree.document_id, object_id, color)
    assert window.cad_controller.set_object_transparency(tree.document_id, object_id, 0.4)
    assert window.cad_controller.set_display_mode(DisplayMode.WIREFRAME)
    assert window.cad_controller.set_view_direction(ViewDirection.TOP)
    assert session.is_dirty
    service.save()
    project_root = session.root_path
    window.cad_controller.shutdown()
    service.close_project()

    reopened_service = ProjectService.create_default(tmp_path / "reopen-config")
    reopened_service.open_project(project_root)
    reopened = MainWindow(
        reopened_service, OcpCadKernel(), IntegrationViewportBackend()
    )
    _wait_until(application, lambda: not reopened.cad_controller.is_busy)
    reopened_tree = reopened.cad_controller.active_tree
    assert reopened_tree is not None
    reopened_id = reopened_tree.presentation_nodes[0].object_id
    appearance = dict(reopened.cad_controller.appearances)[reopened_id]
    assert appearance == ObjectAppearance(False, color, 0.4)
    assert reopened.cad_controller.display_mode is DisplayMode.WIREFRAME
    assert reopened.cad_controller.view_direction is ViewDirection.TOP
    assert not reopened_service.is_dirty
    assert reopened_service.autosave() is None
    assert list((project_root / "autosave").iterdir()) == []
    reopened.close()


def test_project_xcaf_repeated_occurrence_save_open_and_reset_round_trip(
    tmp_path: Path,
) -> None:
    application = _application()
    source = tmp_path / "persistent-assembly.step"
    write_xcaf_step_fixture(source)
    service = ProjectService.create_default(tmp_path / "config")
    session = service.create_project_from_source(
        tmp_path, "Persistent XCAF", source
    )
    backend = IntegrationViewportBackend()
    window = MainWindow(service, OcpCadKernel(), backend)
    _wait_until(application, lambda: not window.cad_controller.is_busy)
    tree = window.cad_controller.active_tree
    assert tree is not None
    repeated = sorted(
        (
            node
            for node in tree.presentation_nodes
            if node.product_name == "Repeated Product"
        ),
        key=lambda node: node.absolute_transform.translation[0],
    )
    assert len(repeated) == 2
    first, second = repeated
    first_source = window.cad_controller._base_appearances[first.object_id]
    second_source = window.cad_controller._base_appearances[second.object_id]
    override_color = ObjectColor(0.25, 0.45, 0.75)

    backend.fail_appearance = "color"
    assert not window.cad_controller.set_object_color(
        tree.document_id, first.object_id, override_color
    )
    assert not session.is_dirty
    assert session.cad_view_states == {}
    assert window.cad_controller.set_object_color(
        tree.document_id, first.object_id, override_color
    )
    assert window.cad_controller.set_object_transparency(
        tree.document_id, first.object_id, 0.35
    )
    assert window.cad_controller.set_object_visibility(
        tree.document_id, second.object_id, False
    )
    state = service.cad_view_state(session.manifest.source_files[0].source_id)
    assert len(state.object_appearances) == 2
    assert all(
        isinstance(item.appearance, ObjectAppearanceOverride)
        for item in state.object_appearances
    )
    service.save()
    project_root = session.root_path
    with closing(sqlite3.connect(project_root / "project.db")) as connection, connection:
        rows = connection.execute(
            """
            SELECT visible, color_r, color_g, color_b, transparency
            FROM cad_xcaf_occurrence_appearance
            ORDER BY occurrence_path
            """
        ).fetchall()
        assert len(rows) == 2
        assert any(row[0] == 0 and row[1:4] == (None, None, None) for row in rows)
    window.cad_controller.shutdown()
    service.close_project()

    reopened_service = ProjectService.create_default(tmp_path / "reopen-config")
    reopened_service.open_project(project_root)
    reopened_backend = IntegrationViewportBackend()
    reopened = MainWindow(reopened_service, OcpCadKernel(), reopened_backend)
    _wait_until(application, lambda: not reopened.cad_controller.is_busy)
    reopened_tree = reopened.cad_controller.active_tree
    assert reopened_tree is not None
    reopened_repeated = sorted(
        (
            node
            for node in reopened_tree.presentation_nodes
            if node.product_name == "Repeated Product"
        ),
        key=lambda node: node.absolute_transform.translation[0],
    )
    reopened_appearances = dict(reopened.cad_controller.appearances)
    assert reopened_appearances[reopened_repeated[0].object_id] == ObjectAppearance(
        True, override_color, 0.35
    )
    assert reopened_appearances[reopened_repeated[1].object_id] == ObjectAppearance(
        False, second_source.color, second_source.transparency
    )
    assert not reopened_service.is_dirty
    assert reopened_service.autosave() is None

    assert reopened.cad_controller.reset_object_appearance(
        reopened_tree.document_id, reopened_repeated[0].object_id
    )
    reset = dict(reopened.cad_controller.appearances)[
        reopened_repeated[0].object_id
    ]
    assert reset == first_source
    pending = reopened_service.cad_view_state(
        session.manifest.source_files[0].source_id
    )
    assert len(pending.object_appearances) == 1
    reopened_service.save()
    with closing(sqlite3.connect(project_root / "project.db")) as connection, connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM cad_xcaf_occurrence_appearance"
        ).fetchone()[0] == 1
    reopened.close()


def test_project_xcaf_save_as_preserves_source_id_and_instance_override(
    tmp_path: Path,
) -> None:
    application = _application()
    source = tmp_path / "save-as-assembly.step"
    write_xcaf_step_fixture(source)
    service = ProjectService.create_default(tmp_path / "config")
    original = service.create_project_from_source(tmp_path, "XCAF Original", source)
    source_id = original.manifest.source_files[0].source_id
    window = MainWindow(service, OcpCadKernel(), IntegrationViewportBackend())
    _wait_until(application, lambda: not window.cad_controller.is_busy)
    tree = window.cad_controller.active_tree
    assert tree is not None
    target = sorted(
        (
            node
            for node in tree.presentation_nodes
            if node.product_name == "Repeated Product"
        ),
        key=lambda node: node.absolute_transform.translation[0],
    )[1]
    color = ObjectColor(0.65, 0.25, 0.45)
    assert window.cad_controller.set_object_color(
        tree.document_id, target.object_id, color
    )
    service.save()
    original_root = original.root_path
    window.cad_controller.shutdown()

    copied = service.save_as(tmp_path, "XCAF Copy")
    assert copied.manifest.source_files[0].source_id == source_id
    copy_root = copied.root_path
    copied_window = MainWindow(
        service, OcpCadKernel(), IntegrationViewportBackend()
    )
    _wait_until(application, lambda: not copied_window.cad_controller.is_busy)
    copied_tree = copied_window.cad_controller.active_tree
    assert copied_tree is not None
    copied_target = sorted(
        (
            node
            for node in copied_tree.presentation_nodes
            if node.product_name == "Repeated Product"
        ),
        key=lambda node: node.absolute_transform.translation[0],
    )[1]
    assert dict(copied_window.cad_controller.appearances)[
        copied_target.object_id
    ].color == color
    assert copied_window.cad_controller.reset_object_appearance(
        copied_tree.document_id, copied_target.object_id
    )
    service.save()
    with closing(sqlite3.connect(original_root / "project.db")) as connection, connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM cad_xcaf_occurrence_appearance"
        ).fetchone()[0] == 1
    with closing(sqlite3.connect(copy_root / "project.db")) as connection, connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM cad_xcaf_occurrence_appearance"
        ).fetchone()[0] == 0
    copied_window.close()


def test_xcaf_stale_and_foreign_occurrence_keys_are_not_applied(
    tmp_path: Path,
    caplog,
) -> None:
    application = _application()
    source = tmp_path / "stale-assembly.step"
    write_xcaf_step_fixture(source)
    service = ProjectService.create_default(tmp_path / "config")
    session = service.create_project_from_source(tmp_path, "Stale XCAF", source)
    source_id = session.manifest.source_files[0].source_id
    stale_key = PersistentXcafOccurrenceKey(
        source_id,
        CadGeometryKind.BREP,
        PersistentKeyScheme.XCAF_OCCURRENCE,
        XcafOccurrenceKeyVersion.V1,
        XcafOccurrencePath("assembly:" + "a" * 32 + "/part:" + "b" * 32),
        XcafProductIdentity("product:" + "c" * 32),
        XcafNodeRole.PART,
    )
    service.stage_cad_view_state(
        CadViewState(
            source_id,
            object_appearances=(
                PersistentObjectAppearance(
                    stale_key,
                    ObjectAppearanceOverride(color=ObjectColor(0.2, 0.3, 0.4)),
                ),
            ),
        )
    )
    service.save()
    timestamp = "2026-01-01T00:00:00Z"
    with closing(sqlite3.connect(session.root_path / "project.db")) as connection, connection:
        connection.execute(
            """
            INSERT INTO cad_xcaf_occurrence_appearance VALUES (
                ?, 'brep', 'xcaf_occurrence', 1, ?, ?, 'part',
                0, NULL, NULL, NULL, NULL, ?
            )
            """,
            (
                str(uuid4()),
                "assembly:" + "d" * 32 + "/part:" + "e" * 32,
                "product:" + "f" * 32,
                timestamp,
            ),
        )
    service.close_project()

    caplog.set_level("WARNING")
    reopened_service = ProjectService.create_default(tmp_path / "reopen-config")
    reopened_service.open_project(session.root_path)
    backend = IntegrationViewportBackend()
    window = MainWindow(reopened_service, OcpCadKernel(), backend)
    _wait_until(application, lambda: not window.cad_controller.is_busy)

    assert backend.color_changes == []
    assert backend.visibility_changes == []
    assert backend.transparency_changes == []
    assert "source_id không hợp lệ" in caplog.text
    assert "persistent path stale/missing" in caplog.text
    assert not reopened_service.is_dirty
    window.close()


def test_xcaf_restore_apply_failure_rolls_back_to_source_without_dirty(
    tmp_path: Path,
) -> None:
    application = _application()
    source = tmp_path / "rollback-assembly.step"
    write_xcaf_step_fixture(source)
    service = ProjectService.create_default(tmp_path / "config")
    session = service.create_project_from_source(tmp_path, "Rollback XCAF", source)
    first = MainWindow(service, OcpCadKernel(), IntegrationViewportBackend())
    _wait_until(application, lambda: not first.cad_controller.is_busy)
    tree = first.cad_controller.active_tree
    assert tree is not None
    target = tree.presentation_nodes[0]
    assert first.cad_controller.set_object_color(
        tree.document_id, target.object_id, ObjectColor(0.3, 0.5, 0.7)
    )
    assert first.cad_controller.set_object_transparency(
        tree.document_id, target.object_id, 0.4
    )
    service.save()
    project_root = session.root_path
    first.cad_controller.shutdown()
    service.close_project()

    reopened_service = ProjectService.create_default(tmp_path / "reopen-config")
    reopened_service.open_project(project_root)
    backend = IntegrationViewportBackend()
    backend.fail_appearance = "transparency"
    reopened = MainWindow(reopened_service, OcpCadKernel(), backend)
    _wait_until(application, lambda: not reopened.cad_controller.is_busy)
    reopened_tree = reopened.cad_controller.active_tree
    assert reopened_tree is not None
    source_appearances = reopened.cad_controller._base_appearances
    assert dict(reopened.cad_controller.appearances) == source_appearances
    assert backend.reset_appearance_changes
    assert not reopened_service.is_dirty
    assert reopened_service.autosave() is None
    reopened.close()


def test_xcaf_save_during_isolate_uses_pre_isolate_visibility(
    tmp_path: Path,
) -> None:
    application = _application()
    source = tmp_path / "isolate-assembly.step"
    write_xcaf_step_fixture(source)
    service = ProjectService.create_default(tmp_path / "config")
    session = service.create_project_from_source(tmp_path, "Isolate XCAF", source)
    window = MainWindow(service, OcpCadKernel(), IntegrationViewportBackend())
    _wait_until(application, lambda: not window.cad_controller.is_busy)
    tree = window.cad_controller.active_tree
    assert tree is not None and len(tree.presentation_nodes) == 3
    first, second = tree.presentation_nodes[:2]
    assert window.cad_controller.set_object_color(
        tree.document_id, first.object_id, ObjectColor(0.4, 0.5, 0.6)
    )
    assert window.cad_controller.isolate_object(
        tree.document_id, first.object_id
    )
    assert not dict(window.cad_controller.appearances)[second.object_id].visible
    service.save()
    project_root = session.root_path
    window.cad_controller.shutdown()
    service.close_project()

    reopened_service = ProjectService.create_default(tmp_path / "reopen-config")
    reopened_service.open_project(project_root)
    reopened = MainWindow(
        reopened_service, OcpCadKernel(), IntegrationViewportBackend()
    )
    _wait_until(application, lambda: not reopened.cad_controller.is_busy)
    reopened_tree = reopened.cad_controller.active_tree
    assert reopened_tree is not None
    appearances = dict(reopened.cad_controller.appearances)
    assert all(
        appearances[node.object_id].visible
        for node in reopened_tree.presentation_nodes
    )
    reopened.close()


def test_bound_project_dirty_changes_only_after_successful_viewer_apply(
    tmp_path: Path,
) -> None:
    application = _application()
    source = tmp_path / "dirty.brep"
    _write_brep(source)
    service = ProjectService.create_default(tmp_path / "config")
    session = service.create_project_from_source(tmp_path, "Dirty Apply", source)
    backend = IntegrationViewportBackend()
    window = MainWindow(service, OcpCadKernel(), backend)
    _wait_until(application, lambda: not window.cad_controller.is_busy)
    tree = window.cad_controller.active_tree
    assert tree is not None
    object_id = tree.presentation_nodes[0].object_id
    before = window.cad_controller.appearances

    backend.fail_appearance = "color"
    assert not window.cad_controller.set_object_color(
        tree.document_id, object_id, ObjectColor(0.2, 0.4, 0.6)
    )
    assert window.cad_controller.appearances == before
    assert not session.is_dirty
    assert session.cad_view_states == {}

    assert window.cad_controller.set_object_color(
        tree.document_id, object_id, ObjectColor(0.2, 0.4, 0.6)
    )
    assert session.is_dirty
    assert session.cad_view_states
    service.save()
    assert not session.is_dirty
    assert window.cad_controller.set_object_transparency(
        tree.document_id, object_id, 0.2
    )
    assert session.is_dirty
    service.save()
    assert window.cad_controller.set_object_visibility(
        tree.document_id, object_id, False
    )
    assert session.is_dirty
    service.save()
    assert window.cad_controller.set_display_mode(DisplayMode.WIREFRAME)
    assert session.is_dirty
    service.save()
    assert window.cad_controller.set_view_direction(ViewDirection.TOP)
    assert session.is_dirty
    window.cad_controller.shutdown()
    service.close_project(discard_changes=True)


def test_save_during_isolate_uses_pre_isolate_visibility(tmp_path: Path) -> None:
    application = _application()
    source = tmp_path / "isolate.brep"
    _write_multi_solid_brep(source)
    service = ProjectService.create_default(tmp_path / "config")
    session = service.create_project_from_source(tmp_path, "Isolate Save", source)
    window = MainWindow(service, OcpCadKernel(), IntegrationViewportBackend())
    _wait_until(application, lambda: not window.cad_controller.is_busy)
    tree = window.cad_controller.active_tree
    assert tree is not None and len(tree.presentation_nodes) == 2
    first, second = tree.presentation_nodes

    assert window.cad_controller.set_object_color(
        tree.document_id, first.object_id, ObjectColor(0.3, 0.4, 0.5)
    )
    assert window.cad_controller.isolate_object(tree.document_id, first.object_id)
    assert not dict(window.cad_controller.appearances)[second.object_id].visible
    service.save()
    project_root = session.root_path
    window.cad_controller.shutdown()
    service.close_project()

    reopened_service = ProjectService.create_default(tmp_path / "reopen-config")
    reopened_service.open_project(project_root)
    reopened = MainWindow(
        reopened_service, OcpCadKernel(), IntegrationViewportBackend()
    )
    _wait_until(application, lambda: not reopened.cad_controller.is_busy)
    reopened_tree = reopened.cad_controller.active_tree
    assert reopened_tree is not None
    appearances = dict(reopened.cad_controller.appearances)
    assert all(appearances[node.object_id].visible for node in reopened_tree.presentation_nodes)
    reopened.close()


def test_stale_kind_and_foreign_source_rows_are_never_applied(
    tmp_path: Path,
    caplog,
) -> None:
    application = _application()
    source = tmp_path / "stale.brep"
    _write_brep(source)
    service = ProjectService.create_default(tmp_path / "config")
    session = service.create_project_from_source(tmp_path, "Stale State", source)
    source_id = session.manifest.source_files[0].source_id
    project_root = session.root_path
    service.close_project()
    timestamp = "2026-01-01T00:00:00Z"
    row = (1, "solid:" + "f" * 32, 0, 0.1, 0.2, 0.3, 0.4, timestamp)
    with closing(sqlite3.connect(project_root / "project.db")) as connection, connection:
        for stored_source, geometry_kind in (
            (source_id, "brep"),
            (source_id, "triangle_mesh"),
            (uuid4(), "brep"),
        ):
            connection.execute(
                "INSERT INTO cad_object_appearance VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (str(stored_source), row[0], row[1], geometry_kind, *row[2:]),
            )

    caplog.set_level("WARNING")
    reopened_service = ProjectService.create_default(tmp_path / "reopen-config")
    reopened_service.open_project(project_root)
    backend = IntegrationViewportBackend()
    window = MainWindow(reopened_service, OcpCadKernel(), backend)
    _wait_until(application, lambda: not window.cad_controller.is_busy)

    assert backend.visibility_changes == []
    assert backend.color_changes == []
    assert backend.transparency_changes == []
    assert "source_id không hợp lệ" in caplog.text
    assert "geometry_kind không khớp" in caplog.text
    assert "topology path stale/missing" in caplog.text
    window.close()


def test_restore_apply_failure_rolls_back_without_partial_state(tmp_path: Path) -> None:
    application = _application()
    source = tmp_path / "restore-failure.brep"
    _write_brep(source)
    service = ProjectService.create_default(tmp_path / "config")
    session = service.create_project_from_source(tmp_path, "Restore Failure", source)
    first_window = MainWindow(service, OcpCadKernel(), IntegrationViewportBackend())
    _wait_until(application, lambda: not first_window.cad_controller.is_busy)
    tree = first_window.cad_controller.active_tree
    assert tree is not None
    object_id = tree.presentation_nodes[0].object_id
    assert first_window.cad_controller.set_object_color(
        tree.document_id, object_id, ObjectColor(0.1, 0.2, 0.3)
    )
    assert first_window.cad_controller.set_object_transparency(
        tree.document_id, object_id, 0.45
    )
    service.save()
    project_root = session.root_path
    first_window.cad_controller.shutdown()
    service.close_project()

    reopened_service = ProjectService.create_default(tmp_path / "reopen-config")
    reopened_service.open_project(project_root)
    backend = IntegrationViewportBackend()
    backend.fail_appearance = "transparency"
    reopened = MainWindow(reopened_service, OcpCadKernel(), backend)
    _wait_until(application, lambda: not reopened.cad_controller.is_busy)

    assert all(
        appearance == ObjectAppearance()
        for _object_id, appearance in reopened.cad_controller.appearances
    )
    assert backend.color_changes[-1][2] == ObjectAppearance().color
    assert backend.transparency_changes[-1][2] == 0.0
    assert not reopened_service.is_dirty
    reopened.close()
