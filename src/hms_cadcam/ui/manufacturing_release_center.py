"""Vietnamese-first job-level Release Center projection for Stage18A Tranche4."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from hms_cadcam.cam.qualification.manufacturing_job import ManufacturingJob, ManufacturingJobState
from hms_cadcam.cam.qualification.manufacturing_release import JobReleaseAssessment


class ManufacturingReleaseCenter(QWidget):
    """Presentation-only job aggregate; services own all release decisions."""

    validate_requested = Signal()
    compare_requested = Signal()
    approve_requested = Signal()
    export_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Stage18ATranche4ManufacturingReleaseCenter")
        root = QVBoxLayout(self)
        self.title = QLabel("Quản lý phát hành sản xuất", self)
        root.addWidget(self.title)
        summary = QGroupBox("Công việc", self)
        form = QFormLayout(summary)
        self.job_id = QLabel("—", summary)
        self.part_revision = QLabel("—", summary)
        self.machine = QLabel("—", summary)
        self.status = QLabel("Chưa chuẩn bị", summary)
        self.risk = QLabel("0 chương trình · 0 thiết lập · 0 dao · 0 lỗi chặn", summary)
        form.addRow("Mã công việc", self.job_id)
        form.addRow("Chi tiết / phiên bản", self.part_revision)
        form.addRow("Máy", self.machine)
        form.addRow("Trạng thái", self.status)
        form.addRow("Rủi ro", self.risk)
        root.addWidget(summary)
        filters = QHBoxLayout()
        self.filter = QComboBox(self)
        self.filter.addItems(["Tất cả", "Chương trình", "Thiết lập", "Dao", "Cảnh báo", "Lỗi chặn", "Phiên bản cũ"])
        self.search = QLineEdit(self)
        self.search.setPlaceholderText("Tìm chương trình / thiết lập / dao / phiên bản")
        filters.addWidget(self.filter)
        filters.addWidget(self.search)
        root.addLayout(filters)
        self.programs = QTableWidget(0, 5, self)
        self.programs.setHorizontalHeaderLabels(["Chương trình NC", "Phiên bản", "Thiết lập / G54", "Dao", "Trạng thái"])
        root.addWidget(self.programs)
        actions = QHBoxLayout()
        self.validate_button = QPushButton("Kiểm tra toàn bộ", self)
        self.compare_button = QPushButton("So sánh phiên bản", self)
        self.approve_button = QPushButton("Xác nhận phát hành", self)
        self.export_button = QPushButton("Xuất gói sản xuất", self)
        for button in (self.validate_button, self.compare_button, self.approve_button, self.export_button):
            actions.addWidget(button)
        root.addLayout(actions)
        self.validate_button.clicked.connect(self.validate_requested)
        self.compare_button.clicked.connect(self.compare_requested)
        self.approve_button.clicked.connect(self.approve_requested)
        self.export_button.clicked.connect(self.export_requested)
        self.clear_job()

    def clear_job(self) -> None:
        self.job_id.setText("—")
        self.part_revision.setText("—")
        self.machine.setText("—")
        self.status.setText("Chưa chuẩn bị")
        self.risk.setText("0 chương trình · 0 thiết lập · 0 dao · 0 lỗi chặn")
        self.programs.setRowCount(0)
        self.approve_button.setEnabled(False)
        self.export_button.setEnabled(False)

    def set_job(self, job: ManufacturingJob, assessment: JobReleaseAssessment) -> None:
        self.job_id.setText(job.job_id)
        self.part_revision.setText(f"{job.part_id} / {job.part_revision}")
        self.machine.setText(f"{job.machine_profile_id} · Chưa nghiệm thu trên máy")
        status_text = {
            ManufacturingJobState.BLOCKED: "Có lỗi chặn",
            ManufacturingJobState.READY_FOR_RELEASE_REVIEW: "Sẵn sàng duyệt phát hành",
        }.get(assessment.state, assessment.state.value)
        self.status.setText(status_text)
        self.risk.setText(f"{len(job.programs)} chương trình · {len(job.setups)} thiết lập · {len(job.tools)} dao · {len(assessment.blockers)} lỗi chặn")
        self.programs.setRowCount(len(job.programs))
        for row, program in enumerate(job.programs):
            values = (program.program_id, str(program.release_revision), f"{program.setup_id} / {program.g54_identity}",
                      ", ".join(f"T{v}" for v in program.tool_numbers), program.qualification_state.value)
            for column, value in enumerate(values):
                self.programs.setItem(row, column, QTableWidgetItem(value))
        self.approve_button.setEnabled(assessment.passed)
        self.export_button.setEnabled(assessment.passed)


__all__ = ["ManufacturingReleaseCenter"]
