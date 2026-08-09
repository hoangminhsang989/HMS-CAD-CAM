"""Compact three-step CAM operation creation wizard for Stage16A."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import QCloseEvent, QKeyEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStackedWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from hms_cadcam.cam.operation_creation import (
    OperationCreationSession,
    OperationCreationState,
    OperationCreationStep,
    OperationStrategyChoice,
    OperationToolChoice,
)
from hms_cadcam.ui.function_editor.model import (
    FunctionEditorDiagnostic,
    FunctionEditorDiagnosticSeverity,
    PresentationValue,
)
from hms_cadcam.ui.function_editor.schema import FunctionEditorSchema
from hms_cadcam.ui.function_editor.state import (
    DraftTransformCallback,
    FunctionEditorDraftState,
    ValidationCallback,
)
from hms_cadcam.ui.function_editor.widgets import FunctionEditorPage
from hms_cadcam.ui.localization import localize_widget_tree, ui_text
from hms_cadcam.ui.ui_tokens import CAM_POPUP_DENSITY


FieldActionCallback = Callable[
    [str, Mapping[str, PresentationValue]], Mapping[str, PresentationValue] | None
]
FinishCallback = Callable[[Mapping[str, PresentationValue]], object]
FinishBindingClaim = Callable[[], OperationCreationSession]
FinishBindingCompletion = Callable[[bool], None]


@dataclass(frozen=True, slots=True)
class OperationCreationEditorBinding:
    """Thin binding to an existing strategy schema and prepare/validate functions."""

    schema: FunctionEditorSchema
    applied_values: Mapping[str, PresentationValue]
    validation_callback: ValidationCallback
    finish_callback: FinishCallback
    field_action_callback: FieldActionCallback | None = None
    draft_transform_callback: DraftTransformCallback | None = None


class OperationCreationWizardAdapter(Protocol):
    def strategy_choices(self) -> tuple[OperationStrategyChoice, ...]: ...

    def tool_choices(
        self, session: OperationCreationSession, query: str = ""
    ) -> tuple[OperationToolChoice, ...]: ...

    def selected_tool_is_compatible(
        self, session: OperationCreationSession, strategy_id: str
    ) -> bool: ...

    def build_editor(
        self,
        session: OperationCreationSession,
        *,
        claim_finish: FinishBindingClaim,
        complete_finish: FinishBindingCompletion,
    ) -> OperationCreationEditorBinding: ...

    def context_is_current(self, session: OperationCreationSession) -> tuple[bool, str]: ...

    def open_tool_management(
        self, session: OperationCreationSession, parent: QWidget
    ) -> None: ...


class OperationCreationWizard(QDialog):
    """Three explicit pages; only Finish crosses the application boundary."""

    operation_created = Signal(str)
    session_changed = Signal(object)

    def __init__(
        self,
        session: OperationCreationSession,
        adapter: OperationCreationWizardAdapter,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("OperationCreationWizard")
        self.setWindowTitle(ui_text("Create CAM operation"))
        self.setAccessibleName(ui_text("Three-step CAM operation creation"))
        self.setAccessibleDescription(
            ui_text("Select a strategy, select a compatible Tool, then configure the operation.")
        )
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self._session = session
        self._adapter = adapter
        self._binding: OperationCreationEditorBinding | None = None
        self._binding_generation = 0
        self._active_binding_generation: int | None = None
        self._binding_claimed = False
        self._editor_page: FunctionEditorPage | None = None
        self._finishing = False
        self._closing_after_create = False
        self._tool_by_id: dict[str, OperationToolChoice] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(7)
        root.addWidget(self._build_progress())
        self.feedback = QLabel()
        self.feedback.setObjectName("OperationCreationFeedback")
        self.feedback.setAccessibleName(ui_text("Operation creation status"))
        self.feedback.setWordWrap(True)
        self.feedback.setVisible(False)
        root.addWidget(self.feedback)
        self.pages = QStackedWidget()
        self.pages.setObjectName("OperationCreationPages")
        self.pages.addWidget(self._build_strategy_page())
        self.pages.addWidget(self._build_tool_page())
        self.pages.addWidget(self._build_parameter_page())
        root.addWidget(self.pages, 1)
        root.addWidget(self._build_navigation())

        self._populate_strategies()
        self._show_step(OperationCreationStep.SELECT_OPERATION)
        self.apply_available_geometry(QRect(0, 0, 1366, 768), 1.0)
        localize_widget_tree(self)

    @property
    def session(self) -> OperationCreationSession:
        return self._session

    @property
    def editor_page(self) -> FunctionEditorPage | None:
        return self._editor_page

    def _build_progress(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("OperationCreationProgress")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        self.step_labels: list[QLabel] = []
        for text in (
            "Step 1 — Select operation",
            "Step 2 — Select Tool",
            "Step 3 — Operation parameters",
        ):
            label = QLabel(ui_text(text))
            label.setWordWrap(True)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setAccessibleName(ui_text(text))
            layout.addWidget(label, 1)
            self.step_labels.append(label)
        return frame

    def _build_strategy_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        title = QLabel(ui_text("Select an operation supported by the current CAM registry."))
        title.setWordWrap(True)
        layout.addWidget(title)
        self.strategy_list = QListWidget()
        self.strategy_list.setObjectName("OperationCreationStrategyList")
        self.strategy_list.setAccessibleName(ui_text("Supported CAM strategies"))
        self.strategy_list.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.strategy_list.currentItemChanged.connect(
            lambda _current, _previous: self._update_navigation()
        )
        self.strategy_list.itemDoubleClicked.connect(lambda _item: self._next())
        layout.addWidget(self.strategy_list, 1)
        return page

    def _build_tool_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.tool_search = QLineEdit()
        self.tool_search.setObjectName("OperationCreationToolSearch")
        self.tool_search.setPlaceholderText(ui_text("Search Tool…"))
        self.tool_search.setAccessibleName(ui_text("Filter project Tools"))
        self.tool_search.setClearButtonEnabled(True)
        self.tool_search.textChanged.connect(self._populate_tools)
        row.addWidget(self.tool_search, 1)
        self.manage_tools = QPushButton(ui_text("Open Tool management"))
        self.manage_tools.setAccessibleName(ui_text("Open existing Tool management"))
        self.manage_tools.clicked.connect(self._open_tool_management)
        row.addWidget(self.manage_tools)
        layout.addLayout(row)
        self.tool_list = QTreeWidget()
        self.tool_list.setObjectName("OperationCreationToolList")
        self.tool_list.setAccessibleName(ui_text("Compatible and incompatible project Tools"))
        self.tool_list.setHeaderLabels(
            tuple(
                ui_text(item)
                for item in ("Tool", "Family", "Diameter / radius", "Holder", "Compatibility")
            )
        )
        self.tool_list.setRootIsDecorated(False)
        self.tool_list.setAlternatingRowColors(True)
        self.tool_list.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.tool_list.currentItemChanged.connect(
            lambda _current, _previous: self._tool_selection_changed()
        )
        self.tool_list.itemDoubleClicked.connect(
            lambda item, _column: self._next() if not item.isDisabled() else None
        )
        layout.addWidget(self.tool_list, 1)
        self.tool_reason = QLabel()
        self.tool_reason.setObjectName("OperationCreationToolReason")
        self.tool_reason.setAccessibleName(ui_text("Tool compatibility explanation"))
        self.tool_reason.setWordWrap(True)
        layout.addWidget(self.tool_reason)
        return page

    def _build_parameter_page(self) -> QWidget:
        page = QWidget()
        self.parameter_layout = QVBoxLayout(page)
        self.parameter_layout.setContentsMargins(0, 0, 0, 0)
        self.parameter_hint = QLabel(
            ui_text(
                "Basic settings are shown first. Open Advanced only when a manual decision is required."
            )
        )
        self.parameter_hint.setWordWrap(True)
        self.parameter_layout.addWidget(self.parameter_hint)
        self.parameter_placeholder = QLabel(
            ui_text("Select a compatible Tool to load the production editor.")
        )
        self.parameter_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.parameter_placeholder.setWordWrap(True)
        self.parameter_layout.addWidget(self.parameter_placeholder, 1)
        return page

    def _build_navigation(self) -> QFrame:
        frame = QFrame()
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(0, 0, 0, 0)
        self.back_button = QPushButton(ui_text("Back"))
        self.back_button.setAccessibleName(ui_text("Go back one operation creation step"))
        self.back_button.clicked.connect(self._back)
        layout.addWidget(self.back_button)
        layout.addStretch(1)
        self.cancel_button = QPushButton(ui_text("Cancel"))
        self.cancel_button.setAccessibleName(ui_text("Cancel operation creation safely"))
        self.cancel_button.clicked.connect(self.reject)
        layout.addWidget(self.cancel_button)
        self.next_button = QPushButton(ui_text("Next"))
        self.next_button.setObjectName("PrimaryPanelAction")
        self.next_button.setAccessibleName(ui_text("Continue to the next operation creation step"))
        self.next_button.clicked.connect(self._next)
        layout.addWidget(self.next_button)
        self.finish_button = QPushButton(ui_text("Create operation"))
        self.finish_button.setObjectName("PrimaryPanelAction")
        self.finish_button.setAccessibleName(ui_text("Create exactly one configured operation"))
        self.finish_button.clicked.connect(self._finish)
        layout.addWidget(self.finish_button)
        return frame

    def _populate_strategies(self) -> None:
        self.strategy_list.clear()
        for choice in self._adapter.strategy_choices():
            item = QListWidgetItem(
                f"{ui_text(choice.display_name)}\n{ui_text(choice.description)}"
            )
            item.setData(Qt.ItemDataRole.UserRole, choice.strategy_id)
            item.setToolTip(ui_text(choice.description))
            item.setData(
                Qt.ItemDataRole.AccessibleDescriptionRole,
                f"{ui_text(choice.display_name)}. {ui_text(choice.description)}",
            )
            self.strategy_list.addItem(item)
            if choice.strategy_id == self._session.strategy_id:
                self.strategy_list.setCurrentItem(item)
        if self.strategy_list.currentItem() is None and self.strategy_list.count():
            self.strategy_list.setCurrentRow(0)

    def _populate_tools(self, _text: str = "") -> None:
        if self._session.strategy_id is None:
            return
        selected_id = self._session.tool_assembly_id
        choices = self._adapter.tool_choices(self._session, self.tool_search.text())
        self._tool_by_id = {str(item.assembly_id): item for item in choices}
        self.tool_list.clear()
        first_compatible: QTreeWidgetItem | None = None
        for choice in choices:
            status = (
                ui_text("Compatible Tool")
                if choice.compatible
                else ui_text("Incompatible Tool")
            )
            item = QTreeWidgetItem(
                (
                    f"{choice.tool_name} · {str(choice.tool_id)[:8]}",
                    choice.family,
                    choice.diameter_text,
                    choice.holder_text,
                    status,
                )
            )
            item.setData(0, Qt.ItemDataRole.UserRole, str(choice.assembly_id))
            accessible = f"{status}. {ui_text(choice.reason)}"
            item.setToolTip(4, accessible)
            item.setStatusTip(4, accessible)
            if not choice.compatible:
                item.setDisabled(True)
            elif first_compatible is None:
                first_compatible = item
            self.tool_list.addTopLevelItem(item)
            if choice.assembly_id == selected_id and choice.compatible:
                self.tool_list.setCurrentItem(item)
        if self.tool_list.currentItem() is None and first_compatible is not None:
            self.tool_list.setCurrentItem(first_compatible)
        self.tool_list.resizeColumnToContents(0)
        self._tool_selection_changed()

    def _selected_tool_choice(self) -> OperationToolChoice | None:
        item = self.tool_list.currentItem()
        if item is None or item.isDisabled():
            return None
        return self._tool_by_id.get(str(item.data(0, Qt.ItemDataRole.UserRole)))

    def _tool_selection_changed(self) -> None:
        item = self.tool_list.currentItem()
        choice = None if item is None else self._tool_by_id.get(
            str(item.data(0, Qt.ItemDataRole.UserRole))
        )
        self.tool_reason.setText(
            ui_text("No compatible Tool found.")
            if choice is None
            else ui_text(choice.reason)
        )
        self.manage_tools.setEnabled(choice is not None)
        self._update_navigation()

    def _show_step(self, step: OperationCreationStep) -> None:
        index = {
            OperationCreationStep.SELECT_OPERATION: 0,
            OperationCreationStep.SELECT_TOOL: 1,
            OperationCreationStep.CONFIGURE_OPERATION: 2,
        }[step]
        self.pages.setCurrentIndex(index)
        for position, label in enumerate(self.step_labels):
            label.setProperty("currentStep", position == index)
            label.style().unpolish(label)
            label.style().polish(label)
        if step is OperationCreationStep.SELECT_TOOL:
            self._populate_tools()
        self._update_navigation()

    def _next(self) -> None:
        self._clear_feedback()
        if self._session.current_step is OperationCreationStep.SELECT_OPERATION:
            item = self.strategy_list.currentItem()
            if item is None:
                return
            strategy_id = str(item.data(Qt.ItemDataRole.UserRole))
            self._invalidate_binding()
            keep = self._adapter.selected_tool_is_compatible(self._session, strategy_id)
            self._session = self._session.select_strategy(
                strategy_id, selected_tool_remains_compatible=keep
            )
            self._binding = None
            self._dispose_editor()
            self._show_step(OperationCreationStep.SELECT_TOOL)
        elif self._session.current_step is OperationCreationStep.SELECT_TOOL:
            choice = self._selected_tool_choice()
            if choice is None:
                return
            self._session = self._session.select_tool(choice)
            generation = self._activate_binding()
            try:
                self._binding = self._adapter.build_editor(
                    self._session,
                    claim_finish=lambda generation=generation: self._claim_binding(
                        generation
                    ),
                    complete_finish=lambda success, generation=generation: self._complete_binding(
                        generation, success
                    ),
                )
                self._install_editor(self._binding)
            except (KeyError, RuntimeError, TypeError, ValueError) as error:
                self._invalidate_binding()
                self._session = self._session.back()
                self._show_feedback(str(error))
                self._show_step(OperationCreationStep.SELECT_TOOL)
                return
            self._show_step(OperationCreationStep.CONFIGURE_OPERATION)
        self.session_changed.emit(self._session)

    def _back(self) -> None:
        if self._session.current_step is OperationCreationStep.SELECT_OPERATION:
            return
        if self._session.current_step is OperationCreationStep.CONFIGURE_OPERATION:
            self._invalidate_binding()
        self._session = self._session.back()
        if self._session.current_step is not OperationCreationStep.CONFIGURE_OPERATION:
            self._dispose_editor()
            self._binding = None
        self._show_step(self._session.current_step)
        self.session_changed.emit(self._session)

    def _install_editor(self, binding: OperationCreationEditorBinding) -> None:
        self._dispose_editor()
        state = FunctionEditorDraftState(
            binding.schema,
            binding.applied_values,
            project_key=str(self._session.project_id),
            operation_key=str(self._session.session_id),
            generation=self._session.project_generation,
            validation_callback=binding.validation_callback,
            draft_transform_callback=binding.draft_transform_callback,
        )
        page = FunctionEditorPage(
            state,
            field_action_callback=binding.field_action_callback,
        )
        page.setObjectName("OperationCreationFunctionEditor")
        page.setAccessibleDescription(
            ui_text("Working copy only; no operation exists until Create operation succeeds.")
        )
        page.footer.hide()
        page.state_changed.connect(lambda _state: self._update_navigation())
        page.apply_compact_density(
            CAM_POPUP_DENSITY.metrics_for(self.size(), display_scale_factor=1.0)
        )
        self.parameter_placeholder.hide()
        self.parameter_layout.addWidget(page, 1)
        self._editor_page = page

    def _dispose_editor(self) -> None:
        page = self._editor_page
        self._editor_page = None
        if page is not None:
            self.parameter_layout.removeWidget(page)
            page.deleteLater()
        self.parameter_placeholder.show()

    def _finish(self) -> None:
        if self._finishing or self._binding is None or self._editor_page is None:
            return
        self._clear_feedback()
        current, reason = self._adapter.context_is_current(self._session)
        if not current:
            self._invalidate_binding()
            self._show_feedback(reason)
            self._update_navigation()
            return
        state = self._editor_page.state
        diagnostics = state.validate()
        errors = tuple(
            item.message
            for item in diagnostics
            if item.severity is FunctionEditorDiagnosticSeverity.ERROR
        )
        self._session = self._session.configure(
            dict(state.values), validation_errors=errors
        )
        if errors:
            self._editor_page.focus_field(
                next(
                    (
                        item.field_id
                        for item in diagnostics
                        if item.severity is FunctionEditorDiagnosticSeverity.ERROR
                        and item.field_id is not None
                    ),
                    "operation_name",
                )
            )
            self._show_feedback(errors[0])
            self._update_navigation()
            return
        self._finishing = True
        self.finish_button.setEnabled(False)
        try:
            result = self._binding.finish_callback(state.applicable_snapshot())
            operation_id = str(
                getattr(result, "node_id", getattr(result, "operation_id", result))
            )
            self._session = self._session.mark_created()
            self._consume_binding()
        except (KeyError, RuntimeError, TypeError, ValueError) as error:
            self._session = self._session.configure(
                dict(state.values), validation_errors=(str(error),)
            )
            self._show_feedback(str(error))
            self._finishing = False
            self._update_navigation()
            return
        self._closing_after_create = True
        self.operation_created.emit(operation_id)
        self.session_changed.emit(self._session)
        self.accept()

    def refresh_live_state(self) -> None:
        """Revalidate project/program/Tool when the owning workspace changes."""
        if self._session.state in {
            OperationCreationState.CREATED,
            OperationCreationState.CANCELLED,
        }:
            return
        current, reason = self._adapter.context_is_current(self._session)
        if not current:
            self._invalidate_binding()
            self._show_feedback(reason)
        if self._session.current_step is OperationCreationStep.SELECT_TOOL:
            self._populate_tools()
        self._update_navigation()

    def apply_available_geometry(self, available: QRect, scale: float) -> None:
        """Clamp the wizard in logical pixels for 100–200% UI scale audits."""
        if not available.isValid():
            return
        metrics = CAM_POPUP_DENSITY.metrics_for(
            available, display_scale_factor=max(1.0, float(scale))
        )
        width = min(max(760, metrics.popup_width), available.width())
        height = min(max(560, metrics.popup_height), available.height())
        self.setMaximumSize(available.size())
        self.resize(width, height)
        if self._editor_page is not None:
            self._editor_page.apply_compact_density(metrics)

    def _open_tool_management(self) -> None:
        try:
            choice = self._selected_tool_choice()
            target_session = (
                self._session.select_tool(choice)
                if choice is not None
                and self._session.tool_assembly_id != choice.assembly_id
                else self._session
            )
            self._adapter.open_tool_management(target_session, self)
        except (KeyError, RuntimeError, TypeError, ValueError) as error:
            self._show_feedback(str(error))

    def _update_navigation(self) -> None:
        step = self._session.current_step
        self.back_button.setEnabled(step is not OperationCreationStep.SELECT_OPERATION)
        self.next_button.setVisible(step is not OperationCreationStep.CONFIGURE_OPERATION)
        self.finish_button.setVisible(step is OperationCreationStep.CONFIGURE_OPERATION)
        if step is OperationCreationStep.SELECT_OPERATION:
            self.next_button.setEnabled(self.strategy_list.currentItem() is not None)
        elif step is OperationCreationStep.SELECT_TOOL:
            self.next_button.setEnabled(self._selected_tool_choice() is not None)
        else:
            current, _reason = self._adapter.context_is_current(self._session)
            self.finish_button.setEnabled(
                current and self._editor_page is not None and not self._finishing
            )
        self.next_button.setDefault(step is not OperationCreationStep.CONFIGURE_OPERATION)
        self.next_button.setAutoDefault(step is not OperationCreationStep.CONFIGURE_OPERATION)
        self.finish_button.setDefault(step is OperationCreationStep.CONFIGURE_OPERATION)
        self.finish_button.setAutoDefault(step is OperationCreationStep.CONFIGURE_OPERATION)

    def _show_feedback(self, text: str) -> None:
        self.feedback.setText(ui_text(text or "Operation creation is not ready."))
        self.feedback.setVisible(True)

    def _clear_feedback(self) -> None:
        self.feedback.clear()
        self.feedback.setVisible(False)

    def reject(self) -> None:
        if (
            not self._closing_after_create
            and self._session.state is not OperationCreationState.CREATED
        ):
            self._invalidate_binding()
            self._session = self._session.cancel()
            self.session_changed.emit(self._session)
        super().reject()

    def closeEvent(self, event: QCloseEvent) -> None:
        if (
            not self._closing_after_create
            and self._session.state is not OperationCreationState.CREATED
        ):
            self._invalidate_binding()
            self._session = self._session.cancel()
            self.session_changed.emit(self._session)
        event.accept()

    def _activate_binding(self) -> int:
        self._invalidate_binding()
        self._active_binding_generation = self._binding_generation
        return self._binding_generation

    def _invalidate_binding(self) -> None:
        self._binding_generation += 1
        self._active_binding_generation = None
        self._binding_claimed = False

    def _claim_binding(self, generation: int) -> OperationCreationSession:
        if (
            self._active_binding_generation != generation
            or self._binding_claimed
            or self._session.state
            in {OperationCreationState.CREATED, OperationCreationState.CANCELLED}
        ):
            raise RuntimeError("Phiên tạo nguyên công không còn hiện hành.")
        self._binding_claimed = True
        return self._session

    def _complete_binding(self, generation: int, success: bool) -> None:
        if self._active_binding_generation != generation:
            return
        if success:
            return
        self._invalidate_binding()

    def _consume_binding(self) -> None:
        self._active_binding_generation = None
        self._binding_claimed = False

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
            return
        super().keyPressEvent(event)


def error_diagnostic(
    message: str,
    *,
    code: str = "creation.invalid",
) -> tuple[FunctionEditorDiagnostic, ...]:
    """Map a thin-adapter validation exception into the existing editor model."""
    return (
        FunctionEditorDiagnostic(
            code,
            message,
            FunctionEditorDiagnosticSeverity.ERROR,
        ),
    )


__all__ = [
    "OperationCreationEditorBinding",
    "OperationCreationWizard",
    "OperationCreationWizardAdapter",
    "error_diagnostic",
]
