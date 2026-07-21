"""Right-dock host, registry boundary and lifecycle cleanup for Function Editors."""

from __future__ import annotations

from collections.abc import Callable, Mapping

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

from hms_cadcam.ui.function_editor.legacy_adapter import (
    LegacyFunctionEditorAdapter,
)
from hms_cadcam.ui.function_editor.model import PresentationValue
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


class FunctionEditorHost(QWidget):
    """Hosts one framework page or one legacy adapter—never nested editors."""

    collapse_requested = Signal()
    editor_replaced = Signal(str)

    def __init__(
        self,
        editor: QWidget,
        tree: QTreeWidget,
        apply_callback: Callable[[], None],
        parent: QWidget | None = None,
        *,
        settings: QSettings | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("FunctionEditorHost")
        self.setAccessibleName("Function Editor")
        self.editor = editor
        self._tree = tree
        self.registry = FunctionEditorRegistry()
        self._state_store = (
            FunctionEditorStateStore(settings) if settings is not None else None
        )
        self._active_page: FunctionEditorPage | None = None
        self._selection_guard = False

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

        tree.itemSelectionChanged.connect(self._selection_changed)
        tree.model().modelReset.connect(self._selection_changed)
        self.show_legacy_editor()

    def _header(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("PanelHeader")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(8, 5, 5, 5)
        label = QLabel("Function Editor")
        label.setObjectName("PanelTitle")
        layout.addWidget(label)
        layout.addStretch(1)
        self.mode_label = QLabel("LEGACY")
        self.mode_label.setObjectName("FunctionEditorHostMode")
        layout.addWidget(self.mode_label)
        self.collapse_button = QToolButton()
        self.collapse_button.setText("×")
        self.collapse_button.setAccessibleName("Thu gọn Function Editor")
        self.collapse_button.setToolTip("Thu gọn Function Editor")
        self.collapse_button.setAutoRaise(True)
        self.collapse_button.clicked.connect(self.collapse_requested)
        layout.addWidget(self.collapse_button)
        return frame

    @property
    def active_page(self) -> FunctionEditorPage | None:
        return self._active_page

    @property
    def current_mode(self) -> str:
        return "framework" if self._active_page is not None else "legacy"

    def show_legacy_editor(self) -> None:
        """Return to the unchanged production editor and clean old callbacks."""
        self._dispose_active_page()
        self.legacy_adapter.selection_changed()
        self.stack.setCurrentWidget(self.legacy_adapter)
        self.mode_label.setText("LEGACY")
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
        )
        page = FunctionEditorPage(
            state,
            state_store=self._state_store,
            apply_callback=apply_callback,
            preview_callback=preview_callback,  # type: ignore[arg-type]
            calculate_callback=calculate_callback,
            close_confirmation=close_confirmation,
        )
        page.close_requested.connect(self._framework_close_requested)
        self.stack.addWidget(page)
        self.stack.setCurrentWidget(page)
        self._active_page = page
        self.mode_label.setText("REFERENCE" if schema.summary.reference_only else "FRAMEWORK")
        self.editor_replaced.emit(schema.editor_id)
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
        if self._selection_guard:
            return
        # Stage 9A.4 deliberately leaves every production strategy on legacy.
        # A later migration can resolve a registered typed strategy here.
        try:
            self.show_legacy_editor()
        except RuntimeError:
            # A model reset can arrive during QObject teardown; no UI survives it.
            return

    def _dispose_active_page(self) -> None:
        page = self._active_page
        if page is None:
            return
        page.state.mark_stale()
        try:
            self.stack.removeWidget(page)
            page.close_requested.disconnect(self._framework_close_requested)
            page.deleteLater()
        except (RuntimeError, TypeError):
            self._active_page = None
            return
        self._active_page = None

    def refresh_summary(self) -> None:
        """Compatibility method used by Stage 9A.2 tests/callers."""
        self.legacy_adapter.refresh_summary()

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        """Keep the dock usable at the Stage 9A.4 300 px narrow width."""
        return QSize(240, 300)
