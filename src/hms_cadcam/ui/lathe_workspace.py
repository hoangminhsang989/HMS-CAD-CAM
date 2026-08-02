"""Responsive metadata-driven Stage 9A.9 Lathe workspace."""

from __future__ import annotations

from enum import StrEnum
import math

from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from hms_cadcam.cam.domain.ids import OperationId
from hms_cadcam.cam.domain.revision import Revision
from hms_cadcam.cam.lathe.capabilities import LatheToolReference
from hms_cadcam.cam.lathe.parameters import (
    LatheParameterDescriptor,
    LatheParameterUpdate,
)
from hms_cadcam.cam.lathe.presenter import (
    LatheOperationSnapshot,
    LathePresenterSnapshot,
    LatheStrategyDescriptor,
)
from hms_cadcam.cam.lathe.types import (
    LatheDiagnosticCode,
    LatheGeometryKind,
    LatheOperationReadiness,
    LatheParameterGroup,
    LatheParameterUnitKind,
    LatheParameterValueKind,
    LatheStrategyFamily,
    LatheStrategyId,
    LatheToolCapability,
)
from hms_cadcam.ui.i18n import translation_service
from hms_cadcam.ui.lathe_adapters import LatheToolChoice
from hms_cadcam.ui.lathe_presenter import (
    LatheQtCommandResult,
    LatheQtDiagnostic,
    LatheQtPresenter,
)
from hms_cadcam.ui.lathe_toolpath import (
    LatheToolpathUiController,
    LatheToolpathUiState,
    LatheToolpathUiStateCode,
)
from hms_cadcam.ui.lathe_simulation import LatheSimulationWindowManager
from hms_cadcam.ui.localization import ui_text


_IDENTITY_ROLE = int(Qt.ItemDataRole.UserRole) + 101
_CHOICE_ROLE = int(Qt.ItemDataRole.UserRole) + 102
_THREAD_SUCCESS_DIAGNOSTIC_CODES = (
    "phase_neutral_synchronized_centerline_preview",
    "thread_feed_derived_from_pitch",
    "nominal_infeed_angle_metadata_only",
    "not_machine_ready",
)
_THREAD_FAILURE_DIAGNOSTIC_CODES = frozenset(
    {
        "missing_internal_bore",
        "invalid_thread_diameter_order",
        "thread_major_exceeds_stock",
        "thread_minor_below_bore",
        "invalid_pitch",
        "invalid_pass_count",
        "invalid_spring_passes",
        "invalid_infeed_angle",
        "thread_range_outside_stock",
        "incompatible_thread_tool",
        "incompatible_thread_geometry",
    }
)
_THREAD_PARAMETER_DIAGNOSTIC_CODES = {
    "pitch_mm": "invalid_pitch",
    "pass_count": "invalid_pass_count",
    "spring_passes": "invalid_spring_passes",
    "infeed_angle_deg": "invalid_infeed_angle",
    "major_diameter_mm": "invalid_thread_diameter_order",
    "minor_diameter_mm": "invalid_thread_diameter_order",
    "start_z_mm": "thread_range_outside_stock",
    "end_z_mm": "thread_range_outside_stock",
}


def _tr(key: str, **values: object) -> str:
    service = translation_service()
    return service.format(key, **values) if values else service.translate_key(key)


def _strategy_key(strategy_id: LatheStrategyId) -> str:
    return f"lathe.strategy.{strategy_id.name.casefold()}.label"


def _family_key(family_id: LatheStrategyFamily) -> str:
    return f"lathe.family.{family_id.name.casefold()}.label"


def _capability_key(capability: LatheToolCapability) -> str:
    return f"lathe.capability.{capability.value.casefold()}.label"


def _geometry_key(kind: LatheGeometryKind) -> str:
    return f"lathe.geometry.{kind.name.casefold()}.label"


class LatheParameterEditor(QWidget):
    """Build exact BASIC/ADVANCED controls from immutable descriptors."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("LatheParameterEditor")
        self.setAccessibleName(ui_text("lathe.parameters.title"))
        self._operation: LatheOperationSnapshot | None = None
        self._descriptors: tuple[LatheParameterDescriptor, ...] = ()
        self._editors: dict[str, QWidget] = {}
        self._optional_checks: dict[str, QCheckBox] = {}
        self._labels: dict[str, QLabel] = {}
        self._refreshing = False

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)
        self.basic_group = QGroupBox()
        self.basic_group.setObjectName("LatheBasicParameters")
        self.basic_form = QFormLayout(self.basic_group)
        self.basic_form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        )
        root.addWidget(self.basic_group)

        self.advanced_toggle = QToolButton()
        self.advanced_toggle.setObjectName("LatheAdvancedToggle")
        self.advanced_toggle.setCheckable(True)
        self.advanced_toggle.setChecked(False)
        self.advanced_toggle.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self.advanced_toggle.toggled.connect(self._advanced_toggled)
        root.addWidget(self.advanced_toggle)
        self.advanced_group = QGroupBox()
        self.advanced_group.setObjectName("LatheAdvancedParameters")
        self.advanced_form = QFormLayout(self.advanced_group)
        self.advanced_form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        )
        self.advanced_group.hide()
        root.addWidget(self.advanced_group)
        root.addStretch(1)
        self.retranslate_ui()

    @property
    def descriptors(self) -> tuple[LatheParameterDescriptor, ...]:
        return self._descriptors

    @property
    def editors(self) -> dict[str, QWidget]:
        """Return a shallow copy keyed by stable parameter ID for focused QA."""

        return dict(self._editors)

    def set_operation(
        self,
        operation: LatheOperationSnapshot | None,
        descriptor: LatheStrategyDescriptor | None,
    ) -> None:
        """Rebuild only when schema changes, then render authoritative values."""

        next_descriptors = () if descriptor is None else descriptor.parameters
        if tuple(item.parameter_id for item in next_descriptors) != tuple(
            item.parameter_id for item in self._descriptors
        ):
            self._rebuild(next_descriptors)
        self._operation = operation
        self._refreshing = True
        try:
            values = {} if operation is None else dict(operation.parameter_values)
            for item in self._descriptors:
                self._set_editor_value(item, values.get(item.parameter_id))
        finally:
            self._refreshing = False

    def set_read_only(self, read_only: bool) -> None:
        """Disable every pending-value control while preserving inspection."""

        for editor in self._editors.values():
            editor.setEnabled(not read_only)
        for check in self._optional_checks.values():
            check.setEnabled(not read_only)

    def updates(self) -> tuple[LatheParameterUpdate, ...]:
        """Return typed changes only, in authoritative descriptor order."""

        operation = self._operation
        if operation is None:
            return ()
        current = dict(operation.parameter_values)
        updates: list[LatheParameterUpdate] = []
        for descriptor in self._descriptors:
            value = self._editor_value(descriptor)
            if value != current.get(descriptor.parameter_id):
                updates.append(LatheParameterUpdate(descriptor.parameter_id, value))
        return tuple(updates)

    def retranslate_ui(self) -> None:
        """Retranslate labels/help without changing editor values."""

        self.setAccessibleName(_tr("lathe.parameters.title"))
        self.basic_group.setTitle(_tr("lathe.parameters.basic"))
        self.advanced_group.setTitle(_tr("lathe.parameters.advanced"))
        self.advanced_toggle.setText(_tr("lathe.parameters.advanced"))
        self.advanced_toggle.setAccessibleName(
            _tr("lathe.parameters.advanced.toggle")
        )
        self.advanced_toggle.setAccessibleDescription(
            _tr("lathe.parameters.advanced.help")
        )
        for descriptor in self._descriptors:
            label = self._labels[descriptor.parameter_id]
            unit = (
                ""
                if descriptor.unit_kind is LatheParameterUnitKind.NONE
                else _tr(f"lathe.unit.{descriptor.unit_kind.value}")
            )
            text = _tr(descriptor.label_key)
            label.setText(text if not unit else f"{text} ({unit})")
            help_text = _tr(descriptor.help_key)
            label.setToolTip(help_text)
            editor = self._editors[descriptor.parameter_id]
            editor.setAccessibleName(text)
            editor.setAccessibleDescription(help_text)
            editor.setToolTip(help_text)
            if isinstance(editor, QComboBox):
                selected = str(editor.currentData())
                for index in range(editor.count()):
                    token = str(editor.itemData(index))
                    editor.setItemText(index, _tr(f"lathe.enum.{token.casefold()}"))
                selected_index = editor.findData(selected)
                if selected_index >= 0:
                    editor.setCurrentIndex(selected_index)

    def _rebuild(
        self, descriptors: tuple[LatheParameterDescriptor, ...]
    ) -> None:
        self._clear_form(self.basic_form)
        self._clear_form(self.advanced_form)
        self._descriptors = descriptors
        self._editors.clear()
        self._optional_checks.clear()
        self._labels.clear()
        for descriptor in descriptors:
            label = QLabel()
            label.setObjectName(f"LatheParameter_{descriptor.parameter_id}Label")
            editor = self._make_editor(descriptor)
            self._labels[descriptor.parameter_id] = label
            self._editors[descriptor.parameter_id] = editor
            form = (
                self.basic_form
                if descriptor.group is LatheParameterGroup.BASIC
                else self.advanced_form
            )
            form.addRow(label, editor)
        self.retranslate_ui()

    def _make_editor(self, descriptor: LatheParameterDescriptor) -> QWidget:
        object_name = f"LatheParameter_{descriptor.parameter_id}"
        if descriptor.value_kind is LatheParameterValueKind.FLOAT:
            editor = QDoubleSpinBox()
            editor.setDecimals(6)
            editor.setSingleStep(0.1)
            minimum = -1.0e12 if descriptor.minimum is None else float(descriptor.minimum)
            maximum = 1.0e12 if descriptor.maximum is None else float(descriptor.maximum)
            if descriptor.exclusive_minimum:
                minimum = math.nextafter(minimum, math.inf)
            if descriptor.exclusive_maximum:
                maximum = math.nextafter(maximum, -math.inf)
            editor.setRange(minimum, maximum)
        elif descriptor.value_kind is LatheParameterValueKind.INTEGER:
            editor = QSpinBox()
            editor.setRange(
                -2_147_483_647
                if descriptor.minimum is None
                else int(descriptor.minimum),
                2_147_483_647
                if descriptor.maximum is None
                else int(descriptor.maximum),
            )
        else:
            editor = QComboBox()
            assert descriptor.enum_type is not None
            for value in descriptor.enum_type:
                editor.addItem(value.value, value.value)
        editor.setObjectName(object_name)
        editor.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        if not descriptor.required:
            wrapper = QWidget()
            wrapper.setObjectName(f"{object_name}OptionalContainer")
            row = QHBoxLayout(wrapper)
            row.setContentsMargins(0, 0, 0, 0)
            check = QCheckBox()
            check.setObjectName(f"{object_name}Optional")
            check.setAccessibleName(_tr("lathe.parameter.optional.enable"))
            check.toggled.connect(editor.setEnabled)
            row.addWidget(check)
            row.addWidget(editor, 1)
            self._optional_checks[descriptor.parameter_id] = check
            setattr(wrapper, "_lathe_value_editor", editor)
            return wrapper
        return editor

    def _value_widget(self, parameter_id: str) -> QWidget:
        editor = self._editors[parameter_id]
        return getattr(editor, "_lathe_value_editor", editor)

    def _set_editor_value(
        self, descriptor: LatheParameterDescriptor, value: object
    ) -> None:
        editor = self._value_widget(descriptor.parameter_id)
        optional = self._optional_checks.get(descriptor.parameter_id)
        blockers = [QSignalBlocker(editor)]
        if optional is not None:
            blockers.append(QSignalBlocker(optional))
            optional.setChecked(value is not None)
            editor.setEnabled(value is not None)
        if value is None:
            return
        if isinstance(editor, QDoubleSpinBox):
            editor.setValue(float(value))
        elif isinstance(editor, QSpinBox):
            editor.setValue(int(value))
        elif isinstance(editor, QComboBox):
            token = value.value if isinstance(value, StrEnum) else str(value)
            index = editor.findData(token)
            if index >= 0:
                editor.setCurrentIndex(index)
        del blockers

    def _editor_value(self, descriptor: LatheParameterDescriptor) -> object:
        optional = self._optional_checks.get(descriptor.parameter_id)
        if optional is not None and not optional.isChecked():
            return None
        editor = self._value_widget(descriptor.parameter_id)
        if isinstance(editor, QDoubleSpinBox):
            return float(editor.value())
        if isinstance(editor, QSpinBox):
            return int(editor.value())
        if isinstance(editor, QComboBox):
            if descriptor.enum_type is None:
                raise TypeError("Lathe enum descriptor has no enum type")
            return descriptor.enum_type(str(editor.currentData()))
        raise TypeError("Unsupported Lathe parameter editor")

    def _advanced_toggled(self, expanded: bool) -> None:
        self.advanced_group.setVisible(expanded)
        self.advanced_toggle.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
        )

    @staticmethod
    def _clear_form(form: QFormLayout) -> None:
        while form.count():
            item = form.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()


class LatheWorkspace(QWidget):
    """One coherent Lathe operation browser/editor surface."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("LatheWorkspace")
        self.setAccessibleName(ui_text("lathe.workspace.title"))
        self._presenter: LatheQtPresenter | None = None
        self._snapshot: LathePresenterSnapshot | None = None
        self._guard = False
        self._delete_armed_operation_id: OperationId | None = None
        self._unavailable_reason_key = "lathe.presenter.unavailable"
        self._outcome_key: str | None = None
        self._outcome_diagnostic: LatheQtDiagnostic | None = None
        self._toolpath_controller: LatheToolpathUiController | None = None
        self._simulation_manager: LatheSimulationWindowManager | None = None
        self._toolpath_state = LatheToolpathUiState(
            LatheToolpathUiStateCode.READY
        )

        root = QVBoxLayout(self)
        self._root_layout = root
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(4)
        self.header_label = QLabel()
        self.header_label.setObjectName("LatheWorkspaceTitle")
        root.addWidget(self.header_label)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setObjectName("LatheWorkspaceSplitter")
        self.splitter.addWidget(self._build_navigation())
        self.splitter.addWidget(self._build_editor())
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setSizes([280, 650])
        root.addWidget(self.splitter, 1)
        root.addWidget(self._build_footer())
        self.bind_presenter(None)
        self.retranslate_ui()

    @property
    def presenter(self) -> LatheQtPresenter | None:
        return self._presenter

    @property
    def snapshot(self) -> LathePresenterSnapshot | None:
        return self._snapshot

    @property
    def parameter_editor(self) -> LatheParameterEditor:
        return self._parameter_editor

    @property
    def toolpath_controller(self) -> LatheToolpathUiController | None:
        return self._toolpath_controller

    def bind_presenter(
        self,
        presenter: LatheQtPresenter | None,
        *,
        unavailable_reason: str = "lathe.presenter.unavailable",
    ) -> None:
        """Bind exactly one presenter and disconnect the prior receiver safely."""

        previous = self._presenter
        if previous is presenter:
            if presenter is None:
                self._unavailable_reason_key = unavailable_reason
                self.unavailable_label.setText(_tr(unavailable_reason))
                self.unavailable_label.show()
                self._render_empty()
            else:
                self._render(presenter.snapshot)
            return
        self._outcome_key = None
        self._outcome_diagnostic = None
        if previous is not None:
            for signal, slot in (
                (previous.snapshot_changed, self._render),
                (previous.command_completed, self._command_completed),
                (previous.revision_conflict, self._revision_conflict),
            ):
                try:
                    signal.disconnect(slot)
                except (RuntimeError, TypeError):
                    pass
        self._presenter = presenter
        if presenter is None:
            self._unavailable_reason_key = unavailable_reason
            self._snapshot = None
            self.unavailable_label.setText(_tr(self._unavailable_reason_key))
            self.unavailable_label.show()
            self._render_empty()
            return
        presenter.snapshot_changed.connect(self._render)
        presenter.command_completed.connect(self._command_completed)
        presenter.revision_conflict.connect(self._revision_conflict)
        self.unavailable_label.hide()
        self._render(presenter.snapshot)

    def bind_toolpath_controller(
        self, controller: LatheToolpathUiController | None
    ) -> None:
        """Create Preview/Cancel controls only for the additive capability."""

        if controller is not None and not isinstance(
            controller, LatheToolpathUiController
        ):
            raise TypeError("Lathe workspace toolpath controller is invalid")
        previous = self._toolpath_controller
        if previous is controller:
            return
        if previous is not None:
            try:
                previous.state_changed.disconnect(self._toolpath_state_changed)
            except (RuntimeError, TypeError):
                pass
        self._toolpath_controller = controller
        existing = getattr(self, "toolpath_action_bar", None)
        if existing is not None:
            self._root_layout.removeWidget(existing)
            existing.setParent(None)
            existing.deleteLater()
            for name in (
                "toolpath_action_bar",
                "preview_toolpath_button",
                "cancel_toolpath_button",
            ):
                if hasattr(self, name):
                    delattr(self, name)
        self._toolpath_state = LatheToolpathUiState(
            LatheToolpathUiStateCode.READY
        )
        if controller is None:
            self._render_outcome()
            return
        bar = QFrame()
        bar.setObjectName("LatheToolpathActionBar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(6, 3, 6, 3)
        row.addStretch(1)
        self.preview_toolpath_button = QPushButton()
        self.preview_toolpath_button.setObjectName("LathePreviewToolpathButton")
        self.preview_toolpath_button.clicked.connect(self._preview_toolpath)
        self.cancel_toolpath_button = QPushButton()
        self.cancel_toolpath_button.setObjectName("LatheCancelCalculationButton")
        self.cancel_toolpath_button.clicked.connect(self._cancel_toolpath)
        row.addWidget(self.preview_toolpath_button)
        row.addWidget(self.cancel_toolpath_button)
        self.toolpath_action_bar = bar
        self._root_layout.insertWidget(self._root_layout.count() - 1, bar)
        controller.state_changed.connect(self._toolpath_state_changed)
        self._toolpath_state = controller.state
        self.retranslate_ui()

    def bind_simulation_manager(
        self, manager: LatheSimulationWindowManager | None
    ) -> None:
        """Bind the optional Stage 12.6A view without owning simulation data."""

        if manager is not None and not isinstance(manager, LatheSimulationWindowManager):
            raise TypeError("Lathe workspace simulation manager is invalid")
        if self._simulation_manager is manager:
            return
        self._simulation_manager = manager
        existing = getattr(self, "simulation_action_bar", None)
        if existing is not None:
            self._root_layout.removeWidget(existing)
            existing.deleteLater()
            del self.simulation_action_bar
            del self.simulation_button
        if manager is None:
            return
        bar = QFrame(self)
        bar.setObjectName("LatheSimulationActionBar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(6, 3, 6, 3)
        row.addStretch(1)
        self.simulation_button = QPushButton(bar)
        self.simulation_button.setObjectName("LatheOpenSimulation12_6A")
        self.simulation_button.clicked.connect(lambda: manager.open())
        row.addWidget(self.simulation_button)
        self.simulation_action_bar = bar
        self._root_layout.insertWidget(self._root_layout.count() - 1, bar)
        self.retranslate_ui()

    def retranslate_ui(self, _language: object = None) -> None:
        """Retranslate presentation while stable IDs and values stay untouched."""

        self.setAccessibleName(_tr("lathe.workspace.title"))
        self.header_label.setText(_tr("lathe.workspace.title"))
        self.strategy_label.setText(_tr("lathe.strategy.browser.title"))
        self.operation_label.setText(_tr("lathe.operation.list.title"))
        self.create_button.setText(_tr("lathe.operation.create"))
        self.delete_button.setText(
            _tr("lathe.operation.delete.confirm")
            if self._delete_armed_operation_id is not None
            else _tr("lathe.operation.delete")
        )
        self.strategy_apply_button.setText(_tr("lathe.strategy.apply"))
        self.enable_check.setText(_tr("lathe.operation.enabled"))
        self.validate_button.setText(_tr("lathe.operation.validate"))
        self.parameters_apply_button.setText(_tr("lathe.parameters.apply"))
        self.tabs.setTabText(0, _tr("lathe.parameters.title"))
        self.tabs.setTabText(1, _tr("lathe.tool.title"))
        self.tabs.setTabText(2, _tr("lathe.geometry.title"))
        self.tabs.setTabText(3, _tr("lathe.diagnostics.title"))
        self.required_tool_caption.setText(_tr("lathe.tool.required_capability"))
        self.tool_bind_button.setText(_tr("lathe.tool.bind"))
        self.tool_clear_button.setText(_tr("lathe.tool.clear"))
        self.geometry_help.setText(_tr("lathe.geometry.current_selection.help"))
        self.geometry_bind_button.setText(_tr("lathe.geometry.bind"))
        self.geometry_clear_button.setText(_tr("lathe.geometry.clear"))
        if self._toolpath_controller is not None:
            self.preview_toolpath_button.setText(
                _tr("lathe.toolpath.preview.action")
            )
            self.cancel_toolpath_button.setText(
                _tr("lathe.toolpath.cancel.action")
            )
        if self._simulation_manager is not None:
            self.simulation_button.setText(_tr("lathe.simulation.title"))
        self._parameter_editor.retranslate_ui()
        if self._presenter is None:
            self.unavailable_label.setText(_tr(self._unavailable_reason_key))
        self._set_accessibility()
        if self._snapshot is not None:
            self._render(self._snapshot)
        self._render_outcome()

    def refresh_geometry_selection(self) -> None:
        """Update non-mutating selection guidance without binding automatically."""

        self.geometry_selection_summary.setText(
            _tr("lathe.geometry.current_selection.ready")
        )

    def _build_navigation(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("LatheNavigationPanel")
        panel.setMinimumWidth(210)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(4, 4, 4, 4)
        self.strategy_label = QLabel()
        layout.addWidget(self.strategy_label)
        self.strategy_tree = QTreeWidget()
        self.strategy_tree.setObjectName("LatheStrategyTree")
        self.strategy_tree.setHeaderHidden(True)
        self.strategy_tree.setRootIsDecorated(True)
        layout.addWidget(self.strategy_tree, 2)
        strategy_row = QHBoxLayout()
        self.create_button = QPushButton()
        self.create_button.setObjectName("LatheCreateOperationButton")
        self.create_button.clicked.connect(self._create_operation)
        self.strategy_apply_button = QPushButton()
        self.strategy_apply_button.setObjectName("LatheStrategyApplyButton")
        self.strategy_apply_button.clicked.connect(self._apply_strategy)
        strategy_row.addWidget(self.create_button)
        strategy_row.addWidget(self.strategy_apply_button)
        layout.addLayout(strategy_row)
        self.operation_label = QLabel()
        layout.addWidget(self.operation_label)
        self.operation_list = QListWidget()
        self.operation_list.setObjectName("LatheOperationList")
        self.operation_list.currentItemChanged.connect(self._operation_selected)
        layout.addWidget(self.operation_list, 2)
        operation_row = QHBoxLayout()
        self.enable_check = QCheckBox()
        self.enable_check.setObjectName("LatheOperationEnabledCheck")
        self.enable_check.toggled.connect(self._enabled_changed)
        self.delete_button = QPushButton()
        self.delete_button.setObjectName("LatheDeleteOperationButton")
        self.delete_button.setAutoDefault(False)
        self.delete_button.clicked.connect(self._delete_operation)
        operation_row.addWidget(self.enable_check)
        operation_row.addStretch(1)
        operation_row.addWidget(self.delete_button)
        layout.addLayout(operation_row)
        return panel

    def _build_editor(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("LatheEditorPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(4, 4, 4, 4)
        self.unavailable_label = QLabel()
        self.unavailable_label.setObjectName("LathePresenterUnavailableState")
        self.unavailable_label.setWordWrap(True)
        layout.addWidget(self.unavailable_label)
        self.tabs = QTabWidget()
        self.tabs.setObjectName("LatheEditorTabs")
        self.tabs.addTab(self._build_parameters_tab(), "")
        self.tabs.addTab(self._build_tool_tab(), "")
        self.tabs.addTab(self._build_geometry_tab(), "")
        self.tabs.addTab(self._build_diagnostics_tab(), "")
        layout.addWidget(self.tabs, 1)
        action_row = QHBoxLayout()
        action_row.addStretch(1)
        self.validate_button = QPushButton()
        self.validate_button.setObjectName("LatheValidateOperationButton")
        self.validate_button.clicked.connect(self._validate_operation)
        self.parameters_apply_button = QPushButton()
        self.parameters_apply_button.setObjectName("LatheParametersApplyButton")
        self.parameters_apply_button.clicked.connect(self._apply_parameters)
        action_row.addWidget(self.validate_button)
        action_row.addWidget(self.parameters_apply_button)
        layout.addLayout(action_row)
        return panel

    def _build_parameters_tab(self) -> QWidget:
        self._parameter_editor = LatheParameterEditor()
        scroll = QScrollArea()
        scroll.setObjectName("LatheParameterScrollArea")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        scroll.setWidget(self._parameter_editor)
        return scroll

    def _build_tool_tab(self) -> QWidget:
        tab = QWidget()
        tab.setObjectName("LatheToolSection")
        layout = QVBoxLayout(tab)
        row = QHBoxLayout()
        self.required_tool_caption = QLabel()
        self.required_tool_value = QLabel("—")
        self.required_tool_value.setObjectName("LatheRequiredToolCapability")
        row.addWidget(self.required_tool_caption)
        row.addWidget(self.required_tool_value, 1)
        layout.addLayout(row)
        self.tool_selector = QComboBox()
        self.tool_selector.setObjectName("LatheToolSelector")
        self.tool_selector.currentIndexChanged.connect(self._tool_choice_changed)
        layout.addWidget(self.tool_selector)
        self.tool_compatibility = QLabel()
        self.tool_compatibility.setObjectName("LatheToolCompatibilityState")
        self.tool_compatibility.setWordWrap(True)
        layout.addWidget(self.tool_compatibility)
        button_row = QHBoxLayout()
        self.tool_bind_button = QPushButton()
        self.tool_bind_button.setObjectName("LatheToolBindButton")
        self.tool_bind_button.clicked.connect(self._bind_tool)
        self.tool_clear_button = QPushButton()
        self.tool_clear_button.setObjectName("LatheToolClearButton")
        self.tool_clear_button.clicked.connect(self._clear_tool)
        button_row.addWidget(self.tool_bind_button)
        button_row.addWidget(self.tool_clear_button)
        button_row.addStretch(1)
        layout.addLayout(button_row)
        layout.addStretch(1)
        return tab

    def _build_geometry_tab(self) -> QWidget:
        tab = QWidget()
        tab.setObjectName("LatheGeometrySection")
        layout = QVBoxLayout(tab)
        self.geometry_help = QLabel()
        self.geometry_help.setWordWrap(True)
        layout.addWidget(self.geometry_help)
        self.geometry_selector = QComboBox()
        self.geometry_selector.setObjectName("LatheGeometrySelector")
        self.geometry_selector.setEnabled(False)
        layout.addWidget(self.geometry_selector)
        self.geometry_selection_summary = QLabel()
        self.geometry_selection_summary.setObjectName(
            "LatheGeometrySelectionSummary"
        )
        self.geometry_selection_summary.setWordWrap(True)
        layout.addWidget(self.geometry_selection_summary)
        button_row = QHBoxLayout()
        self.geometry_bind_button = QPushButton()
        self.geometry_bind_button.setObjectName("LatheGeometryBindButton")
        self.geometry_bind_button.clicked.connect(self._bind_geometry)
        self.geometry_clear_button = QPushButton()
        self.geometry_clear_button.setObjectName("LatheGeometryClearButton")
        self.geometry_clear_button.clicked.connect(self._clear_geometry)
        button_row.addWidget(self.geometry_bind_button)
        button_row.addWidget(self.geometry_clear_button)
        button_row.addStretch(1)
        layout.addLayout(button_row)
        layout.addStretch(1)
        return tab

    def _build_diagnostics_tab(self) -> QWidget:
        tab = QWidget()
        tab.setObjectName("LatheDiagnosticsSection")
        layout = QVBoxLayout(tab)
        self.diagnostics_list = QListWidget()
        self.diagnostics_list.setObjectName("LatheDiagnosticsList")
        layout.addWidget(self.diagnostics_list)
        return tab

    def _build_footer(self) -> QWidget:
        footer = QFrame()
        footer.setObjectName("LatheWorkspaceFooter")
        layout = QHBoxLayout(footer)
        layout.setContentsMargins(6, 3, 6, 3)
        self.readiness_label = QLabel()
        self.readiness_label.setObjectName("LatheReadinessDisplay")
        self.outcome_label = QLabel()
        self.outcome_label.setObjectName("LatheCommandOutcomeDisplay")
        self.outcome_label.setWordWrap(True)
        layout.addWidget(self.readiness_label)
        layout.addStretch(1)
        layout.addWidget(self.outcome_label, 2)
        return footer

    def _render(self, snapshot: LathePresenterSnapshot) -> None:
        if not isinstance(snapshot, LathePresenterSnapshot):
            return
        self._snapshot = snapshot
        self._guard = True
        try:
            self._render_strategies(snapshot)
            self._render_operations(snapshot)
            active = self._active_operation(snapshot)
            descriptor = self._strategy_descriptor(
                snapshot, None if active is None else active.strategy_id
            )
            self._parameter_editor.set_operation(active, descriptor)
            self._render_tool(active, descriptor)
            self._render_geometry(active, descriptor)
            self._render_diagnostics(active)
            self._render_readiness(snapshot, active)
            self._apply_mutation_state(snapshot, active)
            self._render_outcome()
        finally:
            self._guard = False

    def _render_empty(self) -> None:
        self._guard = True
        try:
            self.strategy_tree.clear()
            self.operation_list.clear()
            self.tool_selector.clear()
            self.geometry_selector.clear()
            self.diagnostics_list.clear()
            self._parameter_editor.set_operation(None, None)
            self.readiness_label.setText(_tr("lathe.readiness.unavailable"))
            self._render_outcome()
            self._apply_mutation_state(None, None)
        finally:
            self._guard = False

    def _render_strategies(self, snapshot: LathePresenterSnapshot) -> None:
        selected_id = self._selected_strategy_id()
        if selected_id is None and snapshot.operations:
            selected_id = snapshot.operations[0].strategy_id
        self.strategy_tree.clear()
        by_family: dict[LatheStrategyFamily, QTreeWidgetItem] = {}
        for descriptor in snapshot.strategies:
            parent = by_family.get(descriptor.family_id)
            if parent is None:
                parent = QTreeWidgetItem([_tr(_family_key(descriptor.family_id))])
                parent.setData(0, _IDENTITY_ROLE, descriptor.family_id)
                parent.setFlags(parent.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                self.strategy_tree.addTopLevelItem(parent)
                by_family[descriptor.family_id] = parent
            item = QTreeWidgetItem([_tr(_strategy_key(descriptor.strategy_id))])
            item.setData(0, _IDENTITY_ROLE, descriptor.strategy_id)
            parent.addChild(item)
            if descriptor.strategy_id == selected_id:
                self.strategy_tree.setCurrentItem(item)
            parent.setExpanded(True)
        if self.strategy_tree.currentItem() is None:
            first_family = self.strategy_tree.topLevelItem(0)
            if first_family is not None and first_family.childCount():
                self.strategy_tree.setCurrentItem(first_family.child(0))

    def _render_operations(self, snapshot: LathePresenterSnapshot) -> None:
        self.operation_list.clear()
        for index, operation in enumerate(snapshot.operations, start=1):
            label = _tr(
                "lathe.operation.item",
                index=index,
                strategy=_tr(_strategy_key(operation.strategy_id)),
            )
            item = QListWidgetItem(label)
            item.setData(_IDENTITY_ROLE, operation.ownership.operation_id)
            self.operation_list.addItem(item)
            if operation.ownership.operation_id == snapshot.active_operation_id:
                self.operation_list.setCurrentItem(item)
        active = self._active_operation(snapshot)
        with QSignalBlocker(self.enable_check):
            self.enable_check.setChecked(True if active is None else active.enabled)

    def _render_tool(
        self,
        active: LatheOperationSnapshot | None,
        descriptor: LatheStrategyDescriptor | None,
    ) -> None:
        self.tool_selector.clear()
        if active is None or descriptor is None or self._presenter is None:
            self.required_tool_value.setText("—")
            self.tool_compatibility.setText(_tr("lathe.tool.no_active_operation"))
            return
        required = next(iter(descriptor.required_tool_capabilities))
        self.required_tool_value.setText(_tr(_capability_key(required)))
        choices = self._presenter.tool_choices_for(active.strategy_id)
        selected_reference = (
            None
            if active.tool_binding is None
            else LatheToolReference(
                active.tool_binding.tool_id,
                active.tool_binding.profile_id,
                active.tool_binding.assembly_id,
            )
        )
        for choice in choices:
            compatible = choice.supports(required)
            status = _tr(
                "lathe.tool.compatible" if compatible else "lathe.tool.incompatible"
            )
            self.tool_selector.addItem(
                f"{choice.display_name} · {status}", choice
            )
            if choice.reference == selected_reference:
                self.tool_selector.setCurrentIndex(self.tool_selector.count() - 1)
        if not choices:
            self.tool_selector.addItem(_tr("lathe.tool.empty"), None)
        self._tool_choice_changed()

    def _render_geometry(
        self,
        active: LatheOperationSnapshot | None,
        descriptor: LatheStrategyDescriptor | None,
    ) -> None:
        self.geometry_selector.clear()
        if active is None or descriptor is None:
            self.geometry_selection_summary.setText(
                _tr("lathe.geometry.no_active_operation")
            )
            return
        for kind in descriptor.allowed_geometry_kinds:
            self.geometry_selector.addItem(_tr(_geometry_key(kind)), kind)
        binding = active.geometry_binding
        if binding is None:
            self.geometry_selection_summary.setText(
                _tr("lathe.geometry.not_bound")
            )
            return
        index = self.geometry_selector.findData(binding.kind)
        if index >= 0:
            self.geometry_selector.setCurrentIndex(index)
        self.geometry_selection_summary.setText(
            _tr(
                "lathe.geometry.bound_summary",
                kind=_tr(_geometry_key(binding.kind)),
                count=len(binding.entity_ids),
            )
        )

    def _render_diagnostics(
        self, active: LatheOperationSnapshot | None
    ) -> None:
        self.diagnostics_list.clear()
        if active is None:
            self.diagnostics_list.addItem(_tr("lathe.diagnostics.no_active"))
            return
        for diagnostic in active.diagnostics:
            self.diagnostics_list.addItem(
                self._diagnostic_text(
                    LatheQtDiagnostic.from_domain(diagnostic)
                )
            )
        if not active.diagnostics:
            self.diagnostics_list.addItem(_tr("lathe.diagnostics.none"))

    def _render_readiness(
        self,
        snapshot: LathePresenterSnapshot,
        active: LatheOperationSnapshot | None,
    ) -> None:
        if active is None:
            value = _tr("lathe.readiness.no_operation")
        else:
            value = _tr(
                f"lathe.readiness.{active.readiness.value.casefold()}"
            )
        self.readiness_label.setText(value)
        self.readiness_label.setAccessibleDescription(
            _tr("lathe.readiness.not_calculated")
        )

    def _apply_mutation_state(
        self,
        snapshot: LathePresenterSnapshot | None,
        active: LatheOperationSnapshot | None,
    ) -> None:
        available = snapshot is not None and not snapshot.closed
        writable = available and not snapshot.read_only if snapshot is not None else False
        has_setup = (
            active is not None
            or (
                self._presenter is not None
                and self._presenter.facade.service.session.setup_id is not None
            )
        )
        self.create_button.setEnabled(writable and has_setup)
        for control in (
            self.delete_button,
            self.strategy_apply_button,
            self.enable_check,
            self.parameters_apply_button,
            self.tool_bind_button,
            self.tool_clear_button,
            self.geometry_bind_button,
            self.geometry_clear_button,
        ):
            control.setEnabled(writable and active is not None)
        self.validate_button.setEnabled(available and active is not None)
        self._parameter_editor.set_read_only(not writable or active is None)
        self._tool_choice_changed()
        if self._toolpath_controller is not None:
            calculating = self._toolpath_state.code in {
                LatheToolpathUiStateCode.CALCULATING,
                LatheToolpathUiStateCode.CANCELLING,
            }
            self.preview_toolpath_button.setEnabled(
                bool(
                    writable
                    and active is not None
                    and active.readiness is LatheOperationReadiness.READY
                    and not calculating
                )
            )
            self.cancel_toolpath_button.setEnabled(calculating)

    def _create_operation(self) -> None:
        presenter = self._presenter
        strategy_id = self._selected_strategy_id()
        if presenter is not None and strategy_id is not None:
            presenter.create_operation(strategy_id)

    def _operation_selected(
        self, current: QListWidgetItem | None, _previous: QListWidgetItem | None
    ) -> None:
        if self._guard or self._presenter is None:
            return
        self._delete_armed_operation_id = None
        self.delete_button.setText(_tr("lathe.operation.delete"))
        operation_id = None if current is None else current.data(_IDENTITY_ROLE)
        self._presenter.select_operation(operation_id)

    def _apply_strategy(self) -> None:
        active = self._active_operation(self._snapshot)
        strategy_id = self._selected_strategy_id()
        if self._presenter is not None and active is not None and strategy_id is not None:
            self._presenter.change_strategy(
                active.ownership.operation_id,
                strategy_id,
                active.revision,
            )

    def _enabled_changed(self, enabled: bool) -> None:
        if self._guard:
            return
        active = self._active_operation(self._snapshot)
        if self._presenter is not None and active is not None:
            self._presenter.set_enabled(
                active.ownership.operation_id, enabled, active.revision
            )

    def _delete_operation(self) -> None:
        active = self._active_operation(self._snapshot)
        if self._presenter is None or active is None:
            return
        operation_id = active.ownership.operation_id
        if self._delete_armed_operation_id != operation_id:
            self._delete_armed_operation_id = operation_id
            self.delete_button.setText(_tr("lathe.operation.delete.confirm"))
            self._set_outcome_key("lathe.operation.delete.confirm.help")
            return
        self._delete_armed_operation_id = None
        self._presenter.delete_operation(operation_id, active.revision)

    def _validate_operation(self) -> None:
        active = self._active_operation(self._snapshot)
        if self._presenter is not None and active is not None:
            self._presenter.validate_operation(
                active.ownership.operation_id, active.revision
            )

    def _apply_parameters(self) -> None:
        active = self._active_operation(self._snapshot)
        if self._presenter is None or active is None:
            return
        updates = self._parameter_editor.updates()
        if not updates:
            self._set_outcome_key("lathe.parameters.no_changes")
            return
        self._presenter.apply_parameter_changes(
            active.ownership.operation_id, updates, active.revision
        )

    def _bind_tool(self) -> None:
        active = self._active_operation(self._snapshot)
        choice = self.tool_selector.currentData()
        if (
            self._presenter is not None
            and active is not None
            and isinstance(choice, LatheToolChoice)
        ):
            self._presenter.bind_tool(
                active.ownership.operation_id,
                choice.reference,
                active.revision,
            )

    def _clear_tool(self) -> None:
        active = self._active_operation(self._snapshot)
        if self._presenter is not None and active is not None:
            self._presenter.clear_tool(
                active.ownership.operation_id, active.revision
            )

    def _tool_choice_changed(self, _index: int = -1) -> None:
        active = self._active_operation(self._snapshot)
        descriptor = self._strategy_descriptor(
            self._snapshot, None if active is None else active.strategy_id
        )
        choice = self.tool_selector.currentData()
        compatible = False
        if descriptor is not None and isinstance(choice, LatheToolChoice):
            required = next(iter(descriptor.required_tool_capabilities))
            compatible = choice.supports(required)
        self.tool_compatibility.setText(
            _tr(
                "lathe.tool.compatible"
                if compatible
                else "lathe.tool.incompatible_or_unavailable"
            )
        )
        snapshot = self._snapshot
        writable = bool(snapshot and not snapshot.read_only and not snapshot.closed)
        self.tool_bind_button.setEnabled(
            writable and active is not None and compatible
        )

    def _bind_geometry(self) -> None:
        active = self._active_operation(self._snapshot)
        if self._presenter is not None and active is not None:
            self._presenter.bind_current_geometry(
                active.ownership.operation_id, active.revision
            )

    def _clear_geometry(self) -> None:
        active = self._active_operation(self._snapshot)
        if self._presenter is not None and active is not None:
            self._presenter.clear_geometry(
                active.ownership.operation_id, active.revision
            )

    def _preview_toolpath(self) -> None:
        active = self._active_operation(self._snapshot)
        if self._toolpath_controller is not None and active is not None:
            self._toolpath_controller.preview(active)

    def _cancel_toolpath(self) -> None:
        if self._toolpath_controller is not None:
            self._toolpath_controller.cancel()

    def _toolpath_state_changed(self, state: object) -> None:
        if not isinstance(state, LatheToolpathUiState):
            return
        self._toolpath_state = state
        active = self._active_operation(self._snapshot)
        self._apply_mutation_state(self._snapshot, active)
        self._render_outcome()

    def _command_completed(self, result: LatheQtCommandResult) -> None:
        if not isinstance(result, LatheQtCommandResult):
            return
        if result.accepted and result.changed and self._toolpath_controller is not None:
            self._toolpath_controller.invalidate_after_edit()
        if result.accepted:
            self._set_outcome_key("lathe.command.accepted")
        elif result.diagnostics:
            self._set_outcome_diagnostic(result.diagnostics[0])
        else:
            self._set_outcome_key("lathe.command.rejected")

    def _revision_conflict(self, _result: object) -> None:
        self._set_outcome_key("lathe.diagnostic.revision_mismatch")

    def _set_outcome_key(self, key: str) -> None:
        self._outcome_key = key
        self._outcome_diagnostic = None
        self._render_outcome()

    def _set_outcome_diagnostic(self, diagnostic: LatheQtDiagnostic) -> None:
        self._outcome_key = None
        self._outcome_diagnostic = diagnostic
        self._render_outcome()

    def _render_outcome(self) -> None:
        snapshot = self._snapshot
        if snapshot is not None and snapshot.read_only:
            self.outcome_label.setText(_tr("lathe.read_only"))
            return
        if (
            self._toolpath_controller is not None
            and self._toolpath_state.code is not LatheToolpathUiStateCode.READY
        ):
            text = _tr(
                f"lathe.toolpath.status.{self._toolpath_state.code.value}"
            )
            if self._toolpath_state.code in {
                LatheToolpathUiStateCode.PREVIEW_READY,
                LatheToolpathUiStateCode.CACHE_HIT,
            }:
                by_code = {
                    item.code.value: item
                    for item in self._toolpath_state.diagnostics
                }
                details = tuple(
                    _tr(f"lathe.diagnostic.{code}")
                    for code in _THREAD_SUCCESS_DIAGNOSTIC_CODES
                    if code in by_code
                )
                if details:
                    text = " · ".join((text, *details))
            elif (
                self._toolpath_state.diagnostic is not None
                and self._toolpath_state.diagnostic.code.value
                in _THREAD_FAILURE_DIAGNOSTIC_CODES
            ):
                text = " · ".join(
                    (
                        text,
                        _tr(
                            "lathe.diagnostic."
                            f"{self._toolpath_state.diagnostic.code.value}"
                        ),
                    )
                )
            self.outcome_label.setText(text)
            return
        if self._outcome_diagnostic is not None:
            self.outcome_label.setText(
                self._diagnostic_text(self._outcome_diagnostic)
            )
            return
        if self._outcome_key is not None:
            self.outcome_label.setText(_tr(self._outcome_key))
            return
        self.outcome_label.setText("")

    def _diagnostic_text(self, diagnostic: LatheQtDiagnostic) -> str:
        code = diagnostic.code
        active = self._active_operation(self._snapshot)
        if active is not None and active.strategy_id in {
            LatheStrategyId.OD_THREAD,
            LatheStrategyId.ID_THREAD,
        }:
            if code == "invalid_parameter" and diagnostic.field_id is not None:
                code = _THREAD_PARAMETER_DIAGNOSTIC_CODES.get(
                    diagnostic.field_id,
                    code,
                )
            elif code == "incompatible_tool":
                code = "incompatible_thread_tool"
            elif code == "incompatible_geometry":
                code = "incompatible_thread_geometry"
        key = code if code.startswith("lathe.") else f"lathe.diagnostic.{code}"
        text = _tr(key)
        if diagnostic.field_id:
            text = f"{_tr(f'lathe.parameter.{diagnostic.field_id}.label')}: {text}"
        return text

    def _selected_strategy_id(self) -> LatheStrategyId | None:
        item = self.strategy_tree.currentItem()
        value = None if item is None else item.data(0, _IDENTITY_ROLE)
        try:
            return LatheStrategyId(str(value))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _active_operation(
        snapshot: LathePresenterSnapshot | None,
    ) -> LatheOperationSnapshot | None:
        if snapshot is None:
            return None
        return next(
            (
                item
                for item in snapshot.operations
                if item.ownership.operation_id == snapshot.active_operation_id
            ),
            None,
        )

    @staticmethod
    def _strategy_descriptor(
        snapshot: LathePresenterSnapshot | None,
        strategy_id: LatheStrategyId | None,
    ) -> LatheStrategyDescriptor | None:
        if snapshot is None or strategy_id is None:
            return None
        return next(
            (
                item
                for item in snapshot.strategies
                if item.strategy_id == strategy_id
            ),
            None,
        )

    def _set_accessibility(self) -> None:
        controls = (
            (self.strategy_tree, "lathe.strategy.browser.title"),
            (self.operation_list, "lathe.operation.list.title"),
            (self.create_button, "lathe.operation.create"),
            (self.delete_button, "lathe.operation.delete"),
            (self.strategy_apply_button, "lathe.strategy.apply"),
            (self.enable_check, "lathe.operation.enabled"),
            (self.validate_button, "lathe.operation.validate"),
            (self.parameters_apply_button, "lathe.parameters.apply"),
            (self.tool_selector, "lathe.tool.selector"),
            (self.tool_bind_button, "lathe.tool.bind"),
            (self.tool_clear_button, "lathe.tool.clear"),
            (self.geometry_selector, "lathe.geometry.selector"),
            (self.geometry_bind_button, "lathe.geometry.bind"),
            (self.geometry_clear_button, "lathe.geometry.clear"),
            (self.diagnostics_list, "lathe.diagnostics.title"),
            (self.readiness_label, "lathe.readiness.title"),
        )
        for control, key in controls:
            control.setAccessibleName(_tr(key))
        if self._toolpath_controller is not None:
            self.preview_toolpath_button.setAccessibleName(
                _tr("lathe.toolpath.preview.action")
            )
            self.preview_toolpath_button.setAccessibleDescription(
                _tr("lathe.toolpath.preview.help")
            )
            self.cancel_toolpath_button.setAccessibleName(
                _tr("lathe.toolpath.cancel.action")
            )
            self.cancel_toolpath_button.setAccessibleDescription(
                _tr("lathe.toolpath.cancel.help")
            )
        self.delete_button.setAccessibleDescription(
            _tr("lathe.operation.delete.confirm.help")
        )
        self.tool_compatibility.setAccessibleDescription(
            _tr("lathe.tool.compatibility.help")
        )


__all__ = ["LatheParameterEditor", "LatheWorkspace"]
