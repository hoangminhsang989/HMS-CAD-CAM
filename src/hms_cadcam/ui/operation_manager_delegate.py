"""Compact HMS-specific rendering for Operation Manager rows."""

from __future__ import annotations

from PySide6.QtCore import QModelIndex, QPointF, QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QApplication, QStyle, QStyledItemDelegate, QStyleOptionViewItem

from hms_cadcam.ui.operation_manager_model import NODE_ROLE
from hms_cadcam.ui.operation_manager_types import (
    OperationManagerNode,
    OperationManagerNodeKind,
    OperationManagerSemanticStatus,
)


_STATUS_COLORS = {
    OperationManagerSemanticStatus.CURRENT: ("#23724b", "#e7f5ed"),
    OperationManagerSemanticStatus.ACTIVE: ("#176aa6", "#e7f1fb"),
    OperationManagerSemanticStatus.READY: ("#315f7d", "#edf4f8"),
    OperationManagerSemanticStatus.DRAFT: ("#5d6872", "#f0f2f4"),
    OperationManagerSemanticStatus.MISSING: ("#5d6872", "#f0f2f4"),
    OperationManagerSemanticStatus.NEEDS_INPUT: ("#8a5a0a", "#fff4d7"),
    OperationManagerSemanticStatus.CALCULATING: ("#176aa6", "#e7f1fb"),
    OperationManagerSemanticStatus.STALE: ("#8a5a0a", "#fff4d7"),
    OperationManagerSemanticStatus.WARNING: ("#8a5a0a", "#fff4d7"),
    OperationManagerSemanticStatus.BLOCKED: ("#9b241b", "#fdeceb"),
    OperationManagerSemanticStatus.FAILED: ("#9b241b", "#fdeceb"),
    OperationManagerSemanticStatus.DISABLED: ("#616b74", "#e7eaed"),
}


class OperationManagerDelegate(QStyledItemDelegate):
    """Draw semantic shape + text without storing widgets per row."""

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> None:
        node = index.data(NODE_ROLE)
        if not isinstance(node, OperationManagerNode):
            super().paint(painter, option, index)
            return
        prepared = QStyleOptionViewItem(option)
        self.initStyleOption(prepared, index)
        prepared.text = ""
        prepared.icon = prepared.icon.__class__()
        style = prepared.widget.style() if prepared.widget is not None else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, prepared, painter, prepared.widget)
        painter.save()
        if index.column() == 0:
            self._paint_name(painter, option.rect, option, node)
        else:
            self._paint_status(painter, option.rect, option, node)
        painter.restore()

    def sizeHint(
        self, option: QStyleOptionViewItem, index: QModelIndex
    ) -> QSize:
        node = index.data(NODE_ROLE)
        if isinstance(node, OperationManagerNode) and node.kind is OperationManagerNodeKind.OPERATION:
            return QSize(option.rect.width(), 42)
        return QSize(option.rect.width(), 28)

    @staticmethod
    def _paint_name(
        painter: QPainter,
        rect: QRect,
        option: QStyleOptionViewItem,
        node: OperationManagerNode,
    ) -> None:
        foreground, _background = _STATUS_COLORS[node.status.semantic]
        icon_rect = QRect(rect.left() + 4, rect.center().y() - 5, 11, 11)
        _draw_status_mark(painter, icon_rect, QColor(foreground), node.status.semantic)
        text_left = icon_rect.right() + 6
        text_width = max(0, rect.right() - text_left - 4)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        main_color = QColor("#ffffff" if selected else "#1f2e3a")
        secondary_color = QColor("#e5eef6" if selected else "#5d6b78")
        label_font = QFont(option.font)
        if node.kind in {
            OperationManagerNodeKind.PROJECT,
            OperationManagerNodeKind.JOB,
            OperationManagerNodeKind.SETUP,
            OperationManagerNodeKind.OPERATIONS,
        }:
            label_font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(label_font)
        painter.setPen(main_color)
        metrics = painter.fontMetrics()
        if node.kind is OperationManagerNodeKind.OPERATION:
            label_rect = QRect(text_left, rect.top() + 3, text_width, 19)
            painter.drawText(
                label_rect,
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                metrics.elidedText(node.label, Qt.TextElideMode.ElideRight, text_width),
            )
            secondary_font = QFont(option.font)
            secondary_font.setPointSizeF(max(7.5, option.font.pointSizeF() - 1.0))
            painter.setFont(secondary_font)
            painter.setPen(secondary_color)
            secondary_metrics = painter.fontMetrics()
            summary_rect = QRect(text_left, rect.top() + 21, text_width, 17)
            painter.drawText(
                summary_rect,
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                secondary_metrics.elidedText(
                    node.secondary_summary, Qt.TextElideMode.ElideRight, text_width
                ),
            )
            return
        painter.drawText(
            QRect(text_left, rect.top(), text_width, rect.height()),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            metrics.elidedText(node.label, Qt.TextElideMode.ElideRight, text_width),
        )

    @staticmethod
    def _paint_status(
        painter: QPainter,
        rect: QRect,
        option: QStyleOptionViewItem,
        node: OperationManagerNode,
    ) -> None:
        foreground, background = _STATUS_COLORS[node.status.semantic]
        text = node.status.text
        font = QFont(option.font)
        font.setPointSizeF(max(7.0, option.font.pointSizeF() - 1.0))
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        available = max(18, rect.width() - 8)
        text = metrics.elidedText(text, Qt.TextElideMode.ElideRight, available - 10)
        width = min(available, metrics.horizontalAdvance(text) + 10)
        pill = QRect(rect.right() - width - 4, rect.center().y() - 9, width, 18)
        if option.state & QStyle.StateFlag.State_Selected:
            painter.setBrush(QColor("#ffffff"))
            painter.setPen(QPen(QColor("#d9e9f5")))
        else:
            painter.setBrush(QColor(background))
            painter.setPen(QPen(QColor(foreground)))
        painter.drawRoundedRect(pill, 4, 4)
        painter.setPen(QColor(foreground))
        painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, text)


def _draw_status_mark(
    painter: QPainter,
    rect: QRect,
    color: QColor,
    semantic: OperationManagerSemanticStatus,
) -> None:
    """Draw HMS semantic shapes so state never depends on color alone."""
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(QPen(color, 1.5))
    painter.setBrush(color)
    if semantic in {
        OperationManagerSemanticStatus.CURRENT,
        OperationManagerSemanticStatus.ACTIVE,
        OperationManagerSemanticStatus.READY,
    }:
        painter.drawEllipse(rect)
        painter.setPen(QPen(QColor("#ffffff"), 1.4))
        path = QPainterPath(QPointF(rect.left() + 2.5, rect.center().y()))
        path.lineTo(rect.left() + 4.6, rect.bottom() - 2.5)
        path.lineTo(rect.right() - 2.0, rect.top() + 2.5)
        painter.drawPath(path)
    elif semantic in {
        OperationManagerSemanticStatus.WARNING,
        OperationManagerSemanticStatus.STALE,
        OperationManagerSemanticStatus.NEEDS_INPUT,
    }:
        path = QPainterPath(QPointF(rect.center().x(), rect.top()))
        path.lineTo(rect.right(), rect.bottom())
        path.lineTo(rect.left(), rect.bottom())
        path.closeSubpath()
        painter.drawPath(path)
    elif semantic in {
        OperationManagerSemanticStatus.BLOCKED,
        OperationManagerSemanticStatus.FAILED,
    }:
        painter.drawRoundedRect(rect, 2, 2)
        painter.setPen(QPen(QColor("#ffffff"), 1.4))
        painter.drawLine(
            QPointF(rect.left() + 2.5, rect.top() + 2.5),
            QPointF(rect.right() - 2.5, rect.bottom() - 2.5),
        )
        painter.drawLine(
            QPointF(rect.right() - 2.5, rect.top() + 2.5),
            QPointF(rect.left() + 2.5, rect.bottom() - 2.5),
        )
    elif semantic is OperationManagerSemanticStatus.CALCULATING:
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawArc(rect, 35 * 16, 285 * 16)
    else:
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rect.adjusted(1, 1, -1, -1))
