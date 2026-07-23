"""Native Windows review package for automatic-first Parallel CAM UX."""

from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont, QFontInfo, QImage, QPainter
from PySide6.QtWidgets import QApplication

from hms_cadcam.cam.application import (
    CamQualityProfile,
    basic_mill_resources,
    basic_parallel_resources,
)
from hms_cadcam.cam.cam3d import Cam3DProjectConfig
from hms_cadcam.cam.cam3d.parallel import ParallelGeometryEvidence
from hms_cadcam.cam.cam3d.parallel import calculate_and_publish_parallel_finishing
from hms_cadcam.cam.domain import Length, LengthUnit
from hms_cadcam.project.service import ProjectService
from hms_cadcam.ui.function_editor import ParameterDisclosureLevel
from hms_cadcam.ui.theme import APP_STYLE
from hms_cadcam.ui.ui_tokens import WORKSPACE_STYLE
from tests.manual_stage8a2_3_parallel_editor import (
    ReviewWindow,
    _capture,
    _context,
)
from tests.unit._parallel_finishing_fixtures import planar_fixture
from tests.unit._parallel_finishing_safety_fixtures import holder_collision_fixture
from tests.unit.test_parallel_finishing_persistence import _snapshot


IMAGE_NAMES = (
    "parallel_auto_basic_1366x768.png",
    "parallel_auto_basic_1600x900.png",
    "parallel_auto_summary_1600x900.png",
    "parallel_auto_quality_fast_1600x900.png",
    "parallel_auto_quality_balanced_1600x900.png",
    "parallel_auto_quality_high_1600x900.png",
    "parallel_auto_advanced_1600x900.png",
    "parallel_auto_manual_override_1600x900.png",
    "parallel_auto_override_invalid_1600x900.png",
    "parallel_auto_tool_changed_1600x900.png",
    "parallel_auto_geometry_changed_1600x900.png",
    "parallel_auto_needs_confirmation_1600x900.png",
    "parallel_auto_safe_ready_1600x900.png",
    "parallel_auto_unknown_holder_1600x900.png",
)

LABELS = (
    "Cơ bản · tự động mặc định",
    "Cơ bản · 1600 × 900",
    "Tóm tắt tự động",
    "Chất lượng · Nhanh",
    "Chất lượng · Cân bằng",
    "Chất lượng · Cao",
    "Nâng cao · Tự động",
    "Nâng cao · Tùy chỉnh hợp lệ",
    "Nâng cao · Tùy chỉnh không hợp lệ",
    "Đổi dao · tự tính lại",
    "Đổi hình học · tự tính lại",
    "Thiếu dữ liệu hình học · cần xác nhận",
    "An toàn · sẵn sàng",
    "Holder · chưa xác định",
)


def _set_advanced(page, *sections: str) -> None:
    index = page.disclosure_selector.findData(ParameterDisclosureLevel.ADVANCED)
    page.disclosure_selector.setCurrentIndex(index)
    for section_id, widget in page._section_widgets.items():
        widget.set_expanded(section_id in sections)


def _focus_summary(page, field_id: str) -> None:
    page._section_widgets["automatic_summary"].set_expanded(True)
    page.scroll_area.ensureWidgetVisible(page._field_widgets[field_id], 20, 20)


def _montage(output: Path, images: tuple[Path, ...]) -> Path:
    columns, cell_w, cell_h = 3, 520, 330
    rows = (len(images) + columns - 1) // columns
    canvas = QImage(columns * cell_w, rows * cell_h, QImage.Format.Format_ARGB32)
    canvas.fill(QColor("#e7edf2"))
    painter = QPainter(canvas)
    painter.setFont(QFont("Segoe UI", 10, QFont.Weight.DemiBold))
    painter.setPen(QColor("#233444"))
    try:
        for index, path in enumerate(images):
            source = QImage(str(path))
            if source.isNull():
                raise RuntimeError(f"Không thể đọc ảnh {path}")
            x = (index % columns) * cell_w
            y = (index // columns) * cell_h
            painter.drawText(QRect(x + 8, y + 5, cell_w - 16, 22), LABELS[index])
            scaled = source.scaled(
                cell_w - 16,
                cell_h - 38,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            painter.drawImage(x + (cell_w - scaled.width()) // 2, y + 30, scaled)
    finally:
        painter.end()
    path = output / "UI_STAGE_8A2_3_AUTO_MONTAGE.png"
    if not canvas.save(str(path)):
        raise RuntimeError(f"Không thể lưu montage {path}")
    return path


def generate(output: Path, workspace_root: Path | None = None) -> tuple[Path, ...]:
    output.mkdir(parents=True, exist_ok=True)
    application = QApplication.instance() or QApplication([])
    application.setQuitOnLastWindowClosed(False)
    if application.platformName().casefold() != "windows":
        raise RuntimeError("Gói duyệt Stage 8A.2.3 Auto yêu cầu Qt Windows native")
    family = QFontInfo(application.font()).family()
    if not family or family.casefold() in {"null", "fixed"}:
        raise RuntimeError("Không tìm thấy font Windows native")
    application.setStyleSheet(APP_STYLE + WORKSPACE_STYLE)

    owner = None
    if workspace_root is None:
        owner = tempfile.TemporaryDirectory(prefix="hms_parallel_auto_review_")
        workspace_root = Path(owner.name)
    service = ProjectService.create_default(workspace_root / "config")
    project = service.new_project(workspace_root, "Parallel Auto Review")
    fixture = planar_fixture(
        width=42.0,
        height=14.0,
        project_id=project.manifest.project_id,
        stepover=5.0,
    )
    machine = basic_mill_resources(LengthUnit.MM)[3]
    service.stage_cam_snapshot(
        replace(_snapshot(fixture, fixture.operation), machine_definitions=(machine,))
    )
    service.stage_cam3d_config(
        Cam3DProjectConfig(project.manifest.project_id, (fixture.zone,))
    )
    service.save()
    base = replace(
        _context(fixture, machine),
        geometry_evidence=ParallelGeometryEvidence(
            0.0, 42.0, 0.0, 14.0, "Hộp bao vùng gia công 42 × 14 mm"
        ),
    )
    geometry_changed = replace(
        base,
        geometry_evidence=ParallelGeometryEvidence(
            0.0, 12.0, 0.0, 46.0, "Hộp bao vùng gia công mới 12 × 46 mm"
        ),
    )
    second_tool, second_holder, second_assembly, _second_machine = (
        basic_parallel_resources(LengthUnit.MM)
    )
    second_tool = replace(
        second_tool,
        name="Dao cầu D6 tự động",
        cutting_geometry=replace(
            second_tool.cutting_geometry,
            diameter=Length(6.0, LengthUnit.MM),
        ),
    )
    second_assembly = replace(
        second_assembly,
        name="Cụm dao cầu D6",
        expected_tool_fingerprint=second_tool.content_fingerprint,
    )
    tool_changed = replace(
        base,
        tool_assemblies=base.tool_assemblies + (second_assembly,),
        tool_definitions=base.tool_definitions + (second_tool,),
        holder_definitions=(second_holder,),
    )
    disabled = replace(
        base,
        geometry_evidence=None,
    )
    safe = calculate_and_publish_parallel_finishing(
        project.root_path,
        fixture.operation,
        fixture.context,
        assembly=fixture.assembly,
        tool=fixture.tool,
    )
    if not safe.accepted or safe.artifact is None or safe.safety_report is None:
        raise AssertionError("Fixture SAFE không tạo được bằng chứng review")
    safe_context = replace(
        _context(
            fixture,
            machine,
            operation=safe.operation,
            artifact=safe.artifact,
            report=safe.safety_report,
        ),
        geometry_evidence=base.geometry_evidence,
    )
    unknown_fixture, _unknown_holder = holder_collision_fixture()
    unknown = calculate_and_publish_parallel_finishing(
        project.root_path,
        unknown_fixture.operation,
        unknown_fixture.context,
        assembly=unknown_fixture.assembly,
        tool=unknown_fixture.tool,
    )
    if unknown.safety_report is None:
        raise AssertionError("Fixture UNKNOWN không tạo được safety report")
    unknown_context = _context(
        unknown_fixture,
        machine,
        operation=unknown.operation,
        report=unknown.safety_report,
    )

    window = ReviewWindow()
    captures: list[Path] = []
    try:
        window.show_context(base, str(machine.machine_id), "TỰ ĐỘNG · CÂN BẰNG")
        captures.append(
            _capture(
                application,
                window,
                output,
                IMAGE_NAMES[0],
                (1366, 768),
            )
        )
        captures.append(_capture(application, window, output, IMAGE_NAMES[1]))
        page = window.show_context(base, str(machine.machine_id), "TÓM TẮT TỰ ĐỘNG")
        _focus_summary(page, "automatic_direction_summary")
        captures.append(_capture(application, window, output, IMAGE_NAMES[2]))

        for profile, filename, state in (
            (CamQualityProfile.FAST, IMAGE_NAMES[3], "HỒ SƠ NHANH"),
            (CamQualityProfile.BALANCED, IMAGE_NAMES[4], "HỒ SƠ CÂN BẰNG"),
            (CamQualityProfile.HIGH, IMAGE_NAMES[5], "HỒ SƠ CHẤT LƯỢNG CAO"),
        ):
            page = window.show_context(base, str(machine.machine_id), state)
            page._field_changed("quality_profile", profile.value)
            page._refresh_values()
            captures.append(_capture(application, window, output, filename))

        page = window.show_context(base, str(machine.machine_id), "NÂNG CAO · TỰ ĐỘNG")
        _set_advanced(page, "direction", "cut_parameters")
        captures.append(_capture(application, window, output, IMAGE_NAMES[6]))

        page = window.show_context(base, str(machine.machine_id), "NÂNG CAO · THỦ CÔNG HỢP LỆ")
        _set_advanced(page, "cut_parameters")
        page._field_changed("stepover_override_enabled", True)
        page._field_changed("stepover_mm", "1.2")
        page._field_changed("tolerance_override_enabled", True)
        page._field_changed("tolerance_mm", "0.008")
        page._refresh_values()
        captures.append(_capture(application, window, output, IMAGE_NAMES[7]))

        page = window.show_context(base, str(machine.machine_id), "LỖI NHẬP THỦ CÔNG")
        _set_advanced(page, "cut_parameters")
        page._field_changed("stepover_override_enabled", True)
        page._field_changed("stepover_mm", "-2")
        page._refresh_values()
        page.validate_draft()
        captures.append(_capture(application, window, output, IMAGE_NAMES[8]))

        page = window.show_context(tool_changed, str(machine.machine_id), "ĐỔI DAO · TỰ TÍNH LẠI")
        page._field_changed("tool_assembly_id", str(second_assembly.assembly_id))
        page._refresh_values()
        _focus_summary(page, "automatic_stepover_summary")
        captures.append(_capture(application, window, output, IMAGE_NAMES[9]))

        page = window.show_context(geometry_changed, str(machine.machine_id), "ĐỔI HÌNH HỌC · TỰ TÍNH LẠI")
        _focus_summary(page, "automatic_direction_summary")
        captures.append(_capture(application, window, output, IMAGE_NAMES[10]))

        page = window.show_context(disabled, str(machine.machine_id), "CẦN XÁC NHẬN HƯỚNG")
        _focus_summary(page, "automatic_direction_summary")
        captures.append(_capture(application, window, output, IMAGE_NAMES[11]))
        window.show_context(
            safe_context,
            str(machine.machine_id),
            "SẴN SÀNG · ĐÃ KIỂM TRA PHẠM VI",
            section="capability_safety",
        )
        captures.append(_capture(application, window, output, IMAGE_NAMES[12]))
        window.show_context(
            unknown_context,
            str(machine.machine_id),
            "CHƯA XÁC ĐỊNH · HOLDER KHÔNG KHẢ DỤNG",
            section="capability_safety",
        )
        captures.append(_capture(application, window, output, IMAGE_NAMES[13]))
        captures.append(_montage(output, tuple(captures)))
    finally:
        window.close()
        application.processEvents()
        service.close_project()
        if owner is not None:
            owner.cleanup()
    expected = IMAGE_NAMES + ("UI_STAGE_8A2_3_AUTO_MONTAGE.png",)
    if tuple(path.name for path in captures) != expected:
        raise AssertionError("Gói ảnh review Auto chưa đầy đủ")
    return tuple(captures)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reference_private/DERIVED/UI_STAGE_8A2_3_AUTO"),
    )
    parser.add_argument("--workspace", type=Path)
    arguments = parser.parse_args()
    captures = generate(arguments.output.resolve(), arguments.workspace)
    print(f"generated={len(captures)}")
    print(captures[-1])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
