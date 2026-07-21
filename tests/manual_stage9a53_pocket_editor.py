"""GUI acceptance harness for Stage 9A.5.3 production Pocket editor."""

from __future__ import annotations

import argparse
from dataclasses import replace as dc_replace
import logging
from pathlib import Path
import sys
import tempfile

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from hms_cadcam.cam.domain import ArtifactStatus, GeometryResolutionStatus
from hms_cadcam.project.service import ProjectService
from hms_cadcam.ui.function_editor import (
    FunctionEditorAction,
    FunctionEditorDraftStatus,
    ParameterDisclosureLevel,
)
from hms_cadcam.ui.workspace_shell import WorkspaceId

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.manual_stage9a52_contour_editor import (
    _build_window,
    _capture,
    _operations,
    _select_operation,
    _set_face_selection,
    _service_setup,
    _show_section,
    _wait_until,
    _write_source,
)
from tests.unit.test_drilling_ui import _hole, _resolved


logger = logging.getLogger("hms.manual.stage9a53")


def _select_resolvable_pocket_face(window) -> tuple[int, object, object]:
    workspace = window.cam_workspace
    for face_index in range(1, 7):
        _set_face_selection(window, face_index)
        try:
            reference = workspace._contour_pick_provider()
            resolved = workspace._pocket_resolver(reference)
            if (
                resolved.status is GeometryResolutionStatus.RESOLVED
                and resolved.region is not None
                and abs(resolved.region.normal.dot(_service_setup(window).wcs.z_axis))
                >= 1.0 - 1.0e-9
            ):
                return face_index, reference, resolved
        except (RuntimeError, TypeError, ValueError):
            continue
    raise AssertionError("Không tìm được planar FACE Pocket phù hợp")


def _show_pocket_section(page, section_id: str) -> None:
    _show_section(page, section_id)


def run(output_dir: Path, workspace_root: Path) -> tuple[Path, ...]:
    """Run the production Pocket flow and return ignored screenshot evidence."""
    output_dir.mkdir(parents=True, exist_ok=True)
    application = QApplication.instance() or QApplication([])
    application.setQuitOnLastWindowClosed(False)
    service = ProjectService.create_default(workspace_root / "config")
    source = workspace_root / "stage9a53-box.brep"
    _write_source(source)
    project = service.create_project_from_source(
        workspace_root, "Stage9A53 Pocket Editor", source
    )
    window = _build_window(service)
    captures: list[Path] = []
    old_page = None
    try:
        _wait_until(application, lambda: not window.cad_controller.is_busy)
        window._workspace_changed(WorkspaceId.MILL_2D.value)
        workspace = window.cam_workspace
        workspace.create_job()
        workspace.create_setup()
        workspace.create_basic_resources()
        workspace.add_pocket_operation()
        pocket = next(item for item in _operations(service) if item.strategy_key == "pocket_2_5d")
        _select_operation(window, pocket)
        window.function_editor_dock.show()
        window.function_editor_dock.raise_()
        window.resizeDocks([window.function_editor_dock], [420], Qt.Orientation.Horizontal)
        application.processEvents()
        page = window.function_editor_host.active_page
        if page is None or page.schema.editor_id != "pocket_production_9a5_3":
            raise AssertionError("Pocket không mở production editor mặc định")
        captures.append(_capture(application, window, output_dir, "pocket_basic"))
        service.save()

        face_index, reference, resolved = _select_resolvable_pocket_face(window)
        _set_face_selection(window, face_index)
        page._field_widgets["geometry_summary"].action_button.click()
        application.processEvents()
        if not page.state.values["geometry_reference_id"]:
            raise AssertionError("Pocket Select Geometry không cập nhật typed identity")
        top = resolved.region.boundary.outer_loop.segments[0].start.z
        page._field_changed("top_z", str(top))
        page._field_changed("bottom_z", str(top - 3.0))
        page._field_changed("clearance_height", str(top + 5.0))
        page._field_changed("retract_height", str(top + 2.0))
        _show_pocket_section(page, "geometry")
        captures.append(_capture(application, window, output_dir, "pocket_geometry_region"))

        original_profile_resolver = workspace._profile_resolver
        if original_profile_resolver is not None:
            def profile_with_island(value):
                result = original_profile_resolver(value)
                profile = getattr(result, "profile", None)
                if profile is None:
                    return result
                return dc_replace(
                    result,
                    profile=dc_replace(profile, inner_loops=(profile.outer_loop,)),
                )

            workspace._profile_resolver = profile_with_island
            window.function_editor_host.refresh_current()
            application.processEvents()
            island_page = window.function_editor_host.active_page
            if island_page is None:
                raise AssertionError("Pocket island diagnostic page ontbreekt")
            _show_pocket_section(island_page, "geometry")
            captures.append(
                _capture(application, window, output_dir, "pocket_with_island_diagnostic")
            )
            workspace._profile_resolver = original_profile_resolver
            window.function_editor_host.refresh_current()
            application.processEvents()
            page = window.function_editor_host.active_page

        _show_pocket_section(page, "tool")
        captures.append(_capture(application, window, output_dir, "pocket_tool"))
        _show_pocket_section(page, "cutting")
        captures.append(_capture(application, window, output_dir, "pocket_cutting_pattern"))
        page._field_changed("stepover", "2.0")
        page._field_changed("radial_stock_allowance", "0.2")
        _show_pocket_section(page, "levels")
        captures.append(_capture(application, window, output_dir, "pocket_levels"))
        page._field_changed("stepdown", "1.0")
        _show_pocket_section(page, "entry")
        captures.append(_capture(application, window, output_dir, "pocket_entry"))
        page.disclosure_selector.setCurrentIndex(
            page.disclosure_selector.findData(ParameterDisclosureLevel.ADVANCED)
        )
        application.processEvents()
        _show_pocket_section(page, "linking")
        captures.append(_capture(application, window, output_dir, "pocket_linking"))
        captures.append(_capture(application, window, output_dir, "pocket_inherited_sources"))

        page.disclosure_selector.setCurrentIndex(
            page.disclosure_selector.findData(ParameterDisclosureLevel.ADVANCED)
        )
        application.processEvents()
        captures.append(_capture(application, window, output_dir, "pocket_advanced_collapsed"))
        _show_pocket_section(page, "advanced")
        captures.append(_capture(application, window, output_dir, "pocket_advanced_expanded"))
        page.disclosure_selector.setCurrentIndex(
            page.disclosure_selector.findData(ParameterDisclosureLevel.EXPERT)
        )
        application.processEvents()
        _show_pocket_section(page, "expert")
        captures.append(_capture(application, window, output_dir, "pocket_expert"))

        page._field_changed("stepdown", "0")
        diagnostics = page.validate_draft()
        if not diagnostics or diagnostics[0].field_id != "stepdown":
            raise AssertionError("Pocket inline validation không focus Stepdown")
        if service.is_dirty:
            raise AssertionError("Pocket invalid draft đã mutation project")
        captures.append(_capture(application, window, output_dir, "pocket_invalid"))
        page.reset_draft()
        if page.state.is_dirty:
            raise AssertionError("Pocket Reset Draft không trở về applied state")
        _set_face_selection(window, face_index)
        page._field_widgets["geometry_summary"].action_button.click()
        application.processEvents()
        page._field_changed("top_z", str(top))
        page._field_changed("bottom_z", str(top - 3.0))
        page._field_changed("stepover", "2.0")
        page._field_changed("stepdown", "1.0")
        page._field_changed("radial_stock_allowance", "0.2")
        before_preview = service.cam_snapshot
        page.footer.buttons[FunctionEditorAction.PREVIEW].click()
        application.processEvents()
        if service.cam_snapshot != before_preview or service.cam_snapshot.artifacts:
            raise AssertionError("Pocket Preview đã mutation hoặc tạo artifact")
        page.footer.buttons[FunctionEditorAction.APPLY].click()
        application.processEvents()
        if service.cam_snapshot.artifacts:
            raise AssertionError("Pocket Apply đã tự Calculate")
        refreshed = window.function_editor_host.active_page
        if refreshed is None or refreshed.state.is_dirty:
            detail = "missing" if refreshed is None else (
                f"dirty={refreshed.state.is_dirty} status={refreshed.state.status} "
                f"diagnostics={[item.message for item in refreshed.state.diagnostics]}"
            )
            raise AssertionError(f"Pocket Apply không refresh applied snapshot: {detail}")
        old_page = refreshed
        refreshed.footer.buttons[FunctionEditorAction.CALCULATE].click()
        application.processEvents()
        pocket = next(item for item in _operations(service) if item.strategy_key == "pocket_2_5d")
        if pocket.artifact_state.status is not ArtifactStatus.VALID:
            raise AssertionError("Pocket Calculate rõ ràng không publish artifact")
        refreshed = window.function_editor_host.active_page
        if refreshed is None:
            raise AssertionError("Pocket Calculate đã đóng production page ngoài ý muốn")

        window.resizeDocks([window.function_editor_dock], [300], Qt.Orientation.Horizontal)
        application.processEvents()
        if refreshed.scroll_area.horizontalScrollBar().maximum() != 0:
            raise AssertionError("Pocket editor có horizontal overflow ở 300 px")
        captures.append(_capture(application, window, output_dir, "editor_width_300"))
        window.resizeDocks([window.function_editor_dock], [420], Qt.Orientation.Horizontal)
        application.processEvents()
        captures.append(_capture(application, window, output_dir, "editor_width_420"))
        window.resize(1366, 768)
        application.processEvents()
        if not refreshed.footer.isVisible():
            raise AssertionError("Pocket footer không truy cập được tại 1366×768")
        captures.append(_capture(application, window, output_dir, "window_1366x768"))

        workspace.add_operation()
        facing = next(item for item in _operations(service) if item.strategy_key == "facing_2_5d")
        _select_operation(window, facing)
        if window.function_editor_host.active_page is None or (
            window.function_editor_host.active_page.schema.editor_id != "facing_production_9a5_1"
        ):
            raise AssertionError("Facing production editor bị regression")
        workspace.add_contour_operation()
        contour = next(item for item in _operations(service) if item.strategy_key == "contour_2d")
        _select_operation(window, contour)
        if window.function_editor_host.active_page is None or (
            window.function_editor_host.active_page.schema.editor_id != "contour_production_9a5_2"
        ):
            raise AssertionError("Contour production editor bị regression")

        drill_ref = _hole(
            service.cam_snapshot.jobs[0].setups[0].source_scope.primary_source_id,
            hint="stage9a53-drill",
        )
        workspace._drilling_pick_provider = lambda _axis: drill_ref
        workspace._drilling_resolver = lambda geometry, depth: _resolved(geometry, depth)
        workspace.add_drilling_operation()
        drilling = next(item for item in _operations(service) if item.strategy_key == "drilling_v1")
        _select_operation(window, drilling)
        if window.function_editor_host.current_mode != "legacy":
            raise AssertionError("Drilling không tiếp tục dùng Legacy Editor Adapter")
        captures.append(_capture(application, window, output_dir, "legacy_drilling_editor"))

        if service.post_service.results():
            raise AssertionError("Pocket editor đã tự chạy Post")
        if service.nc_export_service.artifacts():
            raise AssertionError("Pocket editor đã tự Export NC")
        service.save()
        project_root = project.root_path
        service.close_project()
        reopened = service.open_project(project_root)
        window._handle_project_change(reopened)
        application.processEvents()
        if service.is_dirty or len(_operations(service)) != 4:
            raise AssertionError("Save/Open không phục hồi applied values sạch")
        switched = service.new_project(workspace_root, "Stage9A53 Project Switch")
        window._handle_project_change(switched)
        application.processEvents()
        if old_page is not None and old_page.state.status is not FunctionEditorDraftStatus.STALE:
            raise AssertionError("Project switch không invalidate Pocket callback cũ")
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
        "pocket_basic.png",
        "pocket_geometry_region.png",
        "pocket_with_island_diagnostic.png",
        "pocket_tool.png",
        "pocket_cutting_pattern.png",
        "pocket_levels.png",
        "pocket_entry.png",
        "pocket_linking.png",
        "pocket_inherited_sources.png",
        "pocket_advanced_collapsed.png",
        "pocket_advanced_expanded.png",
        "pocket_expert.png",
        "pocket_invalid.png",
        "editor_width_300.png",
        "editor_width_420.png",
        "window_1366x768.png",
        "legacy_drilling_editor.png",
    }
    if {item.name for item in captures} != expected:
        raise AssertionError("Thiếu screenshot Stage 9A.5.3 bắt buộc")
    if any(not item.is_file() or item.stat().st_size == 0 for item in captures):
        raise AssertionError("Có screenshot Stage 9A.5.3 rỗng")
    return tuple(captures)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reference_private/DERIVED/UI_STAGE_9A5_3"),
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
        with tempfile.TemporaryDirectory(prefix="hms_stage9a53_") as raw:
            captures = run(args.output_dir.resolve(), Path(raw))
    logger.info(
        "Stage 9A.5.3 GUI smoke đạt: %d screenshot tại %s",
        len(captures),
        args.output_dir,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
