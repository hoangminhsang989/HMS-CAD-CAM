"""Native Windows review set for the one-screen grid and fit-inside artwork."""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from hms_cadcam.cam.domain import Operation
from hms_cadcam.ui.function_editor import ParameterDisclosureLevel
from tests.manual_stage8a2_3_popup_editor import (
    _arrange,
    _operation_by_strategy,
    _planar_facing_operation,
    _screen_capture,
    _seed_workspace,
    _select_and_open,
)


IMAGE_NAMES = (
    "grid_parallel_basic_1366x768.png",
    "grid_parallel_basic_1600x900.png",
    "grid_parallel_no_scroll_1366x768.png",
    "grid_parallel_auto_summary.png",
    "grid_parallel_advanced.png",
    "illustration_aspect_ratio_wide_popup.png",
    "illustration_aspect_ratio_tall_popup.png",
    "illustration_compact_vs_expanded.png",
    "illustration_facing.png",
    "illustration_planar_facing.png",
    "illustration_contour.png",
    "illustration_pocket.png",
    "illustration_drilling.png",
    "illustration_tapping.png",
    "illustration_reaming.png",
    "illustration_boring.png",
    "illustration_parallel_one_way.png",
    "illustration_parallel_zigzag.png",
    "illustration_parallel_direct_link.png",
    "illustration_parallel_retract_link.png",
    "illustration_quality_fast.png",
    "illustration_quality_balanced.png",
    "illustration_quality_high.png",
    "grid_long_vietnamese_labels.png",
)


def _parallel_page(window):
    page = window.function_editor_host.active_page
    if page is None or page.schema.editor_id != "parallel_finishing_production_8a2_3":
        raise AssertionError("Không mở đúng Parallel production editor")
    return page


def _assert_basic_one_screen(page) -> None:
    QApplication.processEvents()
    if page.maximum_disclosure is not ParameterDisclosureLevel.BASIC:
        raise AssertionError("Ảnh Basic đang ở sai disclosure level")
    if page.responsive_grid_columns != 2:
        raise AssertionError("Parallel Basic chưa dùng grid hai cột")
    if page.scroll_area.verticalScrollBar().maximum() != 0:
        raise AssertionError(
            "Parallel Basic vẫn còn vertical scrollbar: "
            f"maximum={page.scroll_area.verticalScrollBar().maximum()}, "
            f"page={page.size().toTuple()}, "
            f"viewport={page.scroll_area.viewport().size().toTuple()}, "
            f"content_hint={page.content.sizeHint().toTuple()}, "
            f"columns={page.responsive_grid_columns}, "
            f"sections={[(key, widget.sizeHint().toTuple(), widget.geometry().getRect(), widget.field_columns) for key, widget in page._section_widgets.items() if not widget.isHidden()]}, "
            f"illustration={page.illustration_panel.sizeHint().toTuple()}"
        )
    if page.scroll_area.horizontalScrollBar().maximum() != 0:
        raise AssertionError("Parallel Basic vẫn còn horizontal scrollbar")
    if not page.footer.isVisible():
        raise AssertionError("Footer không hiển thị cùng Basic")


def _basic(page) -> None:
    index = page.disclosure_selector.findData(ParameterDisclosureLevel.BASIC)
    page.disclosure_selector.setCurrentIndex(index)
    QApplication.processEvents()


def _set_parallel_artwork(page, **values: object) -> None:
    panel = page.illustration_panel
    if panel is None:
        raise AssertionError("Parallel editor thiếu illustration viewport")
    current = {
        "cut_direction": "one_way",
        "linking_mode": "retract",
        "quality_profile": "balanced",
        "effective_direction_angle_degrees": 20.0,
    }
    current.update(values)
    panel.set_values(current)
    panel.flush_pending_update()
    QApplication.processEvents()


def _capture_operation(
    application: QApplication,
    window,
    output: Path,
    operation: Operation,
    filename: str,
) -> Path:
    page = _select_and_open(window, operation)
    if page is None or page.illustration_panel is None:
        raise AssertionError(f"Operation {operation.strategy_key} thiếu minh họa")
    _arrange(application, window, review_size=(1600, 900))
    _basic(page)
    if page.responsive_grid_columns != 2:
        raise AssertionError(f"{operation.strategy_key} chưa dùng grid hai cột")
    if page.scroll_area.verticalScrollBar().maximum() != 0:
        raise AssertionError(
            f"{operation.strategy_key} Basic còn cuộn dọc: "
            f"maximum={page.scroll_area.verticalScrollBar().maximum()}, "
            f"content={page.content.sizeHint().toTuple()}, "
            f"viewport={page.scroll_area.viewport().size().toTuple()}, "
            f"sections={[(key, widget.sizeHint().toTuple(), len([field for field in widget._fields if not field.isHidden()])) for key, widget in page._section_widgets.items() if not widget.isHidden()]}"
        )
    page.illustration_panel.enlarge_button.click()
    QTest.qWait(100)
    application.processEvents()
    captured = _screen_capture(application, output, filename)
    window.cam_function_popup.close_child_popup()
    return captured


def _combine_pair(left: Path, right: Path, output: Path) -> Path:
    first, second = QImage(str(left)), QImage(str(right))
    if first.isNull() or second.isNull():
        raise RuntimeError("Không đọc được ảnh compact/expanded")
    height = max(first.height(), second.height())
    canvas = QImage(first.width() + second.width(), height, QImage.Format.Format_ARGB32)
    canvas.fill(QColor("#e7edf2"))
    painter = QPainter(canvas)
    painter.drawImage(0, 0, first)
    painter.drawImage(first.width(), 0, second)
    painter.end()
    path = output / "illustration_compact_vs_expanded.png"
    if not canvas.save(str(path)):
        raise RuntimeError(f"Không thể lưu {path}")
    return path


def _montage(output: Path, images: tuple[Path, ...]) -> Path:
    columns, cell_width, cell_height = 4, 420, 260
    rows = (len(images) + columns - 1) // columns
    canvas = QImage(
        columns * cell_width,
        rows * cell_height,
        QImage.Format.Format_ARGB32,
    )
    canvas.fill(QColor("#e7edf2"))
    painter = QPainter(canvas)
    painter.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
    painter.setPen(QColor("#233444"))
    try:
        for index, path in enumerate(images):
            source = QImage(str(path))
            if source.isNull():
                raise RuntimeError(f"Không đọc được {path}")
            x = index % columns * cell_width
            y = index // columns * cell_height
            painter.drawText(
                QRect(x + 7, y + 3, cell_width - 14, 22), path.stem
            )
            scaled = source.scaled(
                cell_width - 14,
                cell_height - 32,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            painter.drawImage(
                x + (cell_width - scaled.width()) // 2,
                y + 26,
                scaled,
            )
    finally:
        painter.end()
    path = output / "UI_STAGE_8A2_3_GRID_ILLUSTRATIONS_MONTAGE.png"
    if not canvas.save(str(path)):
        raise RuntimeError(f"Không thể lưu {path}")
    return path


def generate(output: Path) -> tuple[Path, ...]:
    """Generate the deterministic 24-frame review plus one montage."""
    output.mkdir(parents=True, exist_ok=True)
    application = QApplication.instance() or QApplication([])
    application.setQuitOnLastWindowClosed(False)
    if application.platformName().casefold() != "windows":
        raise RuntimeError("Review grid phải chạy bằng Qt QPA Windows native")
    application.setFont(QFont("Segoe UI", 9))
    owner = tempfile.TemporaryDirectory(prefix="hms_grid_illustrations_")
    service, window = _seed_workspace(application, Path(owner.name))
    captures: list[Path] = []
    try:
        parallel = _operation_by_strategy(service, "parallel_finishing_3d")
        _select_and_open(window, parallel)
        _arrange(application, window, review_size=(1366, 768))
        page = _parallel_page(window)
        _basic(page)
        _assert_basic_one_screen(page)
        captures.append(_screen_capture(application, output, IMAGE_NAMES[0], (1366, 768)))

        _arrange(application, window, review_size=(1600, 900))
        _assert_basic_one_screen(page)
        captures.append(_screen_capture(application, output, IMAGE_NAMES[1]))

        _arrange(application, window, review_size=(1366, 768))
        _assert_basic_one_screen(page)
        captures.append(_screen_capture(application, output, IMAGE_NAMES[2], (1366, 768)))
        page.scroll_area.ensureWidgetVisible(page._section_widgets["automatic_summary"])
        captures.append(_screen_capture(application, output, IMAGE_NAMES[3], (1366, 768)))

        _arrange(application, window, review_size=(1600, 900))
        advanced = page.disclosure_selector.findData(ParameterDisclosureLevel.ADVANCED)
        page.disclosure_selector.setCurrentIndex(advanced)
        page._section_widgets["cut_parameters"].set_expanded(True)
        captures.append(_screen_capture(application, output, IMAGE_NAMES[4]))
        _basic(page)

        panel = page.illustration_panel
        panel.enlarge_button.click()
        QTest.qWait(120)
        child = window.cam_function_popup.child_dialog
        if child is None:
            raise AssertionError("Không mở được illustration child popup")
        child.resize(760, 340)
        captures.append(_screen_capture(application, output, IMAGE_NAMES[5]))
        child.resize(420, 650)
        captures.append(_screen_capture(application, output, IMAGE_NAMES[6]))
        window.cam_function_popup.close_child_popup()

        temp_compact = Path(owner.name) / "compact.png"
        temp_expanded = Path(owner.name) / "expanded.png"
        panel.set_expanded(False)
        _screen_capture(application, temp_compact.parent, temp_compact.name)
        panel.set_expanded(True)
        _screen_capture(application, temp_expanded.parent, temp_expanded.name)
        panel.set_expanded(False)
        captures.append(_combine_pair(temp_compact, temp_expanded, output))

        for strategy, filename in (
            ("facing_2_5d", IMAGE_NAMES[8]),
            ("contour_2d", IMAGE_NAMES[10]),
            ("pocket_2_5d", IMAGE_NAMES[11]),
            ("drilling_v1", IMAGE_NAMES[12]),
            ("tapping_v1", IMAGE_NAMES[13]),
            ("reaming_v1", IMAGE_NAMES[14]),
            ("boring_v1", IMAGE_NAMES[15]),
        ):
            captures.append(
                _capture_operation(
                    application,
                    window,
                    output,
                    _operation_by_strategy(service, strategy),
                    filename,
                )
            )
        captures.insert(
            9,
            _capture_operation(
                application,
                window,
                output,
                _planar_facing_operation(service),
                IMAGE_NAMES[9],
            ),
        )

        _select_and_open(window, parallel)
        _arrange(application, window, review_size=(1600, 900))
        page = _parallel_page(window)
        dynamic_states = (
            ({"cut_direction": "one_way"}, IMAGE_NAMES[16]),
            ({"cut_direction": "zigzag"}, IMAGE_NAMES[17]),
            ({"cut_direction": "zigzag", "linking_mode": "direct"}, IMAGE_NAMES[18]),
            ({"cut_direction": "one_way", "linking_mode": "retract"}, IMAGE_NAMES[19]),
            ({"quality_profile": "fast"}, IMAGE_NAMES[20]),
            ({"quality_profile": "balanced"}, IMAGE_NAMES[21]),
            ({"quality_profile": "high"}, IMAGE_NAMES[22]),
        )
        for values, filename in dynamic_states:
            _set_parallel_artwork(page, **values)
            page.illustration_panel.enlarge_button.click()
            QTest.qWait(100)
            application.processEvents()
            captures.append(_screen_capture(application, output, filename))
            window.cam_function_popup.close_child_popup()

        page.summary.context.setText(
            "Tool cầu chuyên dụng · Bề mặt gia công có tên tiếng Việt rất dài · "
            "liên kết an toàn và thông số tự động vẫn phải đọc đầy đủ"
        )
        page.summary.context.setToolTip(page.summary.context.text())
        captures.append(_screen_capture(application, output, IMAGE_NAMES[23]))
    finally:
        window.project_controller.set_project_change_guard(None)
        window.cam_function_popup.invalidate_project()
        window.close()
        application.processEvents()
        if service.has_project:
            service.close_project(discard_changes=True)
        owner.cleanup()

    ordered = tuple(output / name for name in IMAGE_NAMES)
    missing = tuple(path.name for path in ordered if not path.exists())
    if missing:
        raise AssertionError(f"Thiếu ảnh review grid: {missing}")
    montage = _montage(output, ordered)
    return ordered + (montage,)


if __name__ == "__main__":
    target = Path(
        "reference_private/DERIVED/UI_STAGE_8A2_3_GRID_ILLUSTRATIONS"
    )
    result = generate(target)
    print(f"generated={len(result)}")
    print(target.resolve())
