"""Localized job-level Release Center projection for Stage18A Tranche4."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from hms_cadcam.cam.qualification.manufacturing_job import ManufacturingJob, ManufacturingJobState
from hms_cadcam.cam.qualification.manufacturing_release import JobReleaseAssessment
from hms_cadcam.ui.i18n import translation_service


def _t(key: str) -> str:
    return translation_service().translate_key(f"stage18a.tranche4.{key}")


class ManufacturingReleaseCenter(QWidget):
    """Presentation-only aggregate; release services own every state change."""

    validate_requested = Signal()
    compare_requested = Signal()
    approve_requested = Signal()
    export_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Stage18ATranche4ManufacturingReleaseCenter")
        self._job: ManufacturingJob | None = None
        self._assessment: JobReleaseAssessment | None = None
        root = QVBoxLayout(self)
        self.title = QLabel(self)
        root.addWidget(self.title)
        self.summary = QGroupBox(self)
        form = QFormLayout(self.summary)
        self.job_id = QLabel("—", self.summary)
        self.part_revision = QLabel("—", self.summary)
        self.machine = QLabel("—", self.summary)
        self.status = QLabel(self.summary)
        self.risk = QLabel(self.summary)
        self._form = form
        root.addWidget(self.summary)
        filters = QHBoxLayout()
        self.filter = QComboBox(self)
        self.search = QLineEdit(self)
        filters.addWidget(self.filter)
        filters.addWidget(self.search)
        root.addLayout(filters)
        self.programs = QTableWidget(0, 5, self)
        root.addWidget(self.programs)
        actions = QHBoxLayout()
        self.validate_button = QPushButton(self)
        self.compare_button = QPushButton(self)
        self.approve_button = QPushButton(self)
        self.export_button = QPushButton(self)
        for button in (self.validate_button, self.compare_button, self.approve_button, self.export_button):
            actions.addWidget(button)
        root.addLayout(actions)
        self.validate_button.clicked.connect(self.validate_requested)
        self.compare_button.clicked.connect(self.compare_requested)
        self.approve_button.clicked.connect(self.approve_requested)
        self.export_button.clicked.connect(self.export_requested)
        translation_service().language_changed.connect(self.retranslate_ui)
        self.retranslate_ui()
        self.clear_job()

    def retranslate_ui(self, _language: object = None) -> None:
        self.title.setText(_t("title"))
        self.summary.setTitle(_t("job"))
        self._form.setWidget(0, QFormLayout.ItemRole.LabelRole, QLabel(_t("job"), self.summary))
        self._form.setWidget(0, QFormLayout.ItemRole.FieldRole, self.job_id)
        self._form.setWidget(1, QFormLayout.ItemRole.LabelRole, QLabel(_t("part_revision"), self.summary))
        self._form.setWidget(1, QFormLayout.ItemRole.FieldRole, self.part_revision)
        self._form.setWidget(2, QFormLayout.ItemRole.LabelRole, QLabel(_t("machine"), self.summary))
        self._form.setWidget(2, QFormLayout.ItemRole.FieldRole, self.machine)
        self._form.setWidget(3, QFormLayout.ItemRole.LabelRole, QLabel(_t("status"), self.summary))
        self._form.setWidget(3, QFormLayout.ItemRole.FieldRole, self.status)
        self._form.setWidget(4, QFormLayout.ItemRole.LabelRole, QLabel(_t("risk"), self.summary))
        self._form.setWidget(4, QFormLayout.ItemRole.FieldRole, self.risk)
        current = self.filter.currentIndex()
        self.filter.clear()
        self.filter.addItems([_t(key) for key in ("all", "programs", "setups", "tools", "warnings", "blockers", "old_revision")])
        self.filter.setCurrentIndex(max(0, current))
        self.search.setPlaceholderText(_t("search"))
        self.programs.setHorizontalHeaderLabels([_t("programs"), "NC", _t("setups"), _t("tools"), _t("status")])
        self.validate_button.setText(_t("validate"))
        self.compare_button.setText(_t("compare"))
        self.approve_button.setText(_t("approve"))
        self.export_button.setText(_t("export"))
        if self._job is not None and self._assessment is not None:
            self._render_job()
        else:
            self.status.setText(_t("not_prepared"))
            self.risk.setText(self._risk_text(0, 0, 0, 0))

    def _risk_text(self, programs: int, setups: int, tools: int, blockers: int) -> str:
        return f"{programs} {_t('programs').casefold()} · {setups} {_t('setups').casefold()} · {tools} {_t('tools').casefold()} · {blockers} {_t('blockers').casefold()}"

    def clear_job(self) -> None:
        self._job = None
        self._assessment = None
        self.job_id.setText("—")
        self.part_revision.setText("—")
        self.machine.setText("—")
        self.status.setText(_t("not_prepared"))
        self.risk.setText(self._risk_text(0, 0, 0, 0))
        self.programs.setRowCount(0)
        self.approve_button.setEnabled(False)
        self.export_button.setEnabled(False)

    def set_job(self, job: ManufacturingJob, assessment: JobReleaseAssessment) -> None:
        self._job = job
        self._assessment = assessment
        self._render_job()

    def _render_job(self) -> None:
        assert self._job is not None and self._assessment is not None
        job, assessment = self._job, self._assessment
        self.job_id.setText(job.job_id)
        self.part_revision.setText(f"{job.part_id} / {job.part_revision}")
        self.machine.setText(f"{job.machine_profile_id} · {_t('not_machine_accepted')}")
        status_key = {
            ManufacturingJobState.BLOCKED: "blocked",
            ManufacturingJobState.READY_FOR_RELEASE_REVIEW: "ready_review",
        }.get(assessment.state)
        self.status.setText(_t(status_key) if status_key else assessment.state.value)
        self.risk.setText(self._risk_text(len(job.programs), len(job.setups), len(job.tools), len(assessment.blockers)))
        self.programs.setRowCount(len(job.programs))
        for row, program in enumerate(job.programs):
            values = (program.program_id, str(program.release_revision), f"{program.setup_id} / {program.g54_identity}",
                      ", ".join(f"T{value}" for value in program.tool_numbers), program.qualification_state.value)
            for column, value in enumerate(values):
                self.programs.setItem(row, column, QTableWidgetItem(value))
        self.approve_button.setEnabled(assessment.passed)
        self.export_button.setEnabled(assessment.passed)


__all__ = ["ManufacturingReleaseCenter"]
