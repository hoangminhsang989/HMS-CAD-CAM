"""Composite widgets for the Stage 9A.4 Unified Function Editor."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from hms_cadcam.ui.function_editor.fields import FunctionEditorFieldWidget
from hms_cadcam.ui.function_editor.model import (
    FunctionEditorAction,
    FunctionEditorDiagnostic,
    FunctionEditorDiagnosticSeverity,
    FunctionEditorDraftStatus,
    FunctionEditorPreviewRequest,
    FunctionEditorSummary,
    ParameterDisclosureLevel,
    PresentationValue,
)
from hms_cadcam.ui.function_editor.schema import FunctionEditorSchema
from hms_cadcam.ui.function_editor.sections import FunctionEditorSectionWidget
from hms_cadcam.ui.function_editor.state import (
    ApplyCallback,
    FunctionEditorDraftState,
    FunctionEditorStateStore,
    FunctionEditorUserState,
)


PreviewCallback = Callable[[FunctionEditorPreviewRequest], object]
CalculateCallback = Callable[[Mapping[str, PresentationValue]], object]
CloseConfirmation = Callable[[FunctionEditorDraftState], bool]
FieldActionCallback = Callable[
    [str, Mapping[str, PresentationValue]], Mapping[str, PresentationValue] | None
]

_STATUS_LABELS = {
    FunctionEditorDraftStatus.NO_CHANGES: "No changes",
    FunctionEditorDraftStatus.MODIFIED: "Modified",
    FunctionEditorDraftStatus.INVALID: "Invalid",
    FunctionEditorDraftStatus.APPLYING: "Applying",
    FunctionEditorDraftStatus.APPLIED: "Applied",
    FunctionEditorDraftStatus.STALE: "Stale",
}

_COMPACT_WIDTH = 400


class FunctionEditorSummaryWidget(QFrame):
    """Sticky operation summary with text validation counts."""

    help_requested = Signal()

    def __init__(
        self, summary: FunctionEditorSummary, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setObjectName("FunctionEditorSummary")
        self.setAccessibleName("Tóm tắt Function Editor")
        root = QVBoxLayout(self)
        root.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        root.setContentsMargins(9, 7, 9, 7)
        root.setSpacing(2)
        top = QHBoxLayout()
        self.title = QLabel(summary.title)
        self.title.setObjectName("FunctionEditorSummaryTitle")
        self.title.setWordWrap(False)
        top.addWidget(self.title, 1)
        self.reference_badge = QLabel("REFERENCE DEMO")
        self.reference_badge.setObjectName("FunctionEditorReferenceBadge")
        self.reference_badge.setVisible(summary.reference_only)
        top.addWidget(self.reference_badge)
        self.help_button = QToolButton()
        self.help_button.setText("?")
        self.help_button.setAccessibleName("Mở trợ giúp Function Editor")
        self.help_button.setToolTip("Mở panel trợ giúp ngắn")
        self.help_button.clicked.connect(self.help_requested)
        top.addWidget(self.help_button)
        root.addLayout(top)
        self.context = QLabel()
        self.context.setObjectName("FunctionEditorSummaryContext")
        self.context.setWordWrap(False)
        root.addWidget(self.context)
        status_line = QHBoxLayout()
        self.draft_status = QLabel()
        self.draft_status.setObjectName("FunctionEditorDraftStatus")
        status_line.addWidget(self.draft_status)
        status_line.addStretch(1)
        self.validation = QLabel("0 lỗi · 0 cảnh báo")
        self.validation.setObjectName("FunctionEditorValidationSummary")
        status_line.addWidget(self.validation)
        root.addLayout(status_line)
        self._full_title = ""
        self._full_context = ""
        self.update_summary(summary, FunctionEditorDraftStatus.NO_CHANGES, ())

    def update_summary(
        self,
        summary: FunctionEditorSummary,
        status: FunctionEditorDraftStatus,
        diagnostics: tuple[FunctionEditorDiagnostic, ...],
    ) -> None:
        """Render summary and validation through text as well as color."""
        self._full_title = summary.title
        strategy = summary.strategy or "Không có strategy"
        self._full_context = (
            f"Tool: {summary.tool} · Geometry: {summary.geometry} · {strategy}"
        )
        self._refresh_elided_text()
        self.reference_badge.setVisible(summary.reference_only)
        self.draft_status.setText(
            f"Toolpath: {summary.operation_status} · Draft: {_STATUS_LABELS[status]}"
        )
        errors = sum(
            item.severity is FunctionEditorDiagnosticSeverity.ERROR
            for item in diagnostics
        )
        warnings = sum(
            item.severity is FunctionEditorDiagnosticSeverity.WARNING
            for item in diagnostics
        )
        self.validation.setText(f"● {errors} lỗi · ▲ {warnings} cảnh báo")
        accessible = (
            f"{summary.title}, {strategy}, {_STATUS_LABELS[status]}, "
            f"{errors} lỗi, {warnings} cảnh báo"
        )
        self.setAccessibleDescription(accessible)

    def _refresh_elided_text(self) -> None:
        title_width = max(40, self.title.width())
        context_width = max(40, self.context.width())
        self.title.setText(
            self.title.fontMetrics().elidedText(
                self._full_title, Qt.TextElideMode.ElideRight, title_width
            )
        )
        self.context.setText(
            self.context.fontMetrics().elidedText(
                self._full_context, Qt.TextElideMode.ElideRight, context_width
            )
        )
        self.title.setToolTip(self._full_title)
        self.context.setToolTip(self._full_context)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._refresh_elided_text()


class FunctionEditorDiagnosticView(QFrame):
    """Compact diagnostic section with focusable targets."""

    focus_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("FunctionEditorDiagnosticView")
        self.setAccessibleName("Diagnostics Function Editor")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        root = QVBoxLayout(self)
        root.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        root.setContentsMargins(7, 5, 7, 7)
        root.setSpacing(4)
        header = QHBoxLayout()
        header.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        title = QLabel("DIAGNOSTICS")
        title.setObjectName("FunctionEditorSectionTitle")
        header.addWidget(title)
        header.addStretch(1)
        self.summary = QLabel("Không có lỗi hoặc cảnh báo")
        self.summary.setObjectName("FunctionEditorSectionBadge")
        self.summary.setWordWrap(True)
        header.addWidget(self.summary)
        root.addLayout(header)
        self.list = QListWidget()
        self.list.setObjectName("FunctionEditorDiagnosticList")
        self.list.setAccessibleName("Danh sách lỗi và cảnh báo")
        self.list.setMaximumHeight(150)
        self.list.setMinimumWidth(0)
        self.list.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        self.list.itemActivated.connect(self._activate)
        root.addWidget(self.list)
        self.list.setVisible(False)

    def set_diagnostics(
        self, diagnostics: tuple[FunctionEditorDiagnostic, ...]
    ) -> None:
        """Replace stale validation output; items store stable field IDs only."""
        self.list.clear()
        for diagnostic in diagnostics:
            prefix = {
                FunctionEditorDiagnosticSeverity.ERROR: "● LỖI",
                FunctionEditorDiagnosticSeverity.WARNING: "▲ CẢNH BÁO",
                FunctionEditorDiagnosticSeverity.INFO: "ℹ INFO",
            }[diagnostic.severity]
            item = QListWidgetItem(
                f"{prefix} · {diagnostic.code} · {diagnostic.message}"
            )
            item.setData(Qt.ItemDataRole.UserRole, diagnostic.field_id)
            item.setToolTip("Enter để focus field liên quan" if diagnostic.field_id else "")
            self.list.addItem(item)
        errors = sum(
            item.severity is FunctionEditorDiagnosticSeverity.ERROR
            for item in diagnostics
        )
        warnings = sum(
            item.severity is FunctionEditorDiagnosticSeverity.WARNING
            for item in diagnostics
        )
        self.summary.setText(
            "Không có lỗi hoặc cảnh báo"
            if not diagnostics
            else f"{errors} lỗi · {warnings} cảnh báo"
        )
        self.list.setVisible(bool(diagnostics))

    def _activate(self, item: QListWidgetItem) -> None:
        field_id = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(field_id, str):
            self.focus_requested.emit(field_id)


class FunctionEditorFooterWidget(QFrame):
    """Fixed footer that reflows into a compact two-row layout."""

    action_requested = Signal(str)

    _LABELS = {
        FunctionEditorAction.RESET_DRAFT: "Reset Draft",
        FunctionEditorAction.PREVIEW: "Preview",
        FunctionEditorAction.VALIDATE: "Validate",
        FunctionEditorAction.CALCULATE: "Calculate",
        FunctionEditorAction.APPLY: "Apply",
        FunctionEditorAction.CLOSE: "Close",
    }

    def __init__(
        self, schema: FunctionEditorSchema, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setObjectName("FunctionEditorFooter")
        self.setAccessibleName("Action Function Editor")
        self._compact = False
        self.layout_grid = QGridLayout(self)
        self.layout_grid.setContentsMargins(7, 6, 7, 6)
        self.layout_grid.setHorizontalSpacing(4)
        self.layout_grid.setVerticalSpacing(4)
        supported = set(schema.footer.actions)
        if not schema.footer.preview_supported:
            supported.discard(FunctionEditorAction.PREVIEW)
        if not schema.footer.calculate_supported:
            supported.discard(FunctionEditorAction.CALCULATE)
        if not schema.footer.apply_supported:
            supported.discard(FunctionEditorAction.APPLY)
        self.buttons: dict[FunctionEditorAction, QPushButton] = {}
        self._action_order = tuple(
            action for action in schema.footer.actions if action in supported
        )
        for action in self._action_order:
            if action not in supported:
                continue
            button = QPushButton(self._LABELS[action])
            button.setObjectName(
                "PrimaryPanelAction"
                if action is FunctionEditorAction.APPLY
                else f"FunctionEditorAction_{action.value}"
            )
            button.setAccessibleName(self._LABELS[action])
            button.setToolTip(self._tooltip(action))
            button.clicked.connect(
                lambda _checked=False, value=action: self.action_requested.emit(
                    value.value
                )
            )
            self.buttons[action] = button
        self._place_buttons()

    @staticmethod
    def _tooltip(action: FunctionEditorAction) -> str:
        return {
            FunctionEditorAction.RESET_DRAFT: (
                "Trả toàn bộ draft về snapshot đã Apply gần nhất"
            ),
            FunctionEditorAction.PREVIEW: (
                "Preview transient; không Apply và không Calculate toolpath"
            ),
            FunctionEditorAction.VALIDATE: (
                "Kiểm tra toàn draft và focus lỗi đầu tiên"
            ),
            FunctionEditorAction.CALCULATE: (
                "Chỉ dùng applied state current và hợp lệ"
            ),
            FunctionEditorAction.APPLY: "Áp dụng draft hợp lệ theo một command atomic",
            FunctionEditorAction.CLOSE: "Đóng editor; draft chưa Apply cần xác nhận",
        }[action]

    def _place_buttons(self) -> None:
        for button in self.buttons.values():
            self.layout_grid.removeWidget(button)
        ordered = [self.buttons[action] for action in self._action_order]
        columns = 3 if self._compact else max(1, len(ordered))
        for index, button in enumerate(ordered):
            self.layout_grid.addWidget(button, index // columns, index % columns)
        for column in range(columns):
            self.layout_grid.setColumnStretch(column, 1)

    def set_compact(self, compact: bool) -> None:
        if compact == self._compact:
            return
        self._compact = compact
        self._place_buttons()

    def update_state(self, state: FunctionEditorDraftState) -> None:
        """Apply the standard footer action policy and readable disabled reasons."""
        reset = self.buttons.get(FunctionEditorAction.RESET_DRAFT)
        if reset is not None:
            reset.setEnabled(state.is_dirty)
            reason = (
                self._tooltip(FunctionEditorAction.RESET_DRAFT)
                if state.is_dirty
                else "Draft đang trùng snapshot đã Apply"
            )
            reset.setToolTip(reason)
            reset.setAccessibleDescription(reason)
        apply = self.buttons.get(FunctionEditorAction.APPLY)
        if apply is not None:
            enabled = state.is_dirty and state.status not in {
                FunctionEditorDraftStatus.INVALID,
                FunctionEditorDraftStatus.APPLYING,
            }
            apply.setEnabled(enabled)
            reason = (
                self._tooltip(FunctionEditorAction.APPLY)
                if enabled
                else (
                    "Không có thay đổi" if not state.is_dirty else "Sửa lỗi draft trước khi Apply"
                )
            )
            apply.setToolTip(reason)
            apply.setAccessibleDescription(reason)
        calculate = self.buttons.get(FunctionEditorAction.CALCULATE)
        if calculate is not None:
            calculate.setEnabled(state.can_calculate)
            reason = (
                self._tooltip(FunctionEditorAction.CALCULATE)
                if calculate.isEnabled()
                else (
                    "Calculate chỉ dùng applied state current và hợp lệ"
                )
            )
            calculate.setToolTip(reason)
            calculate.setAccessibleDescription(reason)
        preview = self.buttons.get(FunctionEditorAction.PREVIEW)
        if preview is not None:
            preview.setEnabled(state.status is not FunctionEditorDraftStatus.INVALID)
            reason = (
                self._tooltip(FunctionEditorAction.PREVIEW)
                if preview.isEnabled()
                else "Sửa lỗi draft trước khi Preview"
            )
            preview.setToolTip(reason)
            preview.setAccessibleDescription(reason)


class FunctionEditorPage(QWidget):
    """Sticky summary/footer page backed by typed schema and pure draft state."""

    close_requested = Signal()
    preview_requested = Signal(object)
    calculate_requested = Signal(object)
    state_changed = Signal(object)

    def __init__(
        self,
        state: FunctionEditorDraftState,
        *,
        state_store: FunctionEditorStateStore | None = None,
        apply_callback: ApplyCallback | None = None,
        preview_callback: PreviewCallback | None = None,
        calculate_callback: CalculateCallback | None = None,
        field_action_callback: FieldActionCallback | None = None,
        close_confirmation: CloseConfirmation | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName(f"FunctionEditorPage_{state.schema.editor_id}")
        self.setAccessibleName(f"Function Editor {state.schema.summary.title}")
        self.state = state
        self.schema = state.schema
        self._state_store = state_store
        self._apply_callback = apply_callback
        self._preview_callback = preview_callback
        self._calculate_callback = calculate_callback
        self._field_action_callback = field_action_callback
        self._close_confirmation = close_confirmation
        self._section_widgets: dict[str, FunctionEditorSectionWidget] = {}
        self._field_widgets: dict[str, FunctionEditorFieldWidget] = {}
        self._compact = False
        self._content_section_order: tuple[str, ...] | None = None
        self._user_state = (
            state_store.load(self.schema)
            if state_store is not None
            else FunctionEditorUserState()
        )
        self._highest_disclosure = max(
            (
                level
                for section in self.schema.sections
                for level in (
                    section.disclosure_level,
                    *(field.disclosure_level for field in section.fields),
                )
            ),
            default=ParameterDisclosureLevel.BASIC,
        )
        self.maximum_disclosure = min(
            self._user_state.disclosure_level, self._highest_disclosure
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self.summary = FunctionEditorSummaryWidget(self.schema.summary)
        self.summary.help_requested.connect(self._show_editor_help)
        root.addWidget(self.summary)
        self.preview_status = QLabel()
        self.preview_status.setObjectName("FunctionEditorPreviewStatus")
        self.preview_status.setAccessibleName("Trạng thái Preview Function Editor")
        self.preview_status.setWordWrap(True)
        self.preview_status.setVisible(False)
        root.addWidget(self.preview_status)
        root.addWidget(self._disclosure_bar())
        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("FunctionEditorContentScroll")
        self.scroll_area.setAccessibleName("Nội dung tham số Function Editor")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.content = QWidget()
        self.content.setObjectName("FunctionEditorContent")
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self.content_layout.setContentsMargins(5, 5, 5, 5)
        self.content_layout.setSpacing(6)
        self.help_panel = QFrame()
        self.help_panel.setObjectName("FunctionEditorHelpPanel")
        help_layout = QVBoxLayout(self.help_panel)
        help_layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        help_header = QHBoxLayout()
        help_title = QLabel("HELP")
        help_title.setObjectName("FunctionEditorSectionTitle")
        help_header.addWidget(help_title)
        help_header.addStretch(1)
        help_close = QToolButton()
        help_close.setText("×")
        help_close.setAccessibleName("Đóng help panel")
        help_close.clicked.connect(lambda: self._set_help_visible(False))
        help_header.addWidget(help_close)
        help_layout.addLayout(help_header)
        self.help_text = QLabel(
            "Chọn dấu ? ở section hoặc field để xem mô tả, đơn vị, phạm vi và nguồn mặc định."
        )
        self.help_text.setObjectName("FunctionEditorHelpText")
        self.help_text.setWordWrap(True)
        self.help_text.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByKeyboard
            | Qt.TextInteractionFlag.TextSelectableByMouse
        )
        help_layout.addWidget(self.help_text)
        self.help_panel.setVisible(self._user_state.help_visible)
        self.diagnostic_view = FunctionEditorDiagnosticView()
        self.diagnostic_view.focus_requested.connect(self.focus_field)
        self.scroll_area.setWidget(self.content)
        root.addWidget(self.scroll_area, 1)
        self.footer = FunctionEditorFooterWidget(self.schema)
        self.footer.action_requested.connect(self._action_requested)
        root.addWidget(self.footer)
        self._sync_visibility()
        self._render_state()

    def _disclosure_bar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("FunctionEditorDisclosureBar")
        layout = QGridLayout(bar)
        layout.setContentsMargins(7, 4, 7, 4)
        layout.setSpacing(5)
        label = QLabel("Mức hiển thị")
        label.setAccessibleName("Mức tham số tối đa")
        layout.addWidget(label, 0, 0)
        self.disclosure_selector = QComboBox()
        self.disclosure_selector.setObjectName("FunctionEditorDisclosureSelector")
        self.disclosure_selector.setAccessibleName("Chọn Basic Advanced hoặc Expert")
        for level, text in (
            (ParameterDisclosureLevel.BASIC, "Basic"),
            (ParameterDisclosureLevel.ADVANCED, "Advanced"),
            (ParameterDisclosureLevel.EXPERT, "Expert"),
        ):
            if level <= self._highest_disclosure:
                self.disclosure_selector.addItem(text, level)
        self.disclosure_selector.setCurrentIndex(
            self.disclosure_selector.findData(self.maximum_disclosure)
        )
        self.disclosure_selector.currentIndexChanged.connect(
            self._disclosure_changed
        )
        layout.addWidget(self.disclosure_selector, 0, 1, 1, 2)
        self.collapse_all_button = QToolButton()
        self.collapse_all_button.setText("Collapse All")
        self.collapse_all_button.setAccessibleName("Thu gọn tất cả section")
        self.collapse_all_button.clicked.connect(self.collapse_all)
        layout.addWidget(self.collapse_all_button, 1, 0)
        self.expand_relevant_button = QToolButton()
        self.expand_relevant_button.setText("Expand Relevant")
        self.expand_relevant_button.setAccessibleName("Mở section liên quan")
        self.expand_relevant_button.clicked.connect(self.expand_relevant)
        layout.addWidget(self.expand_relevant_button, 1, 1)
        self.defaults_button = QToolButton()
        self.defaults_button.setText("Defaults")
        self.defaults_button.setAccessibleName("Restore Recommended Defaults")
        self.defaults_button.setToolTip(
            "Nạp default có nguồn vào draft; không tự Apply"
        )
        self.defaults_button.clicked.connect(self.restore_recommended_defaults)
        layout.addWidget(self.defaults_button, 1, 2)
        layout.setColumnStretch(0, 1)
        layout.setColumnStretch(1, 1)
        layout.setColumnStretch(2, 1)
        return bar

    def _disclosure_changed(self, index: int) -> None:
        value = self.disclosure_selector.itemData(index)
        if not isinstance(value, ParameterDisclosureLevel):
            return
        self.maximum_disclosure = value
        self._sync_visibility()
        self._save_user_state()

    def _ensure_section(self, section_id: str) -> FunctionEditorSectionWidget:
        existing = self._section_widgets.get(section_id)
        if existing is not None:
            return existing
        definition = self.schema.section(section_id)
        widget = FunctionEditorSectionWidget(definition)
        widget.expanded_changed.connect(self._section_expanded)
        widget.reset_requested.connect(self.reset_section)
        widget.help_requested.connect(self._show_section_help)
        if self._user_state.has_expansion_state:
            widget.set_expanded(
                section_id in self._user_state.expanded_sections, emit=False
            )
        self._section_widgets[section_id] = widget
        return widget

    def _ensure_field(self, field_id: str) -> FunctionEditorFieldWidget:
        existing = self._field_widgets.get(field_id)
        if existing is not None:
            return existing
        definition = self.schema.field(field_id)
        widget = FunctionEditorFieldWidget(
            definition, self.state.values[field_id]
        )
        widget.value_changed.connect(self._field_changed)
        widget.reset_requested.connect(self.reset_field)
        widget.help_requested.connect(self._show_field_help)
        widget.action_requested.connect(self._field_action_requested)
        widget.set_compact(self._compact)
        self._field_widgets[field_id] = widget
        owner = self.schema.section_for_field(field_id)
        section_widget = self._ensure_section(owner.section_id)
        ordered_ids = [
            item.field_id
            for item in sorted(owner.fields, key=lambda item: (item.order, item.field_id))
        ]
        insert_at = sum(
            1
            for other_id in ordered_ids[: ordered_ids.index(field_id)]
            if other_id in self._field_widgets
        )
        section_widget.insert_field(insert_at, widget)
        return widget

    def _sync_visibility(self) -> None:
        values = dict(self.state.values)
        visible_sections = {
            item.section_id
            for item in self.schema.visible_sections(
                values, self.maximum_disclosure
            )
        }
        for section in self.schema.ordered_sections:
            if section.section_id in visible_sections:
                widget = self._ensure_section(section.section_id)
                widget.setVisible(True)
                visible_definitions = self.schema.visible_fields(
                    section.section_id, values, self.maximum_disclosure
                )
                visible_fields = {
                    item.field_id for item in visible_definitions
                }
                for field in visible_definitions:
                    self._ensure_field(field.field_id).setVisible(True)
                for field in section.fields:
                    existing = self._field_widgets.get(field.field_id)
                    if existing is not None and field.field_id not in visible_fields:
                        existing.setVisible(False)
            elif section.section_id in self._section_widgets:
                self._section_widgets[section.section_id].setVisible(False)
        self._rebuild_content_order()
        self._update_tab_order()

    def _rebuild_content_order(self) -> None:
        section_order = tuple(
            section.section_id
            for section in self.schema.ordered_sections
            if section.section_id in self._section_widgets
        )
        if section_order == self._content_section_order:
            return
        self._content_section_order = section_order
        while self.content_layout.count():
            item = self.content_layout.takeAt(0)
        for section in self.schema.ordered_sections:
            widget = self._section_widgets.get(section.section_id)
            if widget is not None:
                self.content_layout.addWidget(widget)
        self.content_layout.addWidget(self.help_panel)
        self.content_layout.addWidget(self.diagnostic_view)
        self.content_layout.addStretch(1)

    def _field_changed(self, field_id: str, value: object) -> None:
        self.state.edit(field_id, value)  # type: ignore[arg-type]
        self._sync_visibility()
        self._render_state()
        self.state_changed.emit(self.state)

    def _field_action_requested(self, field_id: str, action_id: str) -> None:
        """Run a typed selection action and merge only returned primitives."""
        if self._field_action_callback is None:
            return
        try:
            changed = self._field_action_callback(action_id, self.state.values)
            if changed is None:
                return
            self.state.edit_many(changed)
        except (KeyError, RuntimeError, TypeError, ValueError) as error:
            definition = self.schema.field(field_id)
            diagnostic = FunctionEditorDiagnostic(
                code="field.action_failed",
                message=str(error) or f"Không thể thực hiện {definition.action_label}.",
                severity=FunctionEditorDiagnosticSeverity.ERROR,
                field_id=field_id,
                section_id=self.schema.section_for_field(field_id).section_id,
            )
            self.state.set_diagnostics((diagnostic,))
        self._refresh_values()
        self.state_changed.emit(self.state)

    def _render_state(self) -> None:
        diagnostics = self.state.diagnostics
        for field_id, widget in self._field_widgets.items():
            diagnostic = next(
                (item for item in diagnostics if item.field_id == field_id), None
            )
            widget.set_diagnostic(diagnostic)
        for section_id, widget in self._section_widgets.items():
            widget.set_diagnostics(
                tuple(item for item in diagnostics if item.section_id == section_id)
            )
        self.diagnostic_view.set_diagnostics(diagnostics)
        self.summary.update_summary(
            self.schema.summary, self.state.status, diagnostics
        )
        self.footer.update_state(self.state)
        self._update_tab_order()

    def _refresh_values(self) -> None:
        values = self.state.values
        for field_id, widget in self._field_widgets.items():
            widget.set_value(values[field_id])
        self._sync_visibility()
        self._render_state()

    def validate_draft(self) -> tuple[FunctionEditorDiagnostic, ...]:
        """Validate all applicable draft fields and focus the first error."""
        diagnostics = self.state.validate()
        self._render_state()
        first = next(
            (
                item
                for item in diagnostics
                if item.severity is FunctionEditorDiagnosticSeverity.ERROR
                and item.field_id is not None
            ),
            None,
        )
        if first is not None and first.field_id is not None:
            self.focus_field(first.field_id)
        self.state_changed.emit(self.state)
        return diagnostics

    def focus_field(self, field_id: str) -> None:
        """Reveal the disclosure tier/section, scroll to and focus one field."""
        definition = self.schema.field(field_id)
        required_level = max(
            definition.disclosure_level,
            self.schema.section_for_field(field_id).disclosure_level,
        )
        if required_level > self.maximum_disclosure:
            index = self.disclosure_selector.findData(required_level)
            self.disclosure_selector.setCurrentIndex(index)
        widget = self._ensure_field(field_id)
        section = self._ensure_section(
            self.schema.section_for_field(field_id).section_id
        )
        section.set_expanded(True)
        self.scroll_area.ensureWidgetVisible(widget, 15, 15)
        widget.focus_editor()

    def reset_field(self, field_id: str) -> None:
        self.state.reset_field(field_id)
        self._refresh_values()
        self.state_changed.emit(self.state)

    def reset_section(self, section_id: str) -> None:
        self.state.reset_section(section_id)
        self._refresh_values()
        self.state_changed.emit(self.state)

    def reset_draft(self) -> None:
        self.state.reset_draft()
        self._refresh_values()
        self.state_changed.emit(self.state)

    def restore_recommended_defaults(self) -> None:
        self.state.restore_recommended_defaults()
        self._refresh_values()
        self.state_changed.emit(self.state)

    def apply_draft(self) -> bool:
        """Apply through the configured callback and refresh all presentation state."""
        accepted = self.state.apply(self._apply_callback)
        if accepted:
            self._refresh_values()
        else:
            self._render_state()
            first = next(
                (item for item in self.state.diagnostics if item.field_id), None
            )
            if first is not None and first.field_id is not None:
                self.focus_field(first.field_id)
        self.state_changed.emit(self.state)
        return accepted

    def collapse_all(self) -> None:
        for widget in self._section_widgets.values():
            if widget.isVisible():
                widget.set_expanded(False)
        self._save_user_state()

    def expand_relevant(self) -> None:
        for section_id, widget in self._section_widgets.items():
            if widget.isVisible():
                widget.expand_relevant(
                    tuple(
                        item
                        for item in self.state.diagnostics
                        if item.section_id == section_id
                    )
                )
        self._save_user_state()

    def _section_expanded(self, _section_id: str, _expanded: bool) -> None:
        self._save_user_state()
        self._update_tab_order()

    def _update_tab_order(self) -> None:
        """Keep keyboard traversal deterministic and exclude hidden/collapsed fields."""
        controls: list[QWidget] = [
            self.summary.help_button,
            self.disclosure_selector,
            self.collapse_all_button,
            self.expand_relevant_button,
            self.defaults_button,
        ]
        for section in self.schema.ordered_sections:
            section_widget = self._section_widgets.get(section.section_id)
            if section_widget is None or section_widget.isHidden():
                continue
            controls.extend(
                (
                    section_widget.toggle,
                    section_widget.reset_button,
                    section_widget.help_button,
                )
            )
            if not section_widget.is_expanded:
                continue
            for field in sorted(
                section.fields, key=lambda item: (item.order, item.field_id)
            ):
                field_widget = self._field_widgets.get(field.field_id)
                if field_widget is None or field_widget.isHidden():
                    continue
                controls.append(field_widget.editor)
                if field_widget.action_button.isVisible():
                    controls.append(field_widget.action_button)
                controls.extend((field_widget.reset_button, field_widget.help_button))
        if not self.diagnostic_view.list.isHidden():
            controls.append(self.diagnostic_view.list)
        controls.extend(
            self.footer.buttons[action]
            for action in self.footer._action_order
            if action in self.footer.buttons
        )
        for previous, following in zip(controls, controls[1:], strict=False):
            QWidget.setTabOrder(previous, following)

    def _save_user_state(self) -> None:
        if self._state_store is None:
            return
        expanded = tuple(
            sorted(
                section_id
                for section_id, widget in self._section_widgets.items()
                if widget.is_expanded
            )
        )
        self._user_state = FunctionEditorUserState(
            disclosure_level=self.maximum_disclosure,
            expanded_sections=expanded,
            has_expansion_state=True,
            help_visible=self.help_panel.isVisible(),
        )
        self._state_store.save(self.schema, self._user_state)

    def _show_editor_help(self) -> None:
        self.help_text.setText(
            "Function Editor tách Basic, Geometry, Tool, Cutting, Levels, "
            "Linking, Advanced và Expert. Preview/Validate không mutation domain; "
            "Calculate chỉ dùng applied state."
        )
        self._set_help_visible(True)

    def _show_section_help(self, section_id: str) -> None:
        section = self.schema.section(section_id)
        self.help_text.setText(
            section.help_text
            or f"{section.title}: {section.summary or 'Không có mô tả bổ sung.'}"
        )
        self._set_help_visible(True)

    def _show_field_help(self, field_id: str) -> None:
        field = self.schema.field(field_id)
        parts = [field.help_text or field.tooltip or field.label]
        if field.unit:
            parts.append(f"Đơn vị: {field.unit}.")
        parts.append(f"Nguồn mặc định: {field.source.value}.")
        self.help_text.setText(" ".join(parts))
        self._set_help_visible(True)

    def _set_help_visible(self, visible: bool) -> None:
        self.help_panel.setVisible(visible)
        if visible:
            self.scroll_area.ensureWidgetVisible(self.help_panel)
        self._save_user_state()

    def _action_requested(self, action_value: str) -> None:
        action = FunctionEditorAction(action_value)
        if action is FunctionEditorAction.RESET_DRAFT:
            self.reset_draft()
        elif action is FunctionEditorAction.VALIDATE:
            self.validate_draft()
        elif action is FunctionEditorAction.APPLY:
            self.apply_draft()
        elif action is FunctionEditorAction.PREVIEW:
            request = self.state.preview_request()
            if self._preview_callback is not None:
                try:
                    result = self._preview_callback(request)
                    if self.state.accepts_preview(request):
                        self.preview_status.setText(
                            str(result) if result is not None else "Preview CURRENT"
                        )
                        self.preview_status.setVisible(True)
                    else:
                        self.preview_status.setText("Preview STALE — đã bỏ kết quả cũ")
                        self.preview_status.setVisible(True)
                except (KeyError, RuntimeError, TypeError, ValueError) as error:
                    self.preview_status.setText(f"Preview lỗi: {error}")
                    self.preview_status.setVisible(True)
            self.preview_requested.emit(request)
        elif action is FunctionEditorAction.CALCULATE:
            try:
                snapshot = self.state.calculation_snapshot()
                if self._calculate_callback is not None:
                    self._calculate_callback(snapshot)
                self.calculate_requested.emit(snapshot)
            except (KeyError, RuntimeError, TypeError, ValueError) as error:
                self.preview_status.setText(f"Calculate lỗi: {error}")
                self.preview_status.setVisible(True)
        elif action is FunctionEditorAction.CLOSE:
            self.request_close()

    def request_close(self) -> bool:
        """Never auto-apply or silently discard a dirty draft."""
        if self.state.is_dirty:
            if self._close_confirmation is not None:
                discard = self._close_confirmation(self.state)
            else:
                discard = (
                    QMessageBox.question(
                        self,
                        "Bản nháp chưa Apply",
                        "Bỏ thay đổi bản nháp và đóng Function Editor?",
                        QMessageBox.StandardButton.Discard
                        | QMessageBox.StandardButton.Cancel,
                        QMessageBox.StandardButton.Cancel,
                    )
                    is QMessageBox.StandardButton.Discard
                )
            if not discard:
                return False
            self.state.reset_draft()
        self.close_requested.emit()
        return True

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.footer.set_compact(event.size().width() < _COMPACT_WIDTH)
        compact = event.size().width() < _COMPACT_WIDTH
        if compact == self._compact:
            return
        self._compact = compact
        for field in self._field_widgets.values():
            field.set_compact(compact)
        self.disclosure_selector.setSizePolicy(
            QSizePolicy.Policy.Expanding if compact else QSizePolicy.Policy.Fixed,
            QSizePolicy.Policy.Fixed,
        )

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        """Allow a 300 px dock; content reflows and scrolls internally."""
        return QSize(240, 300)
