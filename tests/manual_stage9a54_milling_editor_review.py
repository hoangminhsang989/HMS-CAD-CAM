"""GUI visual-integration review for Stage 9A.5.4 milling editors."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
import sys
import tempfile

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtWidgets import QApplication

from hms_cadcam.cam.domain import ArtifactStatus
from hms_cadcam.project.service import ProjectService
from hms_cadcam.ui.function_editor import (
    FunctionEditorAction,
    FunctionEditorDraftStatus,
    ParameterDisclosureLevel,
)
from hms_cadcam.ui.workspace_shell import WorkspaceId

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.manual_stage9a51_facing_editors import (
    _planar_fixture_operation,
    _resolved_face,
)
from tests.manual_stage9a52_contour_editor import (
    _build_window,
    _capture,
    _operations,
    _select_operation,
    _select_resolvable_contour_face,
    _set_face_selection,
    _show_section,
    _wait_until,
    _write_source,
)
from tests.manual_stage9a53_pocket_editor import _select_resolvable_pocket_face
from tests.unit.test_drilling_ui import _hole, _resolved


logger = logging.getLogger("hms.manual.stage9a54")


def _page(window, editor_id: str):
    page = window.function_editor_host.active_page
    if page is None or page.schema.editor_id != editor_id:
        raise AssertionError(f"Không mở đúng production editor {editor_id}")
    return page


def _select_by_strategy(window, service: ProjectService, strategy_key: str):
    operation = next(
        item for item in _operations(service) if item.strategy_key == strategy_key
    )
    _select_operation(window, operation)
    return operation


def _capture_review_states(
    application: QApplication,
    window,
    service: ProjectService,
    output_dir: Path,
    captures: list[Path],
    *,
    name: str,
    page,
    invalid_field: str,
) -> None:
    basic_index = page.disclosure_selector.findData(ParameterDisclosureLevel.BASIC)
    page.disclosure_selector.setCurrentIndex(basic_index)
    page.scroll_area.verticalScrollBar().setValue(0)
    application.processEvents()
    captures.append(_capture(application, window, output_dir, f"{name}_basic"))

    advanced_index = page.disclosure_selector.findData(
        ParameterDisclosureLevel.ADVANCED
    )
    if advanced_index < 0:
        raise AssertionError(f"{name} thiếu disclosure Advanced")
    page.disclosure_selector.setCurrentIndex(advanced_index)
    application.processEvents()
    if page._section_widgets["advanced"].is_expanded:
        raise AssertionError(f"{name} Advanced không collapsed mặc định")
    captures.append(
        _capture(application, window, output_dir, f"{name}_advanced_collapsed")
    )
    _show_section(page, "advanced")
    application.processEvents()
    captures.append(
        _capture(application, window, output_dir, f"{name}_advanced_expanded")
    )

    expert_index = page.disclosure_selector.findData(ParameterDisclosureLevel.EXPERT)
    if expert_index >= 0:
        page.disclosure_selector.setCurrentIndex(expert_index)
        application.processEvents()
        if page._section_widgets["expert"].is_expanded:
            raise AssertionError(f"{name} Expert không collapsed mặc định")
        captures.append(
            _capture(application, window, output_dir, f"{name}_expert_collapsed")
        )
    elif name in {"contour", "pocket"}:
        raise AssertionError(f"{name} phải có Expert field thật")

    dirty_before = service.is_dirty
    page._field_changed(invalid_field, "0")
    diagnostics = page.validate_draft()
    if not any(item.field_id == invalid_field for item in diagnostics):
        raise AssertionError(f"{name} không focus inline error {invalid_field}")
    if service.is_dirty is not dirty_before:
        raise AssertionError(f"{name} invalid draft đã thay project dirty")
    captures.append(_capture(application, window, output_dir, f"{name}_invalid"))
    page.reset_draft()
    if page.state.is_dirty:
        raise AssertionError(f"{name} Reset Draft không sạch")

    page.disclosure_selector.setCurrentIndex(basic_index)
    page.scroll_area.verticalScrollBar().setValue(0)
    window.resize(1920, 1080)
    for width in (300, 360, 420, 520):
        window.resizeDocks(
            [window.function_editor_dock], [width], Qt.Orientation.Horizontal
        )
        application.processEvents()
        if page.scroll_area.horizontalScrollBar().maximum() != 0:
            raise AssertionError(f"{name} tràn ngang ở {width} px")
        if not page.footer.isVisible():
            raise AssertionError(f"{name} mất footer ở {width} px")
        captures.append(
            _capture(application, window, output_dir, f"{name}_width_{width}")
        )
    window.resizeDocks(
        [window.function_editor_dock], [420], Qt.Orientation.Horizontal
    )
    application.processEvents()


def _assert_apply_then_calculate(
    application: QApplication,
    window,
    service: ProjectService,
    operation_id,
    page,
) -> None:
    artifacts_before = len(service.cam_snapshot.artifacts)
    page.footer.buttons[FunctionEditorAction.PREVIEW].click()
    application.processEvents()
    if len(service.cam_snapshot.artifacts) != artifacts_before:
        raise AssertionError("Preview đã tạo toolpath artifact")
    page.footer.buttons[FunctionEditorAction.APPLY].click()
    application.processEvents()
    if len(service.cam_snapshot.artifacts) != artifacts_before:
        raise AssertionError("Apply đã tự Calculate")
    refreshed = window.function_editor_host.active_page
    if refreshed is None or refreshed.state.is_dirty:
        raise AssertionError("Apply không refresh applied snapshot sạch")
    refreshed.footer.buttons[FunctionEditorAction.CALCULATE].click()
    application.processEvents()
    operation = next(
        item for item in _operations(service) if item.operation_id == operation_id
    )
    if operation.artifact_state.status is not ArtifactStatus.VALID:
        raise AssertionError("Calculate rõ ràng không publish toolpath")


def _contact_sheet(
    output_dir: Path,
    name: str,
    entries: tuple[tuple[str, str], ...],
) -> Path:
    columns = 2
    cell_width = 800
    cell_height = 475
    rows = (len(entries) + columns - 1) // columns
    canvas = QImage(
        columns * cell_width,
        rows * cell_height,
        QImage.Format.Format_ARGB32,
    )
    canvas.fill(QColor("#eef2f5"))
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    painter.setPen(QColor("#203243"))
    painter.setFont(QFont("Segoe UI", 12, QFont.Weight.DemiBold))
    try:
        for index, (filename, caption) in enumerate(entries):
            image = QImage(str(output_dir / filename))
            if image.isNull():
                raise RuntimeError(f"Không đọc được ảnh contact sheet: {filename}")
            column = index % columns
            row = index // columns
            x = column * cell_width
            y = row * cell_height
            painter.drawText(QRect(x + 12, y + 6, cell_width - 24, 28), caption)
            scaled = image.scaled(
                cell_width - 24,
                cell_height - 48,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            image_x = x + (cell_width - scaled.width()) // 2
            image_y = y + 38 + (cell_height - 44 - scaled.height()) // 2
            painter.drawImage(image_x, image_y, scaled)
    finally:
        painter.end()
    path = output_dir / name
    if not canvas.save(str(path)):
        raise RuntimeError(f"Không lưu được contact sheet {path}")
    return path


def run(output_dir: Path, workspace_root: Path) -> tuple[Path, ...]:
    """Run all four production editors in one real project and capture evidence."""
    output_dir.mkdir(parents=True, exist_ok=True)
    application = QApplication.instance() or QApplication([])
    application.setQuitOnLastWindowClosed(False)
    service = ProjectService.create_default(workspace_root / "config")
    source = workspace_root / "stage9a54-box.brep"
    _write_source(source)
    project = service.create_project_from_source(
        workspace_root, "Stage9A54 Milling Editor Review", source
    )
    window = _build_window(service)
    captures: list[Path] = []
    try:
        _wait_until(application, lambda: not window.cad_controller.is_busy)
        window._workspace_changed(WorkspaceId.MILL_2D.value)
        workspace = window.cam_workspace
        workspace.create_job()
        workspace.create_setup()
        workspace.create_basic_resources()
        workspace.add_operation()
        workspace.add_contour_operation()
        workspace.add_pocket_operation()
        planar_reference, planar_z = _resolved_face(window, 6)
        planar = _planar_fixture_operation(service, planar_reference, planar_z)
        service.save()

        window.show()
        window.resize(1600, 900)
        window.function_editor_dock.show()
        window.function_editor_dock.raise_()
        window.resizeDocks(
            [window.function_editor_dock], [420], Qt.Orientation.Horizontal
        )
        application.processEvents()

        facing = _select_by_strategy(window, service, "facing_2_5d")
        facing_page = _page(window, "facing_production_9a5_1")
        _capture_review_states(
            application,
            window,
            service,
            output_dir,
            captures,
            name="facing",
            page=facing_page,
            invalid_field="stepdown",
        )
        _show_section(facing_page, "tool")
        captures.append(
            _capture(application, window, output_dir, "facing_tool_selection")
        )
        facing_page._field_changed("stepover", "4.0")
        _assert_apply_then_calculate(
            application, window, service, facing.operation_id, facing_page
        )
        service.save()

        _select_operation(window, planar)
        planar_page = _page(window, "planar_face_facing_production_9a5_1")
        _capture_review_states(
            application,
            window,
            service,
            output_dir,
            captures,
            name="planar",
            page=planar_page,
            invalid_field="stepdown",
        )
        _set_face_selection(window, 6)
        planar_page._field_widgets["geometry_summary"].action_button.click()
        application.processEvents()
        _show_section(planar_page, "geometry")
        captures.append(
            _capture(application, window, output_dir, "planar_geometry_selection")
        )
        planar_page._field_changed("stepdown", "0.5")
        _assert_apply_then_calculate(
            application, window, service, planar.operation_id, planar_page
        )
        service.save()

        contour = _select_by_strategy(window, service, "contour_2d")
        contour_page = _page(window, "contour_production_9a5_2")
        _capture_review_states(
            application,
            window,
            service,
            output_dir,
            captures,
            name="contour",
            page=contour_page,
            invalid_field="stepdown",
        )
        contour_face = _select_resolvable_contour_face(window)
        _set_face_selection(window, contour_face)
        contour_page._field_widgets["geometry_summary"].action_button.click()
        application.processEvents()
        _show_section(contour_page, "geometry")
        captures.append(
            _capture(application, window, output_dir, "contour_geometry_selection")
        )
        contour_page._field_changed("side", "inside")
        contour_page._field_changed("direction", "conventional")
        contour_page._field_changed("final_depth", "32.0")
        contour_page._field_changed("stepdown", "3.0")
        contour_page.disclosure_selector.setCurrentIndex(
            contour_page.disclosure_selector.findData(
                ParameterDisclosureLevel.ADVANCED
            )
        )
        contour_page._field_changed("lead_length", "2.0")
        _assert_apply_then_calculate(
            application, window, service, contour.operation_id, contour_page
        )
        service.save()

        pocket = _select_by_strategy(window, service, "pocket_2_5d")
        pocket_page = _page(window, "pocket_production_9a5_3")
        _capture_review_states(
            application,
            window,
            service,
            output_dir,
            captures,
            name="pocket",
            page=pocket_page,
            invalid_field="stepdown",
        )
        pocket_face, _reference, resolved = _select_resolvable_pocket_face(window)
        _set_face_selection(window, pocket_face)
        pocket_page._field_widgets["geometry_summary"].action_button.click()
        application.processEvents()
        top = resolved.region.boundary.outer_loop.segments[0].start.z
        pocket_page._field_changed("top_z", str(top))
        pocket_page._field_changed("bottom_z", str(top - 3.0))
        pocket_page._field_changed("clearance_height", str(top + 5.0))
        pocket_page._field_changed("retract_height", str(top + 2.0))
        _show_section(pocket_page, "geometry")
        captures.append(
            _capture(application, window, output_dir, "pocket_geometry_selection")
        )
        _show_section(pocket_page, "levels")
        captures.append(
            _capture(application, window, output_dir, "source_default_inherited")
        )
        pocket_page._field_changed("stepover", "2.0")
        pocket_page._field_changed("stepdown", "1.0")
        _assert_apply_then_calculate(
            application, window, service, pocket.operation_id, pocket_page
        )
        service.save()

        window.resize(1366, 768)
        application.processEvents()
        if window.viewport.width() < 520 or window.viewport.height() < 360:
            raise AssertionError("Viewport thấp hơn policy tại 1366×768")
        captures.append(_capture(application, window, output_dir, "window_1366x768"))
        window.resize(1920, 1080)
        application.processEvents()
        captures.append(_capture(application, window, output_dir, "window_1920x1080"))

        drill_ref = _hole(
            service.cam_snapshot.jobs[0].setups[0].source_scope.primary_source_id,
            hint="stage9a54-legacy",
        )
        workspace._drilling_pick_provider = lambda _axis: drill_ref
        workspace._drilling_resolver = lambda geometry, depth: _resolved(geometry, depth)
        workspace.add_drilling_operation()
        _select_by_strategy(window, service, "drilling_v1")
        if window.function_editor_host.current_mode != "legacy":
            raise AssertionError("Drilling không dùng Legacy Editor")
        captures.append(
            _capture(application, window, output_dir, "legacy_editor_comparison")
        )

        if any(
            service._cam_application.simulation_service.get(item.operation_id)
            is not None
            for item in _operations(service)
        ):
            raise AssertionError("Review đã tự Simulate")
        if service.post_service.results() or service.nc_export_service.artifacts():
            raise AssertionError("Review đã tự Post/Export")

        service.save()
        project_root = project.root_path
        service.close_project()
        reopened = service.open_project(project_root)
        window._handle_project_change(reopened)
        application.processEvents()
        if service.is_dirty or len(_operations(service)) != 5:
            raise AssertionError("Save/Open không phục hồi năm operation sạch")
        _select_by_strategy(window, service, "pocket_2_5d")
        old_project_page = _page(window, "pocket_production_9a5_3")
        switched = service.new_project(workspace_root, "Stage9A54 Project Switch")
        window._handle_project_change(switched)
        application.processEvents()
        if old_project_page.state.status is not FunctionEditorDraftStatus.STALE:
            raise AssertionError("Project switch không stale callback cũ")
        if workspace._selected_key is not None:
            raise AssertionError("Project switch giữ selection cũ")
        service.save()

        contact_specs = (
            (
                "contact_sheet_basic.png",
                (
                    ("facing_basic.png", "Facing · Basic"),
                    ("planar_basic.png", "Planar Face Facing · Basic"),
                    ("contour_basic.png", "Contour · Basic"),
                    ("pocket_basic.png", "Pocket · Basic"),
                ),
            ),
            (
                "contact_sheet_disclosure.png",
                (
                    ("facing_advanced_collapsed.png", "Facing · Advanced collapsed"),
                    ("facing_advanced_expanded.png", "Facing · Advanced expanded"),
                    ("contour_advanced_collapsed.png", "Contour · Advanced collapsed"),
                    ("contour_advanced_expanded.png", "Contour · Advanced expanded"),
                    ("pocket_advanced_collapsed.png", "Pocket · Advanced collapsed"),
                    ("pocket_advanced_expanded.png", "Pocket · Advanced expanded"),
                ),
            ),
            (
                "contact_sheet_responsive.png",
                (
                    ("pocket_width_300.png", "Editor · 300 px"),
                    ("pocket_width_360.png", "Editor · 360 px"),
                    ("pocket_width_420.png", "Editor · 420 px"),
                    ("pocket_width_520.png", "Editor · 520 px"),
                    ("window_1366x768.png", "Window · 1366×768"),
                    ("window_1920x1080.png", "Window · 1920×1080"),
                ),
            ),
            (
                "contact_sheet_states.png",
                (
                    ("facing_invalid.png", "Facing · Invalid"),
                    ("contour_invalid.png", "Contour · Invalid"),
                    ("pocket_invalid.png", "Pocket · Invalid"),
                    ("source_default_inherited.png", "Source / Default / Derived"),
                    ("contour_geometry_selection.png", "Geometry selection"),
                    ("facing_tool_selection.png", "Tool selection"),
                    ("legacy_editor_comparison.png", "Legacy comparison"),
                ),
            ),
        )
        for filename, entries in contact_specs:
            captures.append(_contact_sheet(output_dir, filename, entries))

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

    required = {
        f"{editor}_{state}.png"
        for editor in ("facing", "planar", "contour", "pocket")
        for state in ("basic", "advanced_collapsed", "advanced_expanded", "invalid")
    }
    required.update(
        f"{editor}_width_{width}.png"
        for editor in ("facing", "planar", "contour", "pocket")
        for width in (300, 360, 420, 520)
    )
    required.update(
        {
            "window_1366x768.png",
            "window_1920x1080.png",
            "source_default_inherited.png",
            "contour_geometry_selection.png",
            "pocket_geometry_selection.png",
            "facing_tool_selection.png",
            "legacy_editor_comparison.png",
            "contact_sheet_basic.png",
            "contact_sheet_disclosure.png",
            "contact_sheet_responsive.png",
            "contact_sheet_states.png",
        }
    )
    captured_names = {item.name for item in captures}
    if not required.issubset(captured_names):
        raise AssertionError(f"Thiếu ảnh Stage 9A.5.4: {sorted(required - captured_names)}")
    if any(not item.is_file() or item.stat().st_size == 0 for item in captures):
        raise AssertionError("Có ảnh Stage 9A.5.4 rỗng")
    return tuple(captures)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reference_private/DERIVED/UI_STAGE_9A5_4"),
    )
    parser.add_argument("--workspace", type=Path)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if args.workspace is not None:
        args.workspace.mkdir(parents=True, exist_ok=True)
        captures = run(args.output_dir.resolve(), args.workspace.resolve())
    else:
        with tempfile.TemporaryDirectory(prefix="hms_stage9a54_") as raw:
            captures = run(args.output_dir.resolve(), Path(raw))
    logger.info(
        "Stage 9A.5.4 GUI smoke đạt: %d ảnh tại %s",
        len(captures),
        args.output_dir,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
