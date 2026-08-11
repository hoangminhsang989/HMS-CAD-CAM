"""Vietnamese-first Stage18A Tranche3 NC verification and release center."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from hms_cadcam.cam.qualification.offline_model import (
    MotionClass,
    NCReleaseCandidate,
    OfflineFindingSeverity,
    OfflineNCVerificationSession,
    OperatorAcknowledgement,
    ReleaseAssessment,
    ReleaseState,
)
from hms_cadcam.cam.qualification.offline_reports import risk_summary
from hms_cadcam.ui.i18n import translation_service
from hms_cadcam.ui.localization import ui_text


_FILTERS: tuple[tuple[str, MotionClass | str | None], ...] = (
    ("All blocks", None), ("Rapid", MotionClass.RAPID),
    ("Cutting", "CUTTING"), ("Tool Change", MotionClass.TOOL_CHANGE),
    ("Spindle", MotionClass.SPINDLE_CONTROL), ("Coolant", MotionClass.COOLANT_CONTROL),
    ("Offset", MotionClass.OFFSET_CONTROL), ("Warning", "WARNING"),
    ("Blocker", "BLOCKER"),
)


class NCReleaseCenter(QWidget):
    """Project typed release state; never performs qualification or CNC I/O."""

    inspect_requested = Signal(int)
    compare_requested = Signal()
    operator_review_requested = Signal(str, str)
    export_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Stage18ATranche3NCReleaseCenter")
        self._session: OfflineNCVerificationSession | None = None
        self._candidate: NCReleaseCandidate | None = None
        self._assessment: ReleaseAssessment | None = None
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(4)
        self.title = QLabel(ui_text("NC verification and handoff"), self)
        self.title.setObjectName("NCReleaseCenterTitle")
        root.addWidget(self.title)

        cards = QHBoxLayout()
        self.nc_group, self.nc_form = self._card("NC")
        self.nc_file = QLabel("—", self.nc_group)
        self.nc_revision = QLabel("—", self.nc_group)
        self.nc_sha = QLabel("—", self.nc_group)
        self.nc_sha.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.nc_form.addRow(ui_text("File"), self.nc_file)
        self.nc_form.addRow(ui_text("Revision"), self.nc_revision)
        self.nc_form.addRow("SHA-256", self.nc_sha)
        cards.addWidget(self.nc_group)

        self.machine_group, machine_form = self._card("Machine")
        self.machine = QLabel("ROBODRILL α-D21MiB", self.machine_group)
        self.controller = QLabel("FANUC 31i-B", self.machine_group)
        machine_form.addRow(ui_text("Machine"), self.machine)
        machine_form.addRow(ui_text("Controller"), self.controller)
        cards.addWidget(self.machine_group)

        self.status_group, status_form = self._card("Status")
        self.level1 = QLabel(ui_text("No verification session."), self.status_group)
        self.level2 = QLabel(ui_text("Physical evidence unavailable"), self.status_group)
        self.machine_ready = QLabel(ui_text("No"), self.status_group)
        self.machine_ready.setProperty("machineReady", False)
        status_form.addRow("Level1", self.level1)
        status_form.addRow("Level2", self.level2)
        status_form.addRow("MACHINE READY", self.machine_ready)
        cards.addWidget(self.status_group)
        root.addLayout(cards)

        self.risk_group = QGroupBox(ui_text("Risk summary"), self)
        risk_form = QFormLayout(self.risk_group)
        self.risk_values: dict[str, QLabel] = {}
        for key, label in (
            ("total_blocks", "Total blocks"), ("rapid_blocks", "Rapid blocks"),
            ("cutting_blocks", "Cutting blocks"), ("tool_changes", "Tool changes"),
            ("warnings", "Warnings"), ("blockers", "Blockers"),
            ("unresolved_blocks", "Unresolved blocks"),
        ):
            value = QLabel("0", self.risk_group)
            self.risk_values[key] = value
            risk_form.addRow(ui_text(label), value)
        root.addWidget(self.risk_group)

        trace_header = QHBoxLayout()
        trace_header.addWidget(QLabel(ui_text("Block trace"), self))
        self.filter_combo = QComboBox(self)
        for label, value in _FILTERS:
            self.filter_combo.addItem(ui_text(label), value)
        self.filter_combo.currentIndexChanged.connect(self._refresh_trace)
        trace_header.addWidget(self.filter_combo)
        root.addLayout(trace_header)
        self.trace = QTableWidget(0, 6, self)
        self.trace.setObjectName("NCReleaseBlockTrace")
        self.trace.setHorizontalHeaderLabels(
            [ui_text(item) for item in ("Line", "Class", "Tool", "Spindle", "Offset", "Findings")]
        )
        self.trace.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.trace.itemSelectionChanged.connect(self._selected_trace)
        root.addWidget(self.trace)

        reviewer = QHBoxLayout()
        self.reviewer_name = QLineEdit(self)
        self.reviewer_name.setPlaceholderText(ui_text("Reviewer identity"))
        self.reviewer_role = QLineEdit(self)
        self.reviewer_role.setPlaceholderText(ui_text("Reviewer role"))
        reviewer.addWidget(self.reviewer_name)
        reviewer.addWidget(self.reviewer_role)
        root.addLayout(reviewer)
        self.software_ack = QCheckBox(OperatorAcknowledgement.REQUIRED_SOFTWARE_STATEMENT, self)
        self.machine_ack = QCheckBox(OperatorAcknowledgement.REQUIRED_MACHINE_READY_STATEMENT, self)
        root.addWidget(self.software_ack)
        root.addWidget(self.machine_ack)

        actions = QHBoxLayout()
        self.details_button = QPushButton(ui_text("View details"), self)
        self.compare_button = QPushButton(ui_text("Compare revision"), self)
        self.review_button = QPushButton(ui_text("Confirm operator"), self)
        self.export_button = QPushButton(ui_text("Export dry-run package"), self)
        actions.addWidget(self.details_button)
        actions.addWidget(self.compare_button)
        actions.addWidget(self.review_button)
        actions.addWidget(self.export_button)
        root.addLayout(actions)
        self.compare_button.clicked.connect(self.compare_requested)
        self.review_button.clicked.connect(self._review)
        self.export_button.clicked.connect(self.export_requested)
        self.details_button.clicked.connect(self._details)
        self.clear_release()
        translation_service().language_changed.connect(self.retranslate_ui)

    def _card(self, title: str) -> tuple[QGroupBox, QFormLayout]:
        group = QGroupBox(ui_text(title), self)
        return group, QFormLayout(group)

    def clear_release(self) -> None:
        self._session = None
        self._candidate = None
        self._assessment = None
        self.nc_file.setText("—")
        self.nc_revision.setText("—")
        self.nc_sha.setText("—")
        self.level1.setText(ui_text("No verification session."))
        self.level2.setText(ui_text("Physical evidence unavailable"))
        self.machine_ready.setText(ui_text("No"))
        self.machine_ready.setProperty("machineReady", False)
        for value in self.risk_values.values():
            value.setText("0")
        self.trace.setRowCount(0)
        self.review_button.setEnabled(False)
        self.export_button.setEnabled(False)
        self.compare_button.setEnabled(False)

    def set_release(
        self,
        session: OfflineNCVerificationSession,
        candidate: NCReleaseCandidate,
        assessment: ReleaseAssessment,
        *,
        filename: str,
    ) -> None:
        if candidate.verification_session_fingerprint != session.session_fingerprint:
            raise ValueError("Release candidate is stale for this verification session")
        self._session = session
        self._candidate = candidate
        self._assessment = assessment
        self.nc_file.setText(filename)
        self.nc_revision.setText(str(candidate.release_revision))
        self.nc_sha.setText(candidate.nc_sha256)
        self.level1.setText(ui_text("Software verification passed") if not session.blocker_count else ui_text("Blocked"))
        self.level2.setText(ui_text("Physical evidence unavailable"))
        self.machine_ready.setText(ui_text("No"))
        for key, value in risk_summary(session).items():
            self.risk_values[key].setText(str(value))
        self._refresh_trace()
        self.review_button.setEnabled(not session.blocker_count)
        self.compare_button.setEnabled(True)
        self.export_button.setEnabled(
            assessment.state is ReleaseState.READY_FOR_EXTERNAL_DRY_RUN_HANDOFF
        )

    def _matches(self, block: object, selected: object) -> bool:
        if selected is None:
            return True
        if selected == "CUTTING":
            return block.motion_class in {MotionClass.CUTTING_LINEAR, MotionClass.CUTTING_ARC}
        if selected == "WARNING":
            finding_ids = set(block.finding_ids)
            return any(
                item.finding_id in finding_ids and item.severity is OfflineFindingSeverity.WARNING
                for item in self._session.findings
            )
        if selected == "BLOCKER":
            finding_ids = set(block.finding_ids)
            return any(
                item.finding_id in finding_ids and item.severity is OfflineFindingSeverity.BLOCKER
                for item in self._session.findings
            )
        return block.motion_class is selected or block.motion_class.value == selected

    def _refresh_trace(self, _index: int = -1) -> None:
        self.trace.setRowCount(0)
        if self._session is None:
            return
        selected = self.filter_combo.currentData()
        blocks = [item for item in self._session.blocks if self._matches(item, selected)]
        self.trace.setRowCount(len(blocks))
        for row, block in enumerate(blocks):
            values = (
                str(block.original_line_number), block.motion_class.value,
                "—" if block.modal_after.tool is None else str(block.modal_after.tool),
                "ON" if block.modal_after.spindle_on else "OFF",
                block.modal_after.work_offset or "—", ", ".join(block.finding_ids),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, block.original_line_number)
                self.trace.setItem(row, column, item)

    def _selected_trace(self) -> None:
        items = self.trace.selectedItems()
        if items:
            self.inspect_requested.emit(int(items[0].data(Qt.ItemDataRole.UserRole)))

    def _details(self) -> None:
        if self.trace.rowCount():
            self.trace.selectRow(0)

    def _review(self) -> None:
        if (
            self._candidate is None or not self.reviewer_name.text().strip()
            or not self.reviewer_role.text().strip() or not self.software_ack.isChecked()
            or not self.machine_ack.isChecked()
        ):
            return
        self.operator_review_requested.emit(
            self.reviewer_name.text().strip(), self.reviewer_role.text().strip()
        )

    def retranslate_ui(self, _language: object = None) -> None:
        self.title.setText(ui_text("NC verification and handoff"))
        self.risk_group.setTitle(ui_text("Risk summary"))
        self.details_button.setText(ui_text("View details"))
        self.compare_button.setText(ui_text("Compare revision"))
        self.review_button.setText(ui_text("Confirm operator"))
        self.export_button.setText(ui_text("Export dry-run package"))
        for index, (label, _value) in enumerate(_FILTERS):
            self.filter_combo.setItemText(index, ui_text(label))
        self.trace.setHorizontalHeaderLabels(
            [ui_text(item) for item in ("Line", "Class", "Tool", "Spindle", "Offset", "Findings")]
        )


__all__ = ["NCReleaseCenter"]
