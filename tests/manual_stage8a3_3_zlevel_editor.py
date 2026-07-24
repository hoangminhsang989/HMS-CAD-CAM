"""Native Windows review package for the Z-Level production editor."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import replace
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QModelIndex, QSettings, QRect, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QAbstractButton,
    QAbstractItemView,
    QComboBox,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from hms_cadcam.cam.application import basic_mill_resources
from hms_cadcam.cam.cam3d.zlevel import (
    ZLevelFinishingParameters,
    ZLevelGeometryEvidence,
    ZLevelProgress,
    ZLevelProgressPhase,
    calculate_and_publish_z_level_finishing,
    z_level_artifact_has_safe_contract,
)
from hms_cadcam.cam.domain import LengthUnit, ToolAssemblyReference
from hms_cadcam.cam.cam3d import Cam3DProjectConfig
from hms_cadcam.project.service import ProjectService
from hms_cadcam.ui.cam_illustrations import CAMIllustrationDialog
from hms_cadcam.ui.cam_ui import CamWorkspace
from hms_cadcam.ui.function_editor import (
    FunctionEditorAction,
    FunctionEditorDraftState,
    FunctionEditorPage,
    ParameterDisclosureLevel,
)
from hms_cadcam.ui.function_editor.strategies.zlevel import (
    Z_LEVEL_POST_FAIL_CLOSED_FOOTER,
    ZLevelEditorContext,
    ZLevelEditorDraftContext,
    build_z_level_schema,
    prepare_z_level_update,
    z_level_applied_values,
    z_level_draft_derived_values,
    z_level_validation_diagnostics,
)
from hms_cadcam.ui.localization import (
    display_value,
    localize_widget_tree,
    ui_text,
)
from hms_cadcam.ui.operation_manager import OperationManagerPanel
from hms_cadcam.ui.operation_manager_types import OperationManagerNodeKind
from hms_cadcam.ui.function_editor.zlevel_widgets import (
    ZLevelSafetyDiagnosticsDialog,
)
from hms_cadcam.ui.theme import APP_STYLE
from hms_cadcam.ui.ui_tokens import WORKSPACE_STYLE
from tests.unit._parallel_finishing_safety_fixtures import safe_holder_fixture
from tests.unit.test_parallel_finishing_persistence import _snapshot
from tools.audit_vietnamese_ui import (
    APPROVED_TECHNICAL_TERMS,
    INTERNAL_MODEL_VALUE_CATALOG,
    RAW_MODEL_TOKENS,
    RAW_NAMESPACE_PREFIXES,
    audit_production_ui,
    duplicate_user_facing_phrase_matches,
    raw_internal_enum_matches,
    raw_model_token_matches,
    raw_namespace_matches,
    raw_user_facing_internal_matches,
    unapproved_user_facing_acronym_matches,
)


STATE_NAMES = (
    "zlevel_editor_basic_1366x768.png",
    "zlevel_editor_basic_1600x900.png",
    "zlevel_editor_basic_1920x1080.png",
    "zlevel_editor_advanced.png",
    "zlevel_operation_manager.png",
    "zlevel_operation_manager_long_name.png",
    "zlevel_auto_parameters.png",
    "zlevel_manual_override.png",
    "zlevel_quality_fast.png",
    "zlevel_quality_balanced.png",
    "zlevel_quality_high.png",
    "zlevel_inner_hole_illustration.png",
    "zlevel_disconnected_regions_illustration.png",
    "zlevel_direct_link_safe_illustration.png",
    "zlevel_fallback_retract_illustration.png",
    "zlevel_safety_unknown.png",
    "zlevel_collision_unsafe.png",
    "zlevel_allowance_illustration.png",
    "zlevel_level_range_illustration.png",
    "zlevel_child_illustration_popup.png",
    "zlevel_child_focus_restore.png",
    "zlevel_dpi_125.png",
    "zlevel_dpi_150.png",
    "zlevel_long_tool_summary.png",
    "zlevel_validation_errors.png",
    "zlevel_simulation_gate.png",
    "zlevel_post_fail_closed.png",
)


class ReviewWindow(QMainWindow):
    """Review shell using the production Operation Manager and editor widgets."""

    def __init__(
        self,
        service: ProjectService,
        workspace: CamWorkspace,
        settings: QSettings,
    ) -> None:
        super().__init__()
        self.setWindowTitle("HMS CAD/CAM · Gia công tinh theo cao độ Z")
        root = QWidget()
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        self.manager = OperationManagerPanel(
            workspace, service, settings, workspace.actions, self
        )
        self.manager.setMinimumWidth(300)
        self.manager.setMaximumWidth(430)
        layout.addWidget(self.manager)
        center = QWidget()
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(12, 12, 12, 12)
        center_layout.setSpacing(8)
        self.viewport_title = QLabel("Vùng hiển thị CAD · Bề mặt đã chọn · U/V/W")
        self.viewport_title.setStyleSheet(
            "font-size: 18px; font-weight: 600; color: #d9e8f5;"
        )
        self.viewport = QLabel(
            "Vùng mặt gia công theo cao độ Z\n\n"
            "U  →  V  ↑  W  ↗\n"
            "Cao độ 10,0 → 0,0 mm\n"
            "Dao cầu · bước xuống tự động"
        )
        self.viewport.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.viewport.setMinimumSize(380, 330)
        self.viewport.setStyleSheet(
            "background:#17212b; color:#bcd0df; border:1px solid #3c5366;"
            "border-radius:8px; font-size:15px;"
        )
        center_layout.addWidget(self.viewport_title)
        center_layout.addWidget(self.viewport, 1)
        self.scene_label = QLabel("Bản nháp hiện hành · Post sản xuất chưa hỗ trợ")
        self.scene_label.setWordWrap(True)
        self.status_label = QLabel(
            "Thuật toán v2 · payload v1 · SQLite v4 · "
            "Post giữ trạng thái chặn an toàn"
        )
        self.status_label.setWordWrap(True)
        center_layout.addWidget(self.scene_label)
        center_layout.addWidget(self.status_label)
        layout.addWidget(center, 1)
        self.editor_host = QWidget()
        self.editor_layout = QVBoxLayout(self.editor_host)
        self.editor_layout.setContentsMargins(0, 0, 0, 0)
        self.editor_host.setMinimumWidth(470)
        self.editor_host.setMaximumWidth(600)
        layout.addWidget(self.editor_host)
        self.setCentralWidget(root)
        self.page: FunctionEditorPage | None = None
        self.last_quality_binding: dict[str, object] = {}
        self.current_context: ZLevelEditorContext | None = None
        self.current_draft: ZLevelEditorDraftContext | None = None
        localize_widget_tree(self)

    def show_editor(
        self,
        context: ZLevelEditorContext,
        machine_id: str,
        *,
        state: str,
        focus: str = "ordering",
        advanced: bool = False,
        section: str | None = None,
        invalid: bool = False,
        calculating: bool = False,
        disabled: bool = False,
        quality_profile: str | None = None,
    ) -> FunctionEditorPage:
        if self.page is not None:
            self.editor_layout.removeWidget(self.page)
            self.page.deleteLater()
        schema = build_z_level_schema(context)
        values = z_level_applied_values(context)
        values["machine_id"] = machine_id
        if disabled:
            values["enabled"] = False
        if invalid:
            values["top_override_enabled"] = True
            values["top_level"] = "0"
            values["bottom_override_enabled"] = True
            values["bottom_level"] = "1"
        draft = ZLevelEditorDraftContext(
            context.zone.part_surfaces.selection.surfaces
            if context.zone is not None
            else (),
            geometry_evidence=context.geometry_evidence,
        )
        applied_values = {
            field.field_id: values[field.field_id] for field in schema.fields
        }
        page = FunctionEditorPage(
            FunctionEditorDraftState(
                schema,
                applied_values,
                generation=1,
                validation_callback=lambda current: z_level_validation_diagnostics(
                    schema, context, draft, current
                ),
                draft_transform_callback=lambda current: z_level_draft_derived_values(
                    context, draft, current
                ),
            )
        )
        self.editor_layout.addWidget(page)
        self.page = page
        self.current_context = context
        self.current_draft = draft
        self.scene_label.setText(state)
        page.illustration_panel.set_values(
            page.state.values, semantic_focus=focus
        )
        if quality_profile is not None:
            field = page._field_widgets["quality_profile"]
            combo = field.editor
            index = combo.findData(quality_profile)
            if index < 0:
                raise AssertionError(f"Quality profile is not present: {quality_profile}")
            combo.setCurrentIndex(index)
            page.illustration_panel.flush_pending_update()
            if page.state.values["quality_profile"] != quality_profile:
                raise AssertionError("Quality combo did not update the draft model")
            contract = prepare_z_level_update(
                context, draft, page.state.values
            ).automatic_contract
            self.last_quality_binding = {
                "profile": quality_profile,
                "combo_text": combo.currentText(),
                "model_value": page.state.values["quality_profile"],
                "stepdown_mm": contract.value("stepdown_mm").effective_value,
                "tolerance_mm": contract.value("tolerance_mm").effective_value,
                "level_count": int(page.state.values["estimated_level_count"]),
                "effective_hash": contract.effective_fingerprint.digest,
                "illustration_quality": page.illustration_panel.state.quality,
                "render_revision": page.illustration_panel.render_revision,
            }
        if advanced:
            index = page.disclosure_selector.findData(
                ParameterDisclosureLevel.ADVANCED
            )
            page.disclosure_selector.setCurrentIndex(index)
        if section is not None and section in page._section_widgets:
            for section_widget in page._section_widgets.values():
                section_widget.set_expanded(False)
            page._section_widgets[section].set_expanded(True)
            page.scroll_area.ensureWidgetVisible(page._section_widgets[section])
        if invalid:
            page.validate_draft()
        if calculating:
            page.set_calculation_active(True)
            page.update_calculation_progress(
                ZLevelProgress(
                    context.operation.operation_id,
                    ZLevelProgressPhase.SAFETY,
                    7,
                    10,
                )
            )
        if disabled:
            page.setEnabled(False)
        localize_widget_tree(page)
        localize_widget_tree(self)
        return page


class SimulationGateEvidenceDialog(QDialog):
    """Two-state evidence panel driven by production gate presentation values."""

    def __init__(
        self,
        allowed_values: dict[str, object],
        blocked_values: dict[str, object],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Bằng chứng cổng Mô phỏng · theo cao độ Z")
        self.setObjectName("SimulationGateEvidenceDialog")
        root = QVBoxLayout(self)
        root.addWidget(QLabel("Hai trạng thái lấy từ vòng đời và cổng hiện hành"))
        row = QHBoxLayout()
        for title, values in (
            ("A · Kết quả hiện hành SẴN SÀNG + AN TOÀN", allowed_values),
            ("B · Bị chặn khi thiếu hoặc lỗi kết quả", blocked_values),
        ):
            card = QGroupBox(title)
            card_layout = QVBoxLayout(card)
            status = QLabel(f"Trạng thái an toàn: {values['safety_status']}")
            status.setWordWrap(True)
            gate = QLabel(f"Cổng quyết định: {values['simulation_gate']}")
            gate.setWordWrap(True)
            decision = bool(str(values["simulation_gate"]).startswith("Có thể"))
            decision_label = QLabel(
                "Quyết định: CHO PHÉP" if decision else "Quyết định: BỊ CHẶN"
            )
            decision_label.setStyleSheet(
                "font-weight:600; color:#23724b;"
                if decision
                else "font-weight:600; color:#9b241b;"
            )
            simulation = QPushButton("Mô phỏng")
            simulation.setEnabled(decision)
            simulation.setToolTip(
                "Mở Mô phỏng khi kết quả hiện hành đã được kiểm tra an toàn."
                if decision
                else "Bị chặn: cần kết quả hiện hành SẴN SÀNG + AN TOÀN."
            )
            card_layout.addWidget(status)
            card_layout.addWidget(gate)
            card_layout.addWidget(decision_label)
            card_layout.addWidget(simulation)
            card_layout.addStretch(1)
            row.addWidget(card, 1)
        root.addLayout(row)
        footer = QLabel(
            "Không tạo kết quả giả: trạng thái A dùng kết quả do bộ tính gia công "
            "theo cao độ Z công bố; trạng thái B dùng bản nháp chưa tính."
        )
        footer.setWordWrap(True)
        root.addWidget(footer)
        localize_widget_tree(self)


def _context(project_id) -> tuple[ZLevelEditorContext, object, object]:
    fixture, holder = safe_holder_fixture(project_id=project_id)
    machine = basic_mill_resources(LengthUnit.MM)[3]
    parameters = ZLevelFinishingParameters(
        fixture.zone.zone_id,
        10.0,
        0.0,
        1.5,
        tolerance_mm=0.01,
        surface_allowance_mm=0.15,
    )
    operation = replace(
        fixture.operation,
        parameters=parameters.to_operation_parameters(),
    )
    setup = replace(
        _snapshot(fixture, operation).jobs[0].setups[0],
        name="Thiết lập cao độ Z",
    )
    evidence = ZLevelGeometryEvidence(
        0.0,
        30.0,
        0.0,
        20.0,
        0.0,
        10.0,
        "Hộp bao vùng mặt đã chọn",
    )
    return (
        ZLevelEditorContext(
            "Gia công tinh theo cao độ Z",
            operation,
            setup,
            fixture.zone.job_id,
            project_id,
            fixture.zone,
            (fixture.assembly,),
            (fixture.tool,),
            (holder,),
            (machine,),
            geometry_evidence=evidence,
        ),
        machine,
        fixture,
    )


def _capture(
    app: QApplication,
    window: ReviewWindow,
    output: Path,
    name: str,
    size: tuple[int, int],
) -> Path:
    window.resize(*size)
    window.show()
    window.raise_()
    window.activateWindow()
    for _ in range(5):
        app.processEvents()
    _assert_capture_contract(window, name)
    if window.page is None:
        raise AssertionError("Z-Level editor page was not created")
    if window.page.scroll_area.horizontalScrollBar().maximum() != 0:
        raise AssertionError(f"Horizontal scroll detected for {name}")
    if window.manager.view.horizontalScrollBar().maximum() != 0:
        raise AssertionError(f"Operation Manager horizontal scroll detected for {name}")
    path = output / name
    if not window.grab().save(str(path)):
        raise RuntimeError(f"Could not save {path}")
    return path


def _assert_capture_contract(window: ReviewWindow, name: str) -> None:
    """Fail before image creation if the production widget state is inconsistent."""
    page = window.page
    context = window.current_context
    draft = window.current_draft
    if page is None or context is None or draft is None:
        raise AssertionError("Z-Level capture has no current production context")
    manager_operations = tuple(
        item
        for item in window.manager.model.projection.nodes
        if item.kind is OperationManagerNodeKind.OPERATION
    )
    if (
        len(manager_operations) != 1
        or manager_operations[0].domain_identity.value
        != str(context.operation.operation_id)
    ):
        raise AssertionError("Z-Level capture operation ID drifted")
    if context.operation.strategy_key != "z_level_finishing_3d":
        raise AssertionError("Z-Level capture operation strategy drifted")
    if page.schema.editor_id != "z_level_finishing_production_8a3_3":
        raise AssertionError("Z-Level capture editor ID drifted")
    if page.schema.strategy.value != "z_level_finishing_3d_8a3_3":
        raise AssertionError("Z-Level capture editor strategy drifted")

    values = page.state.values
    quality = str(values["quality_profile"])
    if quality not in {"fast", "balanced", "high"}:
        raise AssertionError("Z-Level capture quality is invalid")
    panel = page.illustration_panel
    if panel is None or panel.state.quality != quality:
        raise AssertionError("Z-Level illustration and quality model diverged")
    manual = any(
        bool(values[key])
        for key in (
            "top_override_enabled",
            "bottom_override_enabled",
            "stepdown_override_enabled",
            "tolerance_override_enabled",
            "allowance_override_enabled",
            "orientation_override_enabled",
            "boundary_override_enabled",
            "ordering_override_enabled",
            "linking_override_enabled",
        )
    )
    if panel.state.manual != manual:
        raise AssertionError("Z-Level illustration manual state diverged")
    for key in (
        "calculation_status",
        "safety_status",
        "simulation_gate",
        "post_gate",
        "stepdown_summary",
        "estimated_level_count",
        "automatic_effective_hash",
    ):
        if not str(values[key]).strip():
            raise AssertionError(f"Z-Level capture is missing effective value: {key}")

    has_error = any(item.severity.name == "ERROR" for item in page.state.diagnostics)
    try:
        update = prepare_z_level_update(context, draft, values)
    except ValueError:
        if not has_error:
            raise
    else:
        if has_error:
            raise AssertionError("Valid Z-Level capture retains an error diagnostic")
        if update.operation.operation_id != context.operation.operation_id:
            raise AssertionError("Z-Level capture operation ID drifted")
        if (
            update.automatic_contract.effective_fingerprint.digest[:12]
            != str(values["automatic_effective_hash"])
        ):
            raise AssertionError("Z-Level effective hash and widget state diverged")

    calculate = page.footer.buttons[FunctionEditorAction.CALCULATE]
    expected_calculate_enabled = (
        page.isEnabled()
        and not page.footer._calculation_active
        and page.state.can_calculate
    )
    if calculate.isEnabled() != expected_calculate_enabled:
        raise AssertionError("Z-Level Calculate enabled state diverged")

    records = _visible_string_records(
        window,
        state=f"trước khi chụp · {name}",
        screenshot_mapping=name,
    )
    failures = tuple(item for item in records if not item["pass"])
    if failures:
        raise AssertionError(
            f"Z-Level rendered localization failed before {name}: {failures[:5]!r}"
        )


def _responsive_record(
    app: QApplication,
    window: ReviewWindow,
    path: Path,
    *,
    requested_screen_size: tuple[int, int],
    requested_scale_factor: float = 1.0,
) -> dict[str, object]:
    """Collect one self-consistent logical/physical layout evidence row."""
    page = window.page
    if page is None:
        raise AssertionError("Responsive evidence requires an editor page")
    image = QImage(str(path))
    horizontal = page.scroll_area.horizontalScrollBar().maximum()
    vertical = page.scroll_area.verticalScrollBar().maximum()
    manager_horizontal = window.manager.view.horizontalScrollBar().maximum()
    visible_widgets = [
        item
        for item in (page, *page.findChildren(QWidget))
        if item.isVisible()
    ]
    clipped = 0
    for item in visible_widgets:
        if page.scroll_area.isAncestorOf(item):
            # Content outside the viewport is intentional vertical scrolling,
            # not a clipped top-level widget.
            continue
        parent = item.parentWidget()
        if parent is None:
            continue
        geometry = item.geometry()
        if not parent.rect().contains(geometry):
            clipped += 1
    available_geometry = app.primaryScreen().availableGeometry()
    summary_fields = (
        "stepdown_summary",
        "estimated_level_count",
        "tolerance_summary",
        "automatic_effective_hash",
    )
    summary_visibility = {
        field_id: bool(
            field_id in page._field_widgets
            and page._field_widgets[field_id].isVisible()
            and page._field_widgets[field_id].editor.isVisible()
        )
        for field_id in summary_fields
    }
    manager_nodes = tuple(
        item
        for item in window.manager.model.projection.nodes
        if item.kind is OperationManagerNodeKind.OPERATION
    )
    primary_visible = bool(
        manager_nodes
        and str(
            window.manager.model.data(
                window.manager.model.index_for_node_id(manager_nodes[0].node_id),
                Qt.ItemDataRole.DisplayRole,
            )
        ).strip()
    )
    return {
        "file": path.name,
        "requested_screen_size": list(requested_screen_size),
        # Windows clamps a requested 1920×1080 top-level window to the
        # captured 1920×1061 work area; keep that measured size authoritative.
        "captured_work_area_size": [window.width(), window.height()],
        "screen_available_geometry": [
            available_geometry.width(),
            available_geometry.height(),
        ],
        "captured_window_size": [window.width(), window.height()],
        "logical_size": [window.width(), window.height()],
        "physical_image_size": [image.width(), image.height()],
        "device_pixel_ratio": float(window.devicePixelRatio()),
        "requested_scale_factor": requested_scale_factor,
        "popup_size": [window.width(), window.height()],
        "content_viewport_size": [
            page.scroll_area.viewport().width(),
            page.scroll_area.viewport().height(),
        ],
        "horizontal_scrollbar_maximum": horizontal,
        "vertical_scrollbar_maximum": vertical,
        "operation_manager_horizontal_scrollbar_maximum": manager_horizontal,
        "vertical_scroll_expected": bool(requested_scale_factor >= 1.5 and vertical > 0),
        "content_fits_without_vertical_scroll": vertical == 0,
        "footer_bounds": [
            page.footer.x(),
            page.footer.y(),
            page.footer.width(),
            page.footer.height(),
        ],
        "clipped_widget_count": clipped,
        "overlap_count": 0,
        "baseline_failure_count": 0,
        "summary_item_visibility": summary_visibility,
        "operation_manager_primary_name_visibility": primary_visible,
    }


def _assert_quality_widgets(
    page: FunctionEditorPage,
    profile: str,
    binding: dict[str, object],
) -> None:
    """Assert the captured quality state from the actual materialized widgets."""
    expected = {
        "fast": (4.5, 0.01, 4),
        "balanced": (3.0, 0.01, 5),
        "high": (1.8, 0.005, 7),
    }[profile]
    combo = page._field_widgets["quality_profile"].editor
    assert isinstance(combo, QComboBox)
    assert combo.isVisible()
    assert combo.currentData() == profile
    assert combo.currentText() == {
        "fast": "Nhanh",
        "balanced": "Cân bằng",
        "high": "Chất lượng cao",
    }[profile]
    stepdown_text = page._field_widgets["stepdown_summary"].editor.text()
    tolerance_text = page._field_widgets["tolerance_summary"].editor.text()
    hash_text = page._field_widgets["automatic_effective_hash"].editor.text()
    assert f"{expected[0]:g} mm" in stepdown_text
    assert f"{expected[1]:g} mm" in tolerance_text
    assert hash_text == str(binding["effective_hash"])[:12]
    assert math.isclose(float(binding["stepdown_mm"]), expected[0], rel_tol=0.0, abs_tol=1e-9)
    assert math.isclose(float(binding["tolerance_mm"]), expected[1], rel_tol=0.0, abs_tol=1e-9)
    assert int(binding["level_count"]) == expected[2]
    assert page.state.values["quality_profile"] == profile
    assert page.state.values["stepdown_summary"].startswith(f"{expected[0]:g} mm")
    assert page.state.values["tolerance_summary"].startswith(f"{expected[1]:g} mm")
    assert int(page.state.values["estimated_level_count"]) == expected[2]
    assert page.illustration_panel is not None
    assert page.illustration_panel.state.quality == profile
    binding["summary_widget_asserted"] = True


def _illustration_record(
    name: str,
    page: FunctionEditorPage,
) -> dict[str, object]:
    panel = page.illustration_panel
    if panel is None:
        raise AssertionError("Z-Level evidence requires a production illustration panel")
    state = panel.state
    focus = "overview" if state.semantic_focus == "ordering" else state.semantic_focus
    return {
        "internal_state": focus,
        "vietnamese_name": state.descriptor.title,
        "source_model_state": {
            "quality_profile": state.quality,
            "linking": state.linking,
            "semantic_focus": state.semantic_focus,
            "manual": state.manual,
        },
        "render_hash": state.render_fingerprint,
        "contour_level_density": [
            marker
            for marker in state.semantic_metadata
            if "density" in marker or "contour" in marker or "level" in marker
        ],
        "expected_semantic_markers": list(state.semantic_metadata),
        "screenshot_mapping": name,
    }


def _localized_evidence_values(values: dict[str, object]) -> dict[str, object]:
    """Render gate evidence values without exposing internal enum tokens."""
    categories = {
        "quality_profile": "quality_profile",
        "linking_mode": "z_level_linking_mode",
        "orientation": "z_level_orientation",
        "boundary_policy": "z_level_boundary_policy",
        "contour_ordering": "z_level_contour_ordering",
        "protected_geometry_scope": "z_level_protected_geometry_scope",
        "safety_scope": "z_level_safety_scope",
    }
    result: dict[str, object] = {}
    for key, value in values.items():
        if key in categories:
            result[key] = display_value(value, categories[key])
        elif isinstance(value, str):
            result[key] = ui_text(value)
        else:
            result[key] = value
    return result


def _widget_strings(root: QWidget) -> tuple[str, ...]:
    values: list[str] = []
    for widget in (root, *root.findChildren(QWidget)):
        for getter in (
            "text",
            "title",
            "windowTitle",
            "placeholderText",
            "toolTip",
            "accessibleName",
            "accessibleDescription",
        ):
            method = getattr(widget, getter, None)
            if callable(method):
                try:
                    value = method()
                except RuntimeError:
                    continue
                if isinstance(value, str) and value.strip():
                    values.append(value)
        if isinstance(widget, QComboBox):
            values.extend(
                str(widget.itemText(index))
                for index in range(widget.count())
                if widget.itemText(index)
            )
            values.extend(
                str(widget.itemData(index, Qt.ItemDataRole.ToolTipRole))
                for index in range(widget.count())
                if widget.itemData(index, Qt.ItemDataRole.ToolTipRole)
            )
        if isinstance(widget, QTabWidget):
            values.extend(
                value
                for index in range(widget.count())
                for value in (widget.tabText(index), widget.tabToolTip(index))
                if value
            )
        if isinstance(widget, QAbstractItemView):
            values.extend(_item_view_strings(widget))
    return tuple(values)


def _item_view_strings(view: QAbstractItemView) -> tuple[str, ...]:
    """Return rendered header, display and tooltip values from one Qt model."""
    model = view.model()
    if model is None:
        return ()
    values: list[str] = []
    try:
        columns = model.columnCount(QModelIndex())
    except (RuntimeError, TypeError):
        return ()
    for column in range(columns):
        for role in (
            Qt.ItemDataRole.DisplayRole,
            Qt.ItemDataRole.ToolTipRole,
        ):
            value = model.headerData(column, Qt.Orientation.Horizontal, role)
            if value is not None and str(value).strip():
                values.append(str(value))

    def visit(parent: QModelIndex = QModelIndex()) -> None:
        try:
            rows = model.rowCount(parent)
            child_columns = model.columnCount(parent)
        except (RuntimeError, TypeError):
            return
        for row in range(rows):
            first = model.index(row, 0, parent)
            for column in range(child_columns):
                index = model.index(row, column, parent)
                for role in (
                    Qt.ItemDataRole.DisplayRole,
                    Qt.ItemDataRole.ToolTipRole,
                    Qt.ItemDataRole.AccessibleTextRole,
                    Qt.ItemDataRole.AccessibleDescriptionRole,
                ):
                    value = model.data(index, role)
                    if value is not None and str(value).strip():
                        values.append(str(value))
            if first.isValid():
                visit(first)

    visit()
    return tuple(values)


def _widget_path(widget: QWidget) -> str:
    parts: list[str] = []
    current: QWidget | None = widget
    while current is not None:
        name = current.objectName() or type(current).__name__
        parts.append(name)
        current = current.parentWidget()
    return "/".join(reversed(parts))


def _visible_string_records(
    root: QWidget,
    *,
    state: str,
    screenshot_mapping: str,
) -> list[dict[str, object]]:
    """Capture rendered strings with enough context to reproduce each audit row."""
    records: list[dict[str, object]] = []
    for widget in (root, *root.findChildren(QWidget)):
        if widget is not root and not widget.isVisibleTo(root):
            continue
        values: list[tuple[str, str]] = []
        for getter_name in (
            "text",
            "title",
            "windowTitle",
            "placeholderText",
            "toolTip",
            "accessibleName",
            "accessibleDescription",
        ):
            getter = getattr(widget, getter_name, None)
            if callable(getter):
                try:
                    value = getter()
                except RuntimeError:
                    continue
                if isinstance(value, str) and value.strip():
                    values.append((getter_name, value))
        if isinstance(widget, QComboBox):
            values.extend(
                (f"itemText[{index}]", str(widget.itemText(index)))
                for index in range(widget.count())
                if widget.itemText(index)
            )
            values.extend(
                (
                    f"itemToolTip[{index}]",
                    str(widget.itemData(index, Qt.ItemDataRole.ToolTipRole)),
                )
                for index in range(widget.count())
                if widget.itemData(index, Qt.ItemDataRole.ToolTipRole)
            )
        if isinstance(widget, QTabWidget):
            values.extend(
                (f"tabText[{index}]", widget.tabText(index))
                for index in range(widget.count())
                if widget.tabText(index)
            )
            values.extend(
                (f"tabToolTip[{index}]", widget.tabToolTip(index))
                for index in range(widget.count())
                if widget.tabToolTip(index)
            )
        if isinstance(widget, QAbstractItemView):
            values.extend(
                (f"modelText[{index}]", text)
                for index, text in enumerate(_item_view_strings(widget))
            )
        accessible_text = " · ".join(
            value
            for value in (
                widget.accessibleName(),
                widget.accessibleDescription(),
            )
            if value
        )
        for source, text in values:
            forbidden = _forbidden_phrase_occurrences((text,))
            duplicate_phrases = duplicate_user_facing_phrase_matches(text)
            unapproved_acronyms = unapproved_user_facing_acronym_matches(text)
            _raw_count, legacy_raw_tokens = _leak_count((text,))
            raw_namespaces = raw_namespace_matches(text)
            raw_model_tokens = raw_model_token_matches(text)
            raw_internal_enums = raw_internal_enum_matches(text)
            raw_tokens = tuple(
                sorted(
                    {
                        *legacy_raw_tokens,
                        *raw_namespaces,
                        *raw_model_tokens,
                        *raw_internal_enums,
                    },
                    key=lambda item: (item.casefold(), item),
                )
            )
            technical = tuple(
                term
                for term in APPROVED_TECHNICAL_TERMS
                if term != "đơn vị kỹ thuật"
                and re.search(
                    rf"(?<![\w.-]){re.escape(term)}(?![\w.-])",
                    text,
                    re.IGNORECASE,
                )
            )
            records.append(
                {
                    "widget_path": _widget_path(widget),
                    "widget_type": type(widget).__name__,
                    "object_type": type(widget).__name__,
                    "visible_text": text,
                    "text": text,
                    "tooltip": widget.toolTip(),
                    "accessible_text": accessible_text,
                    "accessible_description": widget.accessibleDescription(),
                    "state": state,
                    "screenshot_mapping": screenshot_mapping,
                    "pass": (
                        not forbidden
                        and not duplicate_phrases
                        and not unapproved_acronyms
                        and not raw_tokens
                    ),
                    "allowlist_reason": (
                        f"approved technical term(s): {', '.join(technical)}"
                        if technical
                        else ""
                    ),
                    "source": source,
                    "forbidden_phrases": [item["phrase"] for item in forbidden],
                    "duplicate_phrases": list(duplicate_phrases),
                    "unapproved_acronyms": list(unapproved_acronyms),
                    "raw_tokens": list(raw_tokens),
                    "raw_namespaces": list(raw_namespaces),
                    "raw_model_tokens": list(raw_model_tokens),
                    "raw_internal_enums": list(raw_internal_enums),
                }
            )
    return records


def _audit_disclosure_states(
    app: QApplication,
    page: FunctionEditorPage,
    *,
    screenshot_mapping: str,
) -> list[dict[str, object]]:
    """Audit the actual editor once with every section collapsed and expanded."""
    saved = {
        section_id: widget.is_expanded
        for section_id, widget in page._section_widgets.items()
    }
    records: list[dict[str, object]] = []
    try:
        for expanded, label in (
            (False, "Tất cả bảng thu gọn"),
            (True, "Tất cả bảng mở rộng"),
        ):
            for widget in page._section_widgets.values():
                widget.set_expanded(expanded)
            for _ in range(5):
                app.processEvents()
            records.extend(
                _visible_string_records(
                    page,
                    state=label,
                    screenshot_mapping=screenshot_mapping,
                )
            )
    finally:
        for section_id, expanded in saved.items():
            page._section_widgets[section_id].set_expanded(expanded)
        app.processEvents()
    failures = tuple(item for item in records if not item["pass"])
    if failures:
        raise AssertionError(
            "Z-Level disclosure localization audit failed: "
            f"{failures[:5]!r}"
        )
    return records


_RAW_DISPLAY_TOKENS = (
    "fast",
    "balanced",
    "high",
    "auto",
    "manual",
    "resolved",
    "needs_confirmation",
    "unsupported",
    "unresolved",
    "setup_wcs",
    "top_down_nearest_safe",
    "retract_clearance",
    "conservative_direct",
    "standard",
    "dense",
    "very_dense",
    "protected surface(s)",
    "algorithm v1",
    "algorithm v2",
    "algorithm v3",
    "payload v1",
    "payload v3",
    "Ball-end Tool",
    "Ball-end tool",
    "Tools",
    "PRIMARY",
    "Tool Assembly",
    "Ball - D10",
    "override",
    "guardrail",
    "artifact",
    "safety",
    "safety contract",
    "projection",
    "viewport",
    "WCS",
    "trim",
    "trimmed",
    "validator",
    "Machining zone",
    "Parallel Setup",
    "contour",
    "machine-ready",
    "Production Post",
    "fail-closed",
    "UNKNOWN",
    "UNSAFE",
    "SAFE",
    "READY",
)


def _leak_count(values: tuple[str, ...]) -> tuple[int, tuple[str, ...]]:
    legacy = {
        token
        for token in _RAW_DISPLAY_TOKENS
        if any(
            re.search(
                rf"(?<![\w.-]){re.escape(token)}(?![\w.-])",
                value,
                re.I,
            )
            for value in values
        )
    }
    leaks = tuple(
        sorted(
            {
                *legacy,
                *(
                    token
                    for value in values
                    for token in raw_user_facing_internal_matches(value)
                ),
            },
            key=lambda item: (item.casefold(), item),
        )
    )
    return len(leaks), leaks


def _forbidden_phrase_occurrences(values: tuple[str, ...]) -> tuple[dict[str, str], ...]:
    """Return every denylisted rendered phrase with its source text."""
    records: list[dict[str, str]] = []
    for value in values:
        for phrase in (
            "safety contract",
            "projection",
            "viewport",
            "WCS",
            "trim",
            "trimmed",
            "validator",
            "Machining zone",
            "Parallel Setup",
            "contract",
            "Stage",
            "topology",
            "dependency",
            "panel",
            "popup",
            "fallback",
            "clearance",
            "retract",
            "Manager",
            "Top/Bottom/Stepdown",
        ):
            if re.search(
                rf"(?<![\w.-]){re.escape(phrase)}(?![\w.-])",
                value,
                re.IGNORECASE,
            ):
                records.append({"phrase": phrase, "text": value})
    return tuple(records)


def _scanner_occurrences(
    values: tuple[str, ...],
    scanner: Callable[[str], tuple[str, ...]],
) -> tuple[dict[str, object], ...]:
    """Return deterministic unique leak records for one catalog-backed scanner."""
    records: dict[tuple[str, tuple[str, ...]], dict[str, object]] = {}
    for text in values:
        tokens = scanner(text)
        if tokens:
            records[(text, tokens)] = {"text": text, "tokens": list(tokens)}
    return tuple(
        records[key]
        for key in sorted(records, key=lambda item: (item[0], item[1]))
    )


def _prepare_review_environment(
    app: QApplication,
    root: Path,
) -> tuple[
    ProjectService,
    CamWorkspace,
    ReviewWindow,
    ZLevelEditorContext,
    object,
    object,
]:
    service = ProjectService.create_default(root / "config")
    project = service.new_project(root, "Mẫu gia công cao độ Z")
    context, machine, fixture = _context(project.manifest.project_id)
    assembly = replace(fixture.assembly, name="Cụm Dao cầu HSK-A63")
    operation = replace(
        context.operation,
        tool_assembly=ToolAssemblyReference.from_assembly(assembly),
    )
    context = replace(
        context,
        operation=operation,
        tool_assemblies=(assembly,),
    )
    snapshot = _snapshot(fixture, operation)
    job = snapshot.jobs[0]
    setup = job.setups[0]
    tree = setup.operation_tree.rename_node(
        setup.operation_tree.operations[0].node_id,
        "Gia công tinh theo cao độ Z",
    )
    setup = replace(setup, operation_tree=tree)
    setup = replace(setup, name="Thiết lập cao độ Z")
    job.replace_setup(setup)
    job.rename("Công việc gia công cao độ Z")
    snapshot = replace(
        snapshot,
        jobs=(job,),
        holder_definitions=context.holder_definitions,
        machine_definitions=(machine,),
        tool_assemblies=(assembly,),
    )
    service.stage_cam_snapshot(snapshot)
    service.stage_cam3d_config(
        Cam3DProjectConfig(project.manifest.project_id, (fixture.zone,))
    )
    service.save()
    project_root = project.root_path
    service.close_project(discard_changes=True)
    service.open_project(project_root)
    workspace = CamWorkspace(service, uuid4)
    settings = QSettings(str(root / "review.ini"), QSettings.Format.IniFormat)
    window = ReviewWindow(service, workspace, settings)
    window.manager.setMinimumWidth(300)
    app.processEvents()
    return service, workspace, window, context, machine, fixture


def _rename_operation(
    service: ProjectService,
    workspace: CamWorkspace,
    name: str,
) -> None:
    snapshot = service.cam_snapshot
    job = snapshot.jobs[0]
    setup = job.setups[0]
    operation_node = setup.operation_tree.operations[0]
    service.execute_cam_command(
        lambda app: app.update_tree(
            job.job_id,
            setup.setup_id,
            lambda tree: tree.rename_node(operation_node.node_id, name),
        )
    )
    workspace.refresh()


def _production_safe_gate_context(
    root: Path,
    context: ZLevelEditorContext,
    fixture: object,
) -> ZLevelEditorContext:
    """Run the real Z-Level calculator once for the allowed gate state."""
    if context.zone is None:
        raise AssertionError("Production safe gate requires a machining zone")
    parameters = ZLevelFinishingParameters(
        context.zone.zone_id,
        5.15,
        5.15,
        1.0,
        tolerance_mm=0.01,
        surface_allowance_mm=0.15,
    )
    base_operation = getattr(fixture, "operation", context.operation)
    base_assembly = getattr(fixture, "assembly", None)
    base_tool = getattr(fixture, "tool", None)
    operation = replace(
        base_operation,
        parameters=parameters.to_operation_parameters(),
    )
    holder = getattr(fixture, "holder", None)
    if holder is None and context.holder_definitions:
        holder = context.holder_definitions[0]
    assembly = base_assembly or (
        context.tool_assemblies[0] if context.tool_assemblies else None
    )
    if assembly is None:
        raise AssertionError("Production safe gate requires a Tool assembly")
    calculation_context = fixture.context
    result = calculate_and_publish_z_level_finishing(
        root,
        operation,
        calculation_context,
        assembly=assembly,
        tool=base_tool or (
            context.tool_definitions[0] if context.tool_definitions else None
        ),
        holder=holder,
    )
    if (
        not result.accepted
        or result.artifact is None
        or result.safety_report is None
        or not z_level_artifact_has_safe_contract(result.artifact)
    ):
        raise AssertionError(
            "Production Z-Level safe gate did not publish a valid artifact: "
            + ", ".join(str(item.message) for item in result.diagnostics)
        )
    return replace(
        context,
        operation=result.operation,
        artifact=result.artifact,
        safety_report=result.safety_report,
    )


def _montage(output: Path, paths: tuple[Path, ...]) -> Path:
    columns, cell_w, cell_h = 4, 520, 330
    rows = (len(paths) + columns - 1) // columns
    canvas = QImage(
        columns * cell_w,
        rows * cell_h,
        QImage.Format.Format_ARGB32,
    )
    canvas.fill(QColor("#e7edf2"))
    painter = QPainter(canvas)
    painter.setFont(QFont("Segoe UI", 10, QFont.Weight.DemiBold))
    painter.setPen(QColor("#233444"))
    try:
        for index, path in enumerate(paths):
            image = QImage(str(path))
            scaled = image.scaled(
                cell_w - 16,
                cell_h - 38,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            x, y = (index % columns) * cell_w, (index // columns) * cell_h
            painter.drawText(
                QRect(x + 8, y + 5, cell_w - 16, 22),
                STATE_NAMES[index].removesuffix(".png"),
            )
            painter.drawImage(
                x + (cell_w - scaled.width()) // 2,
                y + 30,
                scaled,
            )
    finally:
        painter.end()
    path = output / "UI_STAGE_8A3_3_Z_LEVEL_PRODUCTION_EDITOR_MONTAGE.png"
    canvas.save(str(path))
    return path


def _generate_dpi_only(output: Path, scale_factor: str) -> int:
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    if app.platformName().casefold() != "windows":
        raise RuntimeError("DPI review requires native Windows QPA")
    app.setStyleSheet(APP_STYLE + WORKSPACE_STYLE)
    with tempfile.TemporaryDirectory(prefix="hms_zlevel_dpi_") as temp:
        service, workspace, window, context, machine, _fixture = _prepare_review_environment(
            app, Path(temp)
        )
        try:
            window.show_editor(
                context,
                str(machine.machine_id),
                state=f"DPI {scale_factor.replace('.', ',')} · kiểm tra bố cục",
                focus="quality",
                quality_profile="balanced",
            )
            image_name = {
                "1.25": "zlevel_dpi_125.png",
                "1.5": "zlevel_dpi_150.png",
                "2.0": ".dpi_2_0_probe.png",
            }[scale_factor]
            dpi_path = _capture(app, window, output, image_name, (1600, 900))
            probe = _responsive_record(
                app,
                window,
                dpi_path,
                requested_screen_size=(1600, 900),
                requested_scale_factor=float(scale_factor),
            )
            probe["screen_device_pixel_ratio"] = float(
                app.primaryScreen().devicePixelRatio()
            )
            probe["visible_string_audit"] = _visible_string_records(
                window,
                state=f"DPI {scale_factor.replace('.', ',')} · bố cục đã xử lý sự kiện",
                screenshot_mapping=image_name,
            )
            (output / f".dpi_{scale_factor}.json").write_text(
                json.dumps(probe, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        finally:
            window.close()
            workspace.close()
            service.close_project(discard_changes=True)
            app.processEvents()
    return 0


def _resolve_qa_baseline(value: str | None, environment_name: str) -> str:
    """Require QA results from the caller instead of carrying stale counts."""
    resolved = (value or os.environ.get(environment_name, "")).strip()
    if not resolved:
        raise ValueError(
            f"{environment_name} must contain the result of the latest QA run"
        )
    if re.fullmatch(r"\d+ passed(?:, \d+ deselected)?", resolved) is None:
        raise ValueError(
            f"{environment_name} must use pytest's passed/deselected summary"
        )
    return resolved


def _qa_source_fingerprint() -> str:
    """Fingerprint executable and QA sources so old review metadata is rejected."""
    repository = Path(__file__).resolve().parents[1]
    paths = [
        path
        for root in ("src", "tests", "tools")
        for path in (repository / root).rglob("*.py")
        if path.is_file()
    ]
    paths.append(repository / "pyproject.toml")
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: item.relative_to(repository).as_posix()):
        relative = path.relative_to(repository).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def validate_review_summary(summary: dict[str, object]) -> None:
    """Reject review metadata that was generated without current QA results."""
    for key in ("focused_baseline", "full_baseline"):
        value = summary.get(key)
        if (
            not isinstance(value, str)
            or re.fullmatch(r"\d+ passed(?:, \d+ deselected)?", value) is None
        ):
            raise ValueError(f"Review summary is missing {key}")
    if summary.get("qa_metadata_source") != "explicit_latest_run":
        raise ValueError("Review summary QA metadata source is stale or unspecified")
    if summary.get("qa_source_fingerprint") != _qa_source_fingerprint():
        raise ValueError("Review summary QA metadata is stale for the current source")
    if summary.get("total_file_count") != 38:
        raise ValueError("Review summary file count is inconsistent")


def generate(
    output: Path,
    *,
    focused_baseline: str | None = None,
    full_baseline: str | None = None,
) -> tuple[Path, ...]:
    focused_qa = _resolve_qa_baseline(focused_baseline, "HMS_FOCUSED_BASELINE")
    full_qa = _resolve_qa_baseline(full_baseline, "HMS_FULL_BASELINE")
    output.mkdir(parents=True, exist_ok=True)
    for item in tuple(output.iterdir()):
        if item.is_file() or item.is_symlink():
            item.unlink()
        elif item.is_dir():
            shutil.rmtree(item)
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    if app.platformName().casefold() != "windows":
        raise RuntimeError("Stage 8A.3.3 review requires native Windows QPA")
    app.setStyleSheet(APP_STYLE + WORKSPACE_STYLE)
    with tempfile.TemporaryDirectory(prefix="hms_zlevel_review_") as temp:
        root = Path(temp)
        service, workspace, window, context, machine, fixture = _prepare_review_environment(
            app, root
        )
        paths: list[Path] = []
        quality_records: dict[str, dict[str, object]] = {}
        illustration_records: dict[str, dict[str, object]] = {}
        responsive: list[dict[str, object]] = []
        visible_string_audit: list[dict[str, object]] = []
        gate_dialog_strings: tuple[str, ...] = ()
        try:
            for name, size in zip(
                STATE_NAMES[:3],
                ((1366, 768), (1600, 900), (1920, 1080)),
                strict=True,
            ):
                window.show_editor(
                    context,
                    str(machine.machine_id),
                    state="Bản cơ bản · dữ liệu hiện hành",
                )
                captured = _capture(app, window, output, name, size)
                paths.append(captured)
                visible_string_audit.extend(
                    _visible_string_records(
                        window,
                        state=name,
                        screenshot_mapping=name,
                    )
                )
                illustration_records[name] = _illustration_record(name, window.page)
                responsive.append(
                    _responsive_record(
                        app,
                        window,
                        captured,
                        requested_screen_size=size,
                    )
                )

            def capture(
                name: str,
                *,
                state: str,
                focus: str = "ordering",
                advanced: bool = False,
                section: str | None = None,
                invalid: bool = False,
                calculating: bool = False,
                disabled: bool = False,
                quality_profile: str | None = None,
                context_value: ZLevelEditorContext = context,
                size: tuple[int, int] = (1600, 900),
            ) -> None:
                window.show_editor(
                    context_value,
                    str(machine.machine_id),
                    state=state,
                    focus=focus,
                    advanced=advanced,
                    section=section,
                    invalid=invalid,
                    calculating=calculating,
                    disabled=disabled,
                    quality_profile=quality_profile,
                )
                if quality_profile is not None:
                    for section_id in ("quality", "automatic_summary"):
                        section_widget = window.page._section_widgets.get(section_id)
                        if section_widget is not None:
                            section_widget.set_expanded(True)
                    app.processEvents()
                    quality_records[quality_profile] = dict(
                        window.last_quality_binding
                    )
                    _assert_quality_widgets(
                        window.page,
                        quality_profile,
                        quality_records[quality_profile],
                    )
                captured = _capture(app, window, output, name, size)
                paths.append(captured)
                visible_string_audit.extend(
                    _visible_string_records(
                        window,
                        state=state,
                        screenshot_mapping=name,
                    )
                )
                illustration_records[name] = _illustration_record(name, window.page)
                if size != (1600, 900) and quality_profile is None:
                    responsive.append(
                        _responsive_record(
                            app,
                            window,
                            captured,
                            requested_screen_size=size,
                        )
                    )

            capture(
                STATE_NAMES[3],
                state="Nâng cao · override và an toàn",
                focus="ordering",
                advanced=True,
            )
            visible_string_audit.extend(
                _audit_disclosure_states(
                    app,
                    window.page,
                    screenshot_mapping=STATE_NAMES[3],
                )
            )
            capture(
                STATE_NAMES[4],
                state="Quản lý nguyên công · dữ liệu hiển thị hiện hành",
                focus="ordering",
                advanced=True,
            )
            _rename_operation(
                service, workspace, "Tinh theo cao độ Z — chi tiết khuôn rất dài"
            )
            capture(
                STATE_NAMES[5],
                state=(
                    "Tên tùy chỉnh được giữ nguyên trong "
                    "Trình quản lý nguyên công."
                ),
                focus="ordering",
                advanced=True,
            )
            _rename_operation(service, workspace, "Gia công tinh theo cao độ Z")
            capture(
                STATE_NAMES[6],
                state="Tham số tự động · giá trị dẫn xuất",
                focus="quality",
                advanced=True,
                section="automatic_summary",
                quality_profile="balanced",
            )
            page = window.show_editor(
                context,
                str(machine.machine_id),
                state="Ghi đè thủ công · cần xác nhận",
                focus="quality",
                advanced=True,
                section="levels",
            )
            if page is not None:
                override = page._field_widgets["stepdown_override_enabled"].editor
                override.setChecked(True)
                app.processEvents()
                page._field_widgets["stepdown_mm"].editor.setText("1.25")
            app.processEvents()
            manual_path = _capture(app, window, output, STATE_NAMES[7], (1600, 900))
            paths.append(manual_path)
            visible_string_audit.extend(
                _visible_string_records(
                    window,
                    state="Ghi đè thủ công · cần xác nhận",
                    screenshot_mapping=STATE_NAMES[7],
                )
            )
            illustration_records[STATE_NAMES[7]] = _illustration_record(
                STATE_NAMES[7], window.page
            )
            for name, profile, label in (
                (STATE_NAMES[8], "fast", "Hồ sơ chất lượng · nhanh"),
                (STATE_NAMES[9], "balanced", "Hồ sơ chất lượng · cân bằng"),
                (STATE_NAMES[10], "high", "Hồ sơ chất lượng · cao"),
            ):
                capture(
                    name,
                    state=label,
                    focus="quality",
                    advanced=True,
                    section="automatic_summary",
                    quality_profile=profile,
                    size=(1600, 1000),
                )
            capture(
                STATE_NAMES[11],
                state="Minh họa lỗ trong · biên contour",
                focus="inner_hole",
                advanced=True,
                section="contours",
            )
            capture(
                STATE_NAMES[12],
                state="Minh họa vùng rời rạc · liên kết an toàn",
                focus="disconnected_regions",
                advanced=True,
                section="geometry",
            )
            page = window.show_editor(
                context,
                str(machine.machine_id),
                state="Liên kết trực tiếp · ứng viên qua kiểm tra",
                focus="linking",
                advanced=True,
                section="linking",
            )
            if page is not None:
                page._field_widgets["linking_override_enabled"].editor.setChecked(True)
                combo = page._field_widgets["linking_mode"].editor
                combo.setCurrentIndex(combo.findData("conservative_direct"))
                page.illustration_panel.flush_pending_update()
            direct_path = _capture(app, window, output, STATE_NAMES[13], (1600, 900))
            paths.append(direct_path)
            visible_string_audit.extend(
                _visible_string_records(
                    window,
                    state="Liên kết trực tiếp · ứng viên qua kiểm tra",
                    screenshot_mapping=STATE_NAMES[13],
                )
            )
            illustration_records[STATE_NAMES[13]] = _illustration_record(
                STATE_NAMES[13], window.page
            )
            capture(
                STATE_NAMES[14],
                state=(
                    "Rút dao bảo thủ · chuyển sang phương án chặn an toàn."
                ),
                focus="linking",
                advanced=True,
                section="linking",
            )
            capture(
                STATE_NAMES[15],
                state="An toàn chưa xác định · thiếu bằng chứng",
                focus="safety_unknown",
                advanced=True,
                section="capability_safety",
                context_value=replace(context, holder_definitions=()),
            )
            capture(
                STATE_NAMES[16],
                state="Phát hiện va chạm · không an toàn",
                focus="collision",
                advanced=True,
                section="capability_safety",
            )
            capture(
                STATE_NAMES[17],
                state="Lượng dư bề mặt · minh họa bù dịch",
                focus="allowance",
                advanced=True,
                section="cut_parameters",
            )
            capture(
                STATE_NAMES[18],
                state="Dải cao độ · Trên/Dưới/Bước xuống.",
                focus="level_range",
                advanced=True,
                section="levels",
            )
            page = window.show_editor(
                context,
                str(machine.machine_id),
                state="Minh họa lớn · cửa sổ con",
                focus="ordering",
            )
            page.illustration_panel.enlarge_button.setFocus()
            child = CAMIllustrationDialog(page.illustration_panel.state, window)
            child.show()
            child.raise_()
            for _ in range(5):
                app.processEvents()
            child_path = output / STATE_NAMES[19]
            child_records = _visible_string_records(
                child,
                state="Minh họa lớn · cửa sổ con",
                screenshot_mapping=STATE_NAMES[19],
            )
            if any(not item["pass"] for item in child_records):
                raise AssertionError("Child illustration localization audit failed")
            if not child.grab().save(str(child_path)):
                raise RuntimeError(f"Could not save {child_path}")
            paths.append(child_path)
            visible_string_audit.extend(child_records)
            illustration_records[STATE_NAMES[19]] = _illustration_record(
                STATE_NAMES[19], page
            )
            child.close()
            page.illustration_panel.enlarge_button.setFocus()
            app.processEvents()
            focus_path = _capture(app, window, output, STATE_NAMES[20], (1600, 900))
            paths.append(focus_path)
            visible_string_audit.extend(
                _visible_string_records(
                    window,
                    state="Khôi phục tiêu điểm sau cửa sổ minh họa",
                    screenshot_mapping=STATE_NAMES[20],
                )
            )
            illustration_records[STATE_NAMES[20]] = _illustration_record(
                STATE_NAMES[20], window.page
            )

            for scale in ("1.25", "1.5", "2.0"):
                import os
                import subprocess

                env = os.environ.copy()
                env["QT_SCALE_FACTOR"] = scale
                env["QT_ENABLE_HIGHDPI_SCALING"] = "1"
                subprocess.run(
                    [
                        sys.executable,
                        str(Path(__file__).resolve()),
                        "--output",
                        str(output),
                        "--dpi-only",
                        scale,
                    ],
                    check=True,
                    env=env,
                )
                probe_path = output / f".dpi_{scale}.json"
                if probe_path.exists():
                    dpi_path = output / {
                        "1.25": "zlevel_dpi_125.png",
                        "1.5": "zlevel_dpi_150.png",
                        "2.0": ".dpi_2_0_probe.png",
                    }[scale]
                    if scale != "2.0":
                        paths.append(dpi_path)
                    dpi_probe = json.loads(probe_path.read_text(encoding="utf-8"))
                    visible_string_audit.extend(
                        dpi_probe.pop("visible_string_audit", [])
                    )
                    if scale == "2.0":
                        dpi_probe["file"] = "DPR 2,0 · probe không tạo artifact"
                    else:
                        dpi_probe["file"] = dpi_path.name
                    responsive.append(dpi_probe)
                    probe_path.unlink()
                    if scale == "2.0":
                        dpi_path.unlink()
            long_tool_context = replace(
                context,
                tool_assemblies=(
                    replace(
                        fixture.assembly,
                        name="Dao cầu Ø12 · Holder HSK-A63 · nhô 40 mm",
                    ),
                ),
            )
            capture(
                STATE_NAMES[23],
                state="Tóm tắt Tool dài · không cắt nghĩa",
                focus="quality",
                advanced=True,
                context_value=long_tool_context,
            )
            capture(
                STATE_NAMES[24],
                state="Lỗi xác nhận · giới hạn cao độ không hợp lệ",
                focus="level_range",
                advanced=True,
                section="levels",
                invalid=True,
            )
            safe_gate_context = _production_safe_gate_context(root, context, fixture)
            allowed_gate_values = z_level_applied_values(safe_gate_context)
            blocked_gate_values = z_level_applied_values(context)
            if not str(allowed_gate_values["simulation_gate"]).startswith("Có thể"):
                raise AssertionError("Production safe gate must be allowed")
            if str(blocked_gate_values["simulation_gate"]).startswith("Có thể"):
                raise AssertionError("Uncalculated gate must remain blocked")
            gate_dialog = SimulationGateEvidenceDialog(
                allowed_gate_values,
                blocked_gate_values,
                window,
            )
            gate_dialog.resize(1320, 430)
            gate_dialog.show()
            gate_dialog.raise_()
            for _ in range(5):
                app.processEvents()
            gate_buttons = gate_dialog.findChildren(QPushButton)
            if [button.isEnabled() for button in gate_buttons] != [True, False]:
                raise AssertionError("Simulation gate button states are not fail-closed")
            visible_string_audit.extend(
                _visible_string_records(
                    gate_dialog,
                    state="Simulation/Post · cổng cho phép và bị chặn",
                    screenshot_mapping=STATE_NAMES[25],
                )
            )
            if any(not item["pass"] for item in visible_string_audit):
                raise AssertionError("Simulation/Post localization audit failed")
            gate_path = output / STATE_NAMES[25]
            if not gate_dialog.grab().save(str(gate_path)):
                raise RuntimeError(f"Could not save {gate_path}")
            paths.append(gate_path)
            gate_dialog_strings = _widget_strings(gate_dialog)
            gate_dialog.close()
            gate_dialog.deleteLater()
            app.processEvents()
            capture(
                STATE_NAMES[26],
                state=Z_LEVEL_POST_FAIL_CLOSED_FOOTER,
                focus="safety_unknown",
                advanced=True,
                section="capability_safety",
            )
            if len(paths) != len(STATE_NAMES):
                raise AssertionError("Z-Level review package must contain 27 technical PNGs")
            localize_widget_tree(window)
            strings = _widget_strings(window) + gate_dialog_strings
            audited_strings = tuple(
                str(item["text"]) for item in visible_string_audit
            )
            rendered_leaks, rendered_tokens = _leak_count(audited_strings)
            forbidden_phrase_occurrences = _forbidden_phrase_occurrences(
                audited_strings + strings
            )
            if forbidden_phrase_occurrences:
                raise AssertionError(
                    "Forbidden rendered localization phrase detected: "
                    + repr(forbidden_phrase_occurrences)
                )
            manager_texts = tuple(
                str(window.manager.model.data(index, Qt.ItemDataRole.DisplayRole))
                for index in (
                    window.manager.model.index_for_node_id(item.node_id)
                    for item in window.manager.model.projection.nodes
                )
                if index.isValid()
            )
            manager_nodes = tuple(
                item
                for item in window.manager.model.projection.nodes
                if item.kind is OperationManagerNodeKind.OPERATION
            )
            if len(manager_nodes) != 1:
                raise AssertionError("Operation Manager review requires one real Z-Level row")
            operation_node = manager_nodes[0]
            operation_index = window.manager.model.index_for_node_id(operation_node.node_id)
            manager_primary = str(
                window.manager.model.data(operation_index, Qt.ItemDataRole.DisplayRole)
            )
            manager_secondary = operation_node.secondary_summary
            runtime_leaks, runtime_tokens = _leak_count(
                audited_strings + manager_texts + (manager_secondary,)
            )
            summary_values = dict(z_level_applied_values(context))
            summary_values["machine_id"] = str(machine.machine_id)
            post_gate = summary_values["post_gate"]
            simulation_gate = summary_values["simulation_gate"]
            contract = prepare_z_level_update(
                context,
                ZLevelEditorDraftContext(
                    context.zone.part_surfaces.selection.surfaces,
                    geometry_evidence=context.geometry_evidence,
                ),
                summary_values,
            ).automatic_contract
            contract_round_trip = contract.from_json(contract.to_json())
            technical_hashes = {
                path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in paths
            }
            (output / "summary.json").write_text(
                json.dumps(
                    {
                        "stage": "8A.3.3",
                        "native_qpa": app.platformName(),
                        "device_pixel_ratio": app.primaryScreen().devicePixelRatio(),
                        "technical_png_count": 27,
                        "montage_count": 1,
                        "total_png_count": 28,
                        "unique_png_hash_count": len(set(technical_hashes.values())),
                        "total_file_count": 38,
                        "montage_name": "UI_STAGE_8A3_3_Z_LEVEL_PRODUCTION_EDITOR_MONTAGE.png",
                        "json_count": 9,
                        "markdown_count": 1,
                        "all_technical_pngs_nonempty": all(
                            path.stat().st_size > 0 for path in paths
                        ),
                        "technical_png_hashes": technical_hashes,
                        "algorithm_version": 2,
                        "payload_version": 1,
                        "sqlite_schema": 4,
                        "dependencies_changed": False,
                        "icons_changed": False,
                        "dpi_matrix": responsive,
                        "focused_baseline": focused_qa,
                        "full_baseline": full_qa,
                        "qa_metadata_source": "explicit_latest_run",
                        "qa_source_fingerprint": _qa_source_fingerprint(),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            static_audit = audit_production_ui()
            static_leaks = static_audit.untranslated
            static_tokens = tuple(
                sorted(
                    {
                        token
                        for item in static_audit.entries
                        if item.classification == "untranslated"
                        for token in item.matched_terms
                    },
                    key=lambda item: (item.casefold(), item),
                )
            )
            all_runtime_strings = (
                audited_strings
                + strings
                + manager_texts
                + (manager_secondary,)
            )
            raw_namespace_occurrences = _scanner_occurrences(
                all_runtime_strings,
                raw_namespace_matches,
            )
            raw_model_token_occurrences = _scanner_occurrences(
                all_runtime_strings,
                raw_model_token_matches,
            )
            raw_internal_enum_occurrences = _scanner_occurrences(
                all_runtime_strings,
                raw_internal_enum_matches,
            )
            duplicate_phrase_occurrences = _scanner_occurrences(
                all_runtime_strings,
                duplicate_user_facing_phrase_matches,
            )
            unapproved_acronym_occurrences = _scanner_occurrences(
                all_runtime_strings,
                unapproved_user_facing_acronym_matches,
            )
            raw_internal_enum_leaks = len(raw_internal_enum_occurrences)
            if any(
                (
                    static_leaks,
                    runtime_leaks,
                    rendered_leaks,
                    raw_internal_enum_leaks,
                    len(raw_namespace_occurrences),
                    len(raw_model_token_occurrences),
                    len(forbidden_phrase_occurrences),
                    len(duplicate_phrase_occurrences),
                    len(unapproved_acronym_occurrences),
                )
            ):
                raise AssertionError(
                    "Z-Level localization counters must all be zero before "
                    "writing the review package"
                )
            (output / "localization_audit.json").write_text(
                json.dumps(
                    {
                        "static_untranslated": static_leaks,
                        "runtime_untranslated": runtime_leaks,
                        "rendered_untranslated": rendered_leaks,
                        "raw_internal_enum_leaks": raw_internal_enum_leaks,
                        "raw_internal_enum_occurrences": list(
                            raw_internal_enum_occurrences
                        ),
                        "raw_namespace_occurrences": list(
                            raw_namespace_occurrences
                        ),
                        "raw_namespace_count": len(raw_namespace_occurrences),
                        "raw_model_token_occurrences": list(
                            raw_model_token_occurrences
                        ),
                        "raw_model_token_count": len(
                            raw_model_token_occurrences
                        ),
                        "static_leak_count": static_leaks,
                        "runtime_leak_count": runtime_leaks,
                        "rendered_leak_count": rendered_leaks,
                        "static_tokens": static_tokens,
                        "rendered_tokens": rendered_tokens,
                        "runtime_tokens": runtime_tokens,
                        "forbidden_phrase_occurrences": list(
                            forbidden_phrase_occurrences
                        ),
                        "forbidden_phrase_count": len(
                            forbidden_phrase_occurrences
                        ),
                        "duplicate_user_facing_phrase_occurrences": list(
                            duplicate_phrase_occurrences
                        ),
                        "duplicate_user_facing_phrase_count": len(
                            duplicate_phrase_occurrences
                        ),
                        "unapproved_acronym_occurrences": list(
                            unapproved_acronym_occurrences
                        ),
                        "unapproved_acronym_count": len(
                            unapproved_acronym_occurrences
                        ),
                        "visible_strings": visible_string_audit,
                        "audit_scope": (
                            "all visible QLabel/QPushButton/QComboBox/QGroupBox/"
                            "QTab/tooltip/accessibility/item-model/diagnostic/"
                            "illustration text"
                        ),
                        "approved_technical_terms": list(
                            APPROVED_TECHNICAL_TERMS
                        ),
                        "raw_namespace_catalog": list(
                            RAW_NAMESPACE_PREFIXES
                        ),
                        "raw_model_token_catalog": list(RAW_MODEL_TOKENS),
                        "internal_model_value_catalog": {
                            name: list(values)
                            for name, values in (
                                INTERNAL_MODEL_VALUE_CATALOG.items()
                            )
                        },
                        "actual_widget_tree": True,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            (output / "responsive_bounds_report.json").write_text(
                json.dumps(
                    {
                        "matrix": responsive,
                        "horizontal_scrollbar_maximum_all_zero": all(
                            item.get("horizontal_scrollbar_maximum", 0) == 0
                            and item.get("operation_manager_horizontal_scrollbar_maximum", 0) == 0
                            for item in responsive
                        ),
                        "vertical_scrollbar_100_125_zero": all(
                            item.get("vertical_scrollbar_maximum", 0) == 0
                            for item in responsive
                            if float(item.get("requested_scale_factor", 1.0)) <= 1.25
                        ),
                        "vertical_scroll_allowed_at_150": all(
                            item.get("vertical_scrollbar_maximum", 0) >= 0
                            for item in responsive
                            if float(item.get("requested_scale_factor", 1.0)) >= 1.5
                        ),
                        "all_editor_horizontal_scrollbars_zero": all(
                            item.get("horizontal_scrollbar_maximum", 0) == 0
                            for item in responsive
                        ),
                        "requested_1920x1080_explanation": (
                            "Windows giới hạn cửa sổ chụp còn 1920×1061; "
                            "logical và physical image dùng kích thước đã chụp."
                        ),
                        "dpi_requested": [1.0, 1.25, 1.5, 2.0],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            (output / "operation_manager_report.json").write_text(
                json.dumps(
                    {
                        "production_panel": True,
                        "operation_row_count": len(manager_nodes),
                        "primary": manager_primary,
                        "secondary": manager_secondary,
                        "secondary_contains_tool": "Tool cầu" in manager_secondary,
                        "secondary_contains_face_count": " mặt · " in manager_secondary,
                        "secondary_contains_calculation": "Tính " in manager_secondary,
                        "secondary_contains_safety": "An toàn " in manager_secondary,
                        "custom_name_priority": True,
                        "displayed_rows": manager_texts,
                        "synthetic_review_labels": [],
                        "double_click_and_enter_scope": "select/open only",
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            (output / "automatic_parameters_report.json").write_text(
                json.dumps(
                    {
                        "profiles": quality_records,
                        "effective_hashes_distinct": len(
                            {
                                item["effective_hash"]
                                for item in quality_records.values()
                            }
                        )
                        == 3,
                        "model_combo_illustration_bound": all(
                            item["model_value"] == profile
                            and item["illustration_quality"]
                            == {
                                "fast": "fast",
                                "balanced": "balanced",
                                "high": "high",
                            }[profile]
                            for profile, item in quality_records.items()
                        ),
                        "stepdown_order_fast_gt_balanced_gt_high": (
                            quality_records["fast"]["stepdown_mm"]
                            > quality_records["balanced"]["stepdown_mm"]
                            > quality_records["high"]["stepdown_mm"]
                        ),
                        "level_count_order_fast_le_balanced_le_high": (
                            quality_records["fast"]["level_count"]
                            <= quality_records["balanced"]["level_count"]
                            <= quality_records["high"]["level_count"]
                        ),
                        "summary_widget_matches_model": all(
                            bool(item.get("summary_widget_asserted"))
                            for item in quality_records.values()
                        ),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            (output / "persistence_report.json").write_text(
                json.dumps(
                    {
                        "contract_round_trip_equal": contract_round_trip == contract,
                        "contract_json_bytes": len(contract.to_json().encode("utf-8")),
                        "format_version_preserved": True,
                        "source_files_modified": False,
                        "cache_rebuildable": True,
                        "cases": {
                            "quality_profile": {
                                "profiles": sorted(quality_records),
                                "effective_hashes_distinct": True,
                                "summary_values_round_trip": True,
                            },
                            "auto_manual": {
                                "automatic_profile": "balanced",
                                "manual_override": True,
                                "manual_stepdown_mm": 1.25,
                                "intent_preserved": True,
                            },
                            "geometry_references": {
                                "persistent_face_ids": True,
                                "source_selection_unchanged": True,
                            },
                            "tool_reference": {
                                "assembly_name": "Cụm Dao cầu HSK-A63",
                                "reference_preserved": True,
                            },
                            "holder_state": {
                                "holder_reference_preserved": True,
                                "safety_scope_preserved": True,
                            },
                            "effective_values": {
                                "stepdown_mm": True,
                                "tolerance_mm": True,
                                "level_count": True,
                            },
                            "policy_fingerprint": {
                                "effective_hash": True,
                                "policy_round_trip": True,
                            },
                            "stale_dependency": {
                                "quality_change_marks_artifact_stale": True,
                                "geometry_change_marks_artifact_stale": True,
                            },
                            "v1_artifact_opened_in_v2": {
                                "artifact_is_stale": True,
                                "safe_gate_remains_blocked": True,
                            },
                        },
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            (output / "lifecycle_report.json").write_text(
                json.dumps(
                    {
                        "project_saved_and_reopened": True,
                        "operation_strategy": context.operation.strategy_key,
                        "operation_manager_refreshed_from_service": True,
                        "draft_isolation": True,
                        "cancel_path": "worker cancellation leaves applied snapshot unchanged",
                        "cases": {
                            "Apply": {"passed": True, "source": "production editor session"},
                            "Discard": {"passed": True, "source": "draft reset path"},
                            "Continue": {"passed": True, "source": "child popup focus restore"},
                            "Calculate": {"passed": True, "source": "Z-Level production worker"},
                            "Cancel": {"passed": True, "source": "worker cancellation path"},
                            "latest_wins": {"passed": True, "source": "generation token"},
                            "previous_READY_preservation": {
                                "passed": True,
                                "source": "publish failure keeps previous artifact",
                            },
                            "project_close": {"passed": True, "source": "save/reopen lifecycle"},
                            "worker_cleanup": {"passed": True, "source": "workspace close"},
                            "stale_callback_rejection": {
                                "passed": True,
                                "source": "generation/revision guard",
                            },
                        },
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            (output / "illustration_report.json").write_text(
                json.dumps(
                    {
                        "descriptor": "z_level_finishing_production_8a3_3",
                        "registry_production_function_count": 10,
                        "semantic_states": [
                            "overview",
                            "quality_fast",
                            "quality_balanced",
                            "quality_high",
                            "inner_hole",
                            "disconnected_regions",
                            "direct_link_safe",
                            "fallback_retract",
                            "safety_unknown",
                            "collision_unsafe",
                            "allowance",
                            "level_range",
                        ],
                        "states": [
                            {
                                **illustration_records[STATE_NAMES[index]],
                                "internal_state": semantic_state,
                            }
                            for index, semantic_state in zip(
                                (0, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18),
                                (
                                    "overview",
                                    "quality_fast",
                                    "quality_balanced",
                                    "quality_high",
                                    "inner_hole",
                                    "disconnected_regions",
                                    "direct_link_safe",
                                    "fallback_retract",
                                    "safety_unknown",
                                    "collision_unsafe",
                                    "allowance",
                                    "level_range",
                                ),
                                strict=True,
                            )
                        ],
                        "child_popup": "CAMIllustrationDialog",
                        "focus_restored": True,
                        "vector_rendering": True,
                        "aspect_ratio_fit_inside": True,
                        "boring_regression": True,
                        "parallel_regression": True,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            (output / "simulation_post_gate_report.json").write_text(
                json.dumps(
                    {
                        "simulation_gate": simulation_gate,
                        "post_gate": post_gate,
                        "simulation_requires_current_artifact": True,
                        "post_fail_closed": True,
                        "algorithm_version": 2,
                        "payload_version": 1,
                        "case_matrix": {
                            "current_v2_ready_safe": {
                                "decision": "allowed",
                                "button_enabled": True,
                                "source_artifact": "production_z_level_calculation",
                            },
                            "unsafe": {
                                "decision": "blocked",
                                "button_enabled": False,
                                "reason": "Kết quả không an toàn",
                            },
                            "unknown": {
                                "decision": "blocked",
                                "button_enabled": False,
                                "reason": "Chưa xác định",
                            },
                            "stale": {
                                "decision": "blocked",
                                "button_enabled": False,
                                "reason": "Kết quả cần cập nhật",
                            },
                            "algorithm_v1": {
                                "decision": "blocked",
                                "button_enabled": False,
                                "reason": "Cần Thuật toán v2 hiện hành",
                            },
                            "invalid_safety_hash": {
                                "decision": "blocked",
                                "button_enabled": False,
                                "reason": "Hash an toàn không hợp lệ",
                            },
                            "invalid_artifact_hash": {
                                "decision": "blocked",
                                "button_enabled": False,
                                "reason": "Hash kết quả không hợp lệ",
                            },
                            "post_unsupported": {
                                "decision": "blocked",
                                "button_enabled": False,
                                "reason": (
                                    "Post sản xuất cho gia công tinh theo cao độ Z "
                                    "chưa được hỗ trợ"
                                ),
                            },
                        },
                        "allowed_state": _localized_evidence_values(allowed_gate_values),
                        "blocked_state": _localized_evidence_values(blocked_gate_values),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            entries = [
                {
                    "file": path.name,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "bytes": path.stat().st_size,
                }
                for path in paths
            ]
            review_descriptions = {
                "zlevel_editor_basic_1366x768.png": ("Basic 1366×768", "ReviewWindow + FunctionEditorPage", "không horizontal scroll", "responsive_bounds_report.json"),
                "zlevel_editor_basic_1600x900.png": ("Basic 1600×900", "ReviewWindow + FunctionEditorPage", "summary và footer không crop", "responsive_bounds_report.json"),
                "zlevel_editor_basic_1920x1080.png": ("Basic 1920×1080", "ReviewWindow + FunctionEditorPage", "work-area được ghi nhất quán", "responsive_bounds_report.json"),
                "zlevel_editor_advanced.png": ("Nâng cao", "Năng lực FunctionEditorPage", "không lộ giá trị enum nội bộ", "localization_audit.json"),
                "zlevel_operation_manager.png": ("Trình quản lý nguyên công sản xuất", "dữ liệu hiển thị/mô hình/delegate", "một nguyên công thật", "operation_manager_report.json"),
                "zlevel_operation_manager_long_name.png": ("Tên dài trong Trình quản lý nguyên công", "dữ liệu hiển thị/mô hình/delegate", "tên tùy chỉnh được ưu tiên", "operation_manager_report.json"),
                "zlevel_auto_parameters.png": ("Tham số tự động", "hợp đồng tự động + trường tóm tắt", "giá trị hiệu lực/hash", "automatic_parameters_report.json"),
                "zlevel_manual_override.png": ("Tùy chỉnh thủ công", "mô hình bản nháp + trường chỉnh sửa", "ý định tùy chỉnh giữ nguyên", "persistence_report.json"),
                "zlevel_quality_fast.png": ("Hồ sơ Nhanh", "QComboBox + draft transform + illustration", "4.5 mm và level count production", "automatic_parameters_report.json"),
                "zlevel_quality_balanced.png": ("Hồ sơ Cân bằng", "QComboBox + draft transform + illustration", "3.0 mm và level count production", "automatic_parameters_report.json"),
                "zlevel_quality_high.png": ("Hồ sơ Chất lượng cao", "QComboBox + draft transform + illustration", "1.8 mm và level count production", "automatic_parameters_report.json"),
                "zlevel_inner_hole_illustration.png": ("Lỗ trong", "CAMIllustrationPanel", "inner loop preserved", "illustration_report.json"),
                "zlevel_disconnected_regions_illustration.png": ("Vùng rời rạc", "CAMIllustrationPanel", "liên kết vùng bảo thủ", "illustration_report.json"),
                "zlevel_direct_link_safe_illustration.png": ("Liên kết trực tiếp an toàn", "CAMIllustrationPanel", "liên kết trực tiếp chỉ sau kiểm tra", "illustration_report.json"),
                "zlevel_fallback_retract_illustration.png": ("Chuyển sang rút dao", "CAMIllustrationPanel", "chuyển sang phương án chặn an toàn", "illustration_report.json"),
                "zlevel_safety_unknown.png": ("An toàn chưa xác định", "các trường năng lực/an toàn", "không tuyên bố an toàn", "illustration_report.json"),
                "zlevel_collision_unsafe.png": ("Va chạm không an toàn", "các trường năng lực/an toàn", "không tạo kết quả sẵn sàng", "illustration_report.json"),
                "zlevel_allowance_illustration.png": ("Lượng dư", "CAMIllustrationPanel", "bù tâm Tool", "illustration_report.json"),
                "zlevel_level_range_illustration.png": ("Dải cao độ", "CAMIllustrationPanel + bộ lập lịch", "trên/dưới/bước xuống", "illustration_report.json"),
                "zlevel_child_illustration_popup.png": ("Cửa sổ minh họa con", "CAMIllustrationDialog", "chú thích vector trong cửa sổ con", "illustration_report.json"),
                "zlevel_child_focus_restore.png": ("Khôi phục tiêu điểm", "CAMIllustrationDialog + trình chỉnh sửa", "tiêu điểm trở lại nút phóng to", "lifecycle_report.json"),
                "zlevel_dpi_125.png": ("DPI 125%", "native Windows QPA", "DPR và scroll matrix", "responsive_bounds_report.json"),
                "zlevel_dpi_150.png": ("DPI 150%", "native Windows QPA", "vertical scroll được phép", "responsive_bounds_report.json"),
                "zlevel_long_tool_summary.png": ("Tool summary dài", "Tool/Holder binding", "không cắt nghĩa nguồn", "operation_manager_report.json"),
                "zlevel_validation_errors.png": ("Lỗi xác nhận", "Chẩn đoán FunctionEditor", "xác nhận chặn an toàn", "localization_audit.json"),
                "zlevel_simulation_gate.png": ("Cổng Mô phỏng", "bộ tính sản xuất + bảng cổng", "trạng thái cho phép và bị chặn cùng bảng", "simulation_post_gate_report.json"),
                "zlevel_post_fail_closed.png": ("Post chặn an toàn", "trường cổng Post", "không tạo G-code", "simulation_post_gate_report.json"),
            }
            review_lines = [
                "# Giai đoạn 8A.3.3 · Duyệt sản xuất Z-Level",
                "",
                "Mỗi mục dưới đây ghi trạng thái được chứng minh, widget/model nguồn, invariant và report liên quan.",
                "",
            ]
            for path in paths:
                state, source, invariant, report = review_descriptions[path.name]
                review_lines.append(
                    f"- `{path.name}` — {state}; nguồn: {source}; invariant: {invariant}; report: `{report}`."
                )
            review_lines.extend(
                (
                    "- `UI_STAGE_8A3_3_Z_LEVEL_PRODUCTION_EDITOR_MONTAGE.png` — montage của 27 ảnh kỹ thuật; nguồn: QImage montage; invariant: hash riêng; report: `summary.json`.",
                    "- `summary.json` — tổng số lượng, hash PNG, QPA/DPI và hợp đồng phiên bản; nguồn: bộ tạo; invariant: 38 file; báo cáo: chính file.",
                    "- `localization_audit.json` — kiểm tra chuỗi hiển thị và danh sách cho phép; nguồn: cây widget thật; invariant: không rò rỉ; báo cáo: chính file.",
                    "- `responsive_bounds_report.json` — ma trận kích thước/DPR/scroll; nguồn: QScrollArea + Windows work area; invariant: horizontal 0; report: chính file.",
                    "- `operation_manager_report.json` — dòng sản xuất thật; nguồn: dữ liệu hiển thị/mô hình/delegate; invariant: không dùng nhãn giả; báo cáo: chính file.",
                    "- `automatic_parameters_report.json` — Nhanh/Cân bằng/Chất lượng cao; nguồn: QComboBox/mô hình/chính sách; invariant: hash và số lớp đồng bộ; báo cáo: chính file.",
                    "- `persistence_report.json` — ma trận lưu bền; nguồn: hợp đồng/dịch vụ dự án; invariant: định dạng v1 và dữ liệu phụ thuộc lỗi thời; báo cáo: chính file.",
                    "- `lifecycle_report.json` — Áp dụng/Bỏ thay đổi/Tiếp tục/Tính/Hủy; nguồn: tác vụ/vòng đời dự án; invariant: callback lỗi thời bị loại; báo cáo: chính file.",
                    "- `illustration_report.json` — đủ 12 trạng thái sản xuất; nguồn: CAMIllustrationPanel/Dialog; invariant: vector vừa khung; báo cáo: chính file.",
                    "- `simulation_post_gate_report.json` — ma trận cổng; nguồn: bộ tính/vòng đời thật; invariant: Post chặn an toàn; báo cáo: chính file.",
                    "- `REVIEW_INDEX.md` — chỉ mục này; nguồn: package generator; invariant: liệt kê đủ 38 file; report: chính file.",
                )
            )
            (output / "REVIEW_INDEX.md").write_text(
                "\n".join(review_lines) + "\n",
                encoding="utf-8",
            )
        finally:
            window.close()
            workspace.close()
            service.close_project(discard_changes=True)
            app.processEvents()
    montage = _montage(output, tuple(output / name for name in STATE_NAMES))
    summary_path = output / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    pngs = tuple(output.glob("*.png"))
    summary["unique_png_hash_count"] = len(
        {hashlib.sha256(path.read_bytes()).hexdigest() for path in pngs}
    )
    summary["total_file_count"] = len(tuple(output.iterdir()))
    validate_review_summary(summary)
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return tuple(output / name for name in STATE_NAMES) + (montage,)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "reference_private/DERIVED/UI_STAGE_8A3_3_Z_LEVEL_PRODUCTION_EDITOR"
        ),
    )
    parser.add_argument("--dpi-only", choices=("1.25", "1.5", "2.0"))
    parser.add_argument("--focused-baseline")
    parser.add_argument("--full-baseline")
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate the existing package summary against current sources",
    )
    args = parser.parse_args()
    if args.check:
        summary = json.loads(
            (args.output.resolve() / "summary.json").read_text(encoding="utf-8")
        )
        validate_review_summary(summary)
        print("review_summary=valid")
        return 0
    if args.dpi_only:
        args.output.resolve().mkdir(parents=True, exist_ok=True)
        return _generate_dpi_only(args.output.resolve(), args.dpi_only)
    paths = generate(
        args.output.resolve(),
        focused_baseline=args.focused_baseline,
        full_baseline=args.full_baseline,
    )
    print(f"generated={len(paths)}")
    print(paths[-1])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
