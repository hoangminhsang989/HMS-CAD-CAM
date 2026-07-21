"""GUI acceptance harness for Stage 9A.5.2 production 2D Contour editor."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
import tempfile
import time

from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.BRepTools import BRepTools
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from hms_cadcam.cam.domain import ArtifactStatus, GeometryResolutionStatus, Operation
from hms_cadcam.cad.ocp import OcpCadKernel
from hms_cadcam.project.service import ProjectService
from hms_cadcam.ui.function_editor import (
    FunctionEditorAction,
    FunctionEditorDraftStatus,
    ParameterDisclosureLevel,
)
from hms_cadcam.ui.main_window import MainWindow
from hms_cadcam.ui.workspace_shell import WorkspaceId
from hms_cadcam.viewer.models import SelectionMetadata, SelectionMode, ViewportStatus
from hms_cadcam.viewer.ocp.backend import OcpCadViewportBackend


logger = logging.getLogger("hms.manual.stage9a52")


class _OffscreenViewportBackend:
    """No-op viewport surface while the real OCP kernel owns CAD geometry."""

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
        raise RuntimeError("Không thể tạo BREP fixture Stage 9A.5.2")


def _build_window(service: ProjectService) -> MainWindow:
    application = QApplication.instance()
    kernel = OcpCadKernel()
    backend = (
        _OffscreenViewportBackend()
        if application is not None and application.platformName() == "offscreen"
        else OcpCadViewportBackend(kernel)
    )
    window = MainWindow(service, kernel, backend)
    window.resize(1600, 900)
    window.show()
    application.processEvents()
    return window


def _wait_until(application: QApplication, predicate, timeout: float = 25.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        application.processEvents()
        if predicate():
            return
        time.sleep(0.02)
    raise TimeoutError("GUI Stage 9A.5.2 không đạt trạng thái sẵn sàng")


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
        raise AssertionError(f"Screenshot Stage 9A.5.2 không hợp lệ: {path}")
    return path


def _operations(service: ProjectService) -> tuple[Operation, ...]:
    return tuple(
        operation
        for job in service.cam_snapshot.jobs
        for setup in job.setups
        for operation in setup.operation_tree.operations
    )


def _select_operation(window: MainWindow, operation: Operation) -> None:
    window.cam_workspace.refresh(("operation", str(operation.node_id)))
    QApplication.processEvents()
    if window.cam_workspace._selected_key != ("operation", str(operation.node_id)):
        raise AssertionError("Operation Manager không giữ stable operation identity")


def _set_face_selection(window: MainWindow, face_index: int) -> None:
    controller = window.cad_controller
    mapping = controller.persistent_object_map
    document_id = controller.active_document_id
    metadata = controller.active_metadata
    if mapping is None or document_id is None or metadata is None:
        raise AssertionError("CAD selection context chưa sẵn sàng")
    controller._active_selection = (
        SelectionMetadata(
            document_id,
            f"{document_id}:face:{face_index}",
            SelectionMode.FACE,
            metadata.bounding_box,
            next(iter(mapping.by_runtime)),
        ),
    )


def _select_resolvable_contour_face(window: MainWindow) -> int:
    workspace = window.cam_workspace
    for face_index in range(1, 7):
        _set_face_selection(window, face_index)
        try:
            reference = workspace._contour_pick_provider()
            resolved = workspace._profile_resolver(reference)
            if (
                resolved.status is GeometryResolutionStatus.RESOLVED
                and resolved.profile is not None
                and abs(resolved.profile.normal.dot(
                    _service_setup(window).wcs.z_axis
                )) >= 1.0 - 1.0e-9
            ):
                return face_index
        except (RuntimeError, TypeError, ValueError):
            continue
    raise AssertionError("Không tìm được planar FACE profile phù hợp Contour")


def _service_setup(window: MainWindow):
    return window._project_service.cam_snapshot.jobs[0].setups[0]


def _show_section(page, section_id: str) -> None:
    section = page._section_widgets[section_id]
    section.set_expanded(True)
    page.scroll_area.ensureWidgetVisible(section)
    page.scroll_area.verticalScrollBar().setValue(
        min(
            page.scroll_area.verticalScrollBar().maximum(),
            max(0, section.y() - 8),
        )
    )


def run(output_dir: Path, workspace_root: Path) -> tuple[Path, ...]:
    """Run the production Contour flow and return ignored screenshot evidence."""
    output_dir.mkdir(parents=True, exist_ok=True)
    application = QApplication.instance() or QApplication([])
    application.setQuitOnLastWindowClosed(False)
    service = ProjectService.create_default(workspace_root / "config")
    source = workspace_root / "stage9a52-box.brep"
    _write_source(source)
    project = service.create_project_from_source(
        workspace_root, "Stage9A52 Contour Editor", source
    )
    window = _build_window(service)
    captures: list[Path] = []
    try:
        _wait_until(application, lambda: not window.cad_controller.is_busy)
        if window.cad_controller.active_source_id is None:
            raise AssertionError("OCP chưa load source Stage 9A.5.2")
        window._workspace_changed(WorkspaceId.MILL_2D.value)
        workspace = window.cam_workspace
        workspace.create_job()
        workspace.create_setup()
        workspace.create_basic_resources()
        workspace.add_contour_operation()
        contour = _operations(service)[0]
        _select_operation(window, contour)
        service.save()
        if service.is_dirty:
            raise AssertionError("Fixture phải clean trước khi sửa draft")

        window.function_editor_dock.show()
        window.function_editor_dock.raise_()
        window.resizeDocks(
            [window.function_editor_dock], [420], Qt.Orientation.Horizontal
        )
        application.processEvents()
        page = window.function_editor_host.active_page
        if page is None or page.schema.editor_id != "contour_production_9a5_2":
            raise AssertionError("Contour không mở production editor mặc định")
        captures.append(_capture(application, window, output_dir, "contour_basic"))

        face_index = _select_resolvable_contour_face(window)
        _set_face_selection(window, face_index)
        page._field_widgets["geometry_summary"].action_button.click()
        application.processEvents()
        if not page.state.values["geometry_reference_id"]:
            raise AssertionError("Select Geometry không cập nhật typed identity")
        _show_section(page, "geometry")
        captures.append(_capture(application, window, output_dir, "contour_geometry"))
        _show_section(page, "tool")
        captures.append(_capture(application, window, output_dir, "contour_tool"))
        if not page.state.values["tool_assembly_id"]:
            raise AssertionError("Contour thiếu Tool Assembly typed selection")

        page._field_changed("side", "inside")
        page._field_changed("direction", "conventional")
        page._field_changed("radial_stock_allowance", "0.2")
        _show_section(page, "cutting")
        captures.append(_capture(application, window, output_dir, "contour_cutting"))
        captures.append(
            _capture(application, window, output_dir, "contour_compensation_fields")
        )

        page._field_changed("final_depth", "32.0")
        page._field_changed("multiple_depth_passes", False)
        stepdown_widget = page._field_widgets.get("stepdown")
        if stepdown_widget is not None and stepdown_widget.isVisible():
            raise AssertionError("Stepdown vẫn hiện khi tắt depth passes")
        page._field_changed("multiple_depth_passes", True)
        page._field_changed("stepdown", "3.0")
        _show_section(page, "levels")
        captures.append(_capture(application, window, output_dir, "contour_levels"))

        page.disclosure_selector.setCurrentIndex(
            page.disclosure_selector.findData(ParameterDisclosureLevel.ADVANCED)
        )
        application.processEvents()
        page._field_changed("lead_length", "2.0")
        _show_section(page, "linking")
        captures.append(_capture(application, window, output_dir, "contour_linking"))
        captures.append(_capture(application, window, output_dir, "contour_lead_in_out"))
        captures.append(
            _capture(application, window, output_dir, "contour_inherited_sources")
        )

        page.disclosure_selector.setCurrentIndex(
            page.disclosure_selector.findData(ParameterDisclosureLevel.ADVANCED)
        )
        application.processEvents()
        captures.append(
            _capture(application, window, output_dir, "contour_advanced_collapsed")
        )
        _show_section(page, "advanced")
        captures.append(
            _capture(application, window, output_dir, "contour_advanced_expanded")
        )
        page.disclosure_selector.setCurrentIndex(
            page.disclosure_selector.findData(ParameterDisclosureLevel.EXPERT)
        )
        application.processEvents()
        _show_section(page, "expert")
        application.processEvents()
        page.scroll_area.verticalScrollBar().setValue(
            page.scroll_area.verticalScrollBar().maximum()
        )
        application.processEvents()
        captures.append(_capture(application, window, output_dir, "contour_expert"))

        page._field_changed("stepdown", "0")
        diagnostics = page.validate_draft()
        if not diagnostics or diagnostics[0].field_id != "stepdown":
            raise AssertionError("Inline validation không focus Stepdown")
        if service.is_dirty:
            raise AssertionError("Invalid draft đã mutation project")
        captures.append(_capture(application, window, output_dir, "contour_invalid"))
        page.reset_draft()
        if page.state.is_dirty:
            raise AssertionError("Reset Draft không trở về applied state")

        _set_face_selection(window, face_index)
        page._field_widgets["geometry_summary"].action_button.click()
        page._field_changed("side", "inside")
        page._field_changed("direction", "conventional")
        page._field_changed("final_depth", "32.0")
        page._field_changed("radial_stock_allowance", "0.2")
        page._field_changed("multiple_depth_passes", True)
        page._field_changed("stepdown", "3.0")
        page._field_changed("lead_length", "2.0")
        before_preview = service.cam_snapshot
        page.footer.buttons[FunctionEditorAction.PREVIEW].click()
        application.processEvents()
        if service.cam_snapshot != before_preview or service.cam_snapshot.artifacts:
            raise AssertionError("Contour Preview đã mutation hoặc tạo artifact")
        page.footer.buttons[FunctionEditorAction.APPLY].click()
        application.processEvents()
        if service.cam_snapshot.artifacts:
            raise AssertionError("Contour Apply đã tự Calculate")
        refreshed = window.function_editor_host.active_page
        if refreshed is None or refreshed.state.is_dirty:
            raise AssertionError("Contour Apply không refresh applied snapshot")
        refreshed.footer.buttons[FunctionEditorAction.CALCULATE].click()
        application.processEvents()
        contour = next(item for item in _operations(service) if item.strategy_key == "contour_2d")
        if contour.artifact_state.status is not ArtifactStatus.VALID:
            raise AssertionError("Contour Calculate rõ ràng không publish artifact")

        window.resizeDocks(
            [window.function_editor_dock], [300], Qt.Orientation.Horizontal
        )
        application.processEvents()
        refreshed = window.function_editor_host.active_page
        if refreshed is None or refreshed.scroll_area.horizontalScrollBar().maximum() != 0:
            raise AssertionError("Contour editor có horizontal overflow ở 300 px")
        captures.append(_capture(application, window, output_dir, "editor_width_300"))
        window.resizeDocks(
            [window.function_editor_dock], [420], Qt.Orientation.Horizontal
        )
        application.processEvents()
        captures.append(_capture(application, window, output_dir, "editor_width_420"))
        window.resize(1366, 768)
        application.processEvents()
        if not refreshed.footer.isVisible():
            raise AssertionError("Footer không truy cập được tại 1366×768")
        captures.append(_capture(application, window, output_dir, "window_1366x768"))

        workspace.add_operation()
        facing = next(item for item in _operations(service) if item.strategy_key == "facing_2_5d")
        _select_operation(window, facing)
        facing_page = window.function_editor_host.active_page
        if facing_page is None or facing_page.schema.editor_id != "facing_production_9a5_1":
            raise AssertionError("Facing production editor bị regression")

        workspace.add_pocket_operation()
        pocket = next(item for item in _operations(service) if item.strategy_key == "pocket_2_5d")
        _select_operation(window, pocket)
        if window.function_editor_host.current_mode != "legacy":
            raise AssertionError("Pocket không tiếp tục dùng Legacy Editor Adapter")
        captures.append(_capture(application, window, output_dir, "legacy_pocket_editor"))

        if any(
            service._cam_application.simulation_service.get(item.operation_id) is not None
            for item in _operations(service)
        ):
            raise AssertionError("Function Editor đã tự chạy Simulation")
        if service.post_service.results():
            raise AssertionError("Function Editor đã tự chạy Post")
        if service.nc_export_service.artifacts():
            raise AssertionError("Function Editor đã tự Export NC")

        service.save()
        project_root = project.root_path
        service.close_project()
        reopened = service.open_project(project_root)
        window._handle_project_change(reopened)
        application.processEvents()
        if service.is_dirty or len(_operations(service)) != 3:
            raise AssertionError("Save/Open không phục hồi applied values sạch")
        old_page = refreshed
        switched = service.new_project(workspace_root, "Stage9A52 Project Switch")
        window._handle_project_change(switched)
        application.processEvents()
        if old_page.state.status is not FunctionEditorDraftStatus.STALE:
            raise AssertionError("Project switch không invalidate callback cũ")
        if workspace._selected_key is not None:
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
        "contour_basic.png",
        "contour_geometry.png",
        "contour_tool.png",
        "contour_cutting.png",
        "contour_levels.png",
        "contour_linking.png",
        "contour_advanced_collapsed.png",
        "contour_advanced_expanded.png",
        "contour_expert.png",
        "contour_invalid.png",
        "contour_compensation_fields.png",
        "contour_lead_in_out.png",
        "contour_inherited_sources.png",
        "editor_width_300.png",
        "editor_width_420.png",
        "window_1366x768.png",
        "legacy_pocket_editor.png",
    }
    if {item.name for item in captures} != expected:
        raise AssertionError("Thiếu screenshot Stage 9A.5.2 bắt buộc")
    if any(not item.is_file() or item.stat().st_size == 0 for item in captures):
        raise AssertionError("Có screenshot Stage 9A.5.2 rỗng")
    return tuple(captures)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reference_private/DERIVED/UI_STAGE_9A5_2"),
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
        with tempfile.TemporaryDirectory(prefix="hms_stage9a52_") as raw:
            captures = run(args.output_dir.resolve(), Path(raw))
    logger.info(
        "Stage 9A.5.2 GUI smoke đạt: %d screenshot tại %s",
        len(captures),
        args.output_dir,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
