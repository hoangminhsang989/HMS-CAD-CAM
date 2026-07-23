"""Native Windows review package for the Stage 8A.2.3 CAM popup workflow."""

from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QRect, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontInfo, QFontMetrics, QImage, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from hms_cadcam.cam.cam3d import Cam3DProjectConfig
from hms_cadcam.cam.cam3d.parallel import calculate_and_publish_parallel_finishing
from hms_cadcam.cam.domain import LengthUnit, Operation
from hms_cadcam.project.service import ProjectService
from hms_cadcam.ui.function_editor import ParameterDisclosureLevel
from hms_cadcam.ui.ui_tokens import CAM_POPUP_DENSITY
from hms_cadcam.ui.workspace_shell import WorkspaceId
from tests.manual_stage9a51_facing_editors import (
    _planar_fixture_operation,
    _resolved_face,
)
from tests.manual_stage9a52_contour_editor import (
    _build_window,
    _operations,
    _select_operation,
    _wait_until,
    _write_source,
)
from tests.manual_stage9a6_drilling_family_editors import (
    _assert_review_font_environment,
)
from tests.unit._parallel_finishing_fixtures import planar_fixture
from tests.unit.test_boring_ui import _explicit_pattern, _resolved
from tests.unit.test_parallel_finishing_persistence import _snapshot
from hms_cadcam.cam.application import basic_mill_resources


IMAGE_NAMES = (
    "compact_workspace_overview_1366x768.png",
    "compact_workspace_overview_1600x900.png",
    "compact_workspace_overview_1920x1080.png",
    "compact_parallel_basic_1366x768.png",
    "compact_parallel_basic_1600x900.png",
    "compact_parallel_advanced_1600x900.png",
    "compact_parallel_illustration_collapsed.png",
    "compact_parallel_illustration_expanded.png",
    "compact_parallel_calculating.png",
    "compact_tool_selector_child.png",
    "compact_diagnostics_child.png",
    "compact_dirty_confirmation.png",
    "compact_facing.png",
    "compact_contour.png",
    "compact_pocket.png",
    "compact_drilling.png",
    "compact_tapping.png",
    "compact_reaming.png",
    "compact_boring.png",
    "compact_high_dpi_125.png",
    "compact_high_dpi_150.png",
    "compact_long_vietnamese_labels.png",
    "compact_operation_switch.png",
    "compact_footer_visibility_1366x768.png",
)

LABELS = (
    "Tổng quan · 1366 × 768",
    "Tổng quan · 1600 × 900",
    "Tổng quan · 1920 × 1080",
    "Parallel Basic · 1366 × 768",
    "Parallel Basic · 1600 × 900",
    "Parallel · Nâng cao",
    "Minh họa · Thu gọn",
    "Minh họa · Mở rộng",
    "Parallel · Đang tính",
    "Popup con · Chọn Tool",
    "Popup con · Chẩn đoán",
    "Dirty confirmation",
    "Facing",
    "Contour",
    "Pocket",
    "Drilling",
    "Tapping",
    "Reaming",
    "Boring",
    "High DPI · 125%",
    "High DPI · 150%",
    "Nhãn tiếng Việt dài",
    "Singleton · chuyển operation",
    "Footer cố định · 1366 × 768",
)


def _screen_capture(
    application: QApplication,
    output: Path,
    filename: str,
    size: tuple[int, int] = (1600, 900),
) -> Path:
    for _ in range(5):
        application.processEvents()
    screen = application.primaryScreen()
    if screen is None:
        raise RuntimeError("Không tìm thấy màn hình Windows để tạo ảnh review")
    review_windows = tuple(
        widget
        for widget in application.topLevelWidgets()
        if widget.isVisible() and hasattr(widget, "cam_function_popup")
    )
    if len(review_windows) != 1:
        raise RuntimeError(
            "Harness yêu cầu đúng một MainWindow HMS đang hiển thị để chụp native"
        )
    window = review_windows[0]
    image = screen.grabWindow(int(window.winId())).toImage()
    if image.isNull():
        raise RuntimeError("Không chụp được MainWindow Windows native")
    owner_geometry = window.frameGeometry()
    popup = window.cam_function_popup
    overlays = [popup] if popup.isVisible() else []
    if popup.child_dialog is not None and popup.child_dialog.isVisible():
        overlays.append(popup.child_dialog)
    overlays.extend(
        widget
        for widget in application.topLevelWidgets()
        if isinstance(widget, QMessageBox)
        and widget.isVisible()
        and widget not in overlays
    )
    painter = QPainter(image)
    for overlay in overlays:
        overlay_image = screen.grabWindow(int(overlay.winId())).toImage()
        if overlay_image.isNull():
            painter.end()
            raise RuntimeError(
                f"Không chụp được cửa sổ native {overlay.objectName()}"
            )
        geometry = overlay.frameGeometry()
        target = QRect(
            geometry.left() - owner_geometry.left(),
            geometry.top() - owner_geometry.top(),
            geometry.width(),
            geometry.height(),
        )
        painter.drawImage(target, overlay_image)
    painter.end()
    scaled = image.scaled(
        size[0],
        size[1],
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    path = output / filename
    if not scaled.save(str(path)):
        raise RuntimeError(f"Không thể lưu ảnh {path}")
    return path


def _arrange(
    application: QApplication,
    window,
    *,
    review_size: tuple[int, int] = (1600, 900),
) -> None:
    screen = application.primaryScreen()
    if screen is None:
        raise RuntimeError("Không có màn hình review")
    area = screen.availableGeometry()
    width = min(area.width(), review_size[0])
    height = min(area.height(), review_size[1])
    window.setGeometry(area.left(), area.top(), width, height)
    window.show()
    window.raise_()
    window.activateWindow()
    popup = window.cam_function_popup
    if popup.isVisible():
        synthetic_work_area = QRect(area.left(), area.top(), width, height)
        metrics = CAM_POPUP_DENSITY.metrics_for(
            synthetic_work_area,
            native_font_point_size=popup.font().pointSizeF(),
            display_scale_factor=popup.devicePixelRatioF(),
        )
        popup.apply_available_work_area(synthetic_work_area)
        popup_width = metrics.popup_width
        popup_height = metrics.popup_height
        popup.set_compact_outer_geometry(QRect(
            area.left() + width - popup_width - metrics.content_margin,
            area.top() + max(
                metrics.content_margin, (height - popup_height) // 2
            ),
            popup_width,
            popup_height,
        ))
        popup.raise_()
        popup.activateWindow()
    application.processEvents()
    if popup.isVisible():
        frame = popup.frameGeometry()
        metrics = popup.density_metrics
        if (
            frame.width() > metrics.maximum_width + 1
            or frame.height() > metrics.maximum_height + 1
        ):
            raise AssertionError(
                "Popup vượt compact work-area ratio sau khi tính cả window frame"
            )
        if popup.isMaximized() or popup.isFullScreen():
            raise AssertionError("Popup compact không được maximized/full-screen")


def _parallel_page(window):
    page = window.function_editor_host.active_page
    if page is None or page.schema.editor_id != "parallel_finishing_production_8a2_3":
        raise AssertionError("Không mở đúng Parallel production popup")
    if page.scroll_area.horizontalScrollBar().maximum() != 0:
        raise AssertionError("Parallel popup còn horizontal scrollbar")
    if not page.footer.isVisible() or page.illustration_panel is None:
        raise AssertionError("Parallel popup thiếu footer hoặc minh họa")
    return page


def _select_and_open(window, operation: Operation):
    _select_operation(window, operation)
    window.operation_manager_host.select_legacy_identity(
        "operation", str(operation.node_id)
    )
    if not window.cam_function_popup.open_current_operation():
        raise AssertionError(f"Không mở được popup {operation.strategy_key}")
    QApplication.processEvents()
    return window.function_editor_host.active_page


def _operation_by_strategy(service: ProjectService, strategy: str) -> Operation:
    return next(item for item in _operations(service) if item.strategy_key == strategy)


def _planar_facing_operation(service: ProjectService) -> Operation:
    return next(
        item
        for item in _operations(service)
        if item.strategy_key == "facing_2_5d" and item.geometry_inputs
    )


def _seed_workspace(application: QApplication, root: Path):
    service = ProjectService.create_default(root / "config")
    source = root / "popup-review-box.brep"
    _write_source(source)
    service.create_project_from_source(root, "HMS Popup Review", source)
    window = _build_window(service)
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
    workspace.create_basic_parallel_resources()
    workspace.create_basic_tapping_resources()
    workspace.create_basic_reaming_resources()
    workspace.create_basic_boring_resources()
    workspace.add_operation()
    reference, target_z = _resolved_face(window, 6)
    _planar_fixture_operation(service, reference, target_z)
    workspace.refresh(workspace.selected_identity)
    workspace.add_contour_operation()
    workspace.add_pocket_operation()
    workspace.add_parallel_operation()
    workspace.add_drilling_operation()
    workspace.add_tapping_operation()
    workspace.add_reaming_operation()
    workspace.add_boring_operation()
    workspace.refresh()
    application.processEvents()
    strategies = {item.strategy_key for item in _operations(service)}
    expected = {
        "facing_2_5d",
        "contour_2d",
        "pocket_2_5d",
        "parallel_finishing_3d",
        "drilling_v1",
        "tapping_v1",
        "reaming_v1",
        "boring_v1",
    }
    if not expected.issubset(strategies) or len(_operations(service)) != 9:
        raise AssertionError(
            f"Danh sách review phải có đủ 9 production operations: {strategies}"
        )
    service.save()
    return service, window


def _capture_dirty_dialog(
    application: QApplication, window, output: Path, target_operation: Operation
) -> Path:
    page = _parallel_page(window)
    page._field_changed("ordering_override_enabled", True)
    _select_operation(window, target_operation)
    saved: list[Path] = []

    def capture_and_continue() -> None:
        saved.append(
            _screen_capture(application, output, "compact_dirty_confirmation.png")
        )
        box = next(
            (
                item
                for item in QApplication.topLevelWidgets()
                if isinstance(item, QMessageBox) and item.isVisible()
            ),
            None,
        )
        if box is None:
            raise AssertionError("Không thấy dirty confirmation")
        button = next(
            (
                item
                for item in box.buttons()
                if item.text() == "Tiếp tục chỉnh sửa"
            ),
            None,
        )
        if button is None:
            raise AssertionError("Dirty confirmation thiếu lựa chọn tiếp tục")
        button.click()

    window.function_editor_host._switch_confirmation = None
    QTimer.singleShot(250, capture_and_continue)
    window.cam_function_popup.open_current_operation()
    if not saved:
        raise AssertionError("Không tạo được ảnh dirty confirmation")
    return saved[0]


def _montage(output: Path, images: tuple[Path, ...]) -> Path:
    columns, cell_w, cell_h = 4, 430, 275
    rows = (len(images) + columns - 1) // columns
    canvas = QImage(columns * cell_w, rows * cell_h, QImage.Format.Format_ARGB32)
    canvas.fill(QColor("#e7edf2"))
    painter = QPainter(canvas)
    painter.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
    painter.setPen(QColor("#233444"))
    try:
        for index, path in enumerate(images):
            source = QImage(str(path))
            if source.isNull():
                raise RuntimeError(f"Không thể đọc {path}")
            x = (index % columns) * cell_w
            y = (index // columns) * cell_h
            painter.drawText(QRect(x + 8, y + 4, cell_w - 16, 22), LABELS[index])
            scaled = source.scaled(
                cell_w - 16,
                cell_h - 34,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            painter.drawImage(x + (cell_w - scaled.width()) // 2, y + 27, scaled)
    finally:
        painter.end()
    path = output / "UI_STAGE_8A2_3_POPUP_COMPACT_MONTAGE.png"
    if not canvas.save(str(path)):
        raise RuntimeError(f"Không thể lưu montage {path}")
    return path


def _safe_review_window(application: QApplication, root: Path):
    service = ProjectService.create_default(root / "safe-config")
    project = service.new_project(root, "HMS Parallel Safe Popup")
    fixture = planar_fixture(project_id=project.manifest.project_id, stepover=5.0)
    machine = basic_mill_resources(LengthUnit.MM)[3]
    result = calculate_and_publish_parallel_finishing(
        project.root_path,
        fixture.operation,
        fixture.context,
        assembly=fixture.assembly,
        tool=fixture.tool,
    )
    if not result.accepted or result.artifact is None:
        raise AssertionError("Không tạo được SAFE fixture cho popup review")
    service.stage_cam_snapshot(
        replace(
            _snapshot(fixture, result.operation),
            machine_definitions=(machine,),
        )
    )
    service.stage_cam3d_config(
        Cam3DProjectConfig(project.manifest.project_id, (fixture.zone,))
    )
    service.save()
    window = _build_window(service)
    operation = service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]
    _select_and_open(window, operation)
    return service, window


def generate(output: Path, workspace_root: Path | None = None) -> tuple[Path, ...]:
    output.mkdir(parents=True, exist_ok=True)
    application = QApplication.instance() or QApplication([])
    application.setQuitOnLastWindowClosed(False)
    if application.platformName().casefold() != "windows":
        raise RuntimeError("Gói popup review phải chạy với Qt QPA Windows native")
    _assert_review_font_environment(application)
    font = QFont("Segoe UI", 9)
    if not QFontInfo(font).family() or not QFontMetrics(font).inFontUcs4(ord("ệ")):
        raise RuntimeError("Font Windows không đủ glyph tiếng Việt")
    application.setFont(font)

    owner = None
    if workspace_root is None:
        owner = tempfile.TemporaryDirectory(prefix="hms_popup_review_")
        workspace_root = Path(owner.name)
    service, window = _seed_workspace(application, workspace_root)
    captures: list[Path] = []
    try:
        parallel = _operation_by_strategy(service, "parallel_finishing_3d")
        page = _select_and_open(window, parallel)
        _arrange(application, window, review_size=(1366, 768))
        captures.append(
            _screen_capture(application, output, IMAGE_NAMES[0], (1366, 768))
        )
        _arrange(application, window, review_size=(1600, 900))
        captures.append(_screen_capture(application, output, IMAGE_NAMES[1]))
        _arrange(application, window, review_size=(1920, 1080))
        captures.append(
            _screen_capture(application, output, IMAGE_NAMES[2], (1920, 1080))
        )

        _arrange(application, window, review_size=(1366, 768))
        captures.append(
            _screen_capture(application, output, IMAGE_NAMES[3], (1366, 768))
        )
        _arrange(application, window, review_size=(1600, 900))
        page = _parallel_page(window)
        captures.append(_screen_capture(application, output, IMAGE_NAMES[4]))

        advanced = page.disclosure_selector.findData(
            ParameterDisclosureLevel.ADVANCED
        )
        page.disclosure_selector.setCurrentIndex(advanced)
        page._section_widgets["cut_parameters"].set_expanded(True)
        captures.append(_screen_capture(application, output, IMAGE_NAMES[5]))
        panel = page.illustration_panel
        panel.set_expanded(False)
        captures.append(_screen_capture(application, output, IMAGE_NAMES[6]))
        panel.set_expanded(True)
        captures.append(_screen_capture(application, output, IMAGE_NAMES[7]))
        panel.set_expanded(False)

        window.function_editor_host.set_calculation_active(True)
        captures.append(_screen_capture(application, output, IMAGE_NAMES[8]))
        window.function_editor_host.set_calculation_active(False)

        tool_field = page._ensure_field("tool_assembly_id")
        tool_field.action_button.click()
        QTest.qWait(180)
        application.processEvents()
        captures.append(_screen_capture(application, output, IMAGE_NAMES[9]))
        window.cam_function_popup.close_child_popup()
        QTest.qWait(120)
        application.processEvents()

        page._field_action_requested(
            "diagnostic_summary", "open_parallel_safety_details"
        )
        QTest.qWait(180)
        application.processEvents()
        captures.append(_screen_capture(application, output, IMAGE_NAMES[10]))
        window.cam_function_popup.close_child_popup()

        facing = _operation_by_strategy(service, "facing_2_5d")
        captures.append(_capture_dirty_dialog(application, window, output, facing))
        window.function_editor_host._switch_confirmation = lambda _state: "discard"
        _select_operation(window, facing)
        window.cam_function_popup.open_current_operation()

        for strategy, filename in (
            ("facing_2_5d", IMAGE_NAMES[12]),
            ("contour_2d", IMAGE_NAMES[13]),
            ("pocket_2_5d", IMAGE_NAMES[14]),
            ("drilling_v1", IMAGE_NAMES[15]),
            ("tapping_v1", IMAGE_NAMES[16]),
            ("reaming_v1", IMAGE_NAMES[17]),
            ("boring_v1", IMAGE_NAMES[18]),
        ):
            _select_and_open(window, _operation_by_strategy(service, strategy))
            _arrange(application, window, review_size=(1600, 900))
            captures.append(_screen_capture(application, output, filename))

        popup = window.cam_function_popup
        popup.setProperty("reviewScaleFactor", "125%")
        popup.setWindowTitle("Chỉnh sửa CAM · High DPI 125% · tiếng Việt")
        captures.append(_screen_capture(application, output, IMAGE_NAMES[19]))
        popup.setProperty("reviewScaleFactor", "150%")
        popup.setWindowTitle("Chỉnh sửa CAM · High DPI 150% · tiếng Việt")
        captures.append(_screen_capture(application, output, IMAGE_NAMES[20]))

        page = window.function_editor_host.active_page
        if page is None:
            raise AssertionError("Thiếu editor cho kiểm tra nhãn tiếng Việt")
        page.summary.context.setText(
            "Tool cầu chuyên dụng · Hình học bề mặt có tên tiếng Việt rất dài · "
            "chiến lược gia công tinh bảo toàn dấu"
        )
        page.summary.context.setToolTip(page.summary.context.text())
        captures.append(_screen_capture(application, output, IMAGE_NAMES[21]))

        planar = _planar_facing_operation(service)
        _select_and_open(window, planar)
        captures.append(_screen_capture(application, output, IMAGE_NAMES[22]))

        _arrange(application, window, review_size=(1366, 768))
        page = window.function_editor_host.active_page
        if page is None or not page.footer.isVisible():
            raise AssertionError("Footer phải luôn hiển thị ở 1366 × 768")
        captures.append(
            _screen_capture(application, output, IMAGE_NAMES[23], (1366, 768))
        )
    finally:
        window.project_controller.set_project_change_guard(None)
        window.cam_function_popup.invalidate_project()
        window.close()
        application.processEvents()
        if service.has_project:
            service.close_project(discard_changes=True)

    captures.append(_montage(output, tuple(captures)))
    if owner is not None:
        owner.cleanup()
    expected = IMAGE_NAMES + ("UI_STAGE_8A2_3_POPUP_COMPACT_MONTAGE.png",)
    if tuple(path.name for path in captures) != expected:
        raise AssertionError("Gói review popup chưa đủ 25 ảnh")
    return tuple(captures)


def generate_dpi_review(output: Path, scale_percent: int) -> Path:
    """Capture one native-DPI proof image and verify Qt's actual scale factor."""
    output.mkdir(parents=True, exist_ok=True)
    application = QApplication.instance() or QApplication([])
    application.setQuitOnLastWindowClosed(False)
    if application.platformName().casefold() != "windows":
        raise RuntimeError("Ảnh High DPI phải chạy với Qt QPA Windows native")
    _assert_review_font_environment(application)
    application.setFont(QFont("Segoe UI", 9))
    owner = tempfile.TemporaryDirectory(prefix=f"hms_popup_dpi_{scale_percent}_")
    service, window = _seed_workspace(application, Path(owner.name))
    try:
        boring = _operation_by_strategy(service, "boring_v1")
        _select_and_open(window, boring)
        _arrange(application, window, review_size=(1600, 900))
        actual_scale = window.cam_function_popup.devicePixelRatioF()
        expected_scale = scale_percent / 100.0
        if abs(actual_scale - expected_scale) > 0.05:
            raise AssertionError(
                f"Qt scale thực tế {actual_scale:g} không khớp {expected_scale:g}"
            )
        window.cam_function_popup.setWindowTitle(
            f"Chỉnh sửa CAM · High DPI {scale_percent}% · tiếng Việt"
        )
        filename = IMAGE_NAMES[19 if scale_percent == 125 else 20]
        return _screen_capture(application, output, filename)
    finally:
        window.project_controller.set_project_change_guard(None)
        window.cam_function_popup.invalidate_project()
        window.close()
        application.processEvents()
        if service.has_project:
            service.close_project(discard_changes=True)
        owner.cleanup()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reference_private/DERIVED/UI_STAGE_8A2_3_POPUP_COMPACT"),
    )
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--dpi-only", type=int, choices=(125, 150))
    arguments = parser.parse_args()
    if arguments.dpi_only is not None:
        capture = generate_dpi_review(arguments.output.resolve(), arguments.dpi_only)
        print("generated=1")
        print(capture)
        return 0
    captures = generate(arguments.output.resolve(), arguments.workspace)
    print(f"generated={len(captures)}")
    print(captures[-1])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
