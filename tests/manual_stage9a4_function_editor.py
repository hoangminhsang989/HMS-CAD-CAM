"""GUI smoke and screenshot regression for Stage 9A.4 Function Editor.

The harness opens the production MainWindow, creates two real Facing operations,
uses the safe presentation-only Contour reference editor, then returns to the
legacy production editor.  It never calculates toolpaths, runs Simulation,
generates Post output or exports NC.
"""

from __future__ import annotations

import argparse
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
from hms_cadcam.ui.function_editor import (
    FunctionEditorAction,
    FunctionEditorDraftStatus,
    ParameterDisclosureLevel,
)
from hms_cadcam.ui.main_window import MainWindow
from hms_cadcam.ui.workspace_shell import WorkspaceId
from hms_cadcam.viewer.models import ViewportStatus
from hms_cadcam.viewer.ocp.backend import OcpCadViewportBackend


logger = logging.getLogger("hms.manual.stage9a4")


class _OffscreenViewportBackend:
    """No-op presentation surface while the real OCP kernel loads the BREP."""

    def __init__(self) -> None:
        self.initialized = False
        self.closed = False
        self._selection_callback = lambda _items: None

    def get_status(self) -> ViewportStatus:
        return ViewportStatus(True, self.initialized and not self.closed, "offscreen")

    def set_selection_callback(self, callback) -> None:
        self._selection_callback = callback

    def initialize(self, native_window_id: int) -> None:
        if native_window_id <= 0:
            raise RuntimeError("Qt offscreen chưa cấp window id")
        self.initialized = True

    def display_document(self, _document_id) -> None:
        return None

    def clear(self) -> None:
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
        raise RuntimeError("Không thể tạo BREP fixture Stage 9A.4")


def _build_window(service: ProjectService) -> MainWindow:
    application = QApplication.instance()
    kernel = OcpCadKernel()
    if application is not None and application.platformName() == "offscreen":
        backend = _OffscreenViewportBackend()
    else:
        backend = OcpCadViewportBackend(kernel)
    window = MainWindow(service, kernel, backend)
    window.resize(1600, 900)
    window.show()
    application.processEvents()
    return window


def _wait_until(application: QApplication, predicate, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        application.processEvents()
        if predicate():
            return
        time.sleep(0.02)
    raise TimeoutError("GUI Stage 9A.4 không đạt trạng thái sẵn sàng")


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
    if image.isNull() or image.width() < 1024 or image.height() < 680:
        raise AssertionError(f"Screenshot Stage 9A.4 không hợp lệ: {path}")
    return path


def _operations(service: ProjectService):
    return tuple(
        operation
        for job in service.cam_snapshot.jobs
        for setup in job.setups
        for operation in setup.operation_tree.operations
    )


def _select_operation(window: MainWindow, operation) -> None:
    window.cam_workspace.refresh(("operation", str(operation.node_id)))
    QApplication.processEvents()
    if window.cam_workspace._selected_key != (
        "operation",
        str(operation.node_id),
    ):
        raise AssertionError("Operation switch không giữ stable domain identity")


def run(output_dir: Path, workspace_root: Path) -> tuple[Path, ...]:
    """Run the required reference/legacy/lifecycle flow and return screenshots."""
    output_dir.mkdir(parents=True, exist_ok=True)
    application = QApplication.instance() or QApplication([])
    application.setQuitOnLastWindowClosed(False)
    service = ProjectService.create_default(workspace_root / "config")
    source = workspace_root / "stage9a4-box.brep"
    _write_source(source)
    service.create_project_from_source(
        workspace_root, "Stage9A4 Function Editor", source
    )
    window = _build_window(service)
    captures: list[Path] = []
    try:
        _wait_until(application, lambda: not window.cad_controller.is_busy)
        if window.cad_controller.active_source_id is None:
            raise AssertionError("OCP chưa load CAD source cho fixture Stage 9A.4")
        window._workspace_changed(WorkspaceId.MILL_2D.value)
        workspace = window.cam_workspace
        workspace.create_job()
        workspace.create_setup()
        workspace.create_basic_resources()
        workspace.add_operation()
        workspace.add_operation()
        operations = _operations(service)
        if len(operations) != 2:
            raise AssertionError("Fixture Stage 9A.4 phải có hai operation")
        _select_operation(window, operations[0])
        service.save()
        dirty_before_reference = service.current_project.is_dirty
        statuses_before = tuple(item.artifact_state.status for item in operations)
        post_before = service.post_service.results()
        nc_before = service.nc_export_service.artifacts()

        window.function_editor_dock.show()
        window.function_editor_dock.raise_()
        window.resizeDocks(
            [window.function_editor_dock], [420], Qt.Orientation.Horizontal
        )
        application.processEvents()
        captures.append(
            _capture(application, window, output_dir, "legacy_editor_adapter")
        )

        page = window.function_editor_host.show_reference_editor(
            close_confirmation=lambda _state: True
        )
        application.processEvents()
        if page.state.status is not FunctionEditorDraftStatus.NO_CHANGES:
            raise AssertionError("Reference editor không bắt đầu từ applied snapshot")
        captures.append(_capture(application, window, output_dir, "basic_editor"))

        page.disclosure_selector.setCurrentIndex(
            page.disclosure_selector.findData(ParameterDisclosureLevel.ADVANCED)
        )
        application.processEvents()
        if page._section_widgets["advanced"].is_expanded:
            raise AssertionError("Advanced không collapsed mặc định")
        page.scroll_area.ensureWidgetVisible(page._section_widgets["advanced"])
        page.scroll_area.verticalScrollBar().setValue(
            page.scroll_area.verticalScrollBar().maximum()
        )
        captures.append(
            _capture(application, window, output_dir, "advanced_collapsed")
        )
        page._section_widgets["advanced"].set_expanded(True)
        page.scroll_area.ensureWidgetVisible(page._section_widgets["advanced"])
        page.scroll_area.verticalScrollBar().setValue(
            page.scroll_area.verticalScrollBar().maximum()
        )
        captures.append(
            _capture(application, window, output_dir, "advanced_expanded")
        )

        page.disclosure_selector.setCurrentIndex(
            page.disclosure_selector.findData(ParameterDisclosureLevel.EXPERT)
        )
        page._section_widgets["expert"].set_expanded(True)
        page.scroll_area.ensureWidgetVisible(page._section_widgets["expert"])
        page.scroll_area.verticalScrollBar().setValue(
            page.scroll_area.verticalScrollBar().maximum()
        )
        captures.append(_capture(application, window, output_dir, "expert_expanded"))

        page.disclosure_selector.setCurrentIndex(
            page.disclosure_selector.findData(ParameterDisclosureLevel.BASIC)
        )
        page.scroll_area.ensureWidgetVisible(page._field_widgets["safe_z"])
        captures.append(
            _capture(application, window, output_dir, "field_source_default")
        )

        page._field_changed("stepdown", "0")
        diagnostics = page.validate_draft()
        if not diagnostics or diagnostics[0].field_id != "stepdown":
            raise AssertionError("Validate không focus/ghi inline field lỗi đầu tiên")
        captures.append(_capture(application, window, output_dir, "inline_error"))
        page.reset_draft()
        if page.state.is_dirty:
            raise AssertionError("Reset Draft không về applied snapshot")

        page._field_changed("feed_rate", "650")
        page.footer.buttons[FunctionEditorAction.PREVIEW].click()
        page.footer.buttons[FunctionEditorAction.APPLY].click()
        if page.state.status is not FunctionEditorDraftStatus.APPLIED:
            raise AssertionError("Safe reference Apply không hoàn thành")
        if service.current_project.is_dirty is not dirty_before_reference:
            raise AssertionError("Reference Apply đã làm project dirty")

        page._show_editor_help()
        page.scroll_area.ensureWidgetVisible(page.help_panel)
        captures.append(
            _capture(application, window, output_dir, "diagnostics_help_panel")
        )

        window.resizeDocks(
            [window.function_editor_dock], [300], Qt.Orientation.Horizontal
        )
        application.processEvents()
        if not 300 <= window.function_editor_dock.width() <= 330:
            raise AssertionError(
                "Function Editor dock không đạt narrow policy 300 px: "
                f"{window.function_editor_dock.width()}"
            )
        if page.scroll_area.horizontalScrollBar().maximum() != 0:
            raise AssertionError("Reference editor tạo horizontal scroll ở 300 px")
        captures.append(_capture(application, window, output_dir, "narrow_300"))
        window.resizeDocks(
            [window.function_editor_dock], [420], Qt.Orientation.Horizontal
        )
        application.processEvents()
        captures.append(_capture(application, window, output_dir, "normal_420"))

        _select_operation(window, operations[1])
        if window.function_editor_host.current_mode != "legacy":
            raise AssertionError("Operation switch không cleanup reference editor")
        if page.state.status is not FunctionEditorDraftStatus.STALE:
            raise AssertionError("Callback/draft cũ không bị stale khi switch")

        current = _operations(service)
        if tuple(item.artifact_state.status for item in current) != statuses_before:
            raise AssertionError("Function Editor đã tự Calculate toolpath")
        if any(item.artifact_state.status is not ArtifactStatus.MISSING for item in current):
            raise AssertionError("Fixture phải giữ Toolpath MISSING")
        if service.post_service.results() != post_before:
            raise AssertionError("Function Editor đã tự Generate Post")
        if service.nc_export_service.artifacts() != nc_before:
            raise AssertionError("Function Editor đã tự Export NC")

        service.close_project()
        window._handle_project_change(None)
        switched = service.new_project(workspace_root, "Stage9A4 Project Switch")
        window._handle_project_change(switched)
        application.processEvents()
        if window.function_editor_host.current_mode != "legacy":
            raise AssertionError("Project switch giữ framework callback cũ")
        if workspace.tree.currentItem() is not None or workspace._selected_key is not None:
            raise AssertionError("Project switch giữ operation selection cũ")
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
        "basic_editor.png",
        "advanced_collapsed.png",
        "advanced_expanded.png",
        "expert_expanded.png",
        "inline_error.png",
        "field_source_default.png",
        "narrow_300.png",
        "normal_420.png",
        "legacy_editor_adapter.png",
        "diagnostics_help_panel.png",
    }
    if {item.name for item in captures} != expected:
        raise AssertionError("Thiếu screenshot Stage 9A.4 bắt buộc")
    if any(not item.is_file() or item.stat().st_size == 0 for item in captures):
        raise AssertionError("Có screenshot Stage 9A.4 rỗng")
    return tuple(captures)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reference_private/DERIVED/UI_STAGE_9A4"),
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
        with tempfile.TemporaryDirectory(prefix="hms_stage9a4_") as raw:
            captures = run(args.output_dir.resolve(), Path(raw))
    logger.info(
        "Stage 9A.4 GUI smoke đạt: %d screenshot tại %s",
        len(captures),
        args.output_dir,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
