"""Compact HMS-specific rendering for Operation Manager rows."""

from __future__ import annotations

from PySide6.QtCore import QModelIndex, QPointF, QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QApplication, QStyle, QStyledItemDelegate, QStyleOptionViewItem

from hms_cadcam.ui.localization import ui_text
from hms_cadcam.ui.operation_manager_model import NODE_ROLE
from hms_cadcam.ui.operation_manager_types import (
    OperationManagerNode,
    OperationManagerNodeKind,
    OperationManagerSemanticStatus,
)
from hms_cadcam.ui.ui_tokens import CAM_POPUP_DENSITY


_TREE_ROW_HEIGHT = CAM_POPUP_DENSITY.metrics_for(QSize(1600, 900)).tree_row_height
_OPERATION_ROW_HEIGHT = 38


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

    def audit_texts(self, index: QModelIndex) -> tuple[str, ...]:
        """Expose the exact delegate-owned strings for rendered UI audits."""
        node = index.data(NODE_ROLE)
        if not isinstance(node, OperationManagerNode):
            value = index.data(Qt.ItemDataRole.DisplayRole)
            return () if value is None else (str(value),)
        if index.column() == 0:
            values = [ui_text(node.label)]
            if node.kind is OperationManagerNodeKind.OPERATION:
                values.append(compact_operation_summary(node))
            return tuple(value for value in values if value)
        return (ui_text(node.status.text),)

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
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(option.rect, QColor("#176aa6"))
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
        height = (
            max(_OPERATION_ROW_HEIGHT, option.fontMetrics.height() * 2 + 7)
            if isinstance(node, OperationManagerNode)
            and node.kind is OperationManagerNodeKind.OPERATION
            else _TREE_ROW_HEIGHT
        )
        return QSize(option.rect.width(), height)

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
        if node.kind is not OperationManagerNodeKind.OPERATION:
            painter.drawText(
                QRect(text_left, rect.top(), text_width, rect.height()),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                metrics.elidedText(
                    ui_text(node.label), Qt.TextElideMode.ElideRight, text_width
                ),
            )
            return

        main_rect = QRect(text_left, rect.top() + 2, text_width, metrics.height() + 2)
        painter.drawText(
            main_rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            metrics.elidedText(
                ui_text(node.label), Qt.TextElideMode.ElideRight, text_width
            ),
        )
        secondary_font = QFont(option.font)
        secondary_font.setPointSizeF(max(7.0, option.font.pointSizeF() - 1.0))
        painter.setFont(secondary_font)
        painter.setPen(QColor("#e3edf4" if selected else "#5f7180"))
        secondary_metrics = painter.fontMetrics()
        secondary_rect = QRect(
            text_left,
            rect.bottom() - secondary_metrics.height() - 2,
            text_width,
            secondary_metrics.height(),
        )
        painter.drawText(
            secondary_rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            secondary_metrics.elidedText(
                compact_operation_summary(node),
                Qt.TextElideMode.ElideRight,
                text_width,
            ),
        )

    @staticmethod
    def _paint_status(
        painter: QPainter,
        rect: QRect,
        option: QStyleOptionViewItem,
        node: OperationManagerNode,
    ) -> None:
        foreground, background = _STATUS_COLORS[node.status.semantic]
        text = ui_text(node.status.text)
        font = QFont(option.font)
        font.setPointSizeF(max(7.0, option.font.pointSizeF() - 1.0))
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        available = max(18, rect.width() - 8)
        text = metrics.elidedText(text, Qt.TextElideMode.ElideRight, available - 10)
        width = min(available, metrics.horizontalAdvance(text) + 10)
        pill = QRect(rect.right() - width - 4, rect.center().y() - 8, width, 16)
        if option.state & QStyle.StateFlag.State_Selected:
            painter.setBrush(QColor("#ffffff"))
            painter.setPen(QPen(QColor("#d9e9f5")))
        else:
            painter.setBrush(QColor(background))
            painter.setPen(QPen(QColor(foreground)))
        painter.drawRoundedRect(pill, 4, 4)
        painter.setPen(QColor(foreground))
        painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, text)


def compact_operation_summary(node: OperationManagerNode) -> str:
    """Return a concise, evidence-first operation summary for the second line."""
    if node.kind is not OperationManagerNodeKind.OPERATION:
        return ui_text(node.secondary_summary)
    parts = tuple(
        ui_text(part.strip())
        for part in node.secondary_summary.split("·")
        if part.strip()
    )
    limit = 4 if parts and parts[0].startswith("Tool cầu") else 2
    return " · ".join(parts[:limit])


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
