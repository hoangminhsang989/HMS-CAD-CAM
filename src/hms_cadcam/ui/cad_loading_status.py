"""Compact, request-owned CAD loading status surface for the main window."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QSize, Qt, Slot
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


class CadLoadingStatusSurface(QWidget):
    """Show request-owned CAD loading state without covering the viewport."""

    def __init__(
        self,
        cancel_request: Callable[[], bool],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._cancel_request = cancel_request
        self._latest_request_id = 0
        self._active_request_id: int | None = None

        self.setObjectName("CadLoadingStatusSurface")
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.setMaximumHeight(24)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 0, 2, 0)
        layout.setSpacing(4)

        self.icon_label = QLabel(self)
        self.icon_label.setObjectName("CadLoadingStatusIcon")
        self.icon_label.setFixedSize(QSize(16, 16))
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.icon_label)

        self.status_label = QLabel("CAD: Sẵn sàng", self)
        self.status_label.setObjectName("CadLoadingStatusLabel")
        self.status_label.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Fixed,
        )
        self.status_label.setMinimumWidth(86)
        self.status_label.setAccessibleName("Trạng thái tải CAD")
        layout.addWidget(self.status_label)

        self.progress_bar = QProgressBar(self)
        self.progress_bar.setObjectName("CadLoadingIndeterminateProgress")
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(12)
        self.progress_bar.setMinimumWidth(48)
        self.progress_bar.setMaximumWidth(72)
        self.progress_bar.setAccessibleName("Tiến trình tải CAD đang chạy")
        self.progress_bar.hide()
        layout.addWidget(self.progress_bar)

        self.cancel_button = QPushButton("Hủy", self)
        self.cancel_button.setObjectName("CadLoadingCancelButton")
        self.cancel_button.setAccessibleName("Hủy tải CAD")
        self.cancel_button.setToolTip("Hủy tải CAD")
        self.cancel_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.cancel_button.setMinimumHeight(20)
        self.cancel_button.setMaximumWidth(54)
        self.cancel_button.clicked.connect(self._cancel_clicked)
        self.cancel_button.hide()
        layout.addWidget(self.cancel_button)

        self._set_icon(QStyle.StandardPixmap.SP_ComputerIcon)

    @property
    def active_request_id(self) -> int | None:
        """Return the request currently allowed to update this surface."""

        return self._active_request_id

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
            self.status_label.setText("Đang tải CAD…")
            self._set_icon(QStyle.StandardPixmap.SP_BrowserReload)
            self.progress_bar.setRange(0, 0)
            self.progress_bar.setTextVisible(False)
            self.progress_bar.show()
            self.cancel_button.setEnabled(True)
            self.cancel_button.show()
            return

        if request is None:
            if self._active_request_id is None and event.state is CadLoadState.FAILED:
                self._show_terminal(event)
            return
        if request.request_id != self._active_request_id:
            return
        self._show_terminal(event)

    def _show_terminal(self, event: CadLoadEvent) -> None:
        if event.error is not None:
            message = event.error.message
        elif event.state is CadLoadState.SUCCEEDED:
            message = "Đã tải CAD."
        elif event.state is CadLoadState.CANCELLED:
            message = "Đã hủy tải CAD."
        else:
            message = "Không thể tải CAD."
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

    @Slot()
    def _cancel_clicked(self) -> None:
        if self._active_request_id is None:
            return
        self.cancel_button.setEnabled(False)
        self._cancel_request()

    def _set_icon(self, pixmap: QStyle.StandardPixmap) -> None:
        icon = self.style().standardIcon(pixmap)
        self.icon_label.setPixmap(icon.pixmap(QSize(16, 16)))
