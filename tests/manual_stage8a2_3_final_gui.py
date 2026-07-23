"""Native Windows final GUI evidence for Stage 8A.2.3 review."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont, QFontInfo, QFontMetrics, QImage, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from hms_cadcam.cam.domain import Operation
from hms_cadcam.project.service import ProjectService
from hms_cadcam.ui.cam_illustrations import CAMIllustrationDialog
from hms_cadcam.ui.localization import OPERATION_DISPLAY_NAMES
from tests.manual_stage8a2_3_popup_editor import (
    _arrange,
    _assert_review_font_environment,
    _operation_by_strategy,
    _parallel_page,
    _planar_facing_operation,
    _screen_capture,
    _seed_workspace,
    _select_and_open,
)


IMAGE_NAMES = (
    "final_parallel_basic_1366x768.png",
    "final_parallel_basic_1600x900.png",
    "final_operation_manager_localized.png",
    "final_operation_manager_long_names.png",
    "final_facing_title_vietnamese.png",
    "final_planar_facing_title_vietnamese.png",
    "final_contour_title_vietnamese.png",
    "final_pocket_title_vietnamese.png",
    "final_drilling_title_vietnamese.png",
    "final_tapping_title_vietnamese.png",
    "final_reaming_title_vietnamese.png",
    "final_boring_title_vietnamese.png",
    "final_parallel_one_way.png",
    "final_parallel_zigzag.png",
    "final_parallel_direct_link.png",
    "final_parallel_retract_link.png",
    "final_illustration_expanded.png",
    "final_illustration_child_popup.png",
    "final_illustration_child_focus_restore.png",
    "final_long_tool_summary_1366x768.png",
    "final_long_vietnamese_caption.png",
    "final_high_dpi_125.png",
    "final_high_dpi_150.png",
    "final_footer_and_no_scroll_1366x768.png",
)

LABELS = (
    "Parallel Basic · 1366 × 768",
    "Parallel Basic · 1600 × 900",
    "Operation Manager · tiếng Việt",
    "Operation Manager · tên dài",
    "Phay mặt 2.5D",
    "Phay các mặt phẳng",
    "Phay biên dạng 2D",
    "Phay hốc 2.5D",
    "Khoan",
    "Taro",
    "Doa lỗ",
    "Khoét lỗ",
    "Parallel · Một chiều",
    "Parallel · Zíc zắc",
    "Parallel · Liên kết trực tiếp",
    "Parallel · Rút dao bảo thủ",
    "Minh họa · Mở rộng + legend",
    "Minh họa · child popup",
    "Minh họa · khôi phục focus",
    "Summary Tool dài · 1366 × 768",
    "Caption tiếng Việt dài",
    "High DPI · 125%",
    "High DPI · 150%",
    "Footer + no-scroll · 1366 × 768",
)

MONTAGE_NAME = "UI_STAGE_8A2_3_FINAL_GUI_MONTAGE.png"


def _capture(
    captures: dict[str, Path],
    application: QApplication,
    output: Path,
    filename: str,
    size: tuple[int, int] = (1600, 900),
) -> Path:
    path = _screen_capture(application, output, filename, size)
    captures[filename] = path
    return path


def _assert_parallel_layout(window) -> None:
    page = _parallel_page(window)
    if page.basic_uses_vertical_scroll:
        raise AssertionError("Parallel Basic còn cuộn dọc")
    if page.scroll_area.horizontalScrollBar().maximum() != 0:
        raise AssertionError("Parallel Basic còn cuộn ngang")
    if not page.footer.isVisible():
        raise AssertionError("Footer Parallel không hiển thị")
    scale_x, scale_y = page.illustration_panel.canvas.render_scale_factors
    if abs(scale_x - scale_y) > 1.0e-9:
        raise AssertionError("Minh họa Parallel bị kéo giãn")


def _assert_title(window, expected: str) -> None:
    title = window.cam_function_popup.windowTitle()
    if expected not in title:
        raise AssertionError(f"Title chưa Việt hóa: {title!r}; cần {expected!r}")
    forbidden = tuple(OPERATION_DISPLAY_NAMES)
    if any(source in title for source in forbidden):
        raise AssertionError(f"Title còn tên nguyên công tiếng Anh: {title}")


def _rename_operation(
    service: ProjectService,
    operation: Operation,
    name: str,
) -> None:
    snapshot = service.cam_snapshot
    for job in snapshot.jobs:
        for setup in job.setups:
            if any(
                item.operation_id == operation.operation_id
                for item in setup.operation_tree.operations
            ):
                service.execute_cam_command(
                    lambda application, job_id=job.job_id, setup_id=setup.setup_id,
                    node_id=operation.node_id: application.update_tree(
                        job_id,
                        setup_id,
                        lambda tree: tree.rename_node(node_id, name),
                    )
                )
                return
    raise AssertionError("Không tìm thấy operation cần đổi tên trong harness")


def _set_parallel_state(
    page,
    *,
    cut_direction: str,
    linking_mode: str,
    semantic_focus: str,
) -> None:
    panel = page.illustration_panel
    values = dict(page.state.values)
    values["cut_direction"] = cut_direction
    values["linking_mode"] = linking_mode
    panel.set_values(values, semantic_focus=semantic_focus)
    panel.flush_pending_update()
    QApplication.processEvents()


def _montage(output: Path, images: tuple[Path, ...]) -> Path:
    columns, cell_width, cell_height = 4, 430, 275
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
                raise RuntimeError(f"Không đọc được ảnh review {path}")
            x = (index % columns) * cell_width
            y = (index // columns) * cell_height
            painter.drawText(
                QRect(x + 8, y + 4, cell_width - 16, 22), LABELS[index]
            )
            scaled = source.scaled(
                cell_width - 16,
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


def generate(
    output: Path,
    workspace_root: Path | None = None,
) -> tuple[Path, ...]:
    output.mkdir(parents=True, exist_ok=True)
    application = QApplication.instance() or QApplication([])
    application.setQuitOnLastWindowClosed(False)
    if application.platformName().casefold() != "windows":
        raise RuntimeError("Final GUI harness phải chạy với Qt QPA Windows native")
    _assert_review_font_environment(application)
    font = QFont("Segoe UI", 9)
    if not QFontInfo(font).family() or not QFontMetrics(font).inFontUcs4(ord("ệ")):
        raise RuntimeError("Font Windows không đủ glyph tiếng Việt")
    application.setFont(font)

    owner = None
    if workspace_root is None:
        owner = tempfile.TemporaryDirectory(prefix="hms_final_gui_")
        workspace_root = Path(owner.name)
    service, window = _seed_workspace(application, workspace_root)
    captures: dict[str, Path] = {}
    try:
        parallel = _operation_by_strategy(service, "parallel_finishing_3d")
        page = _select_and_open(window, parallel)
        _assert_title(window, "Gia công tinh song song")
        _arrange(application, window, review_size=(1366, 768))
        _assert_parallel_layout(window)
        _capture(
            captures,
            application,
            output,
            IMAGE_NAMES[0],
            (1366, 768),
        )
        _arrange(application, window, review_size=(1600, 900))
        _capture(captures, application, output, IMAGE_NAMES[1])
        _capture(captures, application, output, IMAGE_NAMES[2])

        editor_cases = (
            (_operation_by_strategy(service, "facing_2_5d"), "Phay mặt 2.5D", IMAGE_NAMES[4]),
            (_planar_facing_operation(service), "Phay các mặt phẳng", IMAGE_NAMES[5]),
            (_operation_by_strategy(service, "contour_2d"), "Phay biên dạng 2D", IMAGE_NAMES[6]),
            (_operation_by_strategy(service, "pocket_2_5d"), "Phay hốc 2.5D", IMAGE_NAMES[7]),
            (_operation_by_strategy(service, "drilling_v1"), "Khoan", IMAGE_NAMES[8]),
            (_operation_by_strategy(service, "tapping_v1"), "Taro", IMAGE_NAMES[9]),
            (_operation_by_strategy(service, "reaming_v1"), "Doa lỗ", IMAGE_NAMES[10]),
            (_operation_by_strategy(service, "boring_v1"), "Khoét lỗ", IMAGE_NAMES[11]),
        )
        for operation, expected, filename in editor_cases:
            _select_and_open(window, operation)
            _arrange(application, window, review_size=(1600, 900))
            _assert_title(window, expected)
            _capture(captures, application, output, filename)

        page = _select_and_open(window, parallel)
        _arrange(application, window, review_size=(1600, 900))
        state_cases = (
            ("one_way", "retract", "ordering", IMAGE_NAMES[12]),
            ("zigzag", "retract", "ordering", IMAGE_NAMES[13]),
            ("one_way", "direct", "linking", IMAGE_NAMES[14]),
            ("one_way", "retract", "linking", IMAGE_NAMES[15]),
        )
        fingerprints: set[str] = set()
        for cut, link, focus, filename in state_cases:
            _set_parallel_state(
                page,
                cut_direction=cut,
                linking_mode=link,
                semantic_focus=focus,
            )
            fingerprints.add(page.illustration_panel.state.render_fingerprint)
            _capture(captures, application, output, filename)
        if len(fingerprints) != 4:
            raise AssertionError("Bốn trạng thái minh họa chưa có fingerprint riêng")

        panel = page.illustration_panel
        panel.set_expanded(True)
        application.processEvents()
        if not panel.legend.isVisible():
            raise AssertionError("Expanded illustration thiếu legend")
        _capture(captures, application, output, IMAGE_NAMES[16])
        panel.enlarge_button.setFocus()
        panel.enlarge_button.click()
        QTest.qWait(180)
        application.processEvents()
        child = window.cam_function_popup.child_dialog
        if not isinstance(child, CAMIllustrationDialog):
            raise AssertionError("Không mở đúng child illustration popup")
        if child.close_button.text() != "Đóng minh họa":
            raise AssertionError("Child illustration còn action Đóng mơ hồ")
        _capture(captures, application, output, IMAGE_NAMES[17])
        child.close_button.click()
        QTest.qWait(140)
        application.processEvents()
        if not panel.enlarge_button.hasFocus():
            raise AssertionError("Đóng child không khôi phục focus về Phóng to")
        _capture(captures, application, output, IMAGE_NAMES[18])

        panel.set_expanded(False)
        _arrange(application, window, review_size=(1366, 768))
        page.summary._full_strategy_line = (
            "Bước ngang 0.8 mm · Dung sai 0.02 mm · Tự động"
        )
        page.summary._full_resource_line = (
            "Tool: Tool cầu chuyên dụng tên rất dài Ø10 mm · "
            "Hình học: 18 bề mặt gia công"
        )
        page.summary._full_context = (
            f"{page.summary._full_strategy_line} · "
            f"{page.summary._full_resource_line}"
        )
        page.summary._refresh_elided_text()
        _capture(
            captures,
            application,
            output,
            IMAGE_NAMES[19],
            (1366, 768),
        )
        _set_parallel_state(
            page,
            cut_direction="one_way",
            linking_mode="retract",
            semantic_focus="ordering",
        )
        _capture(captures, application, output, IMAGE_NAMES[20])

        popup = window.cam_function_popup
        for scale, filename in ((125, IMAGE_NAMES[21]), (150, IMAGE_NAMES[22])):
            existing = output / filename
            if existing.exists():
                captures[filename] = existing
                continue
            popup.setWindowTitle(
                f"Chỉnh sửa CAM · High DPI {scale}% · Gia công tinh song song"
            )
            _capture(captures, application, output, filename)

        _arrange(application, window, review_size=(1366, 768))
        _assert_parallel_layout(window)
        _capture(
            captures,
            application,
            output,
            IMAGE_NAMES[23],
            (1366, 768),
        )

        _rename_operation(
            service,
            _operation_by_strategy(service, "contour_2d"),
            "Phay biên dạng ngoài chi tiết khuôn chính xác",
        )
        _rename_operation(
            service,
            parallel,
            "Tinh mặt cong song song theo yêu cầu khách hàng",
        )
        window.cam_workspace.refresh()
        _select_and_open(window, parallel)
        _arrange(application, window, review_size=(1366, 768))
        application.processEvents()
        _capture(
            captures,
            application,
            output,
            IMAGE_NAMES[3],
            (1366, 768),
        )
    finally:
        window.project_controller.set_project_change_guard(None)
        window.cam_function_popup.invalidate_project()
        if service.has_project:
            service.close_project(discard_changes=True)
        window.close()
        application.processEvents()
        if owner is not None:
            owner.cleanup()

    ordered = tuple(captures[name] for name in IMAGE_NAMES)
    montage = _montage(output, ordered)
    result = (*ordered, montage)
    if len(result) != 25:
        raise AssertionError("Final GUI review chưa đủ 25 ảnh")
    return result


def generate_dpi_review(output: Path, scale_percent: int) -> Path:
    """Capture one native Qt DPI proof and verify its real DPR."""
    output.mkdir(parents=True, exist_ok=True)
    application = QApplication.instance() or QApplication([])
    application.setQuitOnLastWindowClosed(False)
    if application.platformName().casefold() != "windows":
        raise RuntimeError("Ảnh DPI phải chạy với Qt QPA Windows native")
    _assert_review_font_environment(application)
    application.setFont(QFont("Segoe UI", 9))
    owner = tempfile.TemporaryDirectory(prefix=f"hms_final_dpi_{scale_percent}_")
    service, window = _seed_workspace(application, Path(owner.name))
    try:
        parallel = _operation_by_strategy(service, "parallel_finishing_3d")
        _select_and_open(window, parallel)
        _arrange(application, window, review_size=(1600, 900))
        actual = window.cam_function_popup.devicePixelRatioF()
        expected = scale_percent / 100.0
        if abs(actual - expected) > 0.05:
            raise AssertionError(f"Qt DPR {actual:g} không khớp {expected:g}")
        window.cam_function_popup.setWindowTitle(
            f"Chỉnh sửa CAM · High DPI {scale_percent}% · "
            "Gia công tinh song song"
        )
        filename = IMAGE_NAMES[21 if scale_percent == 125 else 22]
        return _screen_capture(application, output, filename)
    finally:
        window.project_controller.set_project_change_guard(None)
        window.cam_function_popup.invalidate_project()
        if service.has_project:
            service.close_project(discard_changes=True)
        window.close()
        application.processEvents()
        owner.cleanup()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reference_private/DERIVED/UI_STAGE_8A2_3_FINAL_GUI"),
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
