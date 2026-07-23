"""CAD viewport placeholder used before a CAD kernel is integrated."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QFont, QPaintEvent, QPainter, QPen, QResizeEvent
from PySide6.QtWidgets import QLabel, QSizePolicy, QToolBar, QWidget


class CadViewportPlaceholder(QWidget):
    """Draw a CAD-like workspace without providing CAD functionality."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("CadViewportPlaceholder")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(520, 360)
        self._right_toolbar = self._create_right_toolbar()
        self._context_toolbar = self._create_context_toolbar()
        self._view_label = QLabel("Mặt phẳng XY  |  Trên", self)
        self._view_label.setStyleSheet(
            "background: rgba(255,255,255,220); border: 1px solid #c4cbd3; "
            "padding: 5px 9px; color: #2d3742;"
        )

    def _create_right_toolbar(self) -> QToolBar:
        toolbar = QToolBar(self)
        toolbar.setObjectName("ViewportTools")
        toolbar.setOrientation(Qt.Orientation.Vertical)
        for text, tooltip in (
            ("＋", "Phóng to"),
            ("⌖", "Hiện toàn bộ"),
            ("◉", "Phóng theo cửa sổ"),
            ("↻", "Xoay khung nhìn"),
            ("▧", "Khung dây"),
            ("◫", "Tô bóng"),
            ("▤", "Lớp"),
            ("⚙", "Thiết lập hiển thị"),
        ):
            action = toolbar.addAction(text)
            action.setToolTip(f"{tooltip} — chưa khả dụng")
            action.setEnabled(False)
        toolbar.show()
        return toolbar

    def _create_context_toolbar(self) -> QToolBar:
        toolbar = QToolBar(self)
        toolbar.setObjectName("ContextTools")
        for text in ("Chọn", "Điểm", "Cạnh", "Mặt", "Z  0.0", "Mức  1"):
            action = toolbar.addAction(text)
            action.setToolTip(f"{text} — chưa khả dụng")
            action.setEnabled(False)
        toolbar.show()
        return toolbar

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt API name
        """Keep overlay controls aligned when the viewport is resized."""
        super().resizeEvent(event)
        margin = 14
        right_size = self._right_toolbar.sizeHint()
        self._right_toolbar.setGeometry(
            self.width() - right_size.width() - margin,
            72,
            right_size.width(),
            right_size.height(),
        )
        context_size = self._context_toolbar.sizeHint()
        self._context_toolbar.setGeometry(
            max(margin, (self.width() - context_size.width()) // 2),
            self.height() - context_size.height() - 34,
            context_size.width(),
            context_size.height(),
        )
        label_size = self._view_label.sizeHint()
        self._view_label.setGeometry(
            margin,
            self.height() - label_size.height() - 8,
            label_size.width(),
            label_size.height(),
        )
        self._right_toolbar.raise_()
        self._context_toolbar.raise_()
        self._view_label.raise_()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - Qt API name
        """Paint a neutral workspace, crosshair and orientation markers."""
        del event
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#e7e8ea"))
        center = self.rect().center()

        grid_pen = QPen(QColor("#d7d9dc"), 1)
        painter.setPen(grid_pen)
        spacing = 48
        for x in range(center.x() % spacing, self.width(), spacing):
            painter.drawLine(x, 0, x, self.height())
        for y in range(center.y() % spacing, self.height(), spacing):
            painter.drawLine(0, y, self.width(), y)

        painter.setPen(QPen(QColor("#8d9298"), 1))
        painter.drawLine(0, center.y(), self.width(), center.y())
        painter.drawLine(center.x(), 0, center.x(), self.height())
        self._draw_axis(painter, center)
        self._draw_corner_axis(painter, QPoint(54, 62))

        painter.setPen(QColor("#6f7780"))
        painter.setFont(QFont("Segoe UI", 10))
        painter.drawText(
            QRect(0, 18, self.width(), 26),
            Qt.AlignmentFlag.AlignHCenter,
            "VIEWPORT CAD  •  VỊ TRÍ TẠM",
        )
        painter.end()

    @staticmethod
    def _draw_axis(painter: QPainter, origin: QPoint) -> None:
        painter.setPen(QPen(QColor("#d02727"), 3))
        painter.drawLine(origin, origin + QPoint(52, 0))
        painter.drawText(origin + QPoint(57, 5), "X")
        painter.setPen(QPen(QColor("#1b8c39"), 3))
        painter.drawLine(origin, origin + QPoint(0, -52))
        painter.drawText(origin + QPoint(-5, -58), "Y")
        painter.setPen(QPen(QColor("#245fc2"), 3))
        painter.drawEllipse(origin, 4, 4)

    @staticmethod
    def _draw_corner_axis(painter: QPainter, origin: QPoint) -> None:
        painter.setPen(QPen(QColor("#d02727"), 2))
        painter.drawLine(origin, origin + QPoint(30, 0))
        painter.drawText(origin + QPoint(34, 5), "X")
        painter.setPen(QPen(QColor("#1b8c39"), 2))
        painter.drawLine(origin, origin + QPoint(0, -30))
        painter.drawText(origin + QPoint(-4, -35), "Y")
