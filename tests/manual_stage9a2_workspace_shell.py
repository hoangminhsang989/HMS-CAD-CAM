"""Automated GUI and screenshot smoke for Stage 9A.2 workspace shell.

Run with ``QT_QPA_PLATFORM=offscreen`` for unattended verification or without
that variable to exercise the real Windows Qt platform plugin. The script never
calculates a toolpath, generates Post output or exports NC data.
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import tempfile
import time
from pathlib import Path

from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.BRepTools import BRepTools
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from hms_cadcam.cam.domain import ArtifactStatus
from hms_cadcam.cad.ocp import OcpCadKernel
from hms_cadcam.project.service import ProjectService
from hms_cadcam.ui.main_window import MainWindow
from hms_cadcam.ui.workspace_shell import WorkspaceId
from hms_cadcam.viewer.models import ViewportStatus
from hms_cadcam.viewer.ocp.backend import OcpCadViewportBackend

logger = logging.getLogger("hms.manual.stage9a2")


class _OffscreenViewportBackend:
    """No-op presentation surface; OCP kernel still owns/imports the CAD model."""

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

    def set_object_transparency(
        self, _document_id, _object_id, _transparency
    ) -> None:
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
        raise RuntimeError("Không thể tạo BREP fixture Stage 9A.2")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _wait_until(
    application: QApplication,
    predicate,
    *,
    timeout: float = 20.0,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        application.processEvents()
        if predicate():
            return
        time.sleep(0.02)
    raise TimeoutError("GUI Stage 9A.2 không đạt trạng thái sẵn sàng")


def _operation(service: ProjectService):
    return next(
        operation
        for job in service.cam_snapshot.jobs
        for setup in job.setups
        for operation in setup.operation_tree.operations
    )


def _capture(
    application: QApplication,
    window: MainWindow,
    output_dir: Path,
    name: str,
    expected_size: tuple[int, int] | None = None,
) -> Path:
    application.processEvents()
    path = output_dir / f"{name}.png"
    if not window.grab().save(str(path)):
        raise RuntimeError(f"Không thể lưu screenshot {path}")
    image = QImage(str(path))
    if image.isNull():
        raise RuntimeError(f"Screenshot không đọc lại được: {path}")
    if expected_size is not None and image.size().toTuple() != expected_size:
        raise AssertionError(
            f"Sai kích thước {path.name}: {image.size().toTuple()} != {expected_size}"
        )
    return path


def _build_window(service: ProjectService) -> MainWindow:
    kernel = OcpCadKernel()
    application = QApplication.instance()
    if application is not None and application.platformName() == "offscreen":
        backend = _OffscreenViewportBackend()
    else:
        backend = OcpCadViewportBackend(kernel)
    window = MainWindow(service, kernel, backend)
    window.show()
    return window


def run(output_dir: Path, workspace_root: Path) -> tuple[Path, ...]:
    """Run the complete shell smoke and return every generated screenshot."""
    output_dir.mkdir(parents=True, exist_ok=True)
    source = workspace_root / "stage9a2-box.brep"
    _write_source(source)
    source_sha256 = _sha256(source)
    config_dir = workspace_root / "config"
    service = ProjectService.create_default(config_dir)
    session = service.create_project_from_source(
        workspace_root, "Stage9A2 Workspace", source
    )
    project_root = session.root_path
    source_record = session.manifest.source_files[0]
    managed_source = session.root_path / source_record.stored_path
    if source_record.sha256 != source_sha256 or _sha256(managed_source) != source_sha256:
        raise AssertionError("Source copy ban đầu không giữ đúng SHA-256")
    window = _build_window(service)
    captures: list[Path] = []
    try:
        _wait_until(QApplication.instance(), lambda: not window.cad_controller.is_busy)
        if window.cad_controller.active_tree is None:
            raise AssertionError("CAD/BREP chưa được load vào OCP viewer")

        workspace = window.cam_workspace
        window._workspace_changed(WorkspaceId.MILL_2D.value)
        workspace.create_job()
        workspace.create_setup()
        workspace.create_basic_resources()
        workspace.add_operation()
        operation = _operation(service)
        workspace.refresh(("operation", str(operation.node_id)))
        if workspace._selected_key != ("operation", str(operation.node_id)):
            raise AssertionError("Operation selection không giữ typed identity")

        artifact_status_before = operation.artifact_state.status
        post_result_before = workspace.post_panel._result
        exports_before = tuple(service.nc_export_service.artifacts())

        for width, height in ((1366, 768), (1600, 900), (1920, 1080)):
            window.resize(width, height)
            window.operation_manager_dock.show()
            window.operation_manager_dock.raise_()
            window.function_editor_dock.show()
            window.function_editor_dock.raise_()
            window.output_dock.show()
            QApplication.processEvents()
            if window.viewport.width() < 520 or window.viewport.height() < 360:
                raise AssertionError(
                    f"Viewport quá nhỏ tại {width}x{height}: {window.viewport.size()}"
                )
            captures.append(
                _capture(
                    QApplication.instance(),
                    window,
                    output_dir,
                    f"workspace_{width}x{height}",
                    (width, height),
                )
            )

        window.resize(1600, 900)
        window.operation_manager_dock.hide()
        captures.append(
            _capture(
                QApplication.instance(), window, output_dir, "operation_manager_collapsed"
            )
        )
        window.operation_manager_dock.show()
        window.operation_manager_dock.raise_()

        window.function_editor_dock.hide()
        captures.append(
            _capture(
                QApplication.instance(), window, output_dir, "function_editor_collapsed"
            )
        )
        window.function_editor_dock.show()
        window.function_editor_dock.raise_()

        window.output_dock.show()
        window.resizeDocks(
            [window.output_dock], [220], Qt.Orientation.Vertical
        )
        captures.append(
            _capture(
                QApplication.instance(), window, output_dir, "diagnostics_expanded"
            )
        )

        workspace.refresh(("operation", str(operation.node_id)))
        captures.append(
            _capture(
                QApplication.instance(), window, output_dir, "cam_operation_selected"
            )
        )

        window._workspace_changed(WorkspaceId.POST.value)
        workspace.post_tabs.setCurrentWidget(workspace.post_panel)
        captures.append(
            _capture(QApplication.instance(), window, output_dir, "post_panel_selected")
        )

        current_operation = _operation(service)
        if current_operation.artifact_state.status is not artifact_status_before:
            raise AssertionError("Shell đã tự Calculate ngoài yêu cầu")
        if current_operation.artifact_state.status is not ArtifactStatus.MISSING:
            raise AssertionError("Operation fixture phải giữ trạng thái MISSING")
        if workspace.post_panel._result is not post_result_before:
            raise AssertionError("Shell đã tự Generate Post")
        if tuple(service.nc_export_service.artifacts()) != exports_before:
            raise AssertionError("Shell đã tự Export NC")

        service.save()
        service.close_project()
        window._handle_project_change(None)
        switched = service.new_project(workspace_root, "Stage9A2 Switch")
        window._handle_project_change(switched)
        if workspace.tree.currentItem() is not None or workspace._selected_key is not None:
            raise AssertionError("Project switch giữ selection cũ")
        service.save()
        if not window.close():
            raise AssertionError("MainWindow không đóng sạch")
        QApplication.processEvents()

        reopened_service = ProjectService.create_default(config_dir)
        reopened_service.open_project(project_root)
        reopened = _build_window(reopened_service)
        try:
            _wait_until(
                QApplication.instance(), lambda: not reopened.cad_controller.is_busy
            )
            if reopened.workspace_bar.active_workspace is not WorkspaceId.POST:
                raise AssertionError("Active workspace không được restore")
            reopened_operation = _operation(reopened_service)
            if reopened_operation.artifact_state.status is not ArtifactStatus.MISSING:
                raise AssertionError("Close/reopen đã tự Calculate")
            if reopened.cam_workspace.post_panel._result is not None:
                raise AssertionError("Close/reopen đã tự Generate Post")
            if tuple(reopened_service.nc_export_service.artifacts()):
                raise AssertionError("Close/reopen đã tự Export")
            reopened.reset_workspace_layout()
            if reopened.workspace_bar.active_workspace is not WorkspaceId.HOME:
                raise AssertionError("Reset Workspace Layout không về HOME")
            if not reopened.close():
                raise AssertionError("Cửa sổ reopen không đóng sạch")
            QApplication.processEvents()
        finally:
            if reopened.isVisible():
                reopened.cad_controller.shutdown()
                reopened.viewport.shutdown()
                reopened.hide()
    finally:
        if window.isVisible():
            window.cad_controller.shutdown()
            window.viewport.shutdown()
            window.hide()

    expected_names = {
        "workspace_1366x768.png",
        "workspace_1600x900.png",
        "workspace_1920x1080.png",
        "operation_manager_collapsed.png",
        "function_editor_collapsed.png",
        "diagnostics_expanded.png",
        "cam_operation_selected.png",
        "post_panel_selected.png",
    }
    if {path.name for path in captures} != expected_names:
        raise AssertionError("Thiếu screenshot Stage 9A.2 bắt buộc")
    if any(not path.is_file() or path.stat().st_size == 0 for path in captures):
        raise AssertionError("Screenshot Stage 9A.2 rỗng hoặc không tồn tại")
    if _sha256(source) != source_sha256 or _sha256(managed_source) != source_sha256:
        raise AssertionError("Stage 9A.2 đã mutation CAD source")
    return tuple(captures)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reference_private/DERIVED/UI_STAGE_9A2"),
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
        with tempfile.TemporaryDirectory(prefix="hms_stage9a2_") as raw:
            captures = run(args.output_dir.resolve(), Path(raw))
    logger.info("Stage 9A.2 GUI smoke đạt: %d screenshot tại %s", len(captures), args.output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
