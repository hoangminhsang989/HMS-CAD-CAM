"""Stage 4E integration tests for MainWindow, CAD import and project lifecycle."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pytest

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
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QMenu, QToolBar, QToolButton  # noqa: E402

from hms_cadcam.cad.exceptions import CadDocumentNotFoundError  # noqa: E402
from hms_cadcam.cad.kernel import CadKernel  # noqa: E402
from hms_cadcam.cad.models import (  # noqa: E402
    BoundingBox,
    CadDocumentId,
    CadDocumentMetadata,
    CadFormat,
    CadImportResult,
    CadKernelStatus,
    TopologyCounts,
)
from hms_cadcam.cad.ocp import OcpCadKernel  # noqa: E402
from hms_cadcam.project.models import UnitSystem  # noqa: E402
from hms_cadcam.project.service import ProjectService  # noqa: E402
from hms_cadcam.ui.cad_worker import CadImportTask  # noqa: E402
from hms_cadcam.ui.main_window import MainWindow  # noqa: E402
from hms_cadcam.viewer.models import (  # noqa: E402
    DisplayMode,
    KeyboardModifier,
    MouseButton,
    SelectionMetadata,
    SelectionMode,
    ViewDirection,
    ViewportStatus,
)


class IntegrationViewportBackend:
    """Offscreen backend that records presentation and action routing."""

    def __init__(self) -> None:
        self.callback = lambda _items: None
        self.initialized = False
        self.closed = False
        self.current_document: CadDocumentId | None = None
        self.display_history: list[CadDocumentId] = []
        self.clear_count = 0
        self.view_directions: list[ViewDirection] = []
        self.display_modes: list[DisplayMode] = []
        self.selection_modes: list[SelectionMode] = []

    def get_status(self) -> ViewportStatus:
        return ViewportStatus(True, self.initialized and not self.closed, "mock")

    def set_selection_callback(self, callback) -> None:
        self.callback = callback

    def initialize(self, native_window_id: int) -> None:
        assert native_window_id > 0
        self.initialized = True

    def display_document(self, document_id: CadDocumentId) -> None:
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
    window.cad_controller.start_import(broken, CadFormat.STEP)
    _wait_until(application, lambda: not window.cad_controller.is_busy)
    assert window.cad_controller.active_document_id == current
    assert backend.current_document == current
    assert window._import_status.text() == "CAD: Lỗi"
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
    assert "CAD document" in tree_text
    assert "Topology" in tree_text
    assert "Bounding box" in tree_text
    metadata = window.cad_controller.active_metadata
    assert metadata is not None
    document_properties = {
        window._properties_table.item(row, 0).text(): (
            window._properties_table.item(row, 1).text()
        )
        for row in range(window._properties_table.rowCount())
    }
    assert document_properties["Định dạng"] == "BREP"
    assert "Bounding box" in document_properties
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
    assert properties["Loại topology"] == "FACE"
    assert properties["Selection ID"] == item.selection_id
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
