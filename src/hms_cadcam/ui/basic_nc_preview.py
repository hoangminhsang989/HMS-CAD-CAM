"""Explicit UI boundary for the unverified basic Lathe NC preview."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from hms_cadcam.cam.lathe.lathe_post import (
    BasicPostReadiness,
    LatheBasicNcService,
    LatheNcConformanceReport,
)


def _label(key: str, fallback: str) -> str:
    try:
        from hms_cadcam.ui.i18n import translation_service
        value = translation_service().translate_key(key)
        return fallback if not value or value == key else value
    except (ImportError, RuntimeError, AttributeError):
        return fallback


class BasicNcExportAcknowledgementDialog(QDialog):
    """Unchecked-per-session safety acknowledgement required before export."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(_label("lathe.basic_post.export_ack.title", "Confirm unverified NC export"))
        self.setAccessibleName(_label("lathe.basic_post.export_ack.title", "Confirm unverified NC export"))
        layout = QVBoxLayout(self)
        message = QLabel(_label("lathe.basic_post.export_ack.message", "This basic Lathe Post is unverified. Check the full program before machine use."))
        message.setWordWrap(True)
        layout.addWidget(message)
        self.checkbox = QCheckBox(_label("lathe.basic_post.export_ack.checkbox", "I understand this output is unverified and must be checked before CNC use."))
        self.checkbox.setChecked(False)
        self.checkbox.setAccessibleName(self.checkbox.text())
        layout.addWidget(self.checkbox)
        row = QHBoxLayout()
        row.addStretch(1)
        self.cancel_button = QPushButton(_label("lathe.program.close", "Close"))
        self.export_button = QPushButton(_label("lathe.basic_post.export", "Export .NC"))
        self.export_button.setEnabled(False)
        self.export_button.setDefault(True)
        self.cancel_button.clicked.connect(self.reject)
        self.export_button.clicked.connect(self.accept)
        self.checkbox.toggled.connect(self.export_button.setEnabled)
        row.addWidget(self.cancel_button)
        row.addWidget(self.export_button)
        layout.addLayout(row)


class BasicNcPreviewPanel(QWidget):
    """Read-only NC preview with explicit Generate and Export actions."""

    closed = Signal()
    generate_requested = Signal()

    def __init__(self, service: LatheBasicNcService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        if not isinstance(service, LatheBasicNcService):
            raise TypeError("service must be LatheBasicNcService")
        self.service = service
        self.setObjectName("LatheBasicNcPreviewPanel")
        self.setAccessibleName(_label("lathe.basic_post.preview.title", "Basic NC Preview"))
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        profile_row = QHBoxLayout()
        profile_row.addWidget(QLabel(_label("lathe.basic_post.profile", "Post profile")))
        self.profile_field = QLineEdit(service.profile.profile_id)
        self.profile_field.setReadOnly(True)
        self.profile_field.setAccessibleName(_label("lathe.basic_post.profile", "Post profile"))
        profile_row.addWidget(self.profile_field, 1)
        self.badge = QLabel(_label("lathe.basic_post.unverified", "UNVERIFIED OUTPUT — NOT MACHINE-READY"))
        self.badge.setStyleSheet("font-weight: 700; color: #9a2f19;")
        profile_row.addWidget(self.badge)
        root.addLayout(profile_row)
        info_row = QHBoxLayout()
        self.sha_label = QLabel(_label("lathe.basic_post.output_sha", "Output SHA: —"))
        self.filename_label = QLabel(_label("lathe.basic_post.filename", "Suggested filename: —"))
        info_row.addWidget(self.sha_label)
        info_row.addWidget(self.filename_label, 1)
        root.addLayout(info_row)
        self.diagnostics = QListWidget()
        self.diagnostics.setAccessibleName(_label("lathe.program.validation", "Program validation"))
        self.diagnostics.setMaximumHeight(90)
        root.addWidget(self.diagnostics)
        self.conformance_group = QGroupBox(
            _label("lathe.basic_post.conformance.title", "Sample Conformance Review")
        )
        conformance_layout = QVBoxLayout(self.conformance_group)
        conformance_meta = QHBoxLayout()
        self.conformance_revision = QLabel()
        self.conformance_status = QLabel()
        self.conformance_status.setWordWrap(True)
        conformance_meta.addWidget(self.conformance_revision)
        conformance_meta.addWidget(self.conformance_status, 1)
        conformance_layout.addLayout(conformance_meta)
        self.conformance_coverage = QLabel()
        self.conformance_coverage.setWordWrap(True)
        conformance_layout.addWidget(self.conformance_coverage)
        self.conformance_findings = QListWidget()
        self.conformance_findings.setObjectName("LatheBasicNcConformanceFindings")
        self.conformance_findings.setAccessibleName(
            _label("lathe.basic_post.conformance.mandatory", "Mandatory findings")
        )
        self.conformance_findings.setMaximumHeight(105)
        conformance_layout.addWidget(self.conformance_findings)
        self.conformance_review_button = QPushButton(
            _label("lathe.basic_post.conformance.run", "Run Conformance Review")
        )
        self.conformance_review_button.setObjectName("LatheBasicNcConformanceReviewAction")
        self.conformance_review_button.setAccessibleName(
            self.conformance_review_button.text()
        )
        self.conformance_review_button.setEnabled(False)
        self.conformance_review_button.clicked.connect(self._run_conformance_review)
        conformance_layout.addWidget(self.conformance_review_button)
        root.addWidget(self.conformance_group)
        self._retranslate_conformance()
        self.listing = QTextEdit()
        self.listing.setReadOnly(True)
        self.listing.setAcceptRichText(False)
        self.listing.setObjectName("LatheBasicNcListing")
        root.addWidget(self.listing, 1)
        self.status = QLabel(_label("lathe.basic_post.incomplete", "Basic NC preview incomplete"))
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        row = QHBoxLayout()
        row.addStretch(1)
        self.generate_button = QPushButton(_label("lathe.basic_post.generate", "Generate Basic NC Preview"))
        self.export_button = QPushButton(_label("lathe.basic_post.export", "Export .NC"))
        self.export_button.setEnabled(False)
        self.close_button = QPushButton(_label("lathe.program.close", "Close"))
        self.generate_button.setAccessibleName(self.generate_button.text())
        self.export_button.setAccessibleName(self.export_button.text())
        self.generate_button.clicked.connect(self.generate_requested)
        self.export_button.clicked.connect(self._interactive_export)
        self.close_button.clicked.connect(self.close)
        row.addWidget(self.generate_button)
        row.addWidget(self.export_button)
        row.addWidget(self.close_button)
        root.addLayout(row)
        self.setTabOrder(self.generate_button, self.conformance_review_button)
        self.setTabOrder(self.conformance_review_button, self.export_button)
        self.setTabOrder(self.export_button, self.close_button)
        try:
            from hms_cadcam.ui.i18n import translation_service
            translation_service().language_changed.connect(self._retranslate_conformance)
        except (ImportError, RuntimeError, AttributeError):
            pass

    def _retranslate_conformance(self, _language: object = None) -> None:
        self.conformance_group.setTitle(
            _label("lathe.basic_post.conformance.title", "Sample Conformance Review")
        )
        self.conformance_review_button.setText(
            _label("lathe.basic_post.conformance.run", "Run Conformance Review")
        )
        self.conformance_review_button.setAccessibleName(
            self.conformance_review_button.text()
        )
        self.conformance_findings.setAccessibleName(
            _label("lathe.basic_post.conformance.mandatory", "Mandatory findings")
        )
        report = self.service.state.conformance_report
        if report is None:
            self.conformance_revision.setText(
                f"{_label('lathe.basic_post.conformance.behavior_revision', 'Behavior revision')}: "
                f"{self.service.profile.sample_contract_revision}"
            )
            self.conformance_status.setText(
                f"{_label('lathe.basic_post.conformance.external_unavailable', 'External sample unavailable')} ? "
                f"{_label('lathe.basic_post.conformance.structural_only', 'Structural review only')} ? "
                f"{_label('lathe.basic_post.conformance.not_machine_verification', 'Not machine verification')}"
            )
            self.conformance_coverage.setText(
                f"{_label('lathe.basic_post.conformance.strategy_coverage', 'Strategy coverage')}: "
                "11/11 generated"
            )
        else:
            self._show_conformance_report(report)

    def _show_conformance_report(
        self, report: LatheNcConformanceReport
    ) -> None:
        self.conformance_revision.setText(
            f"{_label('lathe.basic_post.conformance.behavior_revision', 'Behavior revision')}: "
            f"{report.behavior_revision}"
        )
        self.conformance_status.setText(
            f"{report.status.value} ? "
            f"{_label('lathe.basic_post.conformance.structural_only', 'Structural review only')} ? "
            f"{_label('lathe.basic_post.conformance.not_machine_verification', 'Not machine verification')}"
        )
        coverage_states = {state for _, state in report.strategy_coverage}
        if coverage_states == {"CONTRACT_DERIVED_NO_OWNER_SAMPLE_COVERAGE"}:
            owner_coverage = _label(
                "lathe.basic_post.conformance.contract_derived",
                "Contract-derived strategy",
            )
        else:
            owner_coverage = _label(
                "lathe.basic_post.conformance.sample_backed",
                "Sample-backed strategy",
            )
        self.conformance_coverage.setText(
            f"{_label('lathe.basic_post.conformance.strategy_coverage', 'Strategy coverage')}: "
            f"11/11 generated; {owner_coverage}; {report.external_sample_state}"
        )
        self.conformance_findings.clear()
        groups = (
            (
                _label("lathe.basic_post.conformance.mandatory", "Mandatory findings"),
                report.mandatory_findings,
            ),
            (
                _label("lathe.basic_post.conformance.safety", "Safety deviations"),
                report.intentional_safe_deviations,
            ),
            (
                _label(
                    "lathe.basic_post.conformance.unsupported",
                    "Unsupported sample features",
                ),
                report.unsupported_sample_features,
            ),
        )
        for heading, findings in groups:
            if not findings:
                self.conformance_findings.addItem(f"{heading}: ?")
            for finding in findings:
                self.conformance_findings.addItem(
                    f"{heading}: {finding.code} [{finding.severity.value}]"
                )

    def _run_conformance_review(self) -> None:
        if self.service.latest is None:
            return
        self._show_conformance_report(self.service.review_latest())

    def show_result(self) -> None:
        snapshot = self.service.latest
        self.diagnostics.clear()
        result = self.service.state.last_result
        for diagnostic in result.diagnostics if result is not None else ():
            self.diagnostics.addItem(diagnostic.code)
        if snapshot is None:
            self.listing.clear()
            self.sha_label.setText(_label("lathe.basic_post.output_sha", "Output SHA: —"))
            self.filename_label.setText(_label("lathe.basic_post.filename", "Suggested filename: —"))
            self.status.setText(_label("lathe.basic_post.incomplete", "Basic NC preview incomplete"))
            self.export_button.setEnabled(False)
            self.conformance_review_button.setEnabled(False)
            self.conformance_findings.clear()
            self._retranslate_conformance()
            return
        self.listing.setPlainText(snapshot.text)
        self.sha_label.setText(f"{_label('lathe.basic_post.output_sha', 'Output SHA')}: {snapshot.sha256}")
        self.filename_label.setText(f"{_label('lathe.basic_post.filename', 'Suggested filename')}: {snapshot.suggested_filename}")
        self.status.setText(snapshot.readiness.value)
        self.export_button.setEnabled(snapshot.readiness is BasicPostReadiness.BASIC_NC_PREVIEW_READY_UNVERIFIED)
        self.conformance_review_button.setEnabled(True)
        if self.service.state.conformance_report is not None:
            self._show_conformance_report(self.service.state.conformance_report)

    def _interactive_export(self) -> None:
        dialog = BasicNcExportAcknowledgementDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        destination, _ = QFileDialog.getSaveFileName(self, _label("lathe.basic_post.export", "Export .NC"), self.service.latest.suggested_filename if self.service.latest else "program.NC", "NC files (*.NC)")
        if not destination:
            return
        if Path(destination).exists() and QMessageBox.question(self, _label("lathe.basic_post.overwrite", "Confirm replacement"), _label("lathe.basic_post.overwrite.message", "Replace the existing .NC file?")) != QMessageBox.StandardButton.Yes:
            return
        result = self.service.export(destination, acknowledged_unverified=True, overwrite_confirmed=True)
        if result.success:
            self.status.setText(result.readiness.value)
            QMessageBox.information(self, _label("lathe.basic_post.export.success", "Export complete"), f"{result.destination}\nSHA-256: {result.sha256}")
        else:
            for diagnostic in result.diagnostics:
                self.diagnostics.addItem(diagnostic.code)
            QMessageBox.warning(self, _label("lathe.basic_post.export.failure", "Export failed"), "; ".join(item.code for item in result.diagnostics))

    def closeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        self.closed.emit()
        super().closeEvent(event)


class BasicNcPreviewController(QObject):
    panel_created = Signal(object)

    def __init__(self, service: LatheBasicNcService, program_provider: Callable[[], object | None] | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        if not isinstance(service, LatheBasicNcService):
            raise TypeError("service must be LatheBasicNcService")
        self.service = service
        self.program_provider = program_provider
        self._panel: BasicNcPreviewPanel | None = None

    @property
    def panel(self) -> BasicNcPreviewPanel | None:
        return self._panel

    def open(self, parent: QWidget | None = None) -> BasicNcPreviewPanel:
        if self._panel is None:
            self._panel = BasicNcPreviewPanel(self.service, parent)
            self._panel.generate_requested.connect(self.generate)
            self._panel.closed.connect(self._panel_closed)
            self.panel_created.emit(self._panel)
        self._panel.show()
        self._panel.raise_()
        self._panel.activateWindow()
        return self._panel

    def generate(self) -> None:
        program = self.program_provider() if self.program_provider is not None else None
        self.service.generate(program)
        if self._panel is not None:
            self._panel.show_result()

    def close(self) -> None:
        if self._panel is not None:
            panel = self._panel
            self._panel = None
            panel.close()
            panel.deleteLater()

    def _panel_closed(self) -> None:
        self._panel = None


__all__ = ["BasicNcExportAcknowledgementDialog", "BasicNcPreviewController", "BasicNcPreviewPanel"]