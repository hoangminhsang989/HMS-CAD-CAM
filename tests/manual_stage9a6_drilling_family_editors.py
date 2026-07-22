"""Manual/offscreen visual review for Stage 9A.6 drilling-family editors."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
import sys
import tempfile

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontDatabase, QFontInfo, QFontMetrics, QImage
from PySide6.QtWidgets import QApplication

from hms_cadcam.cam.domain import Operation
from hms_cadcam.project.service import ProjectService
from hms_cadcam.ui.function_editor import (
    FunctionEditorAction,
    ParameterDisclosureLevel,
)
from hms_cadcam.ui.workspace_shell import WorkspaceId
from hms_cadcam.ui.ui_tokens import FUNCTION_EDITOR_DEFAULT_WIDTH

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.manual_stage9a52_contour_editor import (  # noqa: E402
    _build_window,
    _capture,
    _operations,
    _select_operation,
    _wait_until,
    _write_source,
)
from tests.unit.test_boring_ui import _explicit_pattern, _resolved  # noqa: E402


logger = logging.getLogger("hms.manual.stage9a6")


def _assert_review_font_environment(application: QApplication) -> None:
    """Reject screenshots from a Qt platform that cannot render review text."""
    platform = application.platformName()
    families = QFontDatabase.families()
    if sys.platform == "win32" and platform != "windows":
        raise RuntimeError(
            "GUI review Stage 9A.6 phải chạy với Qt QPA windows; "
            f"platform hiện tại là {platform!r}. Hãy bỏ QT_QPA_PLATFORM=offscreen."
        )
    if not families:
        raise RuntimeError(
            f"Qt QPA {platform!r} không nạp được font; screenshot sẽ thành ô vuông."
        )
    review_font = QFont("Segoe UI", 9)
    resolved = QFontInfo(review_font).family()
    metrics = QFontMetrics(review_font)
    sample = "Tiếng Việt ă â ê ô ơ ư đ · Basic Advanced"
    if any(
        not metrics.inFontUcs4(ord(character))
        for character in sample
        if not character.isspace()
    ):
        raise RuntimeError(
            f"Font review {resolved!r} không đủ glyph tiếng Việt; không tạo screenshot."
        )
    application.setFont(review_font)


def _select_by_strategy(window, service: ProjectService, strategy_key: str) -> Operation:
    operation = next(
        item for item in _operations(service) if item.strategy_key == strategy_key
    )
    _select_operation(window, operation)
    return operation


def _page(window, editor_id: str):
    page = window.function_editor_host.active_page
    if page is None or page.schema.editor_id != editor_id:
        raise AssertionError(f"Không mở đúng production editor {editor_id}")
    return page


def _capture_at(
    application: QApplication,
    window,
    output_dir: Path,
    captures: list[Path],
    name: str,
    width: int,
    height: int,
) -> None:
    window.resize(width, height)
    window.resizeDocks(
        [window.function_editor_dock],
        [FUNCTION_EDITOR_DEFAULT_WIDTH],
        Qt.Orientation.Horizontal,
    )
    application.processEvents()
    page = window.function_editor_host.active_page
    if page is None:
        raise AssertionError(f"Screenshot {name} không có Function Editor active")
    squeezed = tuple(
        field_id
        for field_id, field in page._field_widgets.items()
        if field.isVisible()
        and field.label.width() < 80
    )
    if squeezed:
        raise AssertionError(f"Screenshot {name} còn label bị ép: {squeezed}")
    path = _capture(application, window, output_dir, name)
    image = QImage(str(path))
    if image.size().width() != width or image.size().height() != height:
        raise AssertionError(
            f"Screenshot {name} sai kích thước: {image.width()}×{image.height()}"
        )
    captures.append(path)


def _basic(page, application: QApplication) -> None:
    index = page.disclosure_selector.findData(ParameterDisclosureLevel.BASIC)
    page.disclosure_selector.setCurrentIndex(index)
    page.scroll_area.verticalScrollBar().setValue(0)
    application.processEvents()
    if page.maximum_disclosure is not ParameterDisclosureLevel.BASIC:
        raise AssertionError("Basic screenshot không ở disclosure Basic")
    for section_id in ("advanced", "capability", "expert"):
        section = page._section_widgets.get(section_id)
        if section is not None and section.isVisible():
            raise AssertionError(f"Basic screenshot còn hiện section {section_id}")


def _advanced(page, application: QApplication) -> None:
    index = page.disclosure_selector.findData(ParameterDisclosureLevel.ADVANCED)
    if index < 0:
        raise AssertionError("Production editor thiếu Advanced")
    page.disclosure_selector.setCurrentIndex(index)
    application.processEvents()
    section = page._section_widgets.get("advanced")
    if section is None:
        raise AssertionError("Advanced section chưa được dựng")
    section.set_expanded(True)
    application.processEvents()
    page.scroll_area.verticalScrollBar().setValue(
        min(
            page.scroll_area.verticalScrollBar().maximum(),
            max(0, section.y() - 8),
        )
    )
    application.processEvents()
    if (
        page.maximum_disclosure is not ParameterDisclosureLevel.ADVANCED
        or not section.isVisible()
        or not section.is_expanded
        or page.scroll_area.verticalScrollBar().value() == 0
    ):
        raise AssertionError("Advanced screenshot chưa mở và cuộn đúng section")


def run(output_dir: Path, workspace_root: Path) -> tuple[Path, ...]:
    """Create the four real operations and capture required review states."""
    output_dir.mkdir(parents=True, exist_ok=True)
    application = QApplication.instance() or QApplication([])
    application.setQuitOnLastWindowClosed(False)
    _assert_review_font_environment(application)
    service = ProjectService.create_default(workspace_root / "config")
    source = workspace_root / "stage9a6-box.brep"
    _write_source(source)
    project = service.create_project_from_source(
        workspace_root, "Stage9A6 Drilling Family Review", source
    )
    window = _build_window(service)
    if application.platformName() == "windows":
        window.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        window.show()
    captures: list[Path] = []
    try:
        _wait_until(application, lambda: not window.cad_controller.is_busy)
        window._workspace_changed(WorkspaceId.MILL_2D.value)
        workspace = window.cam_workspace
        workspace.create_job()
        workspace.create_setup()
        setup = service.cam_snapshot.jobs[0].setups[0]
        hole_source = _explicit_pattern(setup.wcs.origin.unit)
        workspace._drilling_pick_provider = lambda _axis: hole_source
        workspace._drilling_resolver = _resolved
        workspace.create_basic_resources()
        workspace.create_basic_tapping_resources()
        workspace.create_basic_reaming_resources()
        workspace.create_basic_boring_resources()
        workspace.add_drilling_operation()
        workspace.add_tapping_operation()
        workspace.add_reaming_operation()
        workspace.add_boring_operation()
        if {item.strategy_key for item in _operations(service)} != {
            "drilling_v1", "tapping_v1", "reaming_v1", "boring_v1"
        }:
            raise AssertionError("Không tạo đủ bốn drilling-family operation")
        service.save()

        window.show()
        window.function_editor_dock.show()
        window.function_editor_dock.raise_()
        application.processEvents()

        _select_by_strategy(window, service, "drilling_v1")
        drilling = _page(window, "drilling_production_9a6")
        _basic(drilling, application)
        _capture_at(
            application, window, output_dir, captures,
            "drilling_basic_1366x768", 1366, 768,
        )
        _capture_at(
            application, window, output_dir, captures,
            "drilling_basic_1600x900", 1600, 900,
        )
        _advanced(drilling, application)
        _capture_at(
            application, window, output_dir, captures,
            "drilling_advanced_1600x900", 1600, 900,
        )
        drilling._field_changed("feed_rate", "0")
        diagnostics = drilling.validate_draft()
        if not any(item.field_id == "feed_rate" for item in diagnostics):
            raise AssertionError("Drilling validation không focus Feed")
        _capture_at(
            application, window, output_dir, captures,
            "drilling_validation_error_1600x900", 1600, 900,
        )
        drilling.reset_draft()

        _select_by_strategy(window, service, "tapping_v1")
        tapping = _page(window, "tapping_production_9a6")
        _basic(tapping, application)
        _capture_at(
            application, window, output_dir, captures,
            "tapping_basic_1600x900", 1600, 900,
        )
        _advanced(tapping, application)
        _capture_at(
            application, window, output_dir, captures,
            "tapping_advanced_1600x900", 1600, 900,
        )
        _capture_at(
            application, window, output_dir, captures,
            "tapping_advanced_1920x1080", 1920, 1080,
        )

        _select_by_strategy(window, service, "reaming_v1")
        reaming = _page(window, "reaming_production_9a6")
        _basic(reaming, application)
        _capture_at(
            application, window, output_dir, captures,
            "reaming_basic_1600x900", 1600, 900,
        )
        reaming._field_changed("dwell_seconds", "0.4")
        if not reaming.state.is_dirty:
            raise AssertionError("Reaming draft không chuyển sang Modified")
        _capture_at(
            application, window, output_dir, captures,
            "reaming_dirty_1600x900", 1600, 900,
        )
        reaming.reset_draft()

        _select_by_strategy(window, service, "boring_v1")
        boring = _page(window, "boring_production_9a6")
        _basic(boring, application)
        _capture_at(
            application, window, output_dir, captures,
            "boring_basic_1600x900", 1600, 900,
        )
        boring._field_changed("enabled", False)
        boring.footer.buttons[FunctionEditorAction.APPLY].click()
        application.processEvents()
        boring = _page(window, "boring_production_9a6")
        if boring.state.values["enabled"] is not False:
            raise AssertionError("Boring disabled state không được Apply")
        _capture_at(
            application, window, output_dir, captures,
            "boring_disabled_1600x900", 1600, 900,
        )

        window.function_editor_host._switch_confirmation = lambda _state: "discard"
        _select_by_strategy(window, service, "tapping_v1")
        switching = _page(window, "tapping_production_9a6")
        switching._field_changed("dwell_seconds", "0.3")
        _select_by_strategy(window, service, "drilling_v1")
        switched_page = _page(window, "drilling_production_9a6")
        if switched_page.state.is_dirty:
            raise AssertionError("Operation switching giữ nhầm draft")
        _capture_at(
            application, window, output_dir, captures,
            "operation_switching_1600x900", 1600, 900,
        )

        if service.cam_snapshot.artifacts:
            raise AssertionError("GUI review đã tự tạo Toolpath artifact")
        if service.post_service.results() or service.nc_export_service.artifacts():
            raise AssertionError("GUI review đã tự Post hoặc Export NC")

        service.save()
        root = project.root_path
        service.close_project()
        reopened = service.open_project(root)
        window._handle_project_change(reopened)
        application.processEvents()
        if service.is_dirty or len(_operations(service)) != 4:
            raise AssertionError("Save/Open không phục hồi bốn operation sạch")
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
        "drilling_basic_1366x768.png",
        "drilling_basic_1600x900.png",
        "drilling_advanced_1600x900.png",
        "drilling_validation_error_1600x900.png",
        "tapping_basic_1600x900.png",
        "tapping_advanced_1600x900.png",
        "tapping_advanced_1920x1080.png",
        "reaming_basic_1600x900.png",
        "reaming_dirty_1600x900.png",
        "boring_basic_1600x900.png",
        "boring_disabled_1600x900.png",
        "operation_switching_1600x900.png",
    }
    if {item.name for item in captures} != expected:
        raise AssertionError("Thiếu screenshot Stage 9A.6 bắt buộc")
    if any(not item.is_file() or item.stat().st_size == 0 for item in captures):
        raise AssertionError("Có screenshot Stage 9A.6 rỗng")
    return tuple(captures)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reference_private/DERIVED/UI_STAGE_9A6"),
    )
    parser.add_argument("--workspace", type=Path)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if args.workspace is not None:
        args.workspace.mkdir(parents=True, exist_ok=True)
        captures = run(args.output_dir.resolve(), args.workspace.resolve())
    else:
        with tempfile.TemporaryDirectory(prefix="hms_stage9a6_") as raw:
            captures = run(args.output_dir.resolve(), Path(raw))
    logger.info(
        "Stage 9A.6 GUI review đạt: %d screenshot tại %s",
        len(captures),
        args.output_dir.resolve(),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
