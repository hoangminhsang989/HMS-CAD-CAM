"""Right-dock host, registry boundary and lifecycle cleanup for Function Editors."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import logging

from PySide6.QtCore import QSettings, QSize, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QStackedWidget,
    QToolButton,
    QTreeWidget,
    QVBoxLayout,
    QWidget,
)

from hms_cadcam.ui.localized_dialogs import QMessageBox
from hms_cadcam.ui.function_editor.legacy_adapter import (
    LegacyFunctionEditorAdapter,
)
from hms_cadcam.ui.function_editor.model import PresentationValue
from hms_cadcam.ui.function_editor.production import FunctionEditorProductionSession
from hms_cadcam.ui.function_editor.reference import build_contour_reference_schema
from hms_cadcam.ui.function_editor.schema import (
    FunctionEditorRegistry,
    FunctionEditorSchema,
)
from hms_cadcam.ui.function_editor.state import (
    FunctionEditorDraftState,
    FunctionEditorStateStore,
)
from hms_cadcam.ui.function_editor.widgets import FunctionEditorPage
from hms_cadcam.ui.localization import ui_text
from hms_cadcam.ui.ui_tokens import CAMPopupMetrics


logger = logging.getLogger(__name__)


class FunctionEditorHost(QWidget):
    """Hosts one framework page or one legacy adapter—never nested editors."""

    collapse_requested = Signal()
    editor_replaced = Signal(str)
    calculation_cancel_requested = Signal()
    child_popup_requested = Signal(str, object)

    def __init__(
        self,
        editor: QWidget,
        tree: QTreeWidget,
        apply_callback: Callable[[], None],
        parent: QWidget | None = None,
        *,
        settings: QSettings | None = None,
        production_provider: Callable[[], FunctionEditorProductionSession | None]
        | None = None,
        selection_restore: Callable[[str, str], bool] | None = None,
        selection_exists: Callable[[tuple[str, str]], bool] | None = None,
        switch_confirmation: Callable[[FunctionEditorDraftState], str] | None = None,
        fallback_callback: Callable[[str], None] | None = None,
        follow_selection: bool = True,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("FunctionEditorHost")
        self.setAccessibleName("Trình chỉnh sửa chức năng")
        self.editor = editor
        self._tree = tree
        self.registry = FunctionEditorRegistry()
        self._state_store = (
            FunctionEditorStateStore(settings) if settings is not None else None
        )
        self._active_page: FunctionEditorPage | None = None
        self._selection_guard = False
        self._production_provider = production_provider
        self._selection_restore = selection_restore
        self._selection_exists = selection_exists
        self._switch_confirmation = switch_confirmation
        self._fallback_callback = fallback_callback
        self._follow_selection = follow_selection
        self._active_session: FunctionEditorProductionSession | None = None
        self._popup_metrics: CAMPopupMetrics | None = None

        root = QVBoxLayout(self)
        root.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._header())
        self.stack = QStackedWidget()
        self.stack.setObjectName("FunctionEditorStack")
        self.stack.setMinimumWidth(0)
        self.legacy_adapter = LegacyFunctionEditorAdapter(
            editor, tree, apply_callback
        )
        self.legacy_adapter.close_requested.connect(self.collapse_requested)
        self.stack.addWidget(self.legacy_adapter)
        root.addWidget(self.stack, 1)

        # Compatibility aliases retained for Stage 9A.2 callers/tests.
        self.scroll_area = self.legacy_adapter.scroll_area
        self.selection_summary = self.legacy_adapter.selection_summary
        self.state_summary = self.legacy_adapter.state_summary
        self.apply_button = self.legacy_adapter.apply_button
        self.close_button = self.legacy_adapter.close_button

        if follow_selection:
            tree.itemSelectionChanged.connect(self._selection_changed)
            tree.model().modelReset.connect(self._selection_changed)
        self.show_legacy_editor()
        if follow_selection and self._production_provider is not None:
            self._selection_changed()

    def _header(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("PanelHeader")
        layout = QHBoxLayout(frame)
        self._header_layout = layout
        layout.setContentsMargins(8, 5, 5, 5)
        label = QLabel("Trình chỉnh sửa chức năng")
        label.setObjectName("PanelTitle")
        layout.addWidget(label)
        layout.addStretch(1)
        self.mode_label = QLabel("TRÌNH CŨ")
        self.mode_label.setObjectName("FunctionEditorHostMode")
        layout.addWidget(self.mode_label)
        self.collapse_button = QToolButton()
        self.collapse_button.setText("×")
        self.collapse_button.setAccessibleName("Thu gọn trình chỉnh sửa chức năng")
        self.collapse_button.setToolTip("Thu gọn trình chỉnh sửa chức năng")
        self.collapse_button.setAutoRaise(True)
        self.collapse_button.clicked.connect(self.collapse_requested)
        layout.addWidget(self.collapse_button)
        return frame

    @property
    def active_page(self) -> FunctionEditorPage | None:
        return self._active_page

    @property
    def active_session(self) -> FunctionEditorProductionSession | None:
        """Return the immutable production binding currently shown by the host."""
        return self._active_session

    @property
    def current_mode(self) -> str:
        return "framework" if self._active_page is not None else "legacy"

    def show_legacy_editor(self) -> None:
        """Return to the unchanged production editor and clean old callbacks."""
        self._dispose_active_page()
        self._active_session = None
        self.legacy_adapter.selection_changed()
        self.stack.setCurrentWidget(self.legacy_adapter)
        self.mode_label.setText("TRÌNH CŨ")
        self.editor_replaced.emit("legacy")

    def show_schema(
        self,
        schema: FunctionEditorSchema,
        applied_values: Mapping[str, PresentationValue] | None = None,
        *,
        project_key: str = "reference-project",
        operation_key: str = "reference-operation",
        generation: int = 0,
        apply_callback: Callable[[Mapping[str, PresentationValue]], object]
        | None = None,
        preview_callback: Callable[[object], object] | None = None,
        calculate_callback: Callable[[Mapping[str, PresentationValue]], object]
        | None = None,
        validation_callback: Callable[[Mapping[str, PresentationValue]], tuple]
        | None = None,
        draft_transform_callback: Callable[
            [Mapping[str, PresentationValue]], Mapping[str, PresentationValue]
        ] | None = None,
        field_action_callback: Callable[
            [str, Mapping[str, PresentationValue]], Mapping[str, PresentationValue] | None
        ] | None = None,
        tool_profile_interaction_callback: Callable[
            [Mapping[str, PresentationValue], frozenset[str]], object
        ] | None = None,
        close_confirmation: Callable[[FunctionEditorDraftState], bool]
        | None = None,
    ) -> FunctionEditorPage:
        """Replace the current page using presentation primitives only."""
        self._dispose_active_page()
        state = FunctionEditorDraftState(
            schema,
            applied_values,
            project_key=project_key,
            operation_key=operation_key,
            generation=generation,
            validation_callback=validation_callback,  # type: ignore[arg-type]
            draft_transform_callback=draft_transform_callback,
        )
        page = FunctionEditorPage(
            state,
            state_store=self._state_store,
            apply_callback=apply_callback,
            preview_callback=preview_callback,  # type: ignore[arg-type]
            calculate_callback=calculate_callback,
            field_action_callback=field_action_callback,
            tool_profile_interaction_callback=tool_profile_interaction_callback,
            close_confirmation=close_confirmation,
        )
        page.close_requested.connect(self._framework_close_requested)
        page.child_popup_requested.connect(self.child_popup_requested)
        page.calculation_cancel_requested.connect(
            self.calculation_cancel_requested
        )
        self.stack.addWidget(page)
        self.stack.setCurrentWidget(page)
        self._active_page = page
        if self._popup_metrics is not None:
            page.apply_compact_density(self._popup_metrics)
        self.mode_label.setText(
            "THAM CHIẾU" if schema.summary.reference_only else "KHUNG MỚI"
        )
        self.editor_replaced.emit(schema.editor_id)
        return page

    def show_production_session(
        self, session: FunctionEditorProductionSession
    ) -> FunctionEditorPage:
        """Open one migrated operation session over its immutable snapshot."""
        page = self.show_schema(
            session.schema,
            session.applied_mapping(),
            project_key=session.project_key,
            operation_key=session.operation_key,
            generation=session.generation,
            apply_callback=session.apply_callback,
            preview_callback=session.preview_callback,
            calculate_callback=session.calculate_callback,
            validation_callback=session.validation_callback,
            draft_transform_callback=session.draft_transform_callback,
            field_action_callback=session.field_action_callback,
            tool_profile_interaction_callback=(
                session.tool_profile_interaction_callback
            ),
            close_confirmation=self._confirm_close,
        )
        self._active_session = session
        return page

    def show_reference_editor(
        self,
        *,
        close_confirmation: Callable[[FunctionEditorDraftState], bool]
        | None = None,
    ) -> FunctionEditorPage:
        """Open the safe Contour reference page; it is never production default."""
        return self.show_schema(
            build_contour_reference_schema(),
            close_confirmation=close_confirmation,
        )

    def _framework_close_requested(self) -> None:
        self.show_legacy_editor()
        self.collapse_requested.emit()

    def _selection_changed(self) -> None:
        self.open_current_selection()

    def open_current_selection(self) -> bool:
        """Explicitly open the selected operation; selection alone need not rebind."""
        if self._selection_guard:
            return False
        if self._production_provider is None:
            try:
                self.show_legacy_editor()
            except RuntimeError:
                return False
            return True
        try:
            session = self._production_provider()
        except (KeyError, RuntimeError, TypeError, ValueError) as error:
            self._show_fallback(str(error) or "Không thể tải production schema.")
            return False
        active = self._active_session
        page = self._active_page
        if session is None:
            return False
        if (
            active is not None
            and page is not None
            and active.selection_key == session.selection_key
            and active.project_key == session.project_key
        ):
            return True
        if active is not None and page is not None and page.state.is_dirty:
            still_exists = (
                self._selection_exists(active.selection_key)
                if self._selection_exists is not None
                else True
            )
            if still_exists:
                decision = self._confirm_switch(page.state)
                if decision == "apply":
                    desired_selection = session.selection_key
                    if not page.apply_draft():
                        self._restore_selection(active.selection_key)
                        return False
                    self._restore_selection(desired_selection)
                    try:
                        session = self._production_provider()
                    except (KeyError, RuntimeError, TypeError, ValueError) as error:
                        self._restore_selection(active.selection_key)
                        self._show_fallback(str(error) or "Không thể tải nguyên công đích.")
                        return False
                    if session is None or session.selection_key != desired_selection:
                        self._restore_selection(active.selection_key)
                        return False
                elif decision == "continue":
                    self._restore_selection(active.selection_key)
                    return False
                elif decision != "discard":
                    self._restore_selection(active.selection_key)
                    return False
        try:
            self.show_production_session(session)
        except (KeyError, RuntimeError, TypeError, ValueError) as error:
            self._show_fallback(str(error) or "Production schema không hợp lệ.")
            return False
        return True

    def _confirm_switch(self, state: FunctionEditorDraftState) -> str:
        if self._switch_confirmation is not None:
            return self._switch_confirmation(state)
        box = QMessageBox(self)
        box.setWindowTitle("Bản nháp chưa áp dụng")
        box.setText("Nguyên công hiện tại có thay đổi chưa áp dụng.")
        box.setInformativeText(
            "Chọn cách xử lý bản nháp trước khi mở nguyên công khác."
        )
        box.setIcon(QMessageBox.Icon.Warning)
        apply_button = box.addButton(
            "Áp dụng và chuyển", QMessageBox.ButtonRole.AcceptRole
        )
        discard_button = box.addButton(
            "Bỏ thay đổi và chuyển", QMessageBox.ButtonRole.DestructiveRole
        )
        continue_button = box.addButton(
            "Tiếp tục chỉnh sửa", QMessageBox.ButtonRole.RejectRole
        )
        box.setDefaultButton(continue_button)
        box.exec()
        selected = box.clickedButton()
        if selected is apply_button:
            return "apply"
        if selected is discard_button:
            return "discard"
        return "continue"

    def _confirm_close(self, _state: FunctionEditorDraftState) -> bool:
        box = QMessageBox(self)
        box.setWindowTitle("Bản nháp chưa áp dụng")
        box.setText("Nguyên công hiện tại có thay đổi chưa áp dụng.")
        box.setInformativeText("Bỏ bản nháp hoặc tiếp tục chỉnh sửa nguyên công.")
        box.setIcon(QMessageBox.Icon.Warning)
        discard = box.addButton(
            "Bỏ thay đổi và đóng", QMessageBox.ButtonRole.DestructiveRole
        )
        keep = box.addButton(
            "Tiếp tục chỉnh sửa", QMessageBox.ButtonRole.RejectRole
        )
        box.setDefaultButton(keep)
        box.exec()
        return box.clickedButton() is discard

    def _restore_selection(self, selection_key: tuple[str, str]) -> None:
        if self._selection_restore is None:
            return
        self._selection_guard = True
        try:
            self._selection_restore(*selection_key)
        finally:
            self._selection_guard = False

    def _show_fallback(self, message: str) -> None:
        logger.error("Function Editor production fallback: %s", message)
        try:
            self.show_legacy_editor()
            self.mode_label.setText("DỰ PHÒNG")
            self.legacy_adapter.state_summary.setText(
                f"Lỗi lược đồ sản xuất · dùng trình cũ dự phòng · {ui_text(message)}"
            )
        except RuntimeError:
            return
        if self._fallback_callback is not None:
            self._fallback_callback(message)

    def refresh_current(self) -> None:
        """Refresh a clean migrated page after one domain status change."""
        if self._production_provider is None:
            return
        active = self._active_session
        page = self._active_page
        if active is None or page is None or page.state.is_dirty:
            return
        if self._selection_exists is not None and not self._selection_exists(
            active.selection_key
        ):
            self.invalidate_current_session()
            self.collapse_requested.emit()
            return
        try:
            session = self._production_provider()
            if (
                session is not None
                and session.selection_key == active.selection_key
                and session.project_key == active.project_key
            ):
                self.show_production_session(session)
        except (KeyError, RuntimeError, TypeError, ValueError) as error:
            self._show_fallback(str(error) or "Không thể refresh production schema.")

    def _dispose_active_page(self) -> None:
        page = self._active_page
        if page is None:
            return
        page.state.mark_stale()
        try:
            self.stack.removeWidget(page)
            page.close_requested.disconnect(self._framework_close_requested)
            page.child_popup_requested.disconnect(self.child_popup_requested)
            page.deleteLater()
        except (RuntimeError, TypeError):
            self._active_page = None
            return
        self._active_page = None

    def request_close(self) -> bool:
        """Run the editor close contract for a footer action or title-bar X."""
        page = self._active_page
        if page is None:
            self.collapse_requested.emit()
            return True
        return page.request_close()

    def invalidate_current_session(self) -> None:
        """Mark callbacks stale and drop Qt bindings after project/operation removal."""
        self._dispose_active_page()
        self._active_session = None
        self.legacy_adapter.selection_changed()
        self.stack.setCurrentWidget(self.legacy_adapter)
        self.mode_label.setText("TRÌNH CŨ")
        self.editor_replaced.emit("legacy")

    def refresh_summary(self) -> None:
        """Compatibility method used by Stage 9A.2 tests/callers."""
        self.legacy_adapter.refresh_summary()

    def set_calculation_active(self, active: bool) -> None:
        """Update the active production page when its worker starts/stops."""
        if self._active_page is not None:
            self._active_page.set_calculation_active(active)

    def update_calculation_progress(self, value: object) -> None:
        """Forward one project-scoped worker report to the active page."""
        if self._active_page is not None:
            self._active_page.update_calculation_progress(value)

    def apply_popup_density(self, metrics: CAMPopupMetrics) -> None:
        """Apply the popup policy to the shared host and whichever editor is active."""
        self._popup_metrics = metrics
        self._header_layout.setContentsMargins(
            metrics.content_margin,
            metrics.row_spacing,
            metrics.row_spacing,
            metrics.row_spacing,
        )
        self._header_layout.setSpacing(metrics.row_spacing)
        if self._active_page is not None:
            self._active_page.apply_compact_density(metrics)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        """Keep the dock usable at the Stage 9A.4 300 px narrow width."""
        return QSize(240, 300)
