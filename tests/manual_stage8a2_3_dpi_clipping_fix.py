"""Native Windows evidence for the Stage 8A.2.3 DPI/clipping correction."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QFont, QFontInfo, QFontMetrics, QImage, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from hms_cadcam.project.service import ProjectService
from hms_cadcam.ui.cam_illustrations import CAMIllustrationDialog
from hms_cadcam.ui.operation_manager_types import OperationManagerNodeKind
from tests.manual_stage8a2_3_final_gui import _set_parallel_state
from tests.manual_stage8a2_3_popup_editor import (
    _arrange,
    _assert_review_font_environment,
    _operation_by_strategy,
    _parallel_page,
    _screen_capture,
    _seed_workspace,
    _select_and_open,
)


IMAGE_NAMES = (
    "fixed_basic_1366x768_100.png",
    "fixed_basic_1600x900_100.png",
    "fixed_basic_1920x1080_100.png",
    "fixed_sections_geometry_tool_quality.png",
    "fixed_summary_six_values.png",
    "fixed_footer_1366x768.png",
    "fixed_operation_manager_1366x768.png",
    "fixed_long_tool_1366x768.png",
    "fixed_long_caption_1600x900.png",
    "fixed_dpi_125_1600x900.png",
    "fixed_dpi_125_1920x1080.png",
    "fixed_dpi_150_1600x900.png",
    "fixed_dpi_150_1920x1080.png",
    "fixed_dpi_150_operation_manager.png",
    "fixed_dpi_150_summary.png",
    "fixed_parallel_one_way.png",
    "fixed_parallel_direct_link.png",
    "fixed_parallel_retract_link.png",
    "fixed_child_illustration_popup.png",
)

LABELS = (
    "Basic 1366×768 · 100%",
    "Basic 1600×900 · 100%",
    "Basic 1920×1080 · 100%",
    "Hình học · Tool · Chất lượng",
    "Tóm tắt · đủ sáu giá trị",
    "Footer · 1366×768",
    "Operation Manager · 1366×768",
    "Tool dài · 1366×768",
    "Caption dài · 1600×900",
    "DPI 125% · 1600×900",
    "DPI 125% · 1920×1080",
    "DPI 150% · 1600×900",
    "DPI 150% · 1920×1080",
    "DPI 150% · Operation Manager",
    "DPI 150% · Summary",
    "Parallel · Một chiều",
    "Parallel · Liên kết trực tiếp",
    "Parallel · Rút dao",
    "Child popup · Đóng minh họa",
)

MONTAGE_NAME = "UI_STAGE_8A2_3_DPI_CLIPPING_FIX_MONTAGE.png"
_SECTION_IDS = ("geometry", "tool", "quality", "automatic_summary")


def _mapped_rect(widget: QWidget, ancestor: QWidget) -> QRect:
    return QRect(widget.mapTo(ancestor, QPoint()), widget.size())


def _assert_inside(widget: QWidget, ancestor: QWidget) -> None:
    if not widget.isVisible():
        raise AssertionError(f"Widget bị ẩn: {widget.objectName()}")
    mapped = _mapped_rect(widget, ancestor)
    if not ancestor.contentsRect().contains(mapped):
        raise AssertionError(
            f"{widget.objectName()} {mapped.getRect()} vượt "
            f"{ancestor.objectName()} {ancestor.contentsRect().getRect()}"
        )


def _assert_label_baseline(label: QLabel) -> None:
    metrics = label.fontMetrics()
    if label.contentsRect().height() < metrics.ascent() + metrics.descent():
        raise AssertionError(f"Nhãn bị cắt baseline: {label.text()!r}")


def _assert_no_overlap(widgets: tuple[QWidget, ...], ancestor: QWidget) -> None:
    visible = tuple(widget for widget in widgets if widget.isVisible())
    rectangles = {
        widget: _mapped_rect(widget, ancestor) for widget in visible
    }
    for index, first in enumerate(visible):
        for second in visible[index + 1 :]:
            if rectangles[first].intersects(rectangles[second]):
                raise AssertionError(
                    f"{first.objectName()} overlap {second.objectName()}"
                )


def _assert_operation_manager(window) -> None:
    panel = window.operation_manager_host
    if panel.view.horizontalScrollBar().maximum() != 0:
        raise AssertionError("Operation Manager còn cuộn ngang")
    operation = next(
        node
        for node in panel.model.projection.nodes
        if node.kind is OperationManagerNodeKind.OPERATION
    )
    index = panel.model.index_for_node_id(operation.node_id)
    depth = 0
    parent = index.parent()
    while parent.isValid():
        depth += 1
        parent = parent.parent()
    readable = (
        panel.view.header().sectionSize(0)
        - depth * panel.view.indentation()
        - 25
    )
    minimum = panel.view.fontMetrics().horizontalAdvance("Gia công")
    if readable < minimum:
        raise AssertionError(
            f"Tên operation chỉ còn {readable}px logic; cần ít nhất {minimum}px"
        )
    tooltip = str(panel.model.data(index, Qt.ItemDataRole.ToolTipRole))
    if operation.label not in tooltip:
        raise AssertionError("Operation Manager thiếu tooltip tên đầy đủ")


def _assert_page(
    application: QApplication,
    window,
    *,
    expects_scroll: bool | None,
    reveal_section: str | None = None,
) -> None:
    page = _parallel_page(window)
    for _ in range(5):
        application.processEvents()
    if page.scroll_area.horizontalScrollBar().maximum() != 0:
        raise AssertionError("Parallel Basic còn cuộn ngang")
    has_scroll = page.scroll_area.verticalScrollBar().maximum() > 0
    if expects_scroll is not None and has_scroll is not expects_scroll:
        section_sizes = {
            key: (
                page._section_widgets[key].size().toTuple(),
                page._section_widgets[key].minimumSizeHint().toTuple(),
            )
            for key in _SECTION_IDS
        }
        root_geometry = tuple(
            (
                item.widget().objectName(),
                item.widget().geometry().getRect(),
                item.widget().sizeHint().toTuple(),
            )
            for index in range(page._root.count())
            if (item := page._root.itemAt(index)).widget() is not None
        )
        raise AssertionError(
            f"Vertical overflow={has_scroll}, dự kiến {expects_scroll}; "
            f"page={page.size().toTuple()}, "
            f"viewport={page.scroll_area.viewport().size().toTuple()}, "
            f"content={page.content.sizeHint().toTuple()}, "
            f"range={page.scroll_area.verticalScrollBar().maximum()}, "
            f"columns={page.responsive_grid_columns}, sections={section_sizes}, "
            f"root={root_geometry}"
        )
    _assert_inside(page.footer, page)
    if page.scroll_area.geometry().intersects(page.footer.geometry()):
        raise AssertionError("Footer overlap vùng nội dung")
    if page.illustration_panel is None:
        raise AssertionError("Parallel thiếu minh họa")
    scale_x, scale_y = page.illustration_panel.canvas.render_scale_factors
    if abs(scale_x - scale_y) > 1.0e-9:
        raise AssertionError("Minh họa bị kéo giãn")

    sections = tuple(page._section_widgets[item] for item in _SECTION_IDS)
    _assert_no_overlap((*sections, page.illustration_panel), page.content)
    for section in sections:
        if section.height() < section.minimumSizeHint().height():
            raise AssertionError(f"Section bị co dưới minimum: {section.objectName()}")
        _assert_label_baseline(section.title_label)
        _assert_inside(section.body, section)
        fields = tuple(field for field in section._fields if not field.isHidden())
        _assert_no_overlap(fields, section.body)
        for field in fields:
            _assert_inside(field, section.body)
            _assert_label_baseline(field.label)
            if field.editor.height() < field.editor.minimumSizeHint().height():
                raise AssertionError(
                    f"Control bị cắt: {field.definition.field_id}"
                )

    summary = page._section_widgets["automatic_summary"]
    for field in summary._fields:
        if field.isHidden() or not isinstance(field.editor, QLabel):
            continue
        _assert_label_baseline(field.editor)
        text_width = field.editor.fontMetrics().horizontalAdvance(
            field.editor.text()
        )
        if text_width > field.editor.contentsRect().width():
            raise AssertionError(
                f"Summary bị cắt: {field.definition.field_id} "
                f"{text_width}>{field.editor.contentsRect().width()}"
            )

    if reveal_section is not None:
        section = page._section_widgets[reveal_section]
        viewport = page.scroll_area.viewport()
        if section.height() <= viewport.height():
            page.scroll_area.ensureWidgetVisible(section, 0, 0)
            for _ in range(4):
                application.processEvents()
            _assert_inside(section, viewport)
        else:
            for field in section._fields:
                if field.isHidden():
                    continue
                page.scroll_area.ensureWidgetVisible(field, 0, 0)
                for _ in range(3):
                    application.processEvents()
                _assert_inside(field, viewport)
            section_top = section.mapTo(page.content, QPoint()).y()
            page.scroll_area.verticalScrollBar().setValue(section_top)
            for _ in range(4):
                application.processEvents()
            _assert_inside(section.title_label, viewport)
    elif expects_scroll is False:
        if page.content.sizeHint().height() > page.scroll_area.viewport().height():
            raise AssertionError("Scroll range 0 nhưng nội dung không fit")
    _assert_operation_manager(window)
    popup = window.cam_function_popup
    if popup.isMaximized() or popup.isFullScreen():
        raise AssertionError("Popup không được maximized/full-screen")


def _capture(
    captures: dict[str, Path],
    application: QApplication,
    output: Path,
    filename: str,
    size: tuple[int, int],
) -> Path:
    path = _screen_capture(application, output, filename, size)
    captures[filename] = path
    return path


def _montage(output: Path, images: tuple[Path, ...]) -> Path:
    columns, cell_width, cell_height = 5, 380, 245
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
                raise RuntimeError(f"Không đọc được ảnh {path}")
            x = index % columns * cell_width
            y = index // columns * cell_height
            painter.drawText(
                QRect(x + 7, y + 4, cell_width - 14, 22),
                LABELS[index],
            )
            scaled = source.scaled(
                cell_width - 14,
                cell_height - 34,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            painter.drawImage(
                x + (cell_width - scaled.width()) // 2,
                y + 27,
                scaled,
            )
    finally:
        painter.end()
    path = output / MONTAGE_NAME
    if not canvas.save(str(path)):
        raise RuntimeError(f"Không lưu được montage {path}")
    return path


def _logical_size(
    physical_size: tuple[int, int],
    scale_percent: int,
) -> tuple[int, int]:
    scale = scale_percent / 100.0
    return (
        round(physical_size[0] / scale),
        round(physical_size[1] / scale),
    )


def generate_dpi_review(output: Path, scale_percent: int) -> tuple[Path, ...]:
    """Generate the real-DPR subset in its own native Qt process."""
    output.mkdir(parents=True, exist_ok=True)
    application = QApplication.instance() or QApplication([])
    application.setQuitOnLastWindowClosed(False)
    if application.platformName().casefold() != "windows":
        raise RuntimeError("DPI review phải chạy với Qt QPA Windows native")
    _assert_review_font_environment(application)
    application.setFont(QFont("Segoe UI", 9))
    owner = tempfile.TemporaryDirectory(
        prefix=f"hms_dpi_clipping_{scale_percent}_"
    )
    service, window = _seed_workspace(application, Path(owner.name))
    captures: dict[str, Path] = {}
    try:
        parallel = _operation_by_strategy(service, "parallel_finishing_3d")
        page = _select_and_open(window, parallel)
        actual = window.cam_function_popup.devicePixelRatioF()
        expected = scale_percent / 100.0
        if abs(actual - expected) > 0.05:
            raise AssertionError(f"Qt DPR {actual:g} không khớp {expected:g}")

        if scale_percent == 125:
            cases = (
                ((1600, 900), IMAGE_NAMES[9]),
                ((1920, 1080), IMAGE_NAMES[10]),
            )
            for physical, filename in cases:
                _arrange(
                    application,
                    window,
                    review_size=_logical_size(physical, scale_percent),
                )
                page.scroll_area.verticalScrollBar().setValue(0)
                _assert_page(application, window, expects_scroll=False)
                _capture(captures, application, output, filename, physical)
        else:
            physical = (1600, 900)
            _arrange(
                application,
                window,
                review_size=_logical_size(physical, scale_percent),
            )
            _assert_page(
                application,
                window,
                expects_scroll=True,
                reveal_section="geometry",
            )
            _capture(captures, application, output, IMAGE_NAMES[11], physical)
            _assert_page(
                application,
                window,
                expects_scroll=True,
                reveal_section="automatic_summary",
            )
            _capture(captures, application, output, IMAGE_NAMES[14], physical)

            physical = (1920, 1080)
            _arrange(
                application,
                window,
                review_size=_logical_size(physical, scale_percent),
            )
            page.scroll_area.verticalScrollBar().setValue(0)
            _assert_page(application, window, expects_scroll=None)
            _capture(captures, application, output, IMAGE_NAMES[12], physical)
            _capture(captures, application, output, IMAGE_NAMES[13], physical)
        return tuple(captures[name] for name in IMAGE_NAMES if name in captures)
    finally:
        window.project_controller.set_project_change_guard(None)
        window.cam_function_popup.invalidate_project()
        if service.has_project:
            service.close_project(discard_changes=True)
        window.close()
        application.processEvents()
        owner.cleanup()


def generate(
    output: Path,
    workspace_root: Path | None = None,
) -> tuple[Path, ...]:
    """Generate 100% evidence, then combine prior real-DPR subprocess output."""
    output.mkdir(parents=True, exist_ok=True)
    application = QApplication.instance() or QApplication([])
    application.setQuitOnLastWindowClosed(False)
    if application.platformName().casefold() != "windows":
        raise RuntimeError("GUI review phải chạy với Qt QPA Windows native")
    _assert_review_font_environment(application)
    font = QFont("Segoe UI", 9)
    if not QFontInfo(font).family() or not QFontMetrics(font).inFontUcs4(ord("ữ")):
        raise RuntimeError("Segoe UI thiếu glyph tiếng Việt")
    application.setFont(font)
    owner = None
    if workspace_root is None:
        owner = tempfile.TemporaryDirectory(prefix="hms_dpi_clipping_100_")
        workspace_root = Path(owner.name)
    service, window = _seed_workspace(application, workspace_root)
    captures: dict[str, Path] = {}
    try:
        parallel = _operation_by_strategy(service, "parallel_finishing_3d")
        page = _select_and_open(window, parallel)
        for physical, filename in (
            ((1366, 768), IMAGE_NAMES[0]),
            ((1600, 900), IMAGE_NAMES[1]),
            ((1920, 1080), IMAGE_NAMES[2]),
        ):
            _arrange(application, window, review_size=physical)
            page.scroll_area.verticalScrollBar().setValue(0)
            _assert_page(application, window, expects_scroll=False)
            _capture(captures, application, output, filename, physical)

        _arrange(application, window, review_size=(1600, 900))
        _assert_page(application, window, expects_scroll=False)
        _capture(captures, application, output, IMAGE_NAMES[3], (1600, 900))
        _capture(captures, application, output, IMAGE_NAMES[4], (1600, 900))

        _arrange(application, window, review_size=(1366, 768))
        _assert_page(application, window, expects_scroll=False)
        _capture(captures, application, output, IMAGE_NAMES[5], (1366, 768))
        _capture(captures, application, output, IMAGE_NAMES[6], (1366, 768))

        page.summary._full_strategy_line = (
            "Bước ngang 0,8 mm · Dung sai 0,02 mm · Tự động"
        )
        page.summary._full_resource_line = (
            "Tool: Cụm dao cầu chuyên dụng tên rất dài Ø10 mm · "
            "Hình học: 18 bề mặt gia công"
        )
        page.summary._full_context = (
            f"{page.summary._full_strategy_line} · "
            f"{page.summary._full_resource_line}"
        )
        page.summary._refresh_elided_text()
        _capture(captures, application, output, IMAGE_NAMES[7], (1366, 768))

        _arrange(application, window, review_size=(1600, 900))
        _set_parallel_state(
            page,
            cut_direction="one_way",
            linking_mode="retract",
            semantic_focus="ordering",
        )
        _capture(captures, application, output, IMAGE_NAMES[8], (1600, 900))
        _capture(captures, application, output, IMAGE_NAMES[15], (1600, 900))
        _set_parallel_state(
            page,
            cut_direction="one_way",
            linking_mode="direct",
            semantic_focus="linking",
        )
        _capture(captures, application, output, IMAGE_NAMES[16], (1600, 900))
        _set_parallel_state(
            page,
            cut_direction="one_way",
            linking_mode="retract",
            semantic_focus="linking",
        )
        _capture(captures, application, output, IMAGE_NAMES[17], (1600, 900))

        page.illustration_panel.enlarge_button.setFocus()
        page.illustration_panel.enlarge_button.click()
        QTest.qWait(180)
        application.processEvents()
        child = window.cam_function_popup.child_dialog
        if not isinstance(child, CAMIllustrationDialog):
            raise AssertionError("Không mở được child illustration popup")
        if child.close_button.text() != "Đóng minh họa":
            raise AssertionError("Child popup sai wording Đóng minh họa")
        _capture(captures, application, output, IMAGE_NAMES[18], (1600, 900))
        child.close_button.click()
        application.processEvents()
        if not page.illustration_panel.enlarge_button.hasFocus():
            raise AssertionError("Child popup không khôi phục focus")
    finally:
        window.project_controller.set_project_change_guard(None)
        window.cam_function_popup.invalidate_project()
        if service.has_project:
            service.close_project(discard_changes=True)
        window.close()
        application.processEvents()
        if owner is not None:
            owner.cleanup()

    for filename in IMAGE_NAMES:
        if filename not in captures:
            path = output / filename
            if not path.exists():
                raise RuntimeError(
                    f"Thiếu {filename}; chạy --dpi-only 125 và 150 trước"
                )
            captures[filename] = path
    ordered = tuple(captures[name] for name in IMAGE_NAMES)
    montage = _montage(output, ordered)
    result = (*ordered, montage)
    if len(result) != 20:
        raise AssertionError("DPI clipping review chưa đủ 20 ảnh")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "reference_private/DERIVED/UI_STAGE_8A2_3_DPI_CLIPPING_FIX"
        ),
    )
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--dpi-only", type=int, choices=(125, 150))
    arguments = parser.parse_args()
    output = arguments.output.resolve()
    if arguments.dpi_only is not None:
        captures = generate_dpi_review(output, arguments.dpi_only)
    else:
        captures = generate(output, arguments.workspace)
    print(f"generated={len(captures)}")
    print(captures[-1])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
