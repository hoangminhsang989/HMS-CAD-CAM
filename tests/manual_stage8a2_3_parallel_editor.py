"""Native Windows GUI review harness for Parallel Function Editor 8A.2.3."""

from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path
import sys
import tempfile
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QPointF, QRect, Qt
from PySide6.QtGui import QColor, QFont, QFontInfo, QImage, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QStackedWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QWidget,
)

from hms_cadcam.cam.application import basic_mill_resources
from hms_cadcam.cam.cam3d import Cam3DProjectConfig
from hms_cadcam.cam.cam3d.parallel import (
    ParallelFinishingGenerator,
    ParallelGeometryEvidence,
    ParallelProgress,
    ParallelProgressPhase,
    calculate_and_publish_parallel_finishing,
)
from hms_cadcam.cam.domain import (
    ArtifactStatus,
    DiagnosticCode,
    DiagnosticSeverity,
    LengthUnit,
    ValidationDiagnostic,
)
from hms_cadcam.cam.toolpath import MarkerEvent
from hms_cadcam.project.service import ProjectService
from hms_cadcam.ui.function_editor import (
    FunctionEditorDraftState,
    FunctionEditorPage,
    ParameterDisclosureLevel,
)
from hms_cadcam.ui.function_editor.parallel_widgets import (
    ParallelSafetyDiagnosticsDialog,
)
from hms_cadcam.ui.function_editor.strategies.parallel import (
    ParallelEditorContext,
    ParallelEditorDraftContext,
    build_parallel_schema,
    parallel_applied_values,
    parallel_draft_derived_values,
    parallel_validation_diagnostics,
)
from hms_cadcam.ui.theme import APP_STYLE
from hms_cadcam.ui.ui_tokens import WORKSPACE_STYLE
from tests.unit._parallel_finishing_fixtures import (
    disconnected_fixture,
    planar_fixture,
)
from tests.unit._parallel_finishing_safety_fixtures import (
    adjacent_wall_fixture,
    holder_collision_fixture,
    safe_holder_fixture,
)
from tests.unit.test_parallel_finishing_persistence import _snapshot


IMAGE_NAMES = (
    "parallel_basic_1366x768.png",
    "parallel_basic_1600x900.png",
    "parallel_basic_1920x1080.png",
    "parallel_advanced_1600x900.png",
    "parallel_geometry_selection_1600x900.png",
    "parallel_tool_holder_verified_1600x900.png",
    "parallel_tool_holder_absent_1600x900.png",
    "parallel_validation_error_1600x900.png",
    "parallel_calculating_1600x900.png",
    "parallel_safe_ready_1600x900.png",
    "parallel_unsafe_diagnostics_1600x900.png",
    "parallel_unknown_holder_1600x900.png",
    "parallel_stale_v2_1600x900.png",
    "parallel_cancelled_1600x900.png",
    "parallel_operation_manager_1600x900.png",
    "parallel_simulation_gate_1600x900.png",
    "parallel_post_gate_1600x900.png",
    "parallel_dirty_state_1600x900.png",
    "parallel_disabled_1600x900.png",
    "UI_STAGE_8A2_3_VI_MONTAGE.png",
)

MONTAGE_LABELS = (
    "Cơ bản · 1366×768",
    "Cơ bản · 1600×900",
    "Cơ bản · 1920×1080",
    "Nâng cao",
    "Đã chọn bề mặt",
    "Holder đã xác minh",
    "Chưa khai báo Holder",
    "Lỗi kiểm tra",
    "Đang tính toán",
    "An toàn · Sẵn sàng",
    "Chẩn đoán không an toàn",
    "Chưa xác định Holder",
    "Cần tính lại · thuật toán v2",
    "Đã hủy",
    "Quản lý nguyên công",
    "Mô phỏng khả dụng",
    "Post bị chặn",
    "Bản nháp đã sửa",
    "Đã tắt",
)


class DirectionViewport(QWidget):
    """Deterministic review-only viewport overlay; never enters project state."""

    def __init__(self) -> None:
        super().__init__()
        self.state = "CHƯA TÍNH TOÁN"
        self.note = "U hướng lượt cắt · V hướng bước ngang · W trục Tool"
        self.setMinimumSize(430, 320)

    def set_state(self, state: str, note: str = "") -> None:
        self.state = state
        if note:
            self.note = note
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#17212b"))
        margin = 55
        body = self.rect().adjusted(margin, 80, -margin, -75)
        painter.setBrush(QColor("#435465"))
        painter.setPen(QPen(QColor("#8aa0b5"), 2))
        painter.drawRoundedRect(body, 8, 8)
        painter.setPen(QPen(QColor("#61c7d9"), 2))
        step = max(18, body.height() // 11)
        for y in range(body.top() + 18, body.bottom() - 8, step):
            painter.drawLine(body.left() + 20, y, body.right() - 20, y)
        origin = QPointF(body.center().x(), body.center().y())
        axes = (
            (QPointF(100, 0), QColor("#ff6978"), "U"),
            (QPointF(0, -90), QColor("#79d47d"), "V"),
            (QPointF(70, -65), QColor("#73a7ff"), "W"),
        )
        painter.setFont(QFont("Segoe UI", 11, QFont.Weight.DemiBold))
        for delta, color, label in axes:
            end = origin + delta
            painter.setPen(QPen(color, 4))
            painter.drawLine(origin, end)
            painter.drawText(int(end.x() + 7), int(end.y() - 5), label)
        painter.setPen(QColor("#f4f7fa"))
        painter.setFont(QFont("Segoe UI", 16, QFont.Weight.DemiBold))
        painter.drawText(QRect(24, 20, self.width() - 48, 32), self.state)
        painter.setFont(QFont("Segoe UI", 10))
        painter.setPen(QColor("#c0ccd7"))
        painter.drawText(
            self.rect().adjusted(24, 0, -24, -24),
            Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft,
            self.note,
        )
        painter.end()


class ReviewWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("HMS CAD/CAM · Trình sửa Gia công tinh song song")
        root = QWidget()
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(1)
        self.manager = QTreeWidget()
        self.manager.setObjectName("OperationManager")
        self.manager.setHeaderLabels(("Quản lý nguyên công", "Trạng thái"))
        self.manager.setMinimumWidth(270)
        self.manager.setMaximumWidth(320)
        job = QTreeWidgetItem(("Công việc duyệt song song", "HIỆN HÀNH"))
        setup = QTreeWidgetItem(("Thiết lập 1 · G54", "HIỆN HÀNH"))
        self.operation = QTreeWidgetItem(
            ("Gia công tinh song song", "CHƯA KIỂM TRA")
        )
        setup.addChild(self.operation)
        job.addChild(setup)
        self.manager.addTopLevelItem(job)
        self.manager.expandAll()
        self.manager.setCurrentItem(self.operation)
        layout.addWidget(self.manager)
        self.center = QStackedWidget()
        self.viewport = DirectionViewport()
        self.center.addWidget(self.viewport)
        self.center.setCurrentWidget(self.viewport)
        layout.addWidget(self.center, 1)
        self.editor_container = QWidget()
        self.editor_layout = QHBoxLayout(self.editor_container)
        self.editor_layout.setContentsMargins(0, 0, 0, 0)
        self.page: FunctionEditorPage | None = None
        self.editor_container.setFixedWidth(460)
        layout.addWidget(self.editor_container)
        self.setCentralWidget(root)
        self.statusBar().showMessage(
            "Thuật toán v3 · Dữ liệu chiến lược v1 · "
            "Khoảng an toàn để điều khiển máy: Chưa xác minh"
        )

    def show_context(
        self,
        context: ParallelEditorContext,
        machine_id: str,
        state: str,
        *,
        diagnostic: bool = False,
        calculating: bool = False,
        section: str | None = None,
        advanced: bool = False,
    ) -> FunctionEditorPage:
        self.show_viewport()
        if self.page is not None:
            self.editor_layout.removeWidget(self.page)
            self.page.deleteLater()
        values = parallel_applied_values(context)
        values["machine_id"] = machine_id
        schema = build_parallel_schema(context)
        draft = ParallelEditorDraftContext(
            context.zone.part_surfaces.selection.surfaces
            if context.zone is not None
            else (),
            geometry_evidence=context.geometry_evidence,
        )
        page = FunctionEditorPage(
            FunctionEditorDraftState(
                schema,
                values,
                generation=1,
                validation_callback=lambda current: parallel_validation_diagnostics(
                    schema, context, draft, current
                ),
                draft_transform_callback=lambda current: parallel_draft_derived_values(
                    context, draft, current
                ),
            ),
        )
        self.editor_layout.addWidget(page)
        self.page = page
        self.viewport.set_state(state)
        self.operation.setText(1, state)
        self.operation.setToolTip(1, state)
        if advanced:
            index = page.disclosure_selector.findData(ParameterDisclosureLevel.ADVANCED)
            page.disclosure_selector.setCurrentIndex(index)
            page._section_widgets["advanced"].set_expanded(True)
        if diagnostic:
            index = page.disclosure_selector.findData(ParameterDisclosureLevel.ADVANCED)
            page.disclosure_selector.setCurrentIndex(index)
            for section_widget in page._section_widgets.values():
                section_widget.set_expanded(False)
            page._field_changed("stepover_override_enabled", True)
            page._field_changed("stepover_mm", "0")
            page.validate_draft()
        if calculating:
            for section_widget in page._section_widgets.values():
                section_widget.set_expanded(False)
            page.set_calculation_active(True)
            page.update_calculation_progress(
                ParallelProgress(
                    context.operation.operation_id,
                    ParallelProgressPhase.SAFETY_VALIDATION,
                    7,
                    10,
                )
            )
        if section is not None:
            definition = schema.section(section)
            if definition.disclosure_level > page.maximum_disclosure:
                index = page.disclosure_selector.findData(
                    definition.disclosure_level
                )
                page.disclosure_selector.setCurrentIndex(index)
            for section_id, section_widget in page._section_widgets.items():
                section_widget.set_expanded(section_id == section)
            page.scroll_area.ensureWidgetVisible(page._section_widgets[section])
        return page

    def show_diagnostics(self, context: ParallelEditorContext) -> None:
        self.editor_container.hide()
        dialog = ParallelSafetyDiagnosticsDialog(
            context.safety_report,
            context.operation.diagnostics,
        )
        dialog.setWindowFlags(Qt.WindowType.Widget)
        self.center.addWidget(dialog)
        self.center.setCurrentWidget(dialog)
        dialog.show()

    def show_viewport(self) -> None:
        self.editor_container.show()
        self.center.setCurrentWidget(self.viewport)


def _context(
    fixture,
    machine,
    *,
    operation=None,
    artifact=None,
    report=None,
    holders=(),
) -> ParallelEditorContext:
    operation = operation or fixture.operation
    setup = replace(
        _snapshot(fixture, operation).jobs[0].setups[0],
        name="Thiết lập song song",
    )
    assembly = replace(fixture.assembly, name="Cụm dao cầu song song")
    tool = replace(fixture.tool, name="Dao cầu")
    return ParallelEditorContext(
        "Gia công tinh song song",
        operation,
        setup,
        fixture.zone.job_id,
        fixture.zone.project_id,
        fixture.zone,
        (assembly,),
        (tool,),
        tuple(holders),
        (machine,),
        artifact,
        report,
        geometry_evidence=ParallelGeometryEvidence(
            0.0, 30.0, 0.0, 12.0, "Hộp bao phục vụ duyệt giao diện"
        ),
    )


def _v2_artifact(artifact):
    events = tuple(
        replace(
            event,
            metadata=tuple(
                (key, "2" if key == "algorithm_version" else value)
                for key, value in event.metadata
            ),
        )
        if isinstance(event, MarkerEvent)
        and event.semantic_key == "parallel.safety.contract"
        else event
        for event in artifact.events
    )
    return replace(artifact, events=events, artifact_fingerprint=None)


def _capture(
    application: QApplication,
    window: ReviewWindow,
    output: Path,
    filename: str,
    size: tuple[int, int] = (1600, 900),
) -> Path:
    window.resize(*size)
    window.show()
    window.raise_()
    window.activateWindow()
    for _ in range(4):
        application.processEvents()
    if (
        window.editor_container.isVisible()
        and (window.page is None or not window.page.footer.isVisible())
    ):
        raise AssertionError("Parallel footer is not reachable")
    if window.page.scroll_area.horizontalScrollBar().maximum() != 0:
        raise AssertionError("Parallel editor has a horizontal scrollbar")
    observer = getattr(window, "runtime_observer", None)
    if observer is not None:
        observer(Path(filename).stem, window)
    path = output / filename
    if getattr(window, "write_review_images", True) and not window.grab().save(str(path)):
        raise RuntimeError(f"Could not save {path}")
    return path


def _montage(output: Path, entries: tuple[Path, ...]) -> Path:
    columns, cell_w, cell_h = 4, 520, 330
    rows = (len(entries) + columns - 1) // columns
    canvas = QImage(columns * cell_w, rows * cell_h, QImage.Format.Format_ARGB32)
    canvas.fill(QColor("#e7edf2"))
    painter = QPainter(canvas)
    painter.setFont(QFont("Segoe UI", 10, QFont.Weight.DemiBold))
    painter.setPen(QColor("#233444"))
    try:
        for index, path in enumerate(entries):
            image = QImage(str(path))
            if image.isNull():
                raise RuntimeError(f"Could not read {path}")
            x = (index % columns) * cell_w
            y = (index // columns) * cell_h
            painter.drawText(
                QRect(x + 8, y + 5, cell_w - 16, 22),
                MONTAGE_LABELS[index],
            )
            scaled = image.scaled(
                cell_w - 16,
                cell_h - 38,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            painter.drawImage(x + (cell_w - scaled.width()) // 2, y + 30, scaled)
    finally:
        painter.end()
    path = output / IMAGE_NAMES[-1]
    if not canvas.save(str(path)):
        raise RuntimeError(f"Could not save {path}")
    return path


def generate(
    output: Path,
    workspace_root: Path | None = None,
    *,
    observer: Callable[[str, QWidget], None] | None = None,
    write_images: bool = True,
) -> tuple[Path, ...]:
    if write_images:
        output.mkdir(parents=True, exist_ok=True)
    application = QApplication.instance() or QApplication([])
    application.setQuitOnLastWindowClosed(False)
    if write_images and application.platformName().casefold() != "windows":
        raise RuntimeError("Stage 8A.2.3 GUI review requires native Windows QPA")
    family = QFontInfo(application.font()).family()
    if write_images and (not family or family.casefold() in {"null", "fixed"}):
        raise RuntimeError("Native Windows UI font did not resolve")
    application.setStyleSheet(APP_STYLE + WORKSPACE_STYLE)

    owner = None
    if workspace_root is None:
        owner = tempfile.TemporaryDirectory(prefix="hms_parallel_editor_")
        workspace_root = Path(owner.name)
    service = ProjectService.create_default(workspace_root / "config")
    project = service.new_project(workspace_root, "Parallel Editor Review")
    base = planar_fixture(project_id=project.manifest.project_id, stepover=5.0)
    machine = basic_mill_resources(LengthUnit.MM)[3]
    service.stage_cam_snapshot(
        replace(_snapshot(base, base.operation), machine_definitions=(machine,))
    )
    service.stage_cam3d_config(Cam3DProjectConfig(project.manifest.project_id, (base.zone,)))
    service.save()

    safe = calculate_and_publish_parallel_finishing(
        project.root_path,
        base.operation,
        base.context,
        assembly=base.assembly,
        tool=base.tool,
    )
    unsafe_fixture, _ = adjacent_wall_fixture()
    unsafe = calculate_and_publish_parallel_finishing(
        project.root_path,
        unsafe_fixture.operation,
        unsafe_fixture.context,
        assembly=unsafe_fixture.assembly,
        tool=unsafe_fixture.tool,
    )
    unknown_fixture, _ = holder_collision_fixture()
    unknown = calculate_and_publish_parallel_finishing(
        project.root_path,
        unknown_fixture.operation,
        unknown_fixture.context,
        assembly=unknown_fixture.assembly,
        tool=unknown_fixture.tool,
    )
    verified_fixture, verified_holder = safe_holder_fixture()
    verified = calculate_and_publish_parallel_finishing(
        project.root_path,
        verified_fixture.operation,
        verified_fixture.context,
        assembly=verified_fixture.assembly,
        tool=verified_fixture.tool,
        holder=verified_holder,
    )
    if not safe.accepted or not verified.accepted:
        raise AssertionError("Deterministic SAFE fixtures did not publish")
    if unsafe.safety_report is None or unknown.safety_report is None:
        raise AssertionError("Deterministic safety fixtures did not report")

    base_context = _context(base, machine)
    safe_context = _context(
        base,
        machine,
        operation=safe.operation,
        artifact=safe.artifact,
        report=safe.safety_report,
    )
    unsafe_context = _context(
        unsafe_fixture,
        machine,
        operation=unsafe.operation,
        report=unsafe.safety_report,
    )
    unknown_context = _context(
        unknown_fixture,
        machine,
        operation=unknown.operation,
        report=unknown.safety_report,
    )
    verified_context = _context(
        verified_fixture,
        machine,
        operation=verified.operation,
        artifact=verified.artifact,
        report=verified.safety_report,
        holders=(verified_holder,),
    )
    disconnected = disconnected_fixture(stepover=5.0)
    geometry_context = _context(disconnected, machine)
    generator = ParallelFinishingGenerator()
    inputs = generator.resolve_inputs(
        base.operation,
        base.context,
        assembly=base.assembly,
        tool=base.tool,
    )
    computing, _token = generator.begin(inputs)
    calculating_context = _context(base, machine, operation=computing.operation)
    stale_context = _context(
        base,
        machine,
        operation=safe.operation,
        artifact=_v2_artifact(safe.artifact),
    )
    cancelled_diagnostic = ValidationDiagnostic(
        DiagnosticSeverity.ERROR,
        DiagnosticCode.PARALLEL_CANCELLED,
        "Tính toán Gia công tinh song song đã bị hủy; không công bố kết quả dở dang.",
        (("safety_status", "cancelled"),),
    )
    cancelled_operation = replace(
        base.operation,
        artifact_state=replace(
            base.operation.artifact_state,
            status=ArtifactStatus.FAILED,
            dirty_reasons=(),
            diagnostics=(cancelled_diagnostic,),
        ),
    )
    cancelled_context = _context(base, machine, operation=cancelled_operation)
    dirty_context = _context(
        base,
        machine,
        operation=replace(
            safe.operation,
            artifact_state=safe.operation.artifact_state.mark_dirty(
                next(iter(base.operation.artifact_state.dirty_reasons))
            ),
        ),
    )
    disabled_context = _context(
        base,
        machine,
        operation=replace(base.operation, enabled=False),
    )

    window = ReviewWindow()
    window.runtime_observer = observer
    window.write_review_images = write_images
    captures: list[Path] = []
    try:
        window.show_context(base_context, str(machine.machine_id), "CHƯA TÍNH TOÁN")
        captures.append(_capture(application, window, output, IMAGE_NAMES[0], (1366, 768)))
        captures.append(_capture(application, window, output, IMAGE_NAMES[1]))
        captures.append(_capture(application, window, output, IMAGE_NAMES[2], (1920, 1080)))

        window.show_context(base_context, str(machine.machine_id), "NÂNG CAO", advanced=True)
        captures.append(_capture(application, window, output, IMAGE_NAMES[3]))
        window.show_context(geometry_context, str(machine.machine_id), "ĐÃ CHỌN 2 BỀ MẶT", section="geometry")
        captures.append(_capture(application, window, output, IMAGE_NAMES[4]))
        window.show_context(verified_context, str(machine.machine_id), "ĐÃ KIỂM TRA PHẠM VI", section="tool")
        captures.append(_capture(application, window, output, IMAGE_NAMES[5]))
        window.show_context(safe_context, str(machine.machine_id), "CHƯA KHAI BÁO HOLDER", section="tool")
        captures.append(_capture(application, window, output, IMAGE_NAMES[6]))
        window.show_context(base_context, str(machine.machine_id), "LỖI KIỂM TRA", diagnostic=True)
        captures.append(_capture(application, window, output, IMAGE_NAMES[7]))
        window.show_context(calculating_context, str(machine.machine_id), "ỨNG VIÊN · ĐANG TÍNH TOÁN", calculating=True)
        captures.append(_capture(application, window, output, IMAGE_NAMES[8]))
        window.show_context(safe_context, str(machine.machine_id), "SẴN SÀNG · ĐÃ KIỂM TRA PHẠM VI", section="capability_safety")
        captures.append(_capture(application, window, output, IMAGE_NAMES[9]))
        window.show_context(unsafe_context, str(machine.machine_id), "KHÔNG AN TOÀN", section="capability_safety")
        window.show_diagnostics(unsafe_context)
        captures.append(_capture(application, window, output, IMAGE_NAMES[10]))
        window.show_viewport()
        window.show_context(unknown_context, str(machine.machine_id), "CHƯA XÁC ĐỊNH · HOLDER KHÔNG KHẢ DỤNG", section="capability_safety")
        captures.append(_capture(application, window, output, IMAGE_NAMES[11]))
        window.show_context(stale_context, str(machine.machine_id), "CẦN TÍNH LẠI · THUẬT TOÁN v2", section="capability_safety")
        captures.append(_capture(application, window, output, IMAGE_NAMES[12]))
        window.show_context(cancelled_context, str(machine.machine_id), "ĐÃ HỦY", section="capability_safety")
        captures.append(_capture(application, window, output, IMAGE_NAMES[13]))
        window.manager.setMinimumWidth(320)
        window.show_context(safe_context, str(machine.machine_id), "QUẢN LÝ NGUYÊN CÔNG · ĐÃ KIỂM TRA PHẠM VI")
        captures.append(_capture(application, window, output, IMAGE_NAMES[14]))
        window.manager.setMinimumWidth(270)
        window.show_context(safe_context, str(machine.machine_id), "MÔ PHỎNG KHẢ DỤNG", section="capability_safety")
        captures.append(_capture(application, window, output, IMAGE_NAMES[15]))
        window.show_context(safe_context, str(machine.machine_id), "POST BỊ CHẶN", section="capability_safety")
        captures.append(_capture(application, window, output, IMAGE_NAMES[16]))
        dirty_page = window.show_context(dirty_context, str(machine.machine_id), "CẦN TÍNH LẠI · ĐÃ SỬA")
        dirty_page._field_changed("stepover_override_enabled", True)
        dirty_page._field_changed("stepover_mm", "4.25")
        captures.append(_capture(application, window, output, IMAGE_NAMES[17]))
        window.show_context(disabled_context, str(machine.machine_id), "ĐÃ TẮT")
        captures.append(_capture(application, window, output, IMAGE_NAMES[18]))
        if observer is not None:
            window.show_context(
                safe_context,
                str(machine.machine_id),
                "KIỂM TRA HIỂN THỊ ĐẦY ĐỦ",
                advanced=True,
            )
            if window.page is not None:
                for section_widget in window.page._section_widgets.values():
                    section_widget.set_expanded(True)
            application.processEvents()
            observer("parallel_all_sections_runtime", window)
        if write_images:
            captures.append(_montage(output, tuple(captures)))
    finally:
        window.close()
        application.processEvents()
        service.close_project()
        if owner is not None:
            owner.cleanup()
    expected_names = IMAGE_NAMES if write_images else IMAGE_NAMES[:-1]
    if tuple(path.name for path in captures) != expected_names:
        raise AssertionError("Stage 8A.2.3 review package is incomplete")
    return tuple(captures)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reference_private/DERIVED/UI_STAGE_8A2_3"),
    )
    parser.add_argument("--workspace", type=Path)
    args = parser.parse_args()
    paths = generate(args.output.resolve(), args.workspace)
    print(f"generated={len(paths)}")
    print(paths[-1])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
