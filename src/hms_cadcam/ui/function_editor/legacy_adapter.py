"""Adapter that keeps the production CAM editor unchanged during migration."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QScrollArea,
    QTreeWidget,
    QVBoxLayout,
    QWidget,
)


class LegacyFunctionEditorAdapter(QWidget):
    """One scroll and one Apply action around the existing production widget."""

    close_requested = Signal()

    def __init__(
        self,
        editor: QWidget,
        tree: QTreeWidget,
        apply_callback: Callable[[], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("LegacyFunctionEditorAdapter")
        self.setAccessibleName("Legacy Editor adapter")
        self.editor = editor
        self._tree = tree
        self._dirty = False
        root = QVBoxLayout(self)
        root.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        summary = QFrame()
        summary.setObjectName("FunctionEditorSummary")
        summary_layout = QVBoxLayout(summary)
        summary_layout.setContentsMargins(8, 6, 8, 6)
        top = QHBoxLayout()
        self.selection_summary = QLabel("Chưa chọn operation")
        self.selection_summary.setObjectName("FunctionEditorSummaryTitle")
        self.selection_summary.setWordWrap(True)
        top.addWidget(self.selection_summary, 1)
        badge = QLabel("LEGACY EDITOR")
        badge.setObjectName("FunctionEditorLegacyBadge")
        badge.setAccessibleName("Editor production cũ")
        top.addWidget(badge)
        summary_layout.addLayout(top)
        self.state_summary = QLabel(
            "Chưa migrate strategy · dùng nguyên validation và Apply hiện tại."
        )
        self.state_summary.setObjectName("PanelSummary")
        self.state_summary.setWordWrap(True)
        summary_layout.addWidget(self.state_summary)
        root.addWidget(summary)

        form = editor.layout()
        if isinstance(form, QFormLayout):
            form.setFieldGrowthPolicy(
                QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
            )
            form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
            classic_apply = getattr(editor, "apply_button", None)
            if isinstance(classic_apply, QWidget):
                form.setRowVisible(classic_apply, False)
        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("FunctionEditorScrollArea")
        self.scroll_area.setAccessibleName("Nội dung Legacy Editor")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.scroll_area.setWidget(editor)
        root.addWidget(self.scroll_area, 1)

        footer = QFrame()
        footer.setObjectName("FunctionEditorFooter")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(8, 6, 8, 6)
        footer_layout.addStretch(1)
        self.apply_button = QPushButton("Apply")
        self.apply_button.setObjectName("PrimaryPanelAction")
        self.apply_button.setAccessibleName("Áp dụng bản nháp bằng Legacy Editor")
        self.apply_button.setToolTip(
            "Dùng nguyên parse, validation và atomic CAM command hiện tại"
        )
        self.apply_button.clicked.connect(apply_callback)
        footer_layout.addWidget(self.apply_button)
        self.close_button = QPushButton("Close")
        self.close_button.setAccessibleName("Đóng Function Editor")
        self.close_button.setToolTip(
            "Ẩn panel; draft legacy vẫn theo lifecycle hiện tại, không tự Apply"
        )
        self.close_button.clicked.connect(self.close_requested)
        footer_layout.addWidget(self.close_button)
        root.addWidget(footer)

        draft_signal = getattr(editor, "draft_changed", None)
        if draft_signal is not None:
            draft_signal.connect(self._draft_changed)
        self.refresh_summary()

    def _draft_changed(self) -> None:
        self._dirty = True
        self.refresh_summary()

    def refresh_summary(self) -> None:
        """Read the current tree item without caching operation identity."""
        try:
            item = self._tree.currentItem()
        except RuntimeError:
            # Qt may emit modelReset while child widgets are being destroyed.
            return
        if item is None:
            self.selection_summary.setText("Chưa chọn operation")
            self.state_summary.setText(
                "Chọn một node trong Operation Manager để mở Legacy Editor."
            )
            self._dirty = False
            return
        self.selection_summary.setText(item.text(0) or "Selection")
        status = item.text(1).strip() or "Không có trạng thái"
        draft = " · Draft modified" if self._dirty else ""
        self.state_summary.setText(
            f"Legacy Editor · Trạng thái: {status}{draft}"
        )

    def selection_changed(self) -> None:
        """Drop presentation-only dirty badge; the legacy coordinator owns drafts."""
        self._dirty = False
        self.refresh_summary()

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        """The legacy form scrolls internally instead of widening the dock."""
        return QSize(240, 300)
