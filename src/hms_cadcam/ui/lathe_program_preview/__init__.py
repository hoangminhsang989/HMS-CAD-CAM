"""Read-only Lathe Program Preview UI for the Stage 12.4A neutral IR."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, Signal, Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QListWidget, QPushButton, QTextEdit, QVBoxLayout, QWidget

from hms_cadcam.cam.lathe.lathe_post import LatheProgramService, LatheProgramSnapshot


def _label(key: str, fallback: str) -> str:
    try:
        from hms_cadcam.ui.i18n import translation_service
        value = translation_service().translate_key(key)
        return fallback if not value or value == key else value
    except (ImportError, RuntimeError, AttributeError):
        return fallback


class LatheProgramPreviewPanel(QWidget):
    """Compact read-only panel with a persistent non-machine-ready warning."""

    closed = Signal()
    refresh_requested = Signal()

    def __init__(self, service: LatheProgramService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        if not isinstance(service, LatheProgramService):
            raise TypeError("service must be LatheProgramService")
        self.setObjectName("LatheProgramPreviewPanel")
        self.setAccessibleName(_label("lathe.program.preview.title", "Program Preview"))
        self._service = service
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)
        self.identity_label = QLabel()
        self.identity_label.setObjectName("LatheProgramIdentityStatus")
        self.profile_label = QLabel()
        self.profile_label.setObjectName("LatheProgramNeutralProfile")
        root.addWidget(self.identity_label)
        root.addWidget(self.profile_label)
        self.operation_summary = QListWidget()
        self.operation_summary.setObjectName("LatheProgramOperationSummary")
        self.operation_summary.setAccessibleName(_label("lathe.program.operation_summary", "Operation summary"))
        self.operation_summary.setMaximumHeight(110)
        root.addWidget(self.operation_summary)
        self.diagnostics = QListWidget()
        self.diagnostics.setObjectName("LatheProgramDiagnostics")
        self.diagnostics.setAccessibleName(_label("lathe.program.validation", "Program validation"))
        self.diagnostics.setMaximumHeight(90)
        root.addWidget(self.diagnostics)
        self.listing = QTextEdit()
        self.listing.setObjectName("LatheProgramNeutralListing")
        self.listing.setReadOnly(True)
        self.listing.setAcceptRichText(False)
        root.addWidget(self.listing, 1)
        self.warning_footer = QLabel()
        self.warning_footer.setObjectName("LatheProgramWarningFooter")
        self.warning_footer.setWordWrap(True)
        self.warning_footer.setStyleSheet("font-weight: 600; color: #9a2f19;")
        root.addWidget(self.warning_footer)
        row = QHBoxLayout()
        row.addStretch(1)
        self.refresh_button = QPushButton(_label("lathe.program.refresh", "Refresh"))
        self.refresh_button.setObjectName("LatheProgramRefresh")
        self.refresh_button.clicked.connect(self.refresh_requested)
        self.close_button = QPushButton(_label("lathe.program.close", "Close"))
        self.close_button.setObjectName("LatheProgramClose")
        self.close_button.clicked.connect(self.close)
        row.addWidget(self.refresh_button)
        row.addWidget(self.close_button)
        root.addLayout(row)
        self._render(None)

    @property
    def service(self) -> LatheProgramService:
        return self._service

    def show_snapshot(self, snapshot: LatheProgramSnapshot | None = None) -> None:
        self._render(snapshot if snapshot is not None else self._service.latest)

    def closeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        self.closed.emit()
        super().closeEvent(event)

    def _render(self, snapshot: LatheProgramSnapshot | None) -> None:
        if snapshot is None:
            self.identity_label.setText(_label("lathe.program.incomplete", "Program Preview — INCOMPLETE"))
            self.profile_label.setText(_label("lathe.program.production_unavailable", "Production Post unavailable"))
            self.listing.clear()
            self.operation_summary.clear()
            self.diagnostics.clear()
        else:
            self.identity_label.setText(f"{_label('lathe.program.preview.title', 'Program Preview')} · {snapshot.identity.program_id} · {snapshot.readiness.value}")
            self.profile_label.setText(f"{_label('lathe.program.neutral_profile', 'Neutral preview profile')}: {snapshot.program.profile_id}")
            self.listing.setPlainText(snapshot.listing)
            self.operation_summary.clear()
            for block in snapshot.program.blocks:
                if block.kind.value == "OPERATION_BEGIN":
                    self.operation_summary.addItem(f"{block.payload.operation_id} · {block.payload.strategy_id}")
            self.diagnostics.clear()
            for diagnostic in snapshot.diagnostics:
                self.diagnostics.addItem(diagnostic.code)
        self.warning_footer.setText(_label("lathe.program.warning_footer", "PREVIEW ONLY — NOT MACHINE-READY — NO CONTROLLER POST PROFILE — DO NOT RUN ON A CNC MACHINE"))


class LatheProgramPreviewWindow(QDialog):
    """Owned dialog wrapper used when a dock panel is not available."""

    def __init__(self, panel: LatheProgramPreviewPanel, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("LatheProgramPreviewWindow")
        self.setWindowTitle(_label("lathe.program.preview.title", "Program Preview"))
        self.setMinimumSize(560, 420)
        layout = QVBoxLayout(self)
        layout.addWidget(panel)


class LatheProgramPreviewController(QObject):
    """One-context singleton owner for the optional preview panel."""

    panel_created = Signal(object)

    def __init__(self, service: LatheProgramService, assemble_callback: Callable[[], object] | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        if not isinstance(service, LatheProgramService):
            raise TypeError("service must be LatheProgramService")
        self.setObjectName("LatheProgramPreviewController")
        self._service = service
        self._assemble_callback = assemble_callback
        self._panel: LatheProgramPreviewPanel | None = None

    @property
    def panel(self) -> LatheProgramPreviewPanel | None:
        return self._panel

    def open(self, parent: QWidget | None = None) -> LatheProgramPreviewPanel:
        if self._panel is None:
            self._panel = LatheProgramPreviewPanel(self._service, parent)
            self._panel.refresh_requested.connect(self.refresh)
            self._panel.closed.connect(self._panel_closed)
            self.panel_created.emit(self._panel)
        self._panel.show()
        self._panel.raise_()
        self._panel.activateWindow()
        return self._panel

    def refresh(self) -> None:
        if self._assemble_callback is not None:
            self._assemble_callback()
        if self._panel is not None:
            self._panel.show_snapshot()

    def close(self) -> None:
        if self._panel is None:
            return
        panel = self._panel
        self._panel = None
        panel.close()
        panel.deleteLater()

    def teardown(self) -> None:
        self.close()

    def _panel_closed(self) -> None:
        self._panel = None


def create_lathe_program_preview_controller(enabled: bool, service: LatheProgramService, parent: QObject | None = None, assemble_callback: Callable[[], object] | None = None) -> LatheProgramPreviewController | None:
    """Create the optional UI boundary only when the feature is explicitly on."""

    if type(enabled) is not bool:
        raise TypeError("enabled must be bool")
    return LatheProgramPreviewController(service, assemble_callback, parent) if enabled else None


LatheProgramPreview = LatheProgramPreviewPanel

__all__ = [
    "LatheProgramPreview", "LatheProgramPreviewController", "LatheProgramPreviewPanel",
    "LatheProgramPreviewWindow", "create_lathe_program_preview_controller",
]
