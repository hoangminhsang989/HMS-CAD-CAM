"""Compact Vietnamese-first wizard for external Level2 evidence workflow."""

from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWizard,
    QWizardPage,
    QWidget,
)

from hms_cadcam.cam.qualification.evidence_model import (
    EvidenceState,
    Level2QualificationRecord,
    Level2Readiness,
    Level2WorkflowState,
)
from hms_cadcam.cam.qualification.physical_model import PhysicalReadinessResult
from hms_cadcam.ui.i18n import translation_service
from hms_cadcam.ui.localization import localize_widget_tree, ui_text


_PAGE_TITLES = (
    "Step 1 — Machine",
    "Step 2 — NC",
    "Step 3 — Setup",
    "Step 4 — Tool and Holder",
    "Step 5 — Fixture",
    "Step 6 — Travel validation",
    "Step 7 — Dry-run",
    "Step 8 — Result",
)

_STATE_TEXT = {
    Level2WorkflowState.LEVEL1_STATICALLY_VALIDATED: "Statically validated",
    Level2WorkflowState.READY_FOR_EXTERNAL_LEVEL2_EVIDENCE: "Ready for machine verification",
    Level2WorkflowState.LEVEL2_EVIDENCE_PENDING: "Waiting for dry-run",
    Level2WorkflowState.DRY_RUN_QUALIFIED: "Dry-run passed",
    Level2WorkflowState.LEVEL2_EVIDENCE_FAILED: "Dry-run failed",
    Level2WorkflowState.LEVEL2_EVIDENCE_STALE: "Evidence is stale",
}


class _SummaryPage(QWizardPage):
    def __init__(self, title_key: str, description_key: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.title_key = title_key
        self.description_key = description_key
        self.setTitle(ui_text(title_key))
        root = QVBoxLayout(self)
        self.description = QLabel(ui_text(description_key), self)
        self.description.setWordWrap(True)
        self.summary = QLabel("—", self)
        self.summary.setWordWrap(True)
        root.addWidget(self.description)
        root.addWidget(self.summary)
        root.addStretch(1)

    def retranslate_ui(self) -> None:
        self.setTitle(ui_text(self.title_key))
        self.description.setText(ui_text(self.description_key))


class PhysicalQualificationWizard(QWizard):
    """Eight-step review/entry surface; domain services own every mutation."""

    save_requested = Signal(object)
    export_requested = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Stage18ATranche2PhysicalQualificationWizard")
        self.setWindowTitle(ui_text("Physical qualification"))
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        self.setOption(QWizard.WizardOption.HaveCustomButton1, True)
        self.setOption(QWizard.WizardOption.HaveCustomButton2, True)
        self._record: Level2QualificationRecord | None = None
        self._readiness: Level2Readiness | None = None
        self._physical_readiness: PhysicalReadinessResult | None = None

        self.machine_page = _SummaryPage(
            _PAGE_TITLES[0], "Select and verify the exact machine profile.", self
        )
        machine_form = QFormLayout()
        self.machine_identity = QLineEdit(self.machine_page)
        self.machine_identity.setReadOnly(True)
        self.machine_fingerprint = QLineEdit(self.machine_page)
        self.machine_fingerprint.setReadOnly(True)
        machine_form.addRow(ui_text("Exact machine profile"), self.machine_identity)
        machine_form.addRow(ui_text("Machine profile fingerprint"), self.machine_fingerprint)
        self.machine_page.layout().insertLayout(2, machine_form)

        self.nc_page = _SummaryPage(
            _PAGE_TITLES[1], "Bind the exact statically qualified NC artifact.", self
        )
        nc_form = QFormLayout()
        self.nc_artifact_id = QLineEdit(self.nc_page)
        self.nc_artifact_id.setReadOnly(True)
        self.nc_sha256 = QLineEdit(self.nc_page)
        self.nc_sha256.setReadOnly(True)
        nc_form.addRow(ui_text("NC artifact"), self.nc_artifact_id)
        nc_form.addRow(ui_text("NC SHA-256"), self.nc_sha256)
        self.nc_page.layout().insertLayout(2, nc_form)

        self.setup_page = _SummaryPage(
            _PAGE_TITLES[2], "Enter or verify G54, part zero, and stock placement.", self
        )
        setup_form = QFormLayout()
        self.work_offset = QComboBox(self.setup_page)
        self.work_offset.addItem("G54", "G54")
        self.translation = QLineEdit(self.setup_page)
        self.orientation = QLineEdit(self.setup_page)
        self.stock_placement = QLineEdit(self.setup_page)
        setup_form.addRow(ui_text("Work offset status"), self.work_offset)
        setup_form.addRow(ui_text("Translation in machine coordinates"), self.translation)
        setup_form.addRow(ui_text("Setup orientation"), self.orientation)
        setup_form.addRow(ui_text("Stock placement"), self.stock_placement)
        self.setup_page.layout().insertLayout(2, setup_form)

        self.tool_page = _SummaryPage(
            _PAGE_TITLES[3], "Review Tool, Holder, gauge length, stick-out, and reach.", self
        )
        self.tool_table = QTableWidget(0, 5, self.tool_page)
        self.tool_table.setHorizontalHeaderLabels(
            [ui_text(value) for value in ("Tool", "Holder", "Gauge length", "Stick-out", "Reach")]
        )
        self.tool_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.tool_page.layout().insertWidget(2, self.tool_table)

        self.fixture_page = _SummaryPage(
            _PAGE_TITLES[4], "Record fixture identity, placement, geometry authority, and state.", self
        )
        fixture_form = QFormLayout()
        self.fixture_identity = QLineEdit(self.fixture_page)
        self.fixture_state = QLineEdit(self.fixture_page)
        self.fixture_envelope = QLineEdit(self.fixture_page)
        for widget in (self.fixture_identity, self.fixture_state, self.fixture_envelope):
            widget.setReadOnly(True)
        fixture_form.addRow(ui_text("Fixture evidence"), self.fixture_identity)
        fixture_form.addRow(ui_text("Verification state"), self.fixture_state)
        fixture_form.addRow(ui_text("Bounding envelope"), self.fixture_envelope)
        self.fixture_page.layout().insertLayout(2, fixture_form)

        self.travel_page = _SummaryPage(
            _PAGE_TITLES[5], "Show known machine-coordinate travel and every physical unknown.", self
        )
        travel_form = QFormLayout()
        self.travel_state = QLineEdit(self.travel_page)
        self.travel_state.setReadOnly(True)
        self.clearance_state = QLineEdit(self.travel_page)
        self.clearance_state.setReadOnly(True)
        travel_form.addRow(ui_text("Physical travel status"), self.travel_state)
        travel_form.addRow(ui_text("Holder and fixture clearance"), self.clearance_state)
        self.travel_page.layout().insertLayout(2, travel_form)

        self.dry_run_page = _SummaryPage(
            _PAGE_TITLES[6], "Record evidence from an externally performed machine check.", self
        )
        evidence_form = QFormLayout()
        self.run_mode = QComboBox(self.dry_run_page)
        for value in ("CONTROLLER_GRAPHICS", "DRY_RUN", "SINGLE_BLOCK", "AIR_CUT"):
            self.run_mode.addItem(value, value)
        self.run_result = QComboBox(self.dry_run_page)
        for value in (EvidenceState.PENDING, EvidenceState.PASS, EvidenceState.FAIL):
            self.run_result.addItem(value.value, value)
        self.operator = QLineEdit(self.dry_run_page)
        self.verifier = QLineEdit(self.dry_run_page)
        self.owner = QLineEdit(self.dry_run_page)
        self.observations = QPlainTextEdit(self.dry_run_page)
        self.observations.setMaximumBlockCount(100)
        evidence_form.addRow(ui_text("Run mode"), self.run_mode)
        evidence_form.addRow(ui_text("Result"), self.run_result)
        evidence_form.addRow(ui_text("Operator"), self.operator)
        evidence_form.addRow(ui_text("Verifier"), self.verifier)
        evidence_form.addRow(ui_text("Acceptance authority"), self.owner)
        evidence_form.addRow(ui_text("Observations"), self.observations)
        self.dry_run_page.layout().insertLayout(2, evidence_form)

        self.result_page = _SummaryPage(
            _PAGE_TITLES[7], "Review Level1, external-readiness, and Level2 eligibility.", self
        )
        result_form = QFormLayout()
        self.result_status = QLabel(ui_text("Statically validated"), self.result_page)
        self.acceptance_boundary = QLabel(ui_text("Not machine accepted"), self.result_page)
        self.missing_list = QListWidget(self.result_page)
        self.blocker_list = QListWidget(self.result_page)
        result_form.addRow(ui_text("Qualification result"), self.result_status)
        result_form.addRow(ui_text("Level3 boundary"), self.acceptance_boundary)
        result_form.addRow(ui_text("Missing evidence"), self.missing_list)
        result_form.addRow(ui_text("Physical blockers"), self.blocker_list)
        self.result_page.layout().insertLayout(2, result_form)

        self._pages = (
            self.machine_page, self.nc_page, self.setup_page, self.tool_page,
            self.fixture_page, self.travel_page, self.dry_run_page, self.result_page,
        )
        for page in self._pages:
            self.addPage(page)
        self.customButtonClicked.connect(self._custom_button_clicked)
        translation_service().language_changed.connect(self.retranslate_ui)
        self.retranslate_ui()

    @property
    def record(self) -> Level2QualificationRecord | None:
        return self._record

    @property
    def readiness(self) -> Level2Readiness | None:
        return self._readiness

    def set_context(
        self,
        record: Level2QualificationRecord,
        readiness: Level2Readiness,
        physical_readiness: PhysicalReadinessResult,
    ) -> None:
        """Project immutable typed state; this widget never promotes a level."""

        if not isinstance(record, Level2QualificationRecord) or not isinstance(
            readiness, Level2Readiness
        ) or not isinstance(physical_readiness, PhysicalReadinessResult):
            raise TypeError("Wizard record/readiness is invalid")
        self._record = record
        self._readiness = readiness
        self._physical_readiness = physical_readiness
        setup = record.setup
        self.machine_identity.setText(setup.machine_profile_id)
        self.machine_fingerprint.setText(setup.machine_profile_fingerprint.digest)
        self.nc_artifact_id.setText(setup.nc_artifact_id)
        self.nc_sha256.setText(setup.nc_sha256)
        translation = setup.work_offset_transform.translation_mm
        orientation = setup.work_offset_transform.orientation_deg
        self.translation.setText(self._components((translation.x, translation.y, translation.z)))
        self.orientation.setText(
            self._components((orientation.x_deg, orientation.y_deg, orientation.z_deg))
        )
        stock = setup.stock
        self.stock_placement.setText(
            f"{stock.dimensions.x_mm:g} × {stock.dimensions.y_mm:g} × {stock.dimensions.z_mm:g} mm; "
            f"XYZ {self._components((stock.origin_machine_mm.x, stock.origin_machine_mm.y, stock.origin_machine_mm.z))}"
        )
        self.tool_table.setRowCount(len(setup.tools))
        for row, tool in enumerate(setup.tools):
            values = (
                f"T{tool.tool_number}",
                "—" if tool.holder_fingerprint is None else tool.holder_fingerprint.digest[:12],
                self._millimetres(tool.gauge_length_mm), self._millimetres(tool.stickout_mm),
                tool.reach_state.value,
            )
            for column, value in enumerate(values):
                self.tool_table.setItem(row, column, QTableWidgetItem(value))
        fixture = setup.fixture
        if fixture is None:
            self.fixture_identity.setText("FIXTURE_PLACEMENT_UNVERIFIED")
            self.fixture_state.setText("UNVERIFIED")
            self.fixture_envelope.setText("—")
        else:
            self.fixture_identity.setText(f"{fixture.fixture_id} — {fixture.fixture_type}")
            self.fixture_state.setText(fixture.verification_state.value)
            envelope = fixture.bounding_envelope
            self.fixture_envelope.setText(
                "—" if envelope is None else f"{envelope.x_mm:g} × {envelope.y_mm:g} × {envelope.z_mm:g} mm"
            )
        self.travel_state.setText(physical_readiness.travel_state.value)
        self.clearance_state.setText(physical_readiness.clearance_state.value)
        self._refresh_result()

    @staticmethod
    def _components(values: Iterable[float | None]) -> str:
        return ", ".join("UNKNOWN" if value is None else f"{value:g}" for value in values)

    @staticmethod
    def _millimetres(value: float | None) -> str:
        return "UNKNOWN" if value is None else f"{value:g} mm"

    def _refresh_result(self) -> None:
        self.missing_list.clear()
        self.blocker_list.clear()
        if self._readiness is None:
            self.result_status.setText(ui_text("Statically validated"))
            return
        self.result_status.setText(ui_text(_STATE_TEXT[self._readiness.workflow_state]))
        self.missing_list.addItems(self._readiness.missing)
        self.blocker_list.addItems(self._readiness.blockers)
        self.acceptance_boundary.setText(ui_text("Not machine accepted"))

    def _custom_button_clicked(self, button: int) -> None:
        if self._record is None:
            return
        button_value = getattr(button, "value", button)
        custom_save = getattr(QWizard.WizardButton.CustomButton1, "value", QWizard.WizardButton.CustomButton1)
        custom_export = getattr(QWizard.WizardButton.CustomButton2, "value", QWizard.WizardButton.CustomButton2)
        if button_value == custom_save:
            self.save_requested.emit(self._record)
        elif button_value == custom_export:
            self.export_requested.emit(self._record)

    def retranslate_ui(self, _language: object = None) -> None:
        """Switch VI/EN/KO labels without mutating qualification state."""

        self.setWindowTitle(ui_text("Physical qualification"))
        self.setButtonText(QWizard.WizardButton.BackButton, ui_text("Back"))
        self.setButtonText(QWizard.WizardButton.NextButton, ui_text("Next"))
        self.setButtonText(QWizard.WizardButton.FinishButton, ui_text("Save"))
        self.setButtonText(QWizard.WizardButton.CustomButton1, ui_text("Save"))
        self.setButtonText(
            QWizard.WizardButton.CustomButton2,
            ui_text("Export verification package"),
        )
        for page in self._pages:
            page.retranslate_ui()
        localize_widget_tree(self)
        self._refresh_result()


__all__ = ["PhysicalQualificationWizard"]
