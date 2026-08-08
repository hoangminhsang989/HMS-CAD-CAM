"""Compact request-owned status surface for cooperative 3D export."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from PySide6.QtCore import QEvent, QSize, QTimer, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QStyle,
    QWidget,
)

from hms_cadcam.cad.export_models import ExportFormatId
from hms_cadcam.ui.i18n import translation_service


def _tr(source: str) -> str:
    return translation_service().translate(source)


class ExportOperationState(StrEnum):
    """Presentation lifecycle; publication truth remains in ExportResult."""

    ACTIVE = "active"
    CANCELLING = "cancelling"
    COMMITTING = "committing"
    SUCCEEDED = "succeeded"
    CANCELLED = "cancelled"
    FAILED = "failed"
    ABANDONED = "abandoned"


@dataclass(frozen=True, slots=True)
class ExportOperationEvent:
    request_id: int
    state: ExportOperationState
    format_id: ExportFormatId

    def __post_init__(self) -> None:
        if self.request_id <= 0:
            raise ValueError("Export operation request ID must be positive")


class CadExportStatusSurface(QWidget):
    """Render only the current export request without modal waiting."""

    geometry_requirement_changed = Signal(int)

    def __init__(
        self,
        cancel_request: Callable[[], bool],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._unbounded_maximum_height = self.maximumHeight()
        self._cancel_request = cancel_request
        self._latest_request_id = 0
        self._active_request_id: int | None = None
        self._last_event: ExportOperationEvent | None = None
        self._required_width = 0
        self._geometry_refresh_timer = QTimer(self)
        self._geometry_refresh_timer.setSingleShot(True)
        self._geometry_refresh_timer.timeout.connect(self._refresh_geometry)

        self.setObjectName("CadExportStatusSurface")
        self.setSizePolicy(
            QSizePolicy.Policy.MinimumExpanding,
            QSizePolicy.Policy.Fixed,
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 0, 2, 0)
        layout.setSpacing(4)

        self.icon_label = QLabel(self)
        self.icon_label.setObjectName("CadExportStatusIcon")
        self.icon_label.setFixedSize(QSize(16, 16))
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.icon_label)

        self.status_label = QLabel(self)
        self.status_label.setObjectName("CadExportStatusLabel")
        self.status_label.setSizePolicy(
            QSizePolicy.Policy.MinimumExpanding,
            QSizePolicy.Policy.Fixed,
        )
        layout.addWidget(self.status_label)

        self.progress_bar = QProgressBar(self)
        self.progress_bar.setObjectName("CadExportIndeterminateProgress")
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(12)
        self.progress_bar.setMinimumWidth(48)
        self.progress_bar.setMaximumWidth(72)
        layout.addWidget(self.progress_bar)

        self.cancel_button = QPushButton(self)
        self.cancel_button.setObjectName("CadExportCancelButton")
        self.cancel_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.cancel_button.setMinimumHeight(20)
        self.cancel_button.setSizePolicy(
            QSizePolicy.Policy.Minimum,
            QSizePolicy.Policy.Fixed,
        )
        self.cancel_button.clicked.connect(self._cancel_clicked)
        layout.addWidget(self.cancel_button)

        translation_service().language_changed.connect(self.retranslate_ui)
        self.retranslate_ui()
        self._set_icon(QStyle.StandardPixmap.SP_BrowserReload)
        self._refresh_geometry()
        self.hide()

    @property
    def active_request_id(self) -> int | None:
        return self._active_request_id

    @property
    def last_event(self) -> ExportOperationEvent | None:
        return self._last_event

    @property
    def required_width(self) -> int:
        """Return the content-derived width required by the current state."""

        return self._required_width

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802 - Qt API name
        super().changeEvent(event)
        if (
            event.type() in {QEvent.Type.FontChange, QEvent.Type.StyleChange}
            and hasattr(self, "cancel_button")
        ):
            self._refresh_geometry()
            self._geometry_refresh_timer.start(0)

    @Slot(object)
    def handle_export_event(self, event: object) -> None:
        """Apply an event only while its request owns this surface."""

        if not isinstance(event, ExportOperationEvent):
            return
        if event.state is ExportOperationState.ACTIVE:
            if event.request_id <= self._latest_request_id:
                return
            self._latest_request_id = event.request_id
            self._active_request_id = event.request_id
            self._last_event = event
            self.show()
            self.progress_bar.show()
            self.cancel_button.setEnabled(True)
            self.cancel_button.show()
            self._set_icon(QStyle.StandardPixmap.SP_BrowserReload)
            self.retranslate_ui()
            return

        if event.request_id != self._active_request_id:
            return
        if event.state is ExportOperationState.ABANDONED:
            self._active_request_id = None
            self._last_event = None
            self.hide()
            self._refresh_geometry()
            return

        self._last_event = event
        if event.state is ExportOperationState.CANCELLING:
            self.cancel_button.setEnabled(False)
            self.cancel_button.show()
        elif event.state is ExportOperationState.COMMITTING:
            self.cancel_button.setEnabled(False)
            self.cancel_button.hide()
        else:
            self._active_request_id = None
            self.progress_bar.hide()
            self.cancel_button.setEnabled(False)
            self.cancel_button.hide()
            if event.state is ExportOperationState.SUCCEEDED:
                self._set_icon(QStyle.StandardPixmap.SP_DialogApplyButton)
            elif event.state is ExportOperationState.CANCELLED:
                self._set_icon(QStyle.StandardPixmap.SP_DialogCancelButton)
            else:
                self._set_icon(QStyle.StandardPixmap.SP_MessageBoxCritical)
        self.retranslate_ui()

    def reset_for_shutdown(self) -> None:
        self._active_request_id = None
        self._last_event = None
        self.progress_bar.hide()
        self.cancel_button.setEnabled(False)
        self.cancel_button.hide()
        self.hide()
        self._refresh_geometry()

    def retranslate_ui(self, _language: object = None) -> None:
        """Retranslate without changing request identity or cancellation state."""

        self.status_label.setAccessibleName(_tr("3D export operation status"))
        self.progress_bar.setAccessibleName(_tr("3D export activity"))
        self.cancel_button.setText(_tr("Cancel"))
        self.cancel_button.setAccessibleName(_tr("Cancel 3D export"))
        self.cancel_button.setToolTip(_tr("Cancel 3D export"))
        event = self._last_event
        if event is not None:
            text = {
                ExportOperationState.ACTIVE: _tr("Exporting 3D data…"),
                ExportOperationState.CANCELLING: _tr("Cancelling 3D export…"),
                ExportOperationState.COMMITTING: _tr(
                    "Cannot cancel because the file is being finalized"
                ),
                ExportOperationState.SUCCEEDED: _tr("3D export completed"),
                ExportOperationState.CANCELLED: _tr("3D export cancelled"),
                ExportOperationState.FAILED: _tr("3D export failed"),
                ExportOperationState.ABANDONED: "",
            }[event.state]
            self.status_label.setText(
                f"{event.format_id.value.upper()} — {text}" if text else ""
            )
        self._refresh_geometry()

    @Slot()
    def _cancel_clicked(self) -> None:
        if self._active_request_id is None or not self.cancel_button.isEnabled():
            return
        self.cancel_button.setEnabled(False)
        self._cancel_request()

    def _refresh_geometry(self) -> None:
        has_status = self._last_event is not None
        self.setMinimumHeight(0)
        self.setMaximumHeight(self._unbounded_maximum_height)
        self.status_label.setMinimumWidth(0)
        self.status_label.ensurePolished()
        if has_status:
            self.status_label.setMinimumWidth(
                self.status_label.sizeHint().width()
            )
        self.cancel_button.setMinimumWidth(0)
        self.cancel_button.ensurePolished()
        if not self.cancel_button.isHidden():
            self.cancel_button.setMinimumWidth(
                self.cancel_button.sizeHint().width()
            )
        layout = self.layout()
        if layout is not None:
            layout.invalidate()
            layout.activate()
            required_width = (
                layout.minimumSize().width() if has_status else 0
            )
            self._required_width = required_width
            self.setMinimumWidth(required_width)
            self.setFixedHeight(
                max(layout.minimumSize().height(), layout.sizeHint().height())
            )
        else:
            self._required_width = 0
            self.setMinimumWidth(0)
        self.cancel_button.updateGeometry()
        self.status_label.updateGeometry()
        self.progress_bar.updateGeometry()
        self.updateGeometry()
        self.geometry_requirement_changed.emit(self._required_width)

    def _set_icon(self, pixmap: QStyle.StandardPixmap) -> None:
        icon = self.style().standardIcon(pixmap)
        self.icon_label.setPixmap(icon.pixmap(QSize(16, 16)))


__all__ = [
    "CadExportStatusSurface",
    "ExportOperationEvent",
    "ExportOperationState",
]
