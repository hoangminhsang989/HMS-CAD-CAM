"""Compact Vietnamese-first Stage18A machine qualification status surface."""

from __future__ import annotations

from PySide6.QtWidgets import QFormLayout, QGroupBox, QLabel, QVBoxLayout, QWidget

from hms_cadcam.cam.qualification.model import (
    FindingCode,
    FindingSeverity,
    MachineQualificationContract,
    QualificationLevel,
    QualificationReport,
)
from hms_cadcam.ui.i18n import translation_service
from hms_cadcam.ui.localization import localize_widget_tree, ui_text


_LEVEL_TEXT = {
    QualificationLevel.UNQUALIFIED: "Unqualified",
    QualificationLevel.STATICALLY_VALIDATED: "Statically validated",
    QualificationLevel.DRY_RUN_QUALIFIED: "Dry-run qualified",
    QualificationLevel.MACHINE_ACCEPTED: "Machine accepted",
}


class MachineQualificationPanel(QWidget):
    """Render immutable qualification state without running Post or export."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Stage18AMachineQualificationPanel")
        self._report: QualificationReport | None = None
        self._contract: MachineQualificationContract | None = None
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(4)
        summary = QGroupBox(ui_text("Machine qualification"), self)
        summary.setObjectName("Stage18AMachineQualificationSummary")
        form = QFormLayout(summary)
        self.machine_value = QLabel(ui_text("No qualification report."), summary)
        self.controller_value = QLabel("—", summary)
        self.level_value = QLabel(ui_text("Unqualified"), summary)
        self.ready_value = QLabel("false", summary)
        self.blocker_value = QLabel("0", summary)
        self.warning_value = QLabel("0", summary)
        for label, widget in (
            ("Machine", self.machine_value),
            ("Controller", self.controller_value),
            ("Qualification level", self.level_value),
            ("MACHINE_READY", self.ready_value),
            ("Blocking errors", self.blocker_value),
            ("Unverified physical items", self.warning_value),
        ):
            form.addRow(ui_text(label), widget)
        root.addWidget(summary)

        self.advanced = QGroupBox(ui_text("Advanced qualification details"), self)
        self.advanced.setObjectName("Stage18AMachineQualificationAdvanced")
        self.advanced.setCheckable(True)
        self.advanced.setChecked(False)
        advanced_form = QFormLayout(self.advanced)
        self.profile_fingerprint = QLabel("—", self.advanced)
        self.profile_fingerprint.setTextInteractionFlags(
            self.profile_fingerprint.textInteractionFlags()
        )
        self.nc_sha = QLabel("—", self.advanced)
        self.work_offset = QLabel("G54 — UNVERIFIED", self.advanced)
        self.offsets = QLabel("OFFSET_NAMESPACE_UNVERIFIED", self.advanced)
        self.tapping = QLabel("TAPPING_MACHINE_READY_OUTPUT_NOT_QUALIFIED", self.advanced)
        self.travel = QLabel("PHYSICAL_TRAVEL_NOT_FULLY_VERIFIED", self.advanced)
        self.safety = QLabel("PHYSICAL_SAFE_POSITION_UNVERIFIED", self.advanced)
        for widget in (
            self.profile_fingerprint, self.nc_sha, self.work_offset,
            self.offsets, self.tapping, self.travel, self.safety,
        ):
            widget.setWordWrap(True)
        for label, widget in (
            ("Machine profile fingerprint", self.profile_fingerprint),
            ("NC SHA-256", self.nc_sha),
            ("Work offset status", self.work_offset),
            ("Offset namespace status", self.offsets),
            ("Tapping status", self.tapping),
            ("Physical travel status", self.travel),
            ("Safety status", self.safety),
        ):
            advanced_form.addRow(ui_text(label), widget)
        root.addWidget(self.advanced)
        self.advanced.toggled.connect(self._advanced_toggled)
        self._advanced_toggled(False)
        localize_widget_tree(self)
        translation_service().language_changed.connect(self.retranslate_ui)

    @property
    def report(self) -> QualificationReport | None:
        return self._report

    def clear_report(self) -> None:
        """Reset presentation state on project/profile switch."""

        self._report = None
        self._contract = None
        self.machine_value.setText(ui_text("No qualification report."))
        self.controller_value.setText("—")
        self.level_value.setText(ui_text("Unqualified"))
        self.ready_value.setText("false")
        self.ready_value.setProperty("qualificationReady", False)
        self.blocker_value.setText("0")
        self.warning_value.setText("0")
        self.profile_fingerprint.setText("—")
        self.nc_sha.setText("—")

    def set_report(
        self,
        report: QualificationReport,
        contract: MachineQualificationContract,
    ) -> None:
        """Project one typed report; never promote qualification in the widget."""

        if not isinstance(report, QualificationReport) or not isinstance(
            contract, MachineQualificationContract
        ):
            raise TypeError("Machine qualification report/contract is invalid")
        if report.machine_contract_fingerprint != contract.fingerprint:
            raise ValueError("Machine qualification report is stale for this profile")
        self._report = report
        self._contract = contract
        errors = sum(item.severity is FindingSeverity.ERROR for item in report.findings)
        warnings = sum(item.severity is FindingSeverity.WARNING for item in report.findings)
        codes = {item.code for item in report.findings}
        self.machine_value.setText(contract.display_name)
        self.controller_value.setText("FANUC 31i-B")
        self.level_value.setText(ui_text(_LEVEL_TEXT[report.qualification_level]))
        self.ready_value.setText("true" if report.machine_ready else "false")
        self.ready_value.setProperty("qualificationReady", report.machine_ready)
        self.blocker_value.setText(str(errors))
        self.warning_value.setText(str(warnings))
        self.profile_fingerprint.setText(contract.fingerprint.digest)
        self.nc_sha.setText(report.nc_sha256)
        self.work_offset.setText(
            "G54 — PHYSICAL_G54_TRANSFORM_UNVERIFIED"
            if FindingCode.PHYSICAL_G54_TRANSFORM_UNVERIFIED in codes
            else "G54"
        )
        self.offsets.setText(
            "OFFSET_NAMESPACE_UNVERIFIED"
            if FindingCode.OFFSET_NAMESPACE_UNVERIFIED in codes
            else "TOOL_NUMBER_MAPPING_VALIDATED / H_MAPPING_STATICALLY_VALIDATED"
        )
        self.tapping.setText("TAPPING_MACHINE_READY_OUTPUT_NOT_QUALIFIED")
        self.travel.setText(
            "PHYSICAL_TRAVEL_NOT_FULLY_VERIFIED"
            if FindingCode.PHYSICAL_TRAVEL_NOT_FULLY_VERIFIED in codes
            else "STATIC_AXIS_SPAN_VALIDATED"
        )
        self.safety.setText(
            "PHYSICAL_SAFE_POSITION_UNVERIFIED"
            if FindingCode.PHYSICAL_SAFE_POSITION_UNVERIFIED in codes
            else "POST_SEQUENCE_VALID"
        )

    def _advanced_toggled(self, visible: bool) -> None:
        for index in range(self.advanced.layout().count()):
            item = self.advanced.layout().itemAt(index)
            if item.widget() is not None:
                item.widget().setVisible(visible)

    def retranslate_ui(self, _language: object = None) -> None:
        """Retranslate canonical source keys without changing report state."""

        localize_widget_tree(self)


__all__ = ["MachineQualificationPanel"]
