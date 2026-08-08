"""Compact, request-owned CAD loading status surface for the main window."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEvent, QSize, QTimer, Qt, Slot
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QStyle,
    QSizePolicy,
    QWidget,
)

from hms_cadcam.ui.cad_loading import CadLoadEvent, CadLoadState
from hms_cadcam.ui.i18n import translation_service
from hms_cadcam.ui.localization import ui_text


class CadLoadingStatusSurface(QWidget):
    """Show request-owned CAD loading state without covering the viewport."""

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
        self._last_event: CadLoadEvent | None = None
        self._geometry_refresh_timer = QTimer(self)
        self._geometry_refresh_timer.setSingleShot(True)
        self._geometry_refresh_timer.timeout.connect(self._refresh_geometry)

        self.setObjectName("CadLoadingStatusSurface")
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 0, 2, 0)
        layout.setSpacing(4)

        self.icon_label = QLabel(self)
        self.icon_label.setObjectName("CadLoadingStatusIcon")
        self.icon_label.setFixedSize(QSize(16, 16))
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.icon_label)

        self.status_label = QLabel(self)
        self.status_label.setObjectName("CadLoadingStatusLabel")
        self.status_label.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Fixed,
        )
        self.status_label.setMinimumWidth(86)
        layout.addWidget(self.status_label)

        self.progress_bar = QProgressBar(self)
        self.progress_bar.setObjectName("CadLoadingIndeterminateProgress")
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(12)
        self.progress_bar.setMinimumWidth(48)
        self.progress_bar.setMaximumWidth(72)
        self.progress_bar.hide()
        layout.addWidget(self.progress_bar)

        self.cancel_button = QPushButton(self)
        self.cancel_button.setObjectName("CadLoadingCancelButton")
        self.cancel_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.cancel_button.setMinimumHeight(20)
        self.cancel_button.setSizePolicy(
            QSizePolicy.Policy.Minimum,
            QSizePolicy.Policy.Fixed,
        )
        self.cancel_button.clicked.connect(self._cancel_clicked)
        self.cancel_button.hide()
        layout.addWidget(self.cancel_button)

        translation_service().language_changed.connect(self.retranslate_ui)
        self.retranslate_ui()
        self._set_icon(QStyle.StandardPixmap.SP_ComputerIcon)
        self._refresh_geometry()

    @property
    def active_request_id(self) -> int | None:
        """Return the request currently allowed to update this surface."""

        return self._active_request_id

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802 - Qt API name
        """Refresh content-derived bounds after runtime font/style changes."""
        super().changeEvent(event)
        if (
            event.type()
            in {
                QEvent.Type.FontChange,
                QEvent.Type.StyleChange,
            }
            and hasattr(self, "cancel_button")
        ):
            self._refresh_geometry()
            self._geometry_refresh_timer.start(0)

    @Slot(object)
    def handle_loading_event(self, event: object) -> None:
        """Apply only the latest request's lifecycle event."""

        if not isinstance(event, CadLoadEvent):
            return
        request = event.request
        if event.state is CadLoadState.LOADING:
            if request is None or request.request_id <= self._latest_request_id:
                return
            self._latest_request_id = request.request_id
            self._active_request_id = request.request_id
            self._last_event = event
            self.status_label.setText(ui_text("Loading CAD…"))
            self._set_icon(QStyle.StandardPixmap.SP_BrowserReload)
            self.progress_bar.setRange(0, 0)
            self.progress_bar.setTextVisible(False)
            self.progress_bar.show()
            self.cancel_button.setEnabled(True)
            self.cancel_button.show()
            self._refresh_geometry()
            return

        if request is None:
            if self._active_request_id is None and event.state is CadLoadState.FAILED:
                self._show_terminal(event)
            return
        if request.request_id != self._active_request_id:
            return
        self._show_terminal(event)

    def _show_terminal(self, event: CadLoadEvent) -> None:
        self._last_event = event
        message = self._terminal_message(event)
        self.status_label.setText(message)
        if event.state is CadLoadState.SUCCEEDED:
            self._set_icon(QStyle.StandardPixmap.SP_DialogApplyButton)
        elif event.state is CadLoadState.CANCELLED:
            self._set_icon(QStyle.StandardPixmap.SP_DialogCancelButton)
        else:
            self._set_icon(QStyle.StandardPixmap.SP_MessageBoxCritical)
        self._active_request_id = None
        self.progress_bar.hide()
        self.cancel_button.setEnabled(False)
        self.cancel_button.hide()
        self._refresh_geometry()

    def reset_for_shutdown(self) -> None:
        """Clear transient loading controls before the window is torn down."""
        self._active_request_id = None
        self._last_event = None
        self.progress_bar.hide()
        self.cancel_button.setEnabled(False)
        self.cancel_button.hide()
        self.status_label.setText(f"CAD: {ui_text('Ready')}")
        self._refresh_geometry()

    def retranslate_ui(self, _language: object = None) -> None:
        """Refresh presentation text without changing request ownership."""
        self.status_label.setAccessibleName(ui_text("CAD loading status"))
        self.progress_bar.setAccessibleName(ui_text("CAD loading progress"))
        self.cancel_button.setText(ui_text("Cancel"))
        self.cancel_button.setAccessibleName(ui_text("Cancel CAD loading"))
        self.cancel_button.setToolTip(ui_text("Cancel CAD loading"))
        event = self._last_event
        if event is None:
            self.status_label.setText(f"CAD: {ui_text('Ready')}")
        elif event.state is CadLoadState.LOADING:
            self.status_label.setText(ui_text("Loading CAD…"))
        elif event.error is None:
            self.status_label.setText(self._terminal_message(event))
        self._refresh_geometry()

    def _refresh_geometry(self) -> None:
        """Recompute compact bounds from the active font, style, and text."""
        self.setMinimumHeight(0)
        self.setMaximumHeight(self._unbounded_maximum_height)
        self.cancel_button.setMinimumWidth(0)
        self.cancel_button.ensurePolished()
        self.cancel_button.setMinimumWidth(self.cancel_button.sizeHint().width())
        layout = self.layout()
        if layout is not None:
            layout.invalidate()
            layout.activate()
            required_height = max(
                layout.minimumSize().height(),
                layout.sizeHint().height(),
            )
            self.setFixedHeight(required_height)
        self.cancel_button.updateGeometry()
        self.status_label.updateGeometry()
        self.progress_bar.updateGeometry()
        self.updateGeometry()

    @staticmethod
    def _terminal_message(event: CadLoadEvent) -> str:
        if event.error is not None:
            return event.error.message
        if event.state is CadLoadState.SUCCEEDED:
            return ui_text("CAD loaded.")
        if event.state is CadLoadState.CANCELLED:
            return ui_text("CAD loading cancelled.")
        return ui_text("CAD loading failed.")

    @Slot()
    def _cancel_clicked(self) -> None:
        if self._active_request_id is None:
            return
        self.cancel_button.setEnabled(False)
        self._cancel_request()

    def _set_icon(self, pixmap: QStyle.StandardPixmap) -> None:
        icon = self.style().standardIcon(pixmap)
        self.icon_label.setPixmap(icon.pixmap(QSize(16, 16)))
