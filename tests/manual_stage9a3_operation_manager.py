"""GUI smoke and screenshot regression for Stage 9A.3 Operation Manager.

The harness uses real project/CAM services and an OCP-created immutable BREP
source.  It does not calculate toolpaths, run Simulation, generate Post or
export NC.  Delete is exercised only after explicit scripted confirmation.
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import tempfile
import time
from dataclasses import replace
from pathlib import Path

from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.BRepTools import BRepTools
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QMessageBox

from hms_cadcam.cam.domain import (
    ArtifactState,
    ArtifactStatus,
    CamNodeId,
    DiagnosticCode,
    DiagnosticSeverity,
    OperationId,
    ValidationDiagnostic,
)
from hms_cadcam.cad.ocp import OcpCadKernel
from hms_cadcam.project.service import ProjectService
from hms_cadcam.ui.main_window import MainWindow
from hms_cadcam.ui.operation_manager_types import (
    OperationManagerFilter,
    OperationManagerNodeKind,
)
from hms_cadcam.ui.workspace_shell import WorkspaceId
from hms_cadcam.viewer.models import ViewportStatus
from hms_cadcam.viewer.ocp.backend import OcpCadViewportBackend


logger = logging.getLogger("hms.manual.stage9a3")


class _OffscreenViewportBackend:
    """No-op presentation surface while OCP still imports the real BREP."""

    def __init__(self) -> None:
        self.initialized = False
        self.closed = False
        self.current_document = None
        self._selection_callback = lambda _items: None

    def get_status(self) -> ViewportStatus:
        return ViewportStatus(True, self.initialized and not self.closed, "offscreen")

    def set_selection_callback(self, callback) -> None:
        self._selection_callback = callback

    def initialize(self, native_window_id: int) -> None:
        if native_window_id <= 0:
            raise RuntimeError("Qt offscreen chưa cấp window id")
        self.initialized = True

    def display_document(self, document_id) -> None:
        self.current_document = document_id

    def clear(self) -> None:
        self.current_document = None
        self._selection_callback(())

    def fit_all(self) -> None:
        return None

    def set_view_direction(self, _direction) -> None:
        return None

    def set_display_mode(self, _mode) -> None:
        return None

    def set_selection_mode(self, _mode) -> None:
        return None

    def select_objects(self, _document_id, _object_ids) -> None:
        return None

    def set_object_visibility(self, _document_id, _object_id, _visible) -> None:
        return None

    def isolate_object(self, _document_id, _object_id) -> None:
        return None

    def reset_isolate(self, _document_id) -> None:
        return None

    def set_object_color(self, _document_id, _object_id, _color) -> None:
        return None

    def set_object_transparency(self, _document_id, _object_id, _value) -> None:
        return None

    def reset_object_appearance(self, _document_id, _object_id) -> None:
        return None

    def resize(self, _width: int, _height: int) -> None:
        return None

    def handle_mouse_press(self, *_args) -> None:
        return None

    def handle_mouse_move(self, *_args) -> None:
        return None

    def handle_mouse_release(self, *_args) -> None:
        return None

    def handle_wheel(self, *_args) -> None:
        return None

    def close(self) -> None:
        self.closed = True


def _write_source(path: Path) -> None:
    shape = BRepPrimAPI_MakeBox(120.0, 80.0, 35.0).Shape()
    if not BRepTools.Write_s(shape, str(path)):
        raise RuntimeError("Không thể tạo BREP fixture Stage 9A.3")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _wait_until(application: QApplication, predicate, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        application.processEvents()
        if predicate():
            return
        time.sleep(0.02)
    raise TimeoutError("GUI Stage 9A.3 không đạt trạng thái sẵn sàng")


def _build_window(service: ProjectService) -> MainWindow:
    kernel = OcpCadKernel()
    application = QApplication.instance()
    backend = (
        _OffscreenViewportBackend()
        if application is not None and application.platformName() == "offscreen"
        else OcpCadViewportBackend(kernel)
    )
    window = MainWindow(service, kernel, backend)
    window.resize(1600, 900)
    window.show()
    return window


def _capture(
    application: QApplication,
    window: MainWindow,
    output_dir: Path,
    name: str,
) -> Path:
    application.processEvents()
    path = output_dir / f"{name}.png"
    if not window.grab().save(str(path)):
        raise RuntimeError(f"Không thể lưu screenshot {path}")
    image = QImage(str(path))
    if image.isNull() or image.width() < 260 or image.height() < 360:
        raise AssertionError(f"Screenshot không hợp lệ: {path}")
    return path


def _capture_context_menu(application, window, menu, output_dir) -> Path:
    menu.popup(window.operation_manager_host.view.mapToGlobal(QPoint(40, 180)))
    application.processEvents()
    path = output_dir / "operation_context_menu.png"
    screen = window.screen()
    captured = screen.grabWindow(0) if screen is not None else menu.grab()
    if captured.isNull() or not captured.save(str(path)):
        if not menu.grab().save(str(path)):
            raise RuntimeError("Không thể chụp context menu Operation Manager")
    menu.close()
    image = QImage(str(path))
    if image.isNull():
        raise AssertionError("Context menu screenshot rỗng")
    return path


def _nodes(panel, kind):
    return tuple(item for item in panel.model.projection.nodes if item.kind is kind)


def _prepare_mixed_operations(service: ProjectService) -> None:
    snapshot = service.cam_snapshot
    job, setup = snapshot.jobs[0], snapshot.jobs[0].setups[0]
    base = setup.operation_tree.operations[0]

    def command(app):
        def mutate(tree):
            changed = tree.rename_node(base.node_id, "Facing Current Draft")
            states = (
                (
                    "Facing Warning",
                    ArtifactState(),
                    True,
                    (
                        ValidationDiagnostic(
                            DiagnosticSeverity.WARNING,
                            DiagnosticCode.FACING_UNSAFE_CLEARANCE,
                            "Fixture Stage 9A.3 cảnh báo clearance.",
                        ),
                    ),
                ),
                (
                    "Facing Stale",
                    ArtifactState(status=ArtifactStatus.DIRTY),
                    True,
                    (),
                ),
                (
                    "Facing Failed",
                    ArtifactState(status=ArtifactStatus.FAILED),
                    True,
                    (),
                ),
                ("Facing Disabled", ArtifactState(), False, ()),
                ("Facing Draft 1", ArtifactState(), True, ()),
                ("Facing Draft 2", ArtifactState(), True, ()),
            )
            for name, artifact_state, enabled, diagnostics in states:
                operation = replace(
                    base,
                    operation_id=OperationId.new(),
                    node_id=CamNodeId.new(),
                    enabled=enabled,
                    artifact_state=artifact_state,
                    diagnostics=diagnostics,
                )
                changed = changed.add_operation(changed.root_id, name, operation)
            return changed

        return app.update_tree(job.job_id, setup.setup_id, mutate)

    service.execute_cam_command(command)


def run(output_dir: Path, workspace_root: Path) -> tuple[Path, ...]:
    """Exercise required empty/mixed/filter/lifecycle states and capture them."""
    output_dir.mkdir(parents=True, exist_ok=True)
    source = workspace_root / "stage9a3-box.brep"
    _write_source(source)
    source_hash = _sha256(source)
    service = ProjectService.create_default(workspace_root / "config")
    application = QApplication.instance() or QApplication([])
    application.setQuitOnLastWindowClosed(False)
    window = _build_window(service)
    captures: list[Path] = []
    try:
        captures.append(_capture(application, window, output_dir, "no_project"))

        session = service.create_project_from_source(
            workspace_root, "Stage9A3 Operation Manager", source
        )
        project_root = session.root_path
        managed_source = project_root / session.manifest.source_files[0].stored_path
        window._handle_project_change(session)
        _wait_until(application, lambda: not window.cad_controller.is_busy)
        window._workspace_changed(WorkspaceId.MILL_2D.value)
        captures.append(_capture(application, window, output_dir, "cad_only_project"))

        workspace = window.cam_workspace
        workspace.create_job()
        workspace.create_setup()
        workspace.create_basic_resources()
        captures.append(_capture(application, window, output_dir, "empty_setup"))

        workspace.add_operation()
        _prepare_mixed_operations(service)
        workspace.refresh()
        panel = window.operation_manager_host
        if len(_nodes(panel, OperationManagerNodeKind.OPERATION)) != 7:
            raise AssertionError("Fixture Stage 9A.3 phải có đúng 7 operation")
        captures.append(_capture(application, window, output_dir, "mixed_status"))

        operation = _nodes(panel, OperationManagerNodeKind.OPERATION)[1]
        operation_index = panel.model.index_for_node_id(operation.node_id)
        panel.view.setCurrentIndex(operation_index)
        panel.view.setExpanded(operation_index, True)
        captures.append(
            _capture(application, window, output_dir, "selected_operation_expanded")
        )

        panel.search.setText("Facing Stale")
        captures.append(_capture(application, window, output_dir, "search_result"))
        panel.search.clear()
        panel.filter.setCurrentIndex(panel.filter.findData(OperationManagerFilter.STALE))
        captures.append(_capture(application, window, output_dir, "filter_stale"))
        panel.filter.setCurrentIndex(
            panel.filter.findData(OperationManagerFilter.DISABLED)
        )
        captures.append(_capture(application, window, output_dir, "filter_disabled"))
        panel.filter.setCurrentIndex(
            panel.filter.findData(OperationManagerFilter.WARNINGS)
        )
        captures.append(_capture(application, window, output_dir, "filter_warning"))
        panel.filter.setCurrentIndex(
            panel.filter.findData(OperationManagerFilter.ERRORS)
        )
        captures.append(_capture(application, window, output_dir, "filter_error"))
        panel.filter.setCurrentIndex(panel.filter.findData(OperationManagerFilter.ALL))

        operation_index = panel.model.index_for_node_id(operation.node_id)
        panel.view.setCurrentIndex(operation_index)
        menu = panel.commands.context_menu(panel.view)
        captures.append(
            _capture_context_menu(application, window, menu, output_dir)
        )
        window.resize(1366, 768)
        captures.append(
            _capture(application, window, output_dir, "operation_manager_1366x768")
        )
        window.resize(1920, 1080)
        captures.append(
            _capture(application, window, output_dir, "operation_manager_1920x1080")
        )
        window.operation_manager_dock.hide()
        application.processEvents()
        if window.operation_manager_dock.isVisible():
            raise AssertionError("Operation Manager không thu gọn")
        captures.append(
            _capture(application, window, output_dir, "operation_manager_collapsed")
        )
        window.operation_manager_dock.show()
        window.operation_manager_dock.raise_()
        application.processEvents()
        if not window.operation_manager_dock.isVisible():
            raise AssertionError("Operation Manager không khôi phục")
        captures.append(
            _capture(application, window, output_dir, "operation_manager_restored")
        )

        statuses_before = tuple(
            item.artifact_state.status
            for job in service.cam_snapshot.jobs
            for setup in job.setups
            for item in setup.operation_tree.operations
        )
        post_before = service.post_service.results()
        nc_before = service.nc_export_service.artifacts()
        if tuple(
            item.artifact_state.status
            for job in service.cam_snapshot.jobs
            for setup in job.setups
            for item in setup.operation_tree.operations
        ) != statuses_before:
            raise AssertionError("Search/filter/selection đã tự tính hoặc đổi operation")
        if service.post_service.results() != post_before:
            raise AssertionError("Operation Manager đã tự Generate Post")
        if service.nc_export_service.artifacts() != nc_before:
            raise AssertionError("Operation Manager đã tự Export NC")

        original_question = QMessageBox.question
        try:
            QMessageBox.question = staticmethod(
                lambda *_args, **_kwargs: QMessageBox.StandardButton.No
            )
            count_before = len(_nodes(panel, OperationManagerNodeKind.OPERATION))
            panel.commands.delete.trigger()
            if len(_nodes(panel, OperationManagerNodeKind.OPERATION)) != count_before:
                raise AssertionError("Delete đã bỏ qua xác nhận No")
            QMessageBox.question = staticmethod(
                lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes
            )
            panel.commands.delete.trigger()
            application.processEvents()
            if len(_nodes(panel, OperationManagerNodeKind.OPERATION)) != count_before - 1:
                raise AssertionError("Delete xác nhận không xóa đúng một operation")
        finally:
            QMessageBox.question = original_question

        operation_count_before_reopen = len(
            _nodes(panel, OperationManagerNodeKind.OPERATION)
        )
        project_id_before_reopen = service.current_project.manifest.project_id
        service.save()
        service.close_project()
        window._handle_project_change(None)
        reopened = service.open_project(project_root)
        window._handle_project_change(reopened)
        _wait_until(application, lambda: not window.cad_controller.is_busy)
        if reopened.manifest.project_id != project_id_before_reopen:
            raise AssertionError("Open đã đổi project identity")
        if len(_nodes(panel, OperationManagerNodeKind.OPERATION)) != operation_count_before_reopen:
            raise AssertionError("Save/Open không giữ hierarchy operation")

        service.close_project()
        window._handle_project_change(None)
        switched = service.new_project(workspace_root, "Stage9A3 Project Switch")
        window._handle_project_change(switched)
        if panel.current_node() is not None and panel.current_node().node_id == operation.node_id:
            raise AssertionError("Project switch giữ selection operation cũ")
        captures.append(_capture(application, window, output_dir, "project_switch"))

        if statuses_before[:1] != (ArtifactStatus.MISSING,):
            raise AssertionError("Fixture trạng thái ban đầu không đúng")
        if post_before or nc_before:
            raise AssertionError("Presentation đã tự tạo Post/NC")
        if _sha256(source) != source_hash or _sha256(managed_source) != source_hash:
            raise AssertionError("Stage 9A.3 đã mutation CAD source")
        service.save()
        if not window.close():
            raise AssertionError("MainWindow không đóng sạch")
        application.processEvents()
    finally:
        if window.isVisible():
            window.cad_controller.shutdown()
            window.viewport.shutdown()
            window.hide()
        if service.has_project:
            service.close_project(discard_changes=True)

    expected = {
        "no_project.png",
        "cad_only_project.png",
        "empty_setup.png",
        "mixed_status.png",
        "selected_operation_expanded.png",
        "search_result.png",
        "filter_stale.png",
        "filter_disabled.png",
        "filter_warning.png",
        "filter_error.png",
        "operation_context_menu.png",
        "operation_manager_1366x768.png",
        "operation_manager_1920x1080.png",
        "operation_manager_collapsed.png",
        "operation_manager_restored.png",
        "project_switch.png",
    }
    if {item.name for item in captures} != expected:
        raise AssertionError("Thiếu screenshot Stage 9A.3 bắt buộc")
    if any(not item.is_file() or item.stat().st_size == 0 for item in captures):
        raise AssertionError("Có screenshot Stage 9A.3 rỗng")
    return tuple(captures)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reference_private/DERIVED/UI_STAGE_9A3"),
    )
    parser.add_argument("--workspace", type=Path)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    application = QApplication.instance() or QApplication([])
    application.setQuitOnLastWindowClosed(False)
    if args.workspace is not None:
        args.workspace.mkdir(parents=True, exist_ok=True)
        captures = run(args.output_dir.resolve(), args.workspace.resolve())
    else:
        with tempfile.TemporaryDirectory(prefix="hms_stage9a3_") as raw:
            captures = run(args.output_dir.resolve(), Path(raw))
    logger.info(
        "Stage 9A.3 GUI smoke đạt: %d screenshot tại %s",
        len(captures),
        args.output_dir,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
