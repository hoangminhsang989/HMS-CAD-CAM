"""GUI acceptance harness for Stage 9A.5.1 production Facing editors.

The script uses the real MainWindow, OCP document/resolver and ProjectService.
It creates Stock Facing, persistent planar-FACE Facing and one legacy Contour,
then verifies draft/apply/calculate/lifecycle policy and captures ignored evidence.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
import logging
import tempfile
import time
from pathlib import Path

from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.BRepTools import BRepTools
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from hms_cadcam.cam.domain import (
    ArtifactStatus,
    CamNodeId,
    FacingBoundarySource,
    FacingParameters,
    GeometryInputId,
    GeometryInputRole,
    GeometryReference,
    GeometryReferenceKind,
    GeometryResolutionStatus,
    Length,
    Operation,
    OperationGeometryInput,
    OperationId,
)
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


logger = logging.getLogger("hms.manual.stage9a51")


class _OffscreenViewportBackend:
    """No-op Qt surface while the real OCP kernel owns CAD geometry."""

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
        raise RuntimeError("Không thể tạo BREP fixture Stage 9A.5.1")


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
    raise TimeoutError("GUI Stage 9A.5.1 không đạt trạng thái sẵn sàng")


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
        raise AssertionError(f"Screenshot Stage 9A.5.1 không hợp lệ: {path}")
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


def _resolved_face(
    window: MainWindow, face_index: int
) -> tuple[GeometryReference, float]:
    _set_face_selection(window, face_index)
    workspace = window.cam_workspace
    reference = workspace._pick_provider()
    resolved = workspace._face_resolver(reference)
    if (
        resolved.status is not GeometryResolutionStatus.RESOLVED
        or resolved.planar_face is None
    ):
        raise ValueError(f"FACE {face_index} không planar/resolved")
    if abs(resolved.planar_face.normal.z) < 1.0 - 1.0e-9:
        raise ValueError(f"FACE {face_index} không song song Setup XY")
    return reference, resolved.planar_face.outer_boundary.points[0].z


def _planar_fixture_operation(
    service: ProjectService,
    reference: GeometryReference,
    target_z: float,
) -> Operation:
    job = service.cam_snapshot.jobs[0]
    setup = job.setups[0]
    stock_operation = setup.operation_tree.operations[0]
    parameters = replace(
        FacingParameters.from_operation_parameters(stock_operation.parameters),
        boundary_source=FacingBoundarySource.PLANAR_FACE,
        target_height=Length(target_z, setup.wcs.origin.unit),
    )
    operation = Operation(
        OperationId.new(),
        CamNodeId.new(),
        stock_operation.family,
        setup.setup_id,
        stock_operation.tool_assembly,
        (
            OperationGeometryInput(
                GeometryInputId.new(),
                GeometryInputRole.BOUNDARY,
                reference,
                True,
                GeometryReferenceKind.FACE,
                0,
            ),
        ),
        parameters.to_operation_parameters(),
        stock_operation.machine_requirement,
    )
    service.execute_cam_command(
        lambda app: app.update_tree(
            job.job_id,
            setup.setup_id,
            lambda tree: tree.add_operation(
                tree.root_id, "Planar Face Facing", operation
            ),
        )
    )
    return operation


def run(output_dir: Path, workspace_root: Path) -> tuple[Path, ...]:
    """Run the full production-editor acceptance flow and return evidence paths."""
    output_dir.mkdir(parents=True, exist_ok=True)
    application = QApplication.instance() or QApplication([])
    application.setQuitOnLastWindowClosed(False)
    service = ProjectService.create_default(workspace_root / "config")
    source = workspace_root / "stage9a51-box.brep"
    _write_source(source)
    project = service.create_project_from_source(
        workspace_root, "Stage9A51 Facing Editors", source
    )
    window = _build_window(service)
    captures: list[Path] = []
    try:
        _wait_until(application, lambda: not window.cad_controller.is_busy)
        if window.cad_controller.active_source_id is None:
            raise AssertionError("OCP chưa load source Stage 9A.5.1")
        window._workspace_changed(WorkspaceId.MILL_2D.value)
        workspace = window.cam_workspace
        workspace.create_job()
        workspace.create_setup()
        workspace.create_basic_resources()
        workspace.add_operation()
        stock_operation = _operations(service)[0]
        _select_operation(window, stock_operation)
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
        if page is None or page.schema.editor_id != "facing_production_9a5_1":
            raise AssertionError("Facing không mở production editor mặc định")
        captures.append(_capture(application, window, output_dir, "facing_basic"))

        page.scroll_area.ensureWidgetVisible(page._section_widgets["geometry"])
        captures.append(
            _capture(application, window, output_dir, "facing_geometry_selection")
        )
        page.scroll_area.ensureWidgetVisible(page._section_widgets["tool"])
        captures.append(
            _capture(application, window, output_dir, "facing_tool_selection")
        )
        page.scroll_area.ensureWidgetVisible(page._field_widgets["stepover"])
        captures.append(
            _capture(application, window, output_dir, "inherited_source_default")
        )

        page.disclosure_selector.setCurrentIndex(
            page.disclosure_selector.findData(ParameterDisclosureLevel.ADVANCED)
        )
        page._section_widgets["advanced"].set_expanded(True)
        page.scroll_area.ensureWidgetVisible(page._section_widgets["advanced"])
        page.scroll_area.verticalScrollBar().setValue(
            page.scroll_area.verticalScrollBar().maximum()
        )
        captures.append(_capture(application, window, output_dir, "facing_advanced"))

        page._field_changed("stepover", "0")
        diagnostics = page.validate_draft()
        if not diagnostics or diagnostics[0].field_id != "stepover":
            raise AssertionError("Facing inline validation không focus Stepover")
        if service.is_dirty:
            raise AssertionError("Invalid draft đã mutation project")
        captures.append(_capture(application, window, output_dir, "facing_invalid"))
        page.reset_draft()
        page._field_changed("stepover", "4.0")
        page.footer.buttons[FunctionEditorAction.PREVIEW].click()
        if service.is_dirty or service.cam_snapshot.artifacts:
            raise AssertionError("Facing Preview đã Apply/Calculate")
        page.footer.buttons[FunctionEditorAction.APPLY].click()
        application.processEvents()
        if service.cam_snapshot.artifacts:
            raise AssertionError("Facing Apply đã tự Calculate")
        refreshed = window.function_editor_host.active_page
        if refreshed is None or refreshed.state.is_dirty:
            raise AssertionError("Facing Apply không refresh applied snapshot")
        refreshed.footer.buttons[FunctionEditorAction.CALCULATE].click()
        application.processEvents()
        if _operations(service)[0].artifact_state.status is not ArtifactStatus.VALID:
            raise AssertionError("Facing Calculate rõ ràng không publish artifact")

        initial_reference, initial_z = _resolved_face(window, 6)
        planar_operation = _planar_fixture_operation(
            service, initial_reference, initial_z
        )
        workspace.refresh(("operation", str(planar_operation.node_id)))
        application.processEvents()
        planar_page = window.function_editor_host.active_page
        if (
            planar_page is None
            or planar_page.schema.editor_id
            != "planar_face_facing_production_9a5_1"
        ):
            raise AssertionError("Planar Face Facing không mở schema riêng")
        captures.append(_capture(application, window, output_dir, "planar_basic"))

        before_cancel = dict(planar_page.state.values)
        real_picker = workspace._pick_provider
        workspace._pick_provider = lambda: (_ for _ in ()).throw(
            RuntimeError("selection cancelled")
        )
        planar_page._field_widgets["geometry_summary"].action_button.click()
        workspace._pick_provider = real_picker
        if dict(planar_page.state.values) != before_cancel:
            raise AssertionError("Cancel geometry selection không giữ draft")

        alternative = None
        for index in range(1, 7):
            try:
                candidate, candidate_z = _resolved_face(window, index)
            except (RuntimeError, TypeError, ValueError):
                continue
            if candidate.reference_id != initial_reference.reference_id:
                alternative = (candidate, candidate_z, index)
                break
        if alternative is None:
            raise AssertionError("Không tìm được planar FACE thứ hai cho Rebind")
        _selected_reference, selected_z, selected_index = alternative
        _set_face_selection(window, selected_index)
        planar_page._field_widgets["geometry_summary"].action_button.click()
        selected_identity = str(planar_page.state.values["geometry_reference_id"])
        if (
            selected_identity == str(initial_reference.reference_id)
            or float(planar_page.state.values["target_height"]) != selected_z
        ):
            raise AssertionError(
                "Planar Select không cập nhật typed draft: "
                f"actual={planar_page.state.values['geometry_reference_id']}/"
                f"{planar_page.state.values['target_height']} expected="
                f"new identity/{selected_z}; diagnostics="
                f"{planar_page.state.diagnostics}"
            )
        planar_page._field_changed("stepdown", "10.0")
        planar_page._field_changed("stepover", "5.0")
        planar_page.footer.buttons[FunctionEditorAction.PREVIEW].click()
        if _operations(service)[1].geometry_inputs[0].reference != initial_reference:
            raise AssertionError("Planar Preview đã mutation operation")
        planar_page.footer.buttons[FunctionEditorAction.APPLY].click()
        application.processEvents()
        applied_planar = _operations(service)[1]
        if (
            str(applied_planar.geometry_inputs[0].reference.reference_id)
            != selected_identity
        ):
            raise AssertionError("Planar Apply không commit FACE đã chọn")
        if applied_planar.artifact_state.status is ArtifactStatus.VALID:
            raise AssertionError("Planar Apply đã tự Calculate")

        planar_page = window.function_editor_host.active_page
        planar_page.disclosure_selector.setCurrentIndex(
            planar_page.disclosure_selector.findData(ParameterDisclosureLevel.ADVANCED)
        )
        planar_page._section_widgets["advanced"].set_expanded(True)
        planar_page.scroll_area.ensureWidgetVisible(
            planar_page._section_widgets["advanced"]
        )
        planar_page.scroll_area.verticalScrollBar().setValue(
            planar_page.scroll_area.verticalScrollBar().maximum()
        )
        captures.append(_capture(application, window, output_dir, "planar_advanced"))
        planar_page.disclosure_selector.setCurrentIndex(
            planar_page.disclosure_selector.findData(ParameterDisclosureLevel.BASIC)
        )
        planar_page.footer.buttons[FunctionEditorAction.CALCULATE].click()
        application.processEvents()
        if _operations(service)[1].artifact_state.status is not ArtifactStatus.VALID:
            raise AssertionError("Planar Calculate rõ ràng không publish artifact")
        planar_page = window.function_editor_host.active_page
        if planar_page is None:
            raise AssertionError("Planar editor không refresh sau Calculate")

        window.resizeDocks(
            [window.function_editor_dock], [300], Qt.Orientation.Horizontal
        )
        application.processEvents()
        if planar_page.scroll_area.horizontalScrollBar().maximum() != 0:
            raise AssertionError("Production editor có horizontal overflow ở 300 px")
        captures.append(_capture(application, window, output_dir, "editor_width_300"))
        window.resizeDocks(
            [window.function_editor_dock], [420], Qt.Orientation.Horizontal
        )
        application.processEvents()
        captures.append(_capture(application, window, output_dir, "editor_width_420"))
        window.resize(1366, 768)
        application.processEvents()
        if not planar_page.footer.isVisible():
            raise AssertionError("Footer không truy cập được tại 1366×768")
        captures.append(_capture(application, window, output_dir, "window_1366x768"))

        workspace.add_contour_operation()
        contour = next(
            item for item in _operations(service) if item.strategy_key == "contour_2d"
        )
        _select_operation(window, contour)
        if window.function_editor_host.current_mode != "legacy":
            raise AssertionError("Contour không fallback về Legacy Editor Adapter")
        captures.append(
            _capture(application, window, output_dir, "legacy_editor_operation")
        )

        if any(
            service._cam_application.simulation_service.get(item.operation_id)
            is not None
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
        old_page = planar_page
        switched = service.new_project(workspace_root, "Stage9A51 Project Switch")
        window._handle_project_change(switched)
        application.processEvents()
        if old_page.state.status is not FunctionEditorDraftStatus.STALE:
            raise AssertionError("Project switch không invalidate editor callback cũ")
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
        "facing_basic.png",
        "facing_advanced.png",
        "facing_invalid.png",
        "facing_geometry_selection.png",
        "facing_tool_selection.png",
        "planar_basic.png",
        "planar_advanced.png",
        "inherited_source_default.png",
        "editor_width_300.png",
        "editor_width_420.png",
        "window_1366x768.png",
        "legacy_editor_operation.png",
    }
    if {item.name for item in captures} != expected:
        raise AssertionError("Thiếu screenshot Stage 9A.5.1 bắt buộc")
    if any(not item.is_file() or item.stat().st_size == 0 for item in captures):
        raise AssertionError("Có screenshot Stage 9A.5.1 rỗng")
    return tuple(captures)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reference_private/DERIVED/UI_STAGE_9A5_1"),
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
        with tempfile.TemporaryDirectory(prefix="hms_stage9a51_") as raw:
            captures = run(args.output_dir.resolve(), Path(raw))
    logger.info(
        "Stage 9A.5.1 GUI smoke đạt: %d screenshot tại %s",
        len(captures),
        args.output_dir,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
