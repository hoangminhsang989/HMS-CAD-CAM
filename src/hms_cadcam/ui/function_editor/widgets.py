"""Composite widgets for the Stage 9A.4 Unified Function Editor."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from PySide6.QtCore import QSize, Qt, QTimer, Signal
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
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from hms_cadcam.ui.localized_dialogs import QMessageBox
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
from hms_cadcam.ui.localization import (
    operation_display_name,
    translate_status,
    ui_text,
)
from hms_cadcam.ui.ui_tokens import (
    CAM_POPUP_DENSITY,
    CAM_RESPONSIVE_GRID,
    CAMPopupMetrics,
)


PreviewCallback = Callable[[FunctionEditorPreviewRequest], object]
CalculateCallback = Callable[[Mapping[str, PresentationValue]], object]
CloseConfirmation = Callable[[FunctionEditorDraftState], bool]
FieldActionCallback = Callable[
    [str, Mapping[str, PresentationValue]], Mapping[str, PresentationValue] | None
]
ToolProfileInteractionCallback = Callable[
    [Mapping[str, PresentationValue], frozenset[str]], object
]

_STATUS_LABELS = {
    FunctionEditorDraftStatus.NO_CHANGES: "Không có thay đổi",
    FunctionEditorDraftStatus.MODIFIED: "Đã sửa",
    FunctionEditorDraftStatus.INVALID: "Không hợp lệ",
    FunctionEditorDraftStatus.APPLYING: "Đang áp dụng",
    FunctionEditorDraftStatus.APPLIED: "Đã áp dụng",
    FunctionEditorDraftStatus.STALE: "Đã lỗi thời",
}


class _FunctionEditorContent(QWidget):
    """Scrollable content whose layout—not a viewport cap—owns its height."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum
        )

class FunctionEditorSummaryWidget(QFrame):
    """Sticky operation summary with text validation counts."""

    help_requested = Signal()

    def __init__(
        self, summary: FunctionEditorSummary, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setObjectName("FunctionEditorSummary")
        self.setAccessibleName("Tóm tắt trình chỉnh sửa chức năng")
        self._root = QVBoxLayout(self)
        self._root.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self._root.setContentsMargins(9, 7, 9, 7)
        self._root.setSpacing(2)
        top = QHBoxLayout()
        self.title = QLabel(operation_display_name(summary.title))
        self.title.setObjectName("FunctionEditorSummaryTitle")
        self.title.setWordWrap(False)
        top.addWidget(self.title, 1)
        self.reference_badge = QLabel("BẢN MẪU THAM CHIẾU")
        self.reference_badge.setObjectName("FunctionEditorReferenceBadge")
        self.reference_badge.setVisible(summary.reference_only)
        top.addWidget(self.reference_badge)
        self.help_button = QToolButton()
        self.help_button.setText("?")
        self.help_button.setAccessibleName("Mở trợ giúp trình chỉnh sửa chức năng")
        self.help_button.setToolTip("Mở bảng trợ giúp ngắn.")
        self.help_button.clicked.connect(self.help_requested)
        top.addWidget(self.help_button)
        self._root.addLayout(top)
        self.context = QLabel()
        self.context.setObjectName("FunctionEditorSummaryContext")
        self.context.setWordWrap(False)
        self.context.setMaximumHeight(self.context.fontMetrics().lineSpacing() * 2 + 2)
        self.context.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed
        )
        self._root.addWidget(self.context)
        status_line = QHBoxLayout()
        self.draft_status = QLabel()
        self.draft_status.setObjectName("FunctionEditorDraftStatus")
        status_line.addWidget(self.draft_status)
        status_line.addStretch(1)
        self.validation = QLabel("0 lỗi · 0 cảnh báo")
        self.validation.setObjectName("FunctionEditorValidationSummary")
        status_line.addWidget(self.validation)
        self._root.addLayout(status_line)
        self._full_title = ""
        self._full_context = ""
        self._full_strategy_line = ""
        self._full_resource_line = ""
        self._compact_context = False
        self.update_summary(summary, FunctionEditorDraftStatus.NO_CHANGES, ())

    def apply_density(self, metrics: CAMPopupMetrics) -> None:
        self._compact_context = (
            metrics.display_scale_factor > 1.0
            or metrics.maximum_height <= 650
        )
        self.context.setVisible(not self._compact_context)
        self._root.setContentsMargins(
            metrics.content_margin,
            metrics.row_spacing,
            metrics.content_margin,
            metrics.row_spacing,
        )
        self._root.setSpacing(metrics.row_spacing)

    def update_summary(
        self,
        summary: FunctionEditorSummary,
        status: FunctionEditorDraftStatus,
        diagnostics: tuple[FunctionEditorDiagnostic, ...],
    ) -> None:
        """Render summary and validation through text as well as color."""
        self._full_title = operation_display_name(summary.title)
        strategy = ui_text(summary.strategy) if summary.strategy else "Không có chiến lược"
        repeated_prefix = f"{self._full_title} · "
        if strategy.startswith(repeated_prefix):
            strategy = strategy[len(repeated_prefix) :]
        self._full_strategy_line = strategy
        self._full_resource_line = (
            f"Tool: {ui_text(summary.tool)} · Hình học: {ui_text(summary.geometry)}"
        )
        self._full_context = (
            f"{self._full_strategy_line} · {self._full_resource_line}"
        )
        self._refresh_elided_text()
        self.reference_badge.setVisible(summary.reference_only)
        self.draft_status.setText(
            f"Đường chạy dao: {translate_status(summary.operation_status)} · "
            f"Bản nháp: {_STATUS_LABELS[status]}"
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
            f"{self._full_title}, {strategy}, {_STATUS_LABELS[status]}, "
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
            "\n".join(
                self.context.fontMetrics().elidedText(
                    line, Qt.TextElideMode.ElideRight, context_width
                )
                for line in (
                    self._full_strategy_line,
                    self._full_resource_line,
                )
                if line
            )
        )
        title_tooltip = self._full_title
        if self._compact_context and self._full_context:
            title_tooltip = f"{title_tooltip}\n{self._full_context}"
        self.title.setToolTip(title_tooltip)
        self.context.setToolTip(self._full_context)
        self.setToolTip(self._full_context if self._compact_context else "")

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._refresh_elided_text()


class FunctionEditorDiagnosticView(QFrame):
    """Compact diagnostic section with focusable targets."""

    focus_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("FunctionEditorDiagnosticView")
        self.setAccessibleName("Chẩn đoán trình chỉnh sửa chức năng")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self._root = QVBoxLayout(self)
        self._root.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self._root.setContentsMargins(7, 5, 7, 7)
        self._root.setSpacing(4)
        header = QHBoxLayout()
        header.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        title = QLabel("CHẨN ĐOÁN")
        title.setObjectName("FunctionEditorSectionTitle")
        header.addWidget(title)
        header.addStretch(1)
        self.summary = QLabel("Không có lỗi hoặc cảnh báo")
        self.summary.setObjectName("FunctionEditorSectionBadge")
        self.summary.setWordWrap(True)
        header.addWidget(self.summary)
        self._root.addLayout(header)
        self.list = QListWidget()
        self.list.setObjectName("FunctionEditorDiagnosticList")
        self.list.setAccessibleName("Danh sách lỗi và cảnh báo")
        self.list.setMaximumHeight(150)
        self.list.setMinimumWidth(0)
        self.list.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        self.list.itemActivated.connect(self._activate)
        self._root.addWidget(self.list)
        self.list.setVisible(False)
        self.setVisible(False)

    def apply_density(self, metrics: CAMPopupMetrics) -> None:
        self._root.setContentsMargins(
            metrics.content_margin,
            metrics.row_spacing,
            metrics.content_margin,
            metrics.row_spacing,
        )
        self._root.setSpacing(metrics.row_spacing)
        self.list.setMaximumHeight(metrics.table_row_height * 5)

    def set_diagnostics(
        self, diagnostics: tuple[FunctionEditorDiagnostic, ...]
    ) -> None:
        """Replace stale validation output; items store stable field IDs only."""
        self.list.clear()
        for diagnostic in diagnostics:
            prefix = {
                FunctionEditorDiagnosticSeverity.ERROR: "● LỖI",
                FunctionEditorDiagnosticSeverity.WARNING: "▲ CẢNH BÁO",
                FunctionEditorDiagnosticSeverity.INFO: "ℹ THÔNG TIN",
            }[diagnostic.severity]
            item = QListWidgetItem(
                f"{prefix} · {diagnostic.code} · {ui_text(diagnostic.message)}"
            )
            item.setData(Qt.ItemDataRole.UserRole, diagnostic.field_id)
            item.setToolTip(
                "Nhấn Enter để chuyển đến trường liên quan"
                if diagnostic.field_id
                else ""
            )
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
        self.setVisible(bool(diagnostics))

    def _activate(self, item: QListWidgetItem) -> None:
        field_id = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(field_id, str):
            self.focus_requested.emit(field_id)


class FunctionEditorFooterWidget(QFrame):
    """Fixed footer that reflows into a compact two-row layout."""

    action_requested = Signal(str)

    _LABELS = {
        FunctionEditorAction.RESET_DRAFT: "Đặt lại bản nháp",
        FunctionEditorAction.PREVIEW: "Xem trước",
        FunctionEditorAction.VALIDATE: "Kiểm tra",
        FunctionEditorAction.CALCULATE: "Tính toán",
        FunctionEditorAction.APPLY: "Áp dụng",
        FunctionEditorAction.SAVE_TOOL_PROFILE: "Lưu cho Tool",
        FunctionEditorAction.CLOSE: "Đóng",
    }

    def __init__(
        self, schema: FunctionEditorSchema, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setObjectName("FunctionEditorFooter")
        self.setAccessibleName("Thao tác trình chỉnh sửa chức năng")
        self._compact = False
        self._calculation_active = False
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

    def apply_density(self, metrics: CAMPopupMetrics) -> None:
        self.layout_grid.setContentsMargins(
            metrics.content_margin,
            metrics.row_spacing,
            metrics.content_margin,
            metrics.row_spacing,
        )
        self.layout_grid.setHorizontalSpacing(metrics.row_spacing)
        self.layout_grid.setVerticalSpacing(metrics.row_spacing)
        self.setMinimumHeight(metrics.footer_height)
        for button in self.buttons.values():
            button.setMinimumHeight(metrics.button_height)

    @staticmethod
    def _tooltip(action: FunctionEditorAction) -> str:
        return {
            FunctionEditorAction.RESET_DRAFT: (
                "Trả toàn bộ bản nháp về bản đã áp dụng gần nhất"
            ),
            FunctionEditorAction.PREVIEW: (
                "Xem trước tạm thời; không áp dụng và không tính đường chạy dao"
            ),
            FunctionEditorAction.VALIDATE: (
                "Kiểm tra toàn bộ bản nháp và chuyển đến lỗi đầu tiên"
            ),
            FunctionEditorAction.CALCULATE: (
                "Chỉ dùng trạng thái đã áp dụng, hiện hành và hợp lệ"
            ),
            FunctionEditorAction.APPLY: "Áp dụng bản nháp hợp lệ bằng một lệnh nguyên tử",
            FunctionEditorAction.SAVE_TOOL_PROFILE: (
                "Xem trước rồi lưu thiết lập cho Tool và chương trình hiện tại"
            ),
            FunctionEditorAction.CLOSE: "Đóng trình chỉnh sửa; bản nháp chưa áp dụng cần xác nhận",
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
        validate = self.buttons.get(FunctionEditorAction.VALIDATE)
        if validate is not None:
            validate.setEnabled(True)
        close = self.buttons.get(FunctionEditorAction.CLOSE)
        if close is not None:
            close.setEnabled(True)
        reset = self.buttons.get(FunctionEditorAction.RESET_DRAFT)
        if reset is not None:
            reset.setEnabled(state.is_dirty)
            reason = (
                self._tooltip(FunctionEditorAction.RESET_DRAFT)
                if state.is_dirty
                else "Bản nháp đang trùng bản đã áp dụng"
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
                    "Không có thay đổi"
                    if not state.is_dirty
                    else "Sửa lỗi bản nháp trước khi áp dụng"
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
                    "Tính toán chỉ dùng trạng thái đã áp dụng, hiện hành và hợp lệ"
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
                else "Sửa lỗi bản nháp trước khi xem trước"
            )
            preview.setToolTip(reason)
            preview.setAccessibleDescription(reason)
        save_profile = self.buttons.get(FunctionEditorAction.SAVE_TOOL_PROFILE)
        if save_profile is not None:
            enabled = state.status not in {
                FunctionEditorDraftStatus.INVALID,
                FunctionEditorDraftStatus.APPLYING,
                FunctionEditorDraftStatus.STALE,
            }
            save_profile.setEnabled(enabled)
            reason = (
                self._tooltip(FunctionEditorAction.SAVE_TOOL_PROFILE)
                if enabled
                else "Sửa lỗi hoặc tải lại nguyên công trước khi lưu cấu hình Tool"
            )
            save_profile.setToolTip(reason)
            save_profile.setAccessibleDescription(reason)
        if self._calculation_active:
            reason = "Đang tính toán; dùng Hủy tính toán để dừng."
            for action, button in self.buttons.items():
                if action is FunctionEditorAction.CLOSE:
                    continue
                button.setEnabled(False)
                button.setToolTip(reason)
                button.setAccessibleDescription(reason)

    def set_calculation_active(
        self, active: bool, state: FunctionEditorDraftState
    ) -> None:
        """Lock conflicting footer actions while one worker request is active."""
        self._calculation_active = active
        self.update_state(state)


class FunctionEditorPage(QWidget):
    """Sticky summary/footer page backed by typed schema and pure draft state."""

    close_requested = Signal()
    preview_requested = Signal(object)
    calculate_requested = Signal(object)
    state_changed = Signal(object)
    calculation_cancel_requested = Signal()
    child_popup_requested = Signal(str, object)

    def __init__(
        self,
        state: FunctionEditorDraftState,
        *,
        state_store: FunctionEditorStateStore | None = None,
        apply_callback: ApplyCallback | None = None,
        preview_callback: PreviewCallback | None = None,
        calculate_callback: CalculateCallback | None = None,
        calculate_task_callback: CalculateCallback | None = None,
        field_action_callback: FieldActionCallback | None = None,
        tool_profile_interaction_callback: (
            ToolProfileInteractionCallback | None
        ) = None,
        close_confirmation: CloseConfirmation | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName(f"FunctionEditorPage_{state.schema.editor_id}")
        self.setAccessibleName(
            "Trình chỉnh sửa chức năng "
            f"{operation_display_name(state.schema.summary.title)}"
        )
        self.state = state
        self.schema = state.schema
        self._state_store = state_store
        self._apply_callback = apply_callback
        self._preview_callback = preview_callback
        self._calculate_callback = calculate_callback
        self._calculate_task_callback = calculate_task_callback
        self._field_action_callback = field_action_callback
        self._tool_profile_interaction_callback = (
            tool_profile_interaction_callback
        )
        self._close_confirmation = close_confirmation
        self._section_widgets: dict[str, FunctionEditorSectionWidget] = {}
        self._field_widgets: dict[str, FunctionEditorFieldWidget] = {}
        self._compact = False
        self._disclosure_compact = False
        self._responsive_grid_columns = 1
        self._content_layout_signature: tuple[object, ...] | None = None
        self._density_metrics = CAM_POPUP_DENSITY.metrics_for(QSize(1600, 900))
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

        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(0, 0, 0, 0)
        self._root.setSpacing(0)
        self._root.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self.summary = FunctionEditorSummaryWidget(self.schema.summary)
        self.summary.help_requested.connect(self._show_editor_help)
        self._root.addWidget(self.summary)
        self.preview_status = QLabel()
        self.preview_status.setObjectName("FunctionEditorPreviewStatus")
        self.preview_status.setAccessibleName("Trạng thái xem trước Trình sửa chức năng")
        self.preview_status.setWordWrap(True)
        self.preview_status.setVisible(False)
        self._root.addWidget(self.preview_status)
        self._root.addWidget(self._disclosure_bar())
        self.illustration_panel = None
        try:
            from hms_cadcam.ui.cam_illustrations import (
                CAMIllustrationPanel,
                CAMIllustrationRegistry,
            )

            illustration = CAMIllustrationRegistry().resolve(self.schema.editor_id)
        except KeyError:
            illustration = None
        if illustration is not None:
            self.illustration_panel = CAMIllustrationPanel(
                illustration, self.state.values
            )
            self.illustration_panel.enlarge_requested.connect(
                lambda value: self.child_popup_requested.emit("illustration", value)
            )
            self.illustration_panel.expanded_changed.connect(
                self._illustration_expanded_changed
            )
        self.parallel_direction_preview = None
        if self.schema.editor_id == "parallel_finishing_production_8a2_3":
            from hms_cadcam.ui.function_editor.parallel_widgets import (
                ParallelDirectionPreviewWidget,
            )

            self.parallel_direction_preview = ParallelDirectionPreviewWidget()
            self.parallel_direction_preview.set_angle(
                self.state.values.get("effective_direction_angle_degrees", 0.0)
            )
            self._root.addWidget(self.parallel_direction_preview)
        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("FunctionEditorContentScroll")
        self.scroll_area.setAccessibleName(
            "Nội dung tham số Trình chỉnh sửa chức năng"
        )
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.scroll_area.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.content = _FunctionEditorContent()
        self.content.setObjectName("FunctionEditorContent")
        self.content_layout = QGridLayout(self.content)
        self.content_layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self.content_layout.setContentsMargins(5, 5, 5, 5)
        self.content_layout.setSpacing(6)
        self.help_panel = QFrame()
        self.help_panel.setObjectName("FunctionEditorHelpPanel")
        help_layout = QVBoxLayout(self.help_panel)
        help_layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        help_header = QHBoxLayout()
        help_title = QLabel("TRỢ GIÚP")
        help_title.setObjectName("FunctionEditorSectionTitle")
        help_header.addWidget(help_title)
        help_header.addStretch(1)
        help_close = QToolButton()
        help_close.setText("×")
        help_close.setAccessibleName("Đóng bảng trợ giúp")
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
        self._root.addWidget(self.scroll_area, 1)
        from hms_cadcam.ui.function_editor.parallel_widgets import (
            ParallelCalculationProgressWidget,
        )

        self.calculation_progress = ParallelCalculationProgressWidget()
        self.calculation_progress.cancel_requested.connect(
            self.calculation_cancel_requested
        )
        self._root.addWidget(self.calculation_progress)
        self.footer = FunctionEditorFooterWidget(self.schema)
        self.footer.action_requested.connect(self._action_requested)
        self._root.addWidget(self.footer)
        self.apply_compact_density(self._density_metrics)
        self._sync_visibility()
        self._render_state()

    def set_calculation_active(self, active: bool) -> None:
        """Expose a non-modal worker state without changing draft values."""
        self.calculation_progress.set_active(active)
        self.footer.set_calculation_active(active, self.state)

    def _illustration_expanded_changed(self, expanded: bool) -> None:
        if (
            expanded
            and self.illustration_panel is not None
            and self.height() < self._density_metrics.illustration_auto_collapse_height
        ):
            self.illustration_panel.set_expanded(False, automatic=True)

    def update_calculation_progress(self, value: object) -> None:
        """Forward one typed CAM 3D finishing progress event to the status row."""
        from hms_cadcam.cam.cam3d.parallel import ParallelProgress
        from hms_cadcam.cam.cam3d.zlevel import ZLevelProgress
        from hms_cadcam.cam.optimization import CamCalculationProgress

        if isinstance(value, (ParallelProgress, ZLevelProgress, CamCalculationProgress)):
            self.calculation_progress.update_progress(value)

    def _disclosure_bar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("FunctionEditorDisclosureBar")
        layout = QGridLayout(bar)
        self._disclosure_layout = layout
        layout.setContentsMargins(7, 4, 7, 4)
        layout.setSpacing(5)
        self.disclosure_label = QLabel("Mức hiển thị")
        self.disclosure_label.setAccessibleName("Mức tham số tối đa")
        layout.addWidget(self.disclosure_label, 0, 0)
        self.disclosure_selector = QComboBox()
        self.disclosure_selector.setObjectName("FunctionEditorDisclosureSelector")
        self.disclosure_selector.setAccessibleName(
            "Chọn mức Cơ bản, Nâng cao hoặc Chuyên gia"
        )
        for level, text in (
            (ParameterDisclosureLevel.BASIC, "Basic"),
            (ParameterDisclosureLevel.ADVANCED, "Advanced"),
            (ParameterDisclosureLevel.EXPERT, "Expert"),
        ):
            if level <= self._highest_disclosure:
                self.disclosure_selector.addItem(ui_text(text), level)
        self.disclosure_selector.setCurrentIndex(
            self.disclosure_selector.findData(self.maximum_disclosure)
        )
        self.disclosure_selector.currentIndexChanged.connect(
            self._disclosure_changed
        )
        layout.addWidget(self.disclosure_selector, 0, 1, 1, 2)
        self.collapse_all_button = QToolButton()
        self.collapse_all_button.setText("Thu gọn tất cả")
        self.collapse_all_button.setAccessibleName("Thu gọn tất cả phần")
        self.collapse_all_button.clicked.connect(self.collapse_all)
        layout.addWidget(self.collapse_all_button, 1, 0)
        self.expand_relevant_button = QToolButton()
        self.expand_relevant_button.setText("Mở phần liên quan")
        self.expand_relevant_button.setAccessibleName("Mở phần liên quan")
        self.expand_relevant_button.clicked.connect(self.expand_relevant)
        layout.addWidget(self.expand_relevant_button, 1, 1)
        self.defaults_button = QToolButton()
        self.defaults_button.setText("Giá trị mặc định")
        self.defaults_button.setAccessibleName("Khôi phục giá trị mặc định khuyến nghị")
        self.defaults_button.setToolTip(
            "Nạp giá trị mặc định có nguồn vào bản nháp; không tự áp dụng"
        )
        self.defaults_button.clicked.connect(self.restore_recommended_defaults)
        layout.addWidget(self.defaults_button, 1, 2)
        layout.setColumnStretch(0, 1)
        layout.setColumnStretch(1, 1)
        layout.setColumnStretch(2, 1)
        return bar

    def _set_disclosure_compact(self, compact: bool) -> None:
        """Use one logical row at scaled work areas that remain wide enough."""
        if compact == self._disclosure_compact:
            return
        self._disclosure_compact = compact
        widgets = (
            self.disclosure_label,
            self.disclosure_selector,
            self.collapse_all_button,
            self.expand_relevant_button,
            self.defaults_button,
        )
        for widget in widgets:
            self._disclosure_layout.removeWidget(widget)
        for column in range(4):
            self._disclosure_layout.setColumnStretch(column, 0)
            self._disclosure_layout.setColumnMinimumWidth(column, 0)
        self.disclosure_label.setVisible(not compact)
        if compact:
            self._disclosure_layout.addWidget(
                self.disclosure_selector, 0, 0
            )
            self._disclosure_layout.addWidget(
                self.collapse_all_button, 0, 1
            )
            self._disclosure_layout.addWidget(
                self.expand_relevant_button, 0, 2
            )
            self._disclosure_layout.addWidget(self.defaults_button, 0, 3)
            self._disclosure_layout.setColumnStretch(0, 1)
        else:
            self._disclosure_layout.addWidget(self.disclosure_label, 0, 0)
            self._disclosure_layout.addWidget(
                self.disclosure_selector, 0, 1, 1, 2
            )
            self._disclosure_layout.addWidget(
                self.collapse_all_button, 1, 0
            )
            self._disclosure_layout.addWidget(
                self.expand_relevant_button, 1, 1
            )
            self._disclosure_layout.addWidget(self.defaults_button, 1, 2)
            for column in range(3):
                self._disclosure_layout.setColumnStretch(column, 1)
        self.disclosure_selector.setAccessibleDescription(
            "Mức hiển thị"
            if compact
            else ""
        )
        self.updateGeometry()

    def _disclosure_changed(self, index: int) -> None:
        value = self.disclosure_selector.itemData(index)
        if not isinstance(value, ParameterDisclosureLevel):
            return
        self.maximum_disclosure = value
        self.scroll_area.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self._sync_visibility()
        self._save_user_state()

    def _ensure_section(self, section_id: str) -> FunctionEditorSectionWidget:
        existing = self._section_widgets.get(section_id)
        if existing is not None:
            return existing
        definition = self.schema.section(section_id)
        widget = FunctionEditorSectionWidget(definition)
        widget.apply_density(self._density_metrics)
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
        widget.apply_density(self._density_metrics)
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
        if self.parallel_direction_preview is not None:
            self.parallel_direction_preview.setVisible(
                self.maximum_disclosure >= ParameterDisclosureLevel.ADVANCED
            )
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
            and not self._section_widgets[section.section_id].isHidden()
        )
        signature: tuple[object, ...] = (
            section_order,
            self._responsive_grid_columns,
            self.maximum_disclosure,
        )
        if signature == self._content_layout_signature:
            return
        self._content_layout_signature = signature
        previous_columns = max(2, self.content_layout.columnCount())
        previous_rows = self.content_layout.rowCount()
        while self.content_layout.count():
            self.content_layout.takeAt(0)
        for column in range(previous_columns):
            self.content_layout.setColumnStretch(column, 0)
            self.content_layout.setColumnMinimumWidth(column, 0)
        for row in range(previous_rows):
            self.content_layout.setRowStretch(row, 0)
            self.content_layout.setRowMinimumHeight(row, 0)

        columns = self._responsive_grid_columns
        basic_with_illustration = (
            self.maximum_disclosure is ParameterDisclosureLevel.BASIC
            and self.illustration_panel is not None
        )
        two_column_basic = (
            basic_with_illustration and columns == 2
        )
        compact_basic = (
            basic_with_illustration
            and (
                two_column_basic
                or self.schema.editor_id
                in {
                    "parallel_finishing_production_8a2_3",
                    "z_level_finishing_production_8a3_3",
                }
            )
        )
        for section_id, widget in self._section_widgets.items():
            widget.set_one_screen_compact(compact_basic and not widget.isHidden())
            widget.set_field_columns(
                2 if two_column_basic and section_id == "automatic_summary" else 1
            )
        for field_id, field in self._field_widgets.items():
            field.set_one_screen_compact(compact_basic and not field.isHidden())
            section_id = self.schema.section_for_field(field_id).section_id
            if (
                compact_basic
                and section_id == "automatic_summary"
                and not field.isHidden()
            ):
                field.set_compact(True)
            elif (
                two_column_basic
                and section_id in {"geometry", "tool", "quality"}
                and not field.isHidden()
            ):
                field.set_compact(True)

        row = 0
        placed: set[str] = set()
        if (
            columns == 2
            and self.schema.editor_id
            in {
                "parallel_finishing_production_8a2_3",
                "z_level_finishing_production_8a3_3",
            }
        ):
            row = self._place_cam3d_finishing_grid(section_order)
            placed.update(
                section_id
                for section_id in (
                    "geometry",
                    "tool",
                    "quality",
                    "automatic_summary",
                )
                if section_id in section_order
            )
        elif columns == 2 and self.illustration_panel is not None:
            first = section_order[0] if section_order else None
            if first is not None:
                self.content_layout.addWidget(self._section_widgets[first], 0, 0)
                placed.add(first)
            self.content_layout.addWidget(self.illustration_panel, 0, 1)
            row = 1
        elif self.illustration_panel is not None:
            self.content_layout.addWidget(self.illustration_panel, row, 0)
            row += 1

        remaining = [item for item in section_order if item not in placed]
        for index, section_id in enumerate(remaining):
            target_row = row + index // columns
            target_column = index % columns
            self.content_layout.addWidget(
                self._section_widgets[section_id], target_row, target_column
            )
        if remaining:
            row += (len(remaining) + columns - 1) // columns
        self.content_layout.addWidget(self.help_panel, row, 0, 1, columns)
        self.content_layout.addWidget(self.diagnostic_view, row + 1, 0, 1, columns)
        self.content_layout.setRowStretch(row + 2, 1)
        for column in range(columns):
            self.content_layout.setColumnStretch(column, 1)
        QTimer.singleShot(0, self, self._sync_content_overflow)

    def _place_cam3d_finishing_grid(
        self, section_order: tuple[str, ...]
    ) -> int:
        """Place the CAM 3D finishing Basic scan path in compact rows."""
        positions = {
            "geometry": (0, 0, 1, 1),
            "tool": (0, 1, 1, 1),
            "quality": (1, 0, 1, 1),
            "automatic_summary": (2, 0, 1, 2),
        }
        for section_id, position in positions.items():
            if section_id in section_order:
                self.content_layout.addWidget(
                    self._section_widgets[section_id], *position
                )
        if self.illustration_panel is not None:
            self.content_layout.addWidget(self.illustration_panel, 1, 1)
        return 3

    @property
    def responsive_grid_columns(self) -> int:
        """Current number of content columns for deterministic GUI tests."""
        return self._responsive_grid_columns

    @property
    def basic_uses_vertical_scroll(self) -> bool:
        """Report the actual Basic scrolling contract."""
        return (
            self.maximum_disclosure is ParameterDisclosureLevel.BASIC
            and self.scroll_area.verticalScrollBar().maximum() > 0
        )

    def _update_responsive_grid(self, content_width: int) -> None:
        # Prime narrow fields before measuring section minima.  A newly added
        # provenance/action row can otherwise report its desktop minimum before
        # its resize event, forcing horizontal scrolling at the supported 300px
        # compact width.
        if min(content_width, self.width()) < 520:
            for field in self._field_widgets.values():
                if not field.isHidden():
                    field.set_compact(True)
            QTimer.singleShot(0, self, self._force_narrow_field_compact)
        visible_hints = [
            widget.minimumSizeHint().width()
            for widget in self._section_widgets.values()
            if not widget.isHidden()
        ]
        size_hint = max(visible_hints, default=0)
        columns = CAM_RESPONSIVE_GRID.columns_for(
            content_width,
            self._density_metrics,
            minimum_size_hint=size_hint,
        )
        self.scroll_area.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        if columns == self._responsive_grid_columns:
            QTimer.singleShot(0, self, self._sync_content_overflow)
            return
        self._responsive_grid_columns = columns
        self._content_layout_signature = None
        self._rebuild_content_order()
        QTimer.singleShot(0, self, self._sync_content_overflow)

    def _force_narrow_field_compact(self) -> None:
        """Keep fields compact after the parent layout assigns a desktop width."""
        if self.width() >= 520:
            return
        for field in self._field_widgets.values():
            if not field.isHidden():
                field.set_compact(True, force=True)
        self.content.updateGeometry()
        self.scroll_area.updateGeometry()

    def _sync_content_overflow(self) -> None:
        """Let QScrollArea expose real overflow instead of shrinking section bodies."""
        self.content_layout.invalidate()
        self.content.updateGeometry()
        self.scroll_area.updateGeometry()

    def _field_changed(self, field_id: str, value: object) -> None:
        self.state.edit(field_id, value)  # type: ignore[arg-type]
        semantic_focus = "ordering"
        if field_id in {
            "linking_mode",
            "clearance_z_mm",
            "retract_z_mm",
            "link_clearance_mm",
        }:
            semantic_focus = "linking"
        elif field_id in {
            "quality_profile",
            "stepover_override_enabled",
            "stepdown_override_enabled",
            "tolerance_override_enabled",
            "allowance_override_enabled",
        }:
            semantic_focus = "quality"
        # A draft transform can update several read-only fields (stepdown,
        # level count, tolerance, effective hash) from one combo edit.  Render
        # the transformed state back into every materialized field widget so
        # the visible summary cannot lag behind the model.
        values = self.state.values
        for materialized_id, widget in self._field_widgets.items():
            widget.set_value(values[materialized_id])
        if self.illustration_panel is not None:
            self.illustration_panel.set_values(
                values,
                semantic_focus=semantic_focus,
            )
        if (
            field_id in {"direction_angle_degrees", "direction_override_enabled"}
            and self.parallel_direction_preview is not None
        ):
            self.parallel_direction_preview.set_angle(
                self.state.values.get("effective_direction_angle_degrees", 0.0)
            )
        self._sync_visibility()
        self._render_state()
        self.state_changed.emit(self.state)

    def _field_action_requested(self, field_id: str, action_id: str) -> None:
        """Run a typed selection action and merge only returned primitives."""
        if action_id == "open_tool_selector":
            definition = self.schema.field(field_id)
            labels = dict(definition.choice_labels)
            widget = self._field_widgets[field_id]
            self.child_popup_requested.emit(
                "tool_selector",
                {
                    "choices": tuple(
                        (choice, ui_text(labels.get(choice, str(choice))))
                        for choice in definition.choices
                    ),
                    "current": self.state.values[field_id],
                    "accept": lambda value: self._field_changed(field_id, value),
                    "focus": widget.editor,
                },
            )
            return
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
        if self.illustration_panel is not None:
            self.illustration_panel.set_values(values)
        if self.parallel_direction_preview is not None:
            self.parallel_direction_preview.set_angle(
                values.get("effective_direction_angle_degrees", 0.0)
            )
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
            # focus_field may materialize an Advanced-only row after the first
            # render pass; render again so its inline diagnostic is not lost.
            self._render_state()
            self._field_widgets[first.field_id].focus_editor()
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
        self._reveal_field_widget(widget)
        QTimer.singleShot(0, self, lambda: self._reveal_field_widget(widget))

    def _reveal_field_widget(self, widget: FunctionEditorFieldWidget) -> None:
        """Finish field reveal after disclosure and section layouts have settled."""
        if widget.isHidden():
            return
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
        if self.illustration_panel is not None:
            controls.extend(
                (
                    self.illustration_panel.expand_button,
                    self.illustration_panel.enlarge_button,
                )
            )
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
            "Trình chỉnh sửa chức năng tách các phần Cơ bản, Hình học, Tool, "
            "Thông số cắt, Cao độ, Liên kết, Nâng cao và Chuyên gia. "
            "Xem trước/Kiểm tra hợp lệ không thay đổi miền; Tính toán chỉ dùng "
            "trạng thái đã áp dụng."
        )
        self._set_help_visible(True)

    def _show_section_help(self, section_id: str) -> None:
        section = self.schema.section(section_id)
        self.help_text.setText(
            ui_text(section.help_text)
            or f"{ui_text(section.title)}: "
            f"{ui_text(section.summary) or 'Không có mô tả bổ sung.'}"
        )
        self._set_help_visible(True)

    def _show_field_help(self, field_id: str) -> None:
        field = self.schema.field(field_id)
        parts = [ui_text(field.help_text or field.tooltip or field.label)]
        if field.unit:
            parts.append(f"Đơn vị: {field.unit}.")
        parts.append(f"Nguồn mặc định: {ui_text(field.source.value)}.")
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
                            ui_text(str(result))
                            if result is not None
                            else "Bản xem trước HIỆN HÀNH"
                        )
                        self.preview_status.setVisible(True)
                    else:
                        self.preview_status.setText(
                            "Bản xem trước ĐÃ LỖI THỜI — đã bỏ kết quả cũ"
                        )
                        self.preview_status.setVisible(True)
                except (KeyError, RuntimeError, TypeError, ValueError) as error:
                    self.preview_status.setText(f"Lỗi xem trước: {ui_text(error)}")
                    self.preview_status.setVisible(True)
            self.preview_requested.emit(request)
        elif action is FunctionEditorAction.CALCULATE:
            try:
                snapshot = self.state.calculation_snapshot()
                callback = self._calculate_task_callback or self._calculate_callback
                if callback is not None:
                    callback(snapshot)
                self.calculate_requested.emit(snapshot)
            except (KeyError, RuntimeError, TypeError, ValueError) as error:
                self.preview_status.setText(f"Lỗi tính toán: {ui_text(error)}")
                self.preview_status.setVisible(True)
        elif action is FunctionEditorAction.SAVE_TOOL_PROFILE:
            if self._tool_profile_interaction_callback is None:
                self.preview_status.setText(
                    "Chương trình hiện tại chưa hỗ trợ lưu cấu hình Tool."
                )
                self.preview_status.setVisible(True)
                return
            try:
                changed = frozenset(
                    key
                    for key, value in self.state.values.items()
                    if self.state.applied_values.get(key) != value
                )
                interaction = self._tool_profile_interaction_callback(
                    self.state.values, changed
                )
                self.child_popup_requested.emit(
                    "tool_profile_save", interaction
                )
            except (KeyError, RuntimeError, TypeError, ValueError) as error:
                self.preview_status.setText(
                    f"Không thể chuẩn bị cấu hình Tool: {ui_text(error)}"
                )
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
                        "Bản nháp chưa áp dụng",
                        "Bỏ thay đổi bản nháp và đóng Trình sửa chức năng?",
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
        self._update_responsive_grid(max(1, event.size().width() - 2))
        self._set_disclosure_compact(event.size().width() >= 520)
        compact = event.size().width() < self._density_metrics.field_reflow_width
        self.footer.set_compact(compact)
        if (
            self.illustration_panel is not None
            and event.size().height()
            < self._density_metrics.illustration_auto_collapse_height
        ):
            self.illustration_panel.set_expanded(False, automatic=True)
        if compact == self._compact:
            return
        self._compact = compact
        self.disclosure_selector.setSizePolicy(
            QSizePolicy.Policy.Expanding if compact else QSizePolicy.Policy.Fixed,
            QSizePolicy.Policy.Fixed,
        )

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        """Allow a 300 px dock; content reflows and scrolls internally."""
        return QSize(240, 300)

    def apply_compact_density(self, metrics: CAMPopupMetrics) -> None:
        """Apply one shared density contract to this editor and all lazy rows."""
        self._density_metrics = metrics
        self._set_disclosure_compact(
            max(self.width(), metrics.popup_width) >= 520
        )
        self.summary.apply_density(metrics)
        self._disclosure_layout.setContentsMargins(
            metrics.content_margin,
            metrics.row_spacing,
            metrics.content_margin,
            metrics.row_spacing,
        )
        self._disclosure_layout.setHorizontalSpacing(metrics.label_spacing)
        self._disclosure_layout.setVerticalSpacing(metrics.row_spacing)
        self.content_layout.setContentsMargins(
            metrics.content_margin,
            metrics.row_spacing,
            metrics.content_margin,
            metrics.row_spacing,
        )
        self.content_layout.setSpacing(metrics.section_spacing)
        self.content_layout.setHorizontalSpacing(metrics.grid_gap)
        self.content_layout.setVerticalSpacing(metrics.grid_gap)
        self.summary.apply_density(metrics)
        self.diagnostic_view.apply_density(metrics)
        self.footer.apply_density(metrics)
        for section in self._section_widgets.values():
            section.apply_density(metrics)
        for field in self._field_widgets.values():
            field.apply_density(metrics)
        if self.illustration_panel is not None:
            self.illustration_panel.apply_density(metrics)
        apply_progress_density = getattr(
            self.calculation_progress, "apply_density", None
        )
        if callable(apply_progress_density):
            apply_progress_density(metrics)
        self._content_layout_signature = None
        self._update_responsive_grid(
            max(1, self.scroll_area.viewport().width() or self.width())
        )
        self._rebuild_content_order()

    def preferred_popup_height(self, metrics: CAMPopupMetrics) -> int:
        """Let sparse editors open shorter while dense editors use the full target."""
        values = dict(self.state.values)
        visible_sections = self.schema.visible_sections(
            values, self.maximum_disclosure
        )
        field_count = sum(
            len(self.schema.visible_fields(section.section_id, values, self.maximum_disclosure))
            for section in visible_sections
        )
        if len(visible_sections) <= 2 and field_count <= 4:
            return min(metrics.popup_height, 540)
        if len(visible_sections) <= 3 and field_count <= 7:
            return min(metrics.popup_height, 590)
        return metrics.popup_height
