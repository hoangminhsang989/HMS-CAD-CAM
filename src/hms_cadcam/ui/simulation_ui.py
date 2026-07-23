"""Qt Simulation 7C.3 panel with atomic policy drafts and issue details."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from hms_cadcam.cam.domain import ArtifactStatus, DiagnosticSeverity, OperationId
from hms_cadcam.cam.simulation import (
    SimulationInputSnapshot,
    SimulationProgress,
    SimulationResult,
    SimulationRunRecord,
    SimulationRunState,
    SimulationSamplingPolicy,
)
from hms_cadcam.viewer.simulation import (
    SimulationDisplayPolicy,
    SimulationPresentation,
)
from hms_cadcam.ui.localization import (
    LocalizedComboBox,
    localize_widget_tree,
    translate_progress_phase,
    translate_status,
    ui_text,
)

_MAX_DISPLAY_POINTS = 1_000_000
_MAX_DISPLAY_MARKERS = 10_000


@dataclass(frozen=True, slots=True)
class SimulationIssueSelection:
    operation_id: OperationId
    issue_index: int
    marker_id: str | None


class SimulationPanel(QWidget):
    """Display and edit simulation state without executing business logic."""

    run_requested = Signal()
    cancel_requested = Signal()
    clear_requested = Signal()
    visibility_requested = Signal(bool)
    policy_applied = Signal(object, object)
    issue_focus_requested = Signal(object)
    issue_selection_cleared = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("CamSimulationPanel")
        self._inputs: SimulationInputSnapshot | None = None
        self._result: SimulationResult | None = None
        self._presentation: SimulationPresentation | None = None
        self._sampling_policy = SimulationSamplingPolicy()
        self._display_policy = SimulationDisplayPolicy()
        self._overlay_visible = True
        self._policy_guard = False
        self._run_started_at: datetime | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        title = QLabel("Simulation 7C.3")
        title.setObjectName("SimulationPanelTitle")
        root.addWidget(title)

        source_group = QGroupBox("Nguồn và trạng thái")
        source_form = QFormLayout(source_group)
        self.source_labels = {
            key: QLabel("—")
            for key in (
                "operation", "artifact", "artifact_state", "stock", "fixtures",
                "tooling", "policy", "latest", "status", "issues", "samples",
                "elapsed", "overlay", "current",
            )
        }
        for value in self.source_labels.values():
            value.setWordWrap(True)
        for label, key in (
            ("Operation", "operation"),
            ("ToolpathArtifact", "artifact"),
            ("Artifact state", "artifact_state"),
            ("Stock", "stock"),
            ("Fixtures", "fixtures"),
            ("Tool / Holder / Machine", "tooling"),
            ("Sampling", "policy"),
            ("Run state", "latest"),
            ("PASS / WARN / FAIL", "status"),
            ("Issues", "issues"),
            ("Samples", "samples"),
            ("Elapsed", "elapsed"),
            ("Overlay", "overlay"),
            ("Result", "current"),
        ):
            source_form.addRow(label, self.source_labels[key])
        root.addWidget(source_group)

        progress_group = QGroupBox("Tiến độ")
        progress_layout = QVBoxLayout(progress_group)
        self.progress_label = QLabel("—")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        progress_layout.addWidget(self.progress_label)
        progress_layout.addWidget(self.progress_bar)
        root.addWidget(progress_group)

        policy_group = QGroupBox("Sampling policy")
        policy_form = QFormLayout(policy_group)
        self.policy_fields = {
            key: QLineEdit()
            for key in (
                "max_linear_step", "chord_tolerance", "max_arc_angle_degrees",
                "geometric_tolerance", "maximum_samples", "maximum_issues",
                "maximum_path_points", "maximum_markers",
            )
        }
        for label, key in (
            ("Max linear step (project unit)", "max_linear_step"),
            ("Chord tolerance (project unit)", "chord_tolerance"),
            ("Max arc angle (degree)", "max_arc_angle_degrees"),
            ("Geometric tolerance (project unit)", "geometric_tolerance"),
            ("Maximum samples (≤ 1,000,000)", "maximum_samples"),
            ("Maximum issues (≤ 10,000)", "maximum_issues"),
            ("Display point cap (≤ 1,000,000)", "maximum_path_points"),
            ("Display marker cap (≤ 10,000)", "maximum_markers"),
        ):
            policy_form.addRow(label, self.policy_fields[key])
        policy_buttons = QHBoxLayout()
        self.apply_policy_button = QPushButton("Áp dụng policy")
        self.reset_policy_button = QPushButton("Mặc định")
        policy_buttons.addWidget(self.apply_policy_button)
        policy_buttons.addWidget(self.reset_policy_button)
        policy_form.addRow(policy_buttons)
        self.policy_error = QLabel()
        self.policy_error.setStyleSheet("color: #d9534f")
        self.policy_error.setWordWrap(True)
        policy_form.addRow("Chẩn đoán", self.policy_error)
        root.addWidget(policy_group)

        actions = QHBoxLayout()
        self.run_button = QPushButton("Run Simulation")
        self.cancel_button = QPushButton("Cancel")
        self.visibility_button = QPushButton("Hide Overlay")
        self.clear_button = QPushButton("Clear Result")
        self.details_button = QPushButton("Open issue details")
        for button in (
            self.run_button, self.cancel_button, self.visibility_button,
            self.clear_button, self.details_button,
        ):
            actions.addWidget(button)
        root.addLayout(actions)

        issue_group = QGroupBox("Simulation issues")
        issue_layout = QVBoxLayout(issue_group)
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Filter"))
        self.issue_filter = LocalizedComboBox()
        for label in ("ALL", "ERROR", "WARNING", "INFO"):
            self.issue_filter.addItem(label, label)
        self.clear_issue_selection_button = QPushButton("Clear selection")
        self.copy_issue_button = QPushButton("Copy technical details")
        filter_row.addWidget(self.issue_filter)
        filter_row.addWidget(self.clear_issue_selection_button)
        filter_row.addWidget(self.copy_issue_button)
        issue_layout.addLayout(filter_row)
        self.issue_table = QTableWidget(0, 8)
        self.issue_table.setObjectName("SimulationIssueTable")
        self.issue_table.setHorizontalHeaderLabels(
            [
                "Severity", "Category", "Operation / Result", "Event",
                "Segment", "Sample", "Entities", "Message / evidence",
            ]
        )
        self.issue_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.issue_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.issue_table.setSortingEnabled(False)
        issue_layout.addWidget(self.issue_table)
        self.issue_details = QLabel("—")
        self.issue_details.setWordWrap(True)
        issue_layout.addWidget(self.issue_details)
        root.addWidget(issue_group)

        self.run_button.clicked.connect(self.run_requested.emit)
        self.cancel_button.clicked.connect(self.cancel_requested.emit)
        self.clear_button.clicked.connect(self.clear_requested.emit)
        self.visibility_button.clicked.connect(self._toggle_visibility)
        self.apply_policy_button.clicked.connect(self.apply_policy_draft)
        self.reset_policy_button.clicked.connect(self.reset_policy_defaults)
        self.issue_filter.currentTextChanged.connect(self._render_issues)
        self.issue_table.itemSelectionChanged.connect(self._select_issue)
        self.clear_issue_selection_button.clicked.connect(self.clear_issue_selection)
        self.copy_issue_button.clicked.connect(self.copy_issue_details)
        self.details_button.clicked.connect(self._show_issue_details)
        self.set_policy(self._sampling_policy, self._display_policy)
        self.clear_source()
        localize_widget_tree(self)

    @property
    def sampling_policy(self) -> SimulationSamplingPolicy:
        return self._sampling_policy

    @property
    def display_policy(self) -> SimulationDisplayPolicy:
        return self._display_policy

    @property
    def inputs(self) -> SimulationInputSnapshot | None:
        return self._inputs

    def clear_source(self) -> None:
        self._inputs = None
        self._result = None
        self._presentation = None
        self._run_started_at = None
        for label in self.source_labels.values():
            label.setText("—")
        self.progress_label.setText("—")
        self.progress_bar.setValue(0)
        self.issue_table.setRowCount(0)
        self.issue_details.setText("—")
        self._set_action_state(can_run=False, active=False, has_result=False)
        localize_widget_tree(self)

    def show_source(
        self,
        inputs: SimulationInputSnapshot,
        *,
        can_run: bool,
        cache_status: str = "—",
    ) -> None:
        self._inputs = inputs
        operation, artifact, setup = inputs.operation, inputs.artifact, inputs.setup
        self.source_labels["operation"].setText(
            f"{operation.operation_id} · rev {operation.revision.value} · "
            f"{'đã bật' if operation.enabled else 'đã tắt'}"
        )
        self.source_labels["artifact"].setText(str(artifact.artifact_id))
        fingerprint = artifact.artifact_fingerprint
        self.source_labels["artifact_state"].setText(
            f"{translate_status(operation.artifact_state.status.value.upper())} · "
            f"{translate_status(artifact.completion_status.value.upper())} · "
            f"{fingerprint.digest[:16] if fingerprint else 'không có dấu vân tay'}"
        )
        self.source_labels["stock"].setText(setup.stock.kind.value.upper())
        self.source_labels["fixtures"].setText(
            str(sum(item.enabled for item in setup.fixtures))
        )
        self.source_labels["tooling"].setText(
            f"{inputs.tool.name} / "
            f"{inputs.holder.name if inputs.holder else 'THIẾU'} / "
            f"{inputs.machine.name if inputs.machine else 'không có'}"
        )
        self.source_labels["policy"].setText(self._policy_summary())
        self.source_labels["latest"].setText("ĐANG CHỜ")
        self.source_labels["current"].setText(
            f"Không có kết quả hiện hành · {ui_text(cache_status)}"
        )
        self.source_labels["status"].setText("—")
        self.source_labels["issues"].setText("0")
        self.source_labels["samples"].setText("0")
        self.source_labels["overlay"].setText("0 / 0 điểm · 0 / 0 dấu")
        self._set_action_state(can_run=can_run, active=False, has_result=False)
        localize_widget_tree(self)

    def show_unavailable(self, operation_id: OperationId, message: str) -> None:
        self.clear_source()
        self.source_labels["operation"].setText(str(operation_id))
        self.source_labels["latest"].setText(ui_text(message))
        self.source_labels["current"].setText("Không có kết quả hiện hành")

    def set_policy(
        self,
        sampling: SimulationSamplingPolicy,
        display: SimulationDisplayPolicy,
    ) -> None:
        self._sampling_policy = sampling
        self._display_policy = display
        self._policy_guard = True
        values = {
            "max_linear_step": sampling.max_linear_step,
            "chord_tolerance": sampling.chord_tolerance,
            "max_arc_angle_degrees": math.degrees(sampling.max_arc_angle),
            "geometric_tolerance": sampling.geometric_tolerance,
            "maximum_samples": sampling.maximum_samples,
            "maximum_issues": sampling.maximum_issues,
            "maximum_path_points": display.maximum_path_points,
            "maximum_markers": display.maximum_markers,
        }
        for key, value in values.items():
            self.policy_fields[key].setText(format(value, ".12g"))
        self._policy_guard = False
        self.policy_error.clear()
        self.source_labels["policy"].setText(self._policy_summary())

    def apply_policy_draft(self) -> bool:
        """Apply all fields atomically; invalid text leaves policy unchanged."""
        try:
            path_cap = int(self.policy_fields["maximum_path_points"].text())
            marker_cap = int(self.policy_fields["maximum_markers"].text())
            if not 2 <= path_cap <= _MAX_DISPLAY_POINTS:
                raise ValueError("Giới hạn điểm hiển thị vượt giới hạn cứng")
            if not 1 <= marker_cap <= _MAX_DISPLAY_MARKERS:
                raise ValueError("Giới hạn dấu hiển thị vượt giới hạn cứng")
            sampling = SimulationSamplingPolicy(
                max_linear_step=float(self.policy_fields["max_linear_step"].text()),
                chord_tolerance=float(self.policy_fields["chord_tolerance"].text()),
                max_arc_angle=math.radians(
                    float(self.policy_fields["max_arc_angle_degrees"].text())
                ),
                geometric_tolerance=float(
                    self.policy_fields["geometric_tolerance"].text()
                ),
                maximum_samples=int(self.policy_fields["maximum_samples"].text()),
                maximum_issues=int(self.policy_fields["maximum_issues"].text()),
            )
            display = SimulationDisplayPolicy(path_cap, marker_cap)
        except (TypeError, ValueError) as error:
            self.policy_error.setText(f"Chính sách không hợp lệ: {error}")
            return False
        self.set_policy(sampling, display)
        self.policy_applied.emit(sampling, display)
        return True

    def reset_policy_defaults(self) -> None:
        sampling = SimulationSamplingPolicy()
        display = SimulationDisplayPolicy()
        self.set_policy(sampling, display)
        self.policy_applied.emit(sampling, display)

    def set_run_record(self, record: SimulationRunRecord | None) -> None:
        if record is None:
            self._run_started_at = None
            self.source_labels["latest"].setText("ĐANG CHỜ")
            self._set_action_state(
                can_run=self._inputs is not None,
                active=False,
                has_result=self._result is not None,
            )
            return
        text = translate_status(record.state.value.upper())
        self._run_started_at = record.started_at
        if record.diagnostic_code is not None:
            text += f" · {record.diagnostic_code.value}"
        if record.diagnostic_message:
            text += f" · {ui_text(record.diagnostic_message)}"
        self.source_labels["latest"].setText(text)
        self._set_elapsed(record.completed_at)
        active = record.state in {
            SimulationRunState.VALIDATING,
            SimulationRunState.RUNNING,
            SimulationRunState.CANCELLING,
        }
        self._set_action_state(
            can_run=self._inputs is not None,
            active=active,
            has_result=self._result is not None,
        )
        localize_widget_tree(self)

    def set_progress(self, progress: SimulationProgress) -> None:
        self._set_elapsed(None)
        percentage = progress.percentage
        percentage_text = "?" if percentage is None else f"{percentage:.1f}%"
        total_text = "?" if progress.total is None else str(progress.total)
        self.progress_label.setText(
            f"{translate_progress_phase(progress.phase)} · "
            f"{progress.processed}/{total_text} · {percentage_text} · "
            f"{progress.issue_count} vấn đề"
        )
        if percentage is None:
            self.progress_bar.setRange(0, 0)
        else:
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(round(percentage))

    def set_result(
        self,
        result: SimulationResult,
        presentation: SimulationPresentation | None,
        *,
        current: bool,
    ) -> None:
        self._result = result
        self._presentation = presentation
        self.source_labels["status"].setText(
            translate_status(result.status.value.upper())
        )
        self.source_labels["issues"].setText(
            f"{result.statistics.error_count} lỗi · "
            f"{result.statistics.warning_count} cảnh báo · "
            f"tổng {len(result.issues)}"
        )
        self.source_labels["samples"].setText(
            f"{result.statistics.sampled_point_count} điểm · "
            f"{result.statistics.sampled_segment_count} đoạn"
        )
        if presentation is None:
            self.source_labels["overlay"].setText("chưa kết xuất")
        else:
            self._overlay_visible = presentation.visible
            self.visibility_button.setText(
                "Ẩn lớp phủ" if presentation.visible else "Hiện lớp phủ"
            )
            self.source_labels["overlay"].setText(
                f"{presentation.displayed_path_point_count} / "
                f"{presentation.total_path_point_count} điểm · "
                f"{presentation.displayed_marker_count} / "
                f"{presentation.total_marker_count} dấu"
            )
        self.source_labels["current"].setText(
            "HIỆN HÀNH" if current else "ĐÃ LỖI THỜI / KHÔNG HIỆN HÀNH"
        )
        self._render_issues()
        self._set_action_state(
            can_run=self._inputs is not None,
            active=False,
            has_result=True,
        )
        localize_widget_tree(self)

    def mark_result_stale(
        self, message: str = "ĐÃ LỖI THỜI / KHÔNG HIỆN HÀNH"
    ) -> None:
        self.source_labels["current"].setText(ui_text(message))
        self._set_action_state(
            can_run=self._inputs is not None,
            active=False,
            has_result=self._result is not None,
        )

    def clear_result_display(self) -> None:
        self._result = None
        self._presentation = None
        self.source_labels["status"].setText("—")
        self.source_labels["issues"].setText("0")
        self.source_labels["samples"].setText("0")
        self.source_labels["overlay"].setText("0 / 0 điểm · 0 / 0 dấu")
        self.source_labels["current"].setText("Không có kết quả hiện hành")
        self.issue_table.setRowCount(0)
        self.issue_details.setText("—")
        self._set_action_state(
            can_run=self._inputs is not None,
            active=False,
            has_result=False,
        )

    def set_cache_diagnostic(self, text: str) -> None:
        current = self.source_labels["current"].text()
        self.source_labels["current"].setText(f"{current} · {ui_text(text)}")

    def clear_issue_selection(self) -> None:
        self.issue_table.clearSelection()
        self.issue_details.setText("—")
        self.issue_selection_cleared.emit()

    def copy_issue_details(self) -> None:
        issue = self._selected_issue()
        if issue is None:
            return
        QApplication.clipboard().setText(
            json.dumps(
                issue.to_dict(),
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                indent=2,
            )
        )

    def _toggle_visibility(self) -> None:
        self._overlay_visible = not self._overlay_visible
        self.visibility_button.setText(
            "Ẩn lớp phủ" if self._overlay_visible else "Hiện lớp phủ"
        )
        self.visibility_requested.emit(self._overlay_visible)

    def _render_issues(self) -> None:
        self.issue_table.setRowCount(0)
        if self._result is None:
            return
        filter_value = str(self.issue_filter.currentData())
        markers = {
            marker.issue_index: marker.marker_id
            for marker in self._presentation.markers
        } if self._presentation is not None else {}
        for issue_index, issue in enumerate(self._result.issues):
            if filter_value != "ALL" and issue.severity.value.upper() != filter_value:
                continue
            row = self.issue_table.rowCount()
            self.issue_table.insertRow(row)
            evidence = "; ".join(f"{key}={value}" for key, value in issue.evidence)
            values = (
                translate_status(issue.severity.value.upper()),
                ui_text(issue.category.value),
                f"{issue.operation_id} / {self._result.result_id}",
                "—" if issue.event_index is None else str(issue.event_index),
                "—" if issue.segment_index is None else str(issue.segment_index),
                "—" if issue.sample_index is None else str(issue.sample_index),
                ", ".join(issue.involved_entities) or "—",
                f"{ui_text(issue.message_key)} · {evidence}"
                if evidence
                else ui_text(issue.message_key),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, issue_index)
                item.setData(Qt.ItemDataRole.UserRole + 1, markers.get(issue_index))
                self.issue_table.setItem(row, column, item)
        localize_widget_tree(self.issue_table)

    def _selected_issue(self):
        if self._result is None:
            return None
        row = self.issue_table.currentRow()
        item = self.issue_table.item(row, 0) if row >= 0 else None
        if item is None:
            return None
        issue_index = item.data(Qt.ItemDataRole.UserRole)
        if type(issue_index) is not int or not 0 <= issue_index < len(self._result.issues):
            return None
        return self._result.issues[issue_index]

    def _select_issue(self) -> None:
        issue = self._selected_issue()
        if issue is None or self._result is None:
            return
        row = self.issue_table.currentRow()
        item = self.issue_table.item(row, 0)
        issue_index = item.data(Qt.ItemDataRole.UserRole)
        marker_id = item.data(Qt.ItemDataRole.UserRole + 1)
        evidence = ", ".join(f"{key}={value}" for key, value in issue.evidence)
        self.issue_details.setText(
            f"{issue.code.value} · sự kiện={issue.event_index} · "
            f"đoạn={issue.segment_index} · mẫu={issue.sample_index} · "
            f"thực thể={','.join(issue.involved_entities) or '—'} · "
            f"bằng chứng={evidence or '—'}"
        )
        self.issue_focus_requested.emit(
            SimulationIssueSelection(issue.operation_id, issue_index, marker_id)
        )

    def _show_issue_details(self) -> None:
        if self._selected_issue() is None and self.issue_table.rowCount():
            self.issue_table.selectRow(0)

    def _set_action_state(
        self,
        *,
        can_run: bool,
        active: bool,
        has_result: bool,
    ) -> None:
        self.run_button.setEnabled(can_run and not active)
        self.cancel_button.setEnabled(active)
        self.visibility_button.setEnabled(has_result and not active)
        self.clear_button.setEnabled(has_result and not active)
        self.details_button.setEnabled(has_result)
        self.apply_policy_button.setEnabled(not active)
        self.reset_policy_button.setEnabled(not active)

    def _policy_summary(self) -> str:
        value = self._sampling_policy
        display = self._display_policy
        return (
            f"bước thẳng {value.max_linear_step:g} · dây cung {value.chord_tolerance:g} · "
            f"cung {math.degrees(value.max_arc_angle):g}° · hình học "
            f"{value.geometric_tolerance:g} · mẫu {value.maximum_samples} · "
            f"vấn đề {value.maximum_issues} · hiển thị "
            f"{display.maximum_path_points}/{display.maximum_markers}"
        )

    def _set_elapsed(self, completed_at: datetime | None) -> None:
        if self._run_started_at is None:
            self.source_labels["elapsed"].setText("—")
            return
        end = completed_at or datetime.now(timezone.utc)
        seconds = max(0.0, (end - self._run_started_at).total_seconds())
        self.source_labels["elapsed"].setText(f"{seconds:.3f} s")
