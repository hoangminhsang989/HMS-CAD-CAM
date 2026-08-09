"""Dedicated production Tool Library management dialog for Stage16A WP2."""

from __future__ import annotations

import logging
from typing import Callable

from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from hms_cadcam.cam.domain import (
    DEFAULT_TOOL_PROFILE_REGISTRY,
    HolderDefinition,
    LengthUnit,
    Revision,
    ToolCommonDefaults,
    ToolCoolantCapability,
    ToolDefinition,
    ToolDefinitionId,
    ToolFamily,
    ToolHand,
    ToolProfileSaveMode,
    ToolProgramProfile,
    preview_tool_profile_capture,
)
from hms_cadcam.cam.tool_library import (
    ARCHIVE_UNAVAILABLE_WITH_CURRENT_SCHEMA,
    ToolDefinitionDraft,
    ToolLibraryRecord,
    ToolLibrarySort,
    tool_library_records,
)
from hms_cadcam.project.service import ProjectService
from hms_cadcam.ui.localization import localize_widget_tree, ui_text
from hms_cadcam.ui.tool_program_profiles import (
    ToolProfileEditorDialog,
    ToolProgramProfilesWidget,
)


LOGGER = logging.getLogger(__name__)


_FAMILY_LABELS = {
    ToolFamily.END_MILL: "End mill",
    ToolFamily.BALL_END_MILL: "Ball end mill",
    ToolFamily.BULL_NOSE_END_MILL: "Bull nose end mill",
    ToolFamily.DRILL: "Drill",
    ToolFamily.CENTER_DRILL: "Center drill",
    ToolFamily.CHAMFER_MILL: "Chamfer mill",
    ToolFamily.FACE_MILL: "Face mill",
    ToolFamily.REAMER: "Reamer",
    ToolFamily.TAP: "Tap",
    ToolFamily.BORING_BAR: "Boring bar",
    ToolFamily.TURNING_INSERT: "Turning insert",
    ToolFamily.CUSTOM: "Custom Tool",
}


def _family_label(family: ToolFamily) -> str:
    return ui_text(_FAMILY_LABELS[family])


class ToolDefinitionDialog(QDialog):
    """Compact Basic/Geometry/Assembly authoring surface."""

    def __init__(
        self,
        *,
        tool: ToolDefinition | None = None,
        holders: tuple[HolderDefinition, ...] = (),
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ToolDefinitionDialog")
        self.setWindowTitle(ui_text("Create Tool" if tool is None else "Edit Tool"))
        self.setAccessibleName(self.windowTitle())
        self.setAccessibleDescription(
            ui_text("Edit real Tool definition and optional assembly fields.")
        )
        self.setModal(True)
        self._tool = tool
        self._holders = holders
        initial = (
            ToolDefinitionDraft.from_tool(tool)
            if tool is not None
            else ToolDefinitionDraft(
                "",
                ToolFamily.BALL_END_MILL,
                LengthUnit.MM,
                10.0,
                30.0,
                80.0,
                40.0,
                10.0,
                50.0,
                detail_size=1.0,
                assembly_name="",
                stickout=40.0,
                gauge_length=80.0,
            )
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 8)
        root.setSpacing(7)
        self.tabs = QTabWidget()
        self.tabs.setObjectName("ToolDefinitionTabs")
        root.addWidget(self.tabs, 1)
        self.tabs.addTab(self._build_basic(initial), ui_text("Basic"))
        self.tabs.addTab(self._build_geometry(initial), ui_text("Geometry"))
        self.tabs.addTab(self._build_assembly(initial), ui_text("Assembly"))

        self.error_label = QLabel()
        self.error_label.setObjectName("ToolDefinitionValidation")
        self.error_label.setWordWrap(True)
        self.error_label.setVisible(False)
        root.addWidget(self.error_label)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        self.save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        self.save_button.setText(ui_text("Save Tool"))
        self.save_button.setAccessibleName(ui_text("Save validated Tool"))
        cancel = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        cancel.setText(ui_text("Cancel"))
        cancel.setAccessibleName(ui_text("Cancel Tool edit without changes"))
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self.family.currentIndexChanged.connect(self._family_changed)
        self.create_assembly.toggled.connect(self._assembly_toggled)
        self._family_changed()
        self._assembly_toggled(self.create_assembly.isChecked())
        self.setMinimumSize(620, 500)
        self.resize(700, 580)
        localize_widget_tree(self)

    def _build_basic(self, initial: ToolDefinitionDraft) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        self.name = QLineEdit(initial.name)
        self.name.setAccessibleName(ui_text("Tool name"))
        form.addRow(ui_text("Tool name"), self.name)
        self.family = QComboBox()
        self.family.setAccessibleName(ui_text("Tool family"))
        for family in ToolFamily:
            self.family.addItem(_family_label(family), family)
        self.family.setCurrentIndex(max(0, self.family.findData(initial.family)))
        form.addRow(ui_text("Tool family"), self.family)
        self.unit = QComboBox()
        self.unit.setAccessibleName(ui_text("Tool unit"))
        self.unit.addItem("mm", LengthUnit.MM)
        self.unit.addItem("inch", LengthUnit.INCH)
        self.unit.setCurrentIndex(max(0, self.unit.findData(initial.unit)))
        self.unit.setEnabled(self._tool is None)
        form.addRow(ui_text("Unit"), self.unit)
        self.manufacturer = QLineEdit(initial.manufacturer or "")
        self.manufacturer.setAccessibleName(ui_text("Manufacturer"))
        form.addRow(ui_text("Manufacturer"), self.manufacturer)
        self.model = QLineEdit(initial.model or "")
        self.model.setAccessibleName(ui_text("Model"))
        form.addRow(ui_text("Model"), self.model)
        coolant = QGroupBox(ui_text("Coolant capabilities"))
        coolant_layout = QHBoxLayout(coolant)
        self.coolant_checks: dict[ToolCoolantCapability, QCheckBox] = {}
        for capability, label in (
            (ToolCoolantCapability.FLOOD, "Flood"),
            (ToolCoolantCapability.MIST, "Mist"),
            (ToolCoolantCapability.AIR, "Air"),
            (ToolCoolantCapability.THROUGH_TOOL, "Through Tool"),
        ):
            check = QCheckBox(ui_text(label))
            check.setChecked(capability in initial.coolant_capabilities)
            check.setAccessibleName(ui_text(label))
            coolant_layout.addWidget(check)
            self.coolant_checks[capability] = check
        form.addRow(coolant)
        if self._tool is not None:
            revision = QLabel(
                f"{self._tool.revision.value} / "
                f"{self._tool.configuration_revision.value}"
            )
            revision.setAccessibleName(ui_text("Physical / configuration revision"))
            form.addRow(ui_text("Physical / configuration revision"), revision)
        return page

    @staticmethod
    def _spin(value: float, name: str) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setDecimals(6)
        spin.setRange(0.0, 1.0e9)
        spin.setValue(float(value))
        spin.setKeyboardTracking(False)
        spin.setAccessibleName(ui_text(name))
        return spin

    def _build_geometry(self, initial: ToolDefinitionDraft) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        self.principal_size = self._spin(initial.principal_size, "Principal size")
        form.addRow(ui_text("Principal size"), self.principal_size)
        self.cutting_length = self._spin(initial.cutting_length, "Cutting length")
        form.addRow(ui_text("Cutting length"), self.cutting_length)
        self.detail_size_label = QLabel()
        self.detail_size = self._spin(initial.detail_size or 0.0, "Variant size")
        form.addRow(self.detail_size_label, self.detail_size)
        self.detail_angle_label = QLabel()
        self.detail_angle = self._spin(
            initial.detail_angle_degrees or 0.0, "Variant angle"
        )
        self.detail_angle.setMaximum(179.999999)
        form.addRow(self.detail_angle_label, self.detail_angle)
        self.detail_text_label = QLabel(ui_text("Custom description"))
        self.detail_text = QLineEdit(initial.detail_text or "")
        self.detail_text.setAccessibleName(ui_text("Custom description"))
        form.addRow(self.detail_text_label, self.detail_text)
        self.hand_label = QLabel(ui_text("Hand"))
        self.hand = QComboBox()
        self.hand.addItem(ui_text("Right"), ToolHand.RIGHT)
        self.hand.addItem(ui_text("Left"), ToolHand.LEFT)
        self.hand.setCurrentIndex(max(0, self.hand.findData(initial.hand)))
        self.hand.setAccessibleName(ui_text("Tool hand"))
        form.addRow(self.hand_label, self.hand)
        self.overall_length = self._spin(initial.overall_length, "Overall length")
        form.addRow(ui_text("Overall length"), self.overall_length)
        self.usable_length = self._spin(initial.usable_length, "Usable length")
        form.addRow(ui_text("Usable length"), self.usable_length)
        self.shank_diameter = self._spin(initial.shank_diameter, "Shank diameter")
        form.addRow(ui_text("Shank diameter"), self.shank_diameter)
        self.shank_length = self._spin(initial.shank_length, "Shank length")
        form.addRow(ui_text("Shank length"), self.shank_length)
        return page

    def _build_assembly(self, initial: ToolDefinitionDraft) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        self.create_assembly = QCheckBox(ui_text("Create an available Tool Assembly"))
        self.create_assembly.setChecked(initial.create_assembly and self._tool is None)
        self.create_assembly.setEnabled(self._tool is None)
        self.create_assembly.setAccessibleName(
            ui_text("Create assembly for operation selection")
        )
        form.addRow(self.create_assembly)
        self.assembly_name = QLineEdit(initial.assembly_name or "")
        self.assembly_name.setAccessibleName(ui_text("Assembly name"))
        form.addRow(ui_text("Assembly name"), self.assembly_name)
        self.stickout = self._spin(
            initial.stickout or initial.usable_length, "Stickout"
        )
        form.addRow(ui_text("Stickout"), self.stickout)
        self.gauge_length = self._spin(
            initial.gauge_length or initial.overall_length, "Gauge length"
        )
        form.addRow(ui_text("Gauge length"), self.gauge_length)
        self.holder = QComboBox()
        self.holder.setAccessibleName(ui_text("Holder"))
        self.holder.addItem(ui_text("No Holder"), None)
        for holder in self._holders:
            self.holder.addItem(f"{holder.name} · {str(holder.holder_id)[:8]}", holder.holder_id)
        if initial.holder_id is not None:
            self.holder.setCurrentIndex(max(0, self.holder.findData(initial.holder_id)))
        form.addRow(ui_text("Holder"), self.holder)
        note = QLabel(
            ui_text(
                "Existing Tool edits preserve assembly identity and refresh its revision safely."
            )
        )
        note.setWordWrap(True)
        form.addRow(note)
        return page

    def _family_changed(self) -> None:
        try:
            family = ToolFamily(self.family.currentData())
        except (TypeError, ValueError):
            return
        needs_size = family in {
            ToolFamily.BULL_NOSE_END_MILL,
            ToolFamily.CHAMFER_MILL,
            ToolFamily.TAP,
            ToolFamily.BORING_BAR,
            ToolFamily.TURNING_INSERT,
        }
        needs_angle = family in {
            ToolFamily.DRILL,
            ToolFamily.CENTER_DRILL,
            ToolFamily.CHAMFER_MILL,
        }
        needs_text = family is ToolFamily.CUSTOM
        needs_hand = family in {ToolFamily.TAP, ToolFamily.BORING_BAR}
        size_label = {
            ToolFamily.BULL_NOSE_END_MILL: "Corner radius",
            ToolFamily.CHAMFER_MILL: "Tip diameter",
            ToolFamily.TAP: "Thread pitch",
            ToolFamily.BORING_BAR: "Maximum bore diameter",
            ToolFamily.TURNING_INSERT: "Nose radius",
        }.get(family, "Variant size")
        angle_label = (
            "Included angle"
            if family is ToolFamily.CHAMFER_MILL
            else "Point angle"
        )
        self.detail_size_label.setText(ui_text(size_label))
        self.detail_angle_label.setText(ui_text(angle_label))
        for widget in (self.detail_size_label, self.detail_size):
            widget.setVisible(needs_size)
        for widget in (self.detail_angle_label, self.detail_angle):
            widget.setVisible(needs_angle)
        for widget in (self.detail_text_label, self.detail_text):
            widget.setVisible(needs_text)
        for widget in (self.hand_label, self.hand):
            widget.setVisible(needs_hand)

    def _assembly_toggled(self, enabled: bool) -> None:
        enabled = bool(enabled and self._tool is None)
        for widget in (
            self.assembly_name,
            self.stickout,
            self.gauge_length,
            self.holder,
        ):
            widget.setEnabled(enabled)

    @property
    def draft(self) -> ToolDefinitionDraft:
        try:
            family = ToolFamily(self.family.currentData())
            unit = LengthUnit(self.unit.currentData())
            hand = ToolHand(self.hand.currentData())
        except (TypeError, ValueError):
            raise ValueError("Tool family or unit is invalid")
        uses_detail_size = family in {
            ToolFamily.BULL_NOSE_END_MILL,
            ToolFamily.CHAMFER_MILL,
            ToolFamily.TAP,
            ToolFamily.BORING_BAR,
            ToolFamily.TURNING_INSERT,
        }
        uses_detail_angle = family in {
            ToolFamily.DRILL,
            ToolFamily.CENTER_DRILL,
            ToolFamily.CHAMFER_MILL,
        }
        return ToolDefinitionDraft(
            name=self.name.text().strip(),
            family=family,
            unit=unit,
            principal_size=self.principal_size.value(),
            cutting_length=self.cutting_length.value(),
            overall_length=self.overall_length.value(),
            usable_length=self.usable_length.value(),
            shank_diameter=self.shank_diameter.value(),
            shank_length=self.shank_length.value(),
            detail_size=(
                self.detail_size.value() if uses_detail_size else None
            ),
            detail_angle_degrees=(
                self.detail_angle.value() if uses_detail_angle else None
            ),
            detail_text=(
                self.detail_text.text().strip()
                if family is ToolFamily.CUSTOM
                else None
            ),
            hand=hand,
            coolant_capabilities=tuple(
                capability
                for capability, check in self.coolant_checks.items()
                if check.isChecked()
            ),
            manufacturer=self.manufacturer.text().strip() or None,
            model=self.model.text().strip() or None,
            common_defaults=(
                self._tool.common_defaults if self._tool is not None else ToolCommonDefaults()
            ),
            create_assembly=self.create_assembly.isChecked() and self._tool is None,
            assembly_name=self.assembly_name.text().strip() or None,
            stickout=self.stickout.value(),
            gauge_length=self.gauge_length.value(),
            holder_id=self.holder.currentData(),
        )

    def _validate_and_accept(self) -> None:
        try:
            draft = self.draft
            if not draft.name:
                raise ValueError(ui_text("Tool name is required."))
            positive = (
                draft.principal_size,
                draft.cutting_length,
                draft.overall_length,
                draft.usable_length,
                draft.shank_diameter,
                draft.shank_length,
            )
            if any(value <= 0.0 for value in positive):
                raise ValueError(ui_text("Tool dimensions must be greater than zero."))
            if draft.usable_length > draft.overall_length:
                raise ValueError(ui_text("Usable length cannot exceed overall length."))
            if draft.cutting_length > draft.usable_length:
                raise ValueError(ui_text("Cutting length cannot exceed usable length."))
            if draft.shank_length > draft.overall_length:
                raise ValueError(ui_text("Shank length cannot exceed overall length."))
            if draft.create_assembly:
                if draft.stickout is None or draft.stickout <= 0.0:
                    raise ValueError(ui_text("Stickout must be greater than zero."))
                if draft.gauge_length is None or draft.gauge_length < draft.stickout:
                    raise ValueError(ui_text("Gauge length cannot be shorter than stickout."))
        except (TypeError, ValueError) as error:
            self.error_label.setText(str(error))
            self.error_label.setVisible(True)
            return
        self.save_button.setEnabled(False)
        self.accept()


class ToolCommonDefaultsDialog(QDialog):
    """Edit only persisted ``ToolCommonDefaults`` through finite controls."""

    def __init__(
        self, defaults: ToolCommonDefaults, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ToolCommonDefaultsDialog")
        self.setWindowTitle(ui_text("Edit common Tool defaults"))
        self.setAccessibleName(self.windowTitle())
        self.setModal(True)
        root = QVBoxLayout(self)
        form = QFormLayout()
        self._numbers: dict[str, tuple[QCheckBox, QDoubleSpinBox]] = {}
        for field_id, label, value in (
            ("spindle_speed_rpm", "Spindle speed", defaults.spindle_speed_rpm),
            ("cutting_feed_mm_per_min", "Cutting feed", defaults.cutting_feed_mm_per_min),
            ("plunge_feed_mm_per_min", "Plunge feed", defaults.plunge_feed_mm_per_min),
            (
                "maximum_cutting_depth_mm",
                "Maximum cutting depth",
                defaults.maximum_cutting_depth_mm,
            ),
        ):
            row = QWidget()
            layout = QHBoxLayout(row)
            layout.setContentsMargins(0, 0, 0, 0)
            enabled = QCheckBox(ui_text("Use"))
            enabled.setChecked(value is not None)
            spin = QDoubleSpinBox()
            spin.setDecimals(6)
            spin.setRange(0.0, 1.0e9)
            spin.setValue(float(value or 0.0))
            spin.setEnabled(value is not None)
            spin.setAccessibleName(ui_text(label))
            enabled.toggled.connect(spin.setEnabled)
            layout.addWidget(enabled)
            layout.addWidget(spin, 1)
            form.addRow(ui_text(label), row)
            self._numbers[field_id] = (enabled, spin)
        self.coolant = QComboBox()
        for value, label in (
            (None, "Not configured"),
            ("off", "Off"),
            ("flood", "Flood"),
            ("mist", "Mist"),
            ("air", "Air"),
            ("through_tool", "Through Tool"),
        ):
            self.coolant.addItem(ui_text(label), value)
        self.coolant.setCurrentIndex(max(0, self.coolant.findData(defaults.coolant_mode)))
        form.addRow(ui_text("Coolant mode"), self.coolant)
        self.quality = QComboBox()
        for value, label in (
            (None, "Not configured"),
            ("fast", "Fast"),
            ("balanced", "Balanced"),
            ("high", "High quality"),
        ):
            self.quality.addItem(ui_text(label), value)
        self.quality.setCurrentIndex(max(0, self.quality.findData(defaults.quality_profile)))
        form.addRow(ui_text("Quality profile"), self.quality)
        self.reference = QLineEdit(defaults.cutting_data_reference or "")
        self.reference.setAccessibleName(ui_text("Cutting-data reference"))
        form.addRow(ui_text("Cutting-data reference"), self.reference)
        root.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText(ui_text("Save defaults"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(ui_text("Cancel"))
        buttons.accepted.connect(self._accept_if_valid)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self.setMinimumSize(480, 360)
        localize_widget_tree(self)

    @property
    def defaults(self) -> ToolCommonDefaults:
        values = {
            field_id: spin.value() if enabled.isChecked() else None
            for field_id, (enabled, spin) in self._numbers.items()
        }
        return ToolCommonDefaults(
            values["spindle_speed_rpm"],
            values["cutting_feed_mm_per_min"],
            values["plunge_feed_mm_per_min"],
            self.coolant.currentData(),
            self.quality.currentData(),
            values["maximum_cutting_depth_mm"],
            self.reference.text().strip() or None,
        )

    def _accept_if_valid(self) -> None:
        try:
            self.defaults
        except (TypeError, ValueError) as error:
            QMessageBox.warning(self, ui_text("Invalid Tool defaults"), str(error))
            return
        self.accept()


class ToolLibraryDialog(QDialog):
    """Dedicated list/detail workflow; all persistence crosses ProjectService."""

    mutation_committed = Signal(object)

    def __init__(
        self,
        service: ProjectService,
        *,
        initial_tool_id: ToolDefinitionId | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ToolLibraryDialog")
        self.setWindowTitle(ui_text("Production Tool Library"))
        self.setAccessibleName(self.windowTitle())
        self.setAccessibleDescription(
            ui_text("Manage project Tools without creating operations or downstream output.")
        )
        self.setModal(True)
        self._service = service
        self._selected_tool_id = initial_tool_id
        self._records: dict[str, ToolLibraryRecord] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(6)
        root.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setObjectName("ToolLibrarySplitter")
        split.addWidget(self._build_list_surface())
        split.addWidget(self._build_detail_surface())
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        root.addWidget(split, 1)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.button(QDialogButtonBox.StandardButton.Close).setText(ui_text("Close"))
        close.rejected.connect(self.reject)
        root.addWidget(close)
        self.apply_available_geometry(QRect(0, 0, 1366, 768), 1.0)
        self._refresh()
        localize_widget_tree(self)

    def _build_list_surface(self) -> QWidget:
        frame = QFrame()
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(0, 0, 5, 0)
        filters = QGridLayout()
        filters.setHorizontalSpacing(5)
        filters.setVerticalSpacing(4)
        self.search = QLineEdit()
        self.search.setObjectName("ToolLibrarySearch")
        self.search.setPlaceholderText(ui_text("Search name, ID, family, size, assembly or Holder…"))
        self.search.setAccessibleName(ui_text("Search Tool Library"))
        self.search.setClearButtonEnabled(True)
        self.search.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed
        )
        self.search.textChanged.connect(self._refresh)
        filters.addWidget(self.search, 0, 0, 1, 2)
        self.family_filter = QComboBox()
        self.family_filter.setAccessibleName(ui_text("Filter by Tool family"))
        self.family_filter.addItem(ui_text("All families"), None)
        for family in ToolFamily:
            self.family_filter.addItem(_family_label(family), family)
        self.family_filter.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.family_filter.setMinimumContentsLength(10)
        self.family_filter.currentIndexChanged.connect(self._refresh)
        filters.addWidget(self.family_filter, 1, 0)
        self.compatibility_filter = QComboBox()
        self.compatibility_filter.setAccessibleName(ui_text("Filter by strategy compatibility"))
        self.compatibility_filter.addItem(ui_text("All strategies"), None)
        for schema in DEFAULT_TOOL_PROFILE_REGISTRY.schemas:
            self.compatibility_filter.addItem(ui_text(schema.display_name_vi), schema.strategy_id)
        self.compatibility_filter.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.compatibility_filter.setMinimumContentsLength(10)
        self.compatibility_filter.currentIndexChanged.connect(self._refresh)
        filters.addWidget(self.compatibility_filter, 1, 1)
        layout.addLayout(filters)
        sort_row = QHBoxLayout()
        sort_row.addWidget(QLabel(ui_text("Sort")))
        self.sort = QComboBox()
        for value, label in (
            (ToolLibrarySort.NAME, "Name"),
            (ToolLibrarySort.FAMILY, "Family"),
            (ToolLibrarySort.PRINCIPAL_SIZE, "Principal size"),
            (ToolLibrarySort.CONFIGURATION_REVISION, "Configuration revision"),
            (ToolLibrarySort.USAGE, "Usage"),
        ):
            self.sort.addItem(ui_text(label), value)
        self.sort.currentIndexChanged.connect(self._refresh)
        sort_row.addWidget(self.sort)
        self.descending = QCheckBox(ui_text("Descending"))
        self.descending.toggled.connect(self._refresh)
        sort_row.addWidget(self.descending)
        sort_row.addStretch(1)
        layout.addLayout(sort_row)
        self.table = QTreeWidget()
        self.table.setObjectName("ToolLibraryTable")
        self.table.setAccessibleName(ui_text("Project Tool list"))
        self.table.setColumnCount(7)
        self.table.setHeaderLabels(
            tuple(
                ui_text(value)
                for value in (
                    "Tool",
                    "Tool ID",
                    "Family",
                    "Principal size",
                    "Assembly / Holder",
                    "Revision",
                    "Usage",
                )
            )
        )
        self.table.setRootIsDecorated(False)
        self.table.setAlternatingRowColors(True)
        self.table.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.table.header().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        for column in range(1, 7):
            self.table.header().setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents
            )
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.currentItemChanged.connect(self._selection_changed)
        layout.addWidget(self.table, 1)
        actions = QGridLayout()
        actions.setHorizontalSpacing(4)
        actions.setVerticalSpacing(4)
        self.create_button = self._action_button("Create", self._create)
        self.edit_button = self._action_button("Edit", self._edit)
        self.duplicate_button = self._action_button("Duplicate", self._duplicate)
        self.archive_button = self._action_button("Archive / Unarchive", lambda: None)
        self.archive_button.setEnabled(False)
        self.archive_button.setToolTip(ui_text(ARCHIVE_UNAVAILABLE_WITH_CURRENT_SCHEMA))
        self.delete_button = self._action_button("Delete", self._delete)
        self.delete_button.setDefault(False)
        self.delete_button.setAutoDefault(False)
        for index, button in enumerate((
            self.create_button,
            self.edit_button,
            self.duplicate_button,
            self.archive_button,
            self.delete_button,
        )):
            actions.addWidget(button, index // 3, index % 3)
        layout.addLayout(actions)
        return frame

    def _action_button(self, text: str, callback: Callable[[], None]) -> QPushButton:
        button = QPushButton(ui_text(text))
        button.setAccessibleName(ui_text(f"{text} Tool"))
        button.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed
        )
        button.clicked.connect(callback)
        return button

    def _build_detail_surface(self) -> QWidget:
        frame = QFrame()
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(5, 0, 0, 0)
        self.detail_title = QLabel(ui_text("Select a Tool to view details."))
        self.detail_title.setObjectName("ToolLibraryDetailTitle")
        self.detail_title.setWordWrap(True)
        layout.addWidget(self.detail_title)
        self.detail_identity = QLabel()
        self.detail_identity.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByKeyboard)
        self.detail_identity.setWordWrap(True)
        layout.addWidget(self.detail_identity)
        self.compatibility = QLabel()
        self.compatibility.setAccessibleName(ui_text("Tool strategy compatibility"))
        self.compatibility.setWordWrap(True)
        layout.addWidget(self.compatibility)
        self.usage = QLabel()
        self.usage.setAccessibleName(ui_text("Tool usage and references"))
        self.usage.setWordWrap(True)
        layout.addWidget(self.usage)
        common_row = QHBoxLayout()
        self.common_status = QLabel()
        self.common_status.setWordWrap(True)
        common_row.addWidget(self.common_status, 1)
        self.edit_defaults = QPushButton(ui_text("Edit common defaults"))
        self.edit_defaults.setAccessibleName(ui_text("Edit Tool common defaults"))
        self.edit_defaults.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed
        )
        self.edit_defaults.clicked.connect(self._edit_common_defaults)
        common_row.addWidget(self.edit_defaults)
        layout.addLayout(common_row)
        self.profiles = ToolProgramProfilesWidget(parent=frame)
        self.profiles.set_expanded(True)
        self.profiles.save_current_button.hide()
        self.profiles.action_requested.connect(self._profile_action)
        layout.addWidget(self.profiles, 1)
        return frame

    def _refresh(self, *_args: object) -> None:
        try:
            family_data = self.family_filter.currentData()
            family = (
                None if family_data is None else ToolFamily(family_data)
            )
            records = tool_library_records(
                self._service.cam_snapshot,
                query=self.search.text(),
                family=family,
                compatible_strategy_id=self.compatibility_filter.currentData(),
                sort=self.sort.currentData() or ToolLibrarySort.NAME,
                descending=self.descending.isChecked(),
            )
        except (RuntimeError, TypeError, ValueError) as error:
            LOGGER.exception("Tool Library refresh failed")
            self._show_error(str(error))
            return
        selected = self._selected_tool_id
        self._records = {str(item.tool.tool_id): item for item in records}
        self.table.blockSignals(True)
        self.table.clear()
        selected_item = None
        for record in records:
            tool = record.tool
            assemblies = ", ".join(record.assembly_names) or ui_text("No assembly")
            holders = ", ".join(value for value in record.holder_texts if value)
            assembly_text = assemblies + (f" / {holders}" if holders else "")
            row = QTreeWidgetItem(
                (
                    tool.name,
                    str(tool.tool_id)[:12],
                    _family_label(tool.family),
                    f"{record.principal_size:g} {tool.unit.value}",
                    assembly_text,
                    f"{tool.revision.value} / {tool.configuration_revision.value}",
                    str(len(record.usages)),
                )
            )
            row.setData(0, Qt.ItemDataRole.UserRole, str(tool.tool_id))
            self.table.addTopLevelItem(row)
            if tool.tool_id == selected:
                selected_item = row
        self.table.blockSignals(False)
        if selected_item is None and self.table.topLevelItemCount():
            selected_item = self.table.topLevelItem(0)
        self.table.setCurrentItem(selected_item)
        if selected_item is None:
            self._selected_tool_id = None
            self._render_detail(None)
        else:
            self._selection_changed(selected_item, None)
    def _selection_changed(
        self, current: QTreeWidgetItem | None, _previous: QTreeWidgetItem | None
    ) -> None:
        key = str(current.data(0, Qt.ItemDataRole.UserRole)) if current else ""
        record = self._records.get(key)
        self._selected_tool_id = record.tool.tool_id if record is not None else None
        self._render_detail(record)

    def _render_detail(self, record: ToolLibraryRecord | None) -> None:
        enabled = record is not None
        self.edit_button.setEnabled(enabled)
        self.duplicate_button.setEnabled(enabled)
        self.edit_defaults.setEnabled(enabled)
        if record is None:
            self.detail_title.setText(ui_text("Select a Tool to view details."))
            self.detail_identity.clear()
            self.compatibility.clear()
            self.usage.clear()
            self.common_status.clear()
            self.delete_button.setEnabled(False)
            return
        tool = record.tool
        self.detail_title.setText(f"{tool.name} · {_family_label(tool.family)}")
        self.detail_identity.setText(
            f"ID: {tool.tool_id}\n"
            f"{ui_text('Physical revision')}: {tool.revision.value} · "
            f"{ui_text('Configuration revision')}: {tool.configuration_revision.value}"
        )
        strategy_names = tuple(
            ui_text(DEFAULT_TOOL_PROFILE_REGISTRY.schema(value).display_name_vi)
            for value in record.compatible_strategy_ids
        )
        self.compatibility.setText(
            f"{ui_text('Compatible strategies')}: "
            + (", ".join(strategy_names) or ui_text("None"))
        )
        locations = "; ".join(
            f"{item.job_name} / {item.setup_name} / {item.strategy_id}"
            for item in record.usages[:8]
        )
        self.usage.setText(
            f"{ui_text('Assemblies')}: {len(record.assembly_ids)} · "
            f"{ui_text('Operation references')}: {len(record.usages)}"
            + (f"\n{locations}" if locations else "")
        )
        self.common_status.setText(
            ui_text("Common defaults configured")
            if not tool.common_defaults.is_empty
            else ui_text("Common defaults not configured")
        )
        holder_fp = next(
            (
                profile.source_holder_fingerprint
                for profile in tool.program_profiles
                if profile.source_holder_fingerprint is not None
            ),
            None,
        )
        self.profiles.bind_tool(tool, holder_fingerprint=holder_fp)
        self.delete_button.setEnabled(not record.referenced)
        self.delete_button.setToolTip(
            ui_text("Delete is blocked while assemblies or operations reference this Tool.")
            if record.referenced
            else ui_text("Delete this unreferenced Tool")
        )

    def _current_record(self) -> ToolLibraryRecord:
        if self._selected_tool_id is None:
            raise ValueError(ui_text("Select a Tool first."))
        record = self._records.get(str(self._selected_tool_id))
        if record is None:
            raise ValueError(ui_text("The selected Tool is no longer available."))
        return record

    def _execute(self, command: Callable[[object], object]) -> bool:
        try:
            self._service.execute_cam_command(
                command, expected_generation=self._service.cam_generation
            )
        except (KeyError, RuntimeError, TypeError, ValueError) as error:
            LOGGER.warning(ui_text("Tool Library mutation rejected: %s"), error)
            self._show_error(str(error))
            self._refresh()
            return False
        self.mutation_committed.emit(self._selected_tool_id)
        self._refresh()
        return True

    def _create(self) -> None:
        before = {item.tool_id for item in self._service.cam_snapshot.tool_definitions}
        dialog = ToolDefinitionDialog(
            holders=self._service.cam_snapshot.holder_definitions, parent=self
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        draft = dialog.draft
        if self._execute(lambda app: app.create_managed_tool(draft)):
            after = self._service.cam_snapshot.tool_definitions
            created = tuple(item for item in after if item.tool_id not in before)
            if len(created) == 1:
                self._selected_tool_id = created[0].tool_id
                self._refresh()

    def _edit(self) -> None:
        record = self._current_record()
        tool = record.tool
        dialog = ToolDefinitionDialog(
            tool=tool,
            holders=self._service.cam_snapshot.holder_definitions,
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        draft = dialog.draft
        self._execute(
            lambda app: app.update_managed_tool(
                tool.tool_id,
                draft,
                expected_revision=tool.revision,
                expected_configuration_revision=tool.configuration_revision,
            )
        )

    def _duplicate(self) -> None:
        tool = self._current_record().tool
        before = {item.tool_id for item in self._service.cam_snapshot.tool_definitions}

        def command(app):
            app.duplicate_tool_definition(tool.tool_id)
            return app.snapshot

        if self._execute(command):
            created = tuple(
                item
                for item in self._service.cam_snapshot.tool_definitions
                if item.tool_id not in before
            )
            if len(created) == 1:
                self._selected_tool_id = created[0].tool_id
                self._refresh()

    def _delete(self) -> None:
        record = self._current_record()
        tool = record.tool
        if record.referenced:
            self._show_error(
                ui_text("Delete is blocked while assemblies or operations reference this Tool.")
            )
            return
        box = self.build_delete_confirmation(tool)
        delete = box.button(QMessageBox.StandardButton.Yes)
        cancel = box.button(QMessageBox.StandardButton.Cancel)
        box.exec()
        if box.clickedButton() is not delete:
            return
        self._execute(
            lambda app: app.remove_managed_tool(
                tool.tool_id,
                expected_revision=tool.revision,
                expected_configuration_revision=tool.configuration_revision,
            )
        )

    def build_delete_confirmation(self, tool: ToolDefinition) -> QMessageBox:
        """Build the keyboard-safe destructive surface for audit and execution."""
        if not isinstance(tool, ToolDefinition):
            raise TypeError("Delete confirmation requires ToolDefinition")
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(ui_text("Delete unreferenced Tool"))
        box.setText(ui_text("Delete this Tool permanently?"))
        box.setInformativeText(f"{tool.name}\n{tool.tool_id}")
        delete = box.addButton(QMessageBox.StandardButton.Yes)
        delete.setText(ui_text("Delete"))
        delete.setDefault(False)
        delete.setAutoDefault(False)
        cancel = box.addButton(QMessageBox.StandardButton.Cancel)
        cancel.setText(ui_text("Cancel"))
        cancel.setDefault(True)
        box.setDefaultButton(cancel)
        localize_widget_tree(box)
        return box

    def _edit_common_defaults(self) -> None:
        tool = self._current_record().tool
        dialog = ToolCommonDefaultsDialog(tool.common_defaults, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        defaults = dialog.defaults
        self._execute(
            lambda app: app.update_tool_common_defaults(
                tool.tool_id,
                defaults,
                expected_configuration_revision=tool.configuration_revision,
            )
        )

    def _profile_action(self, action: str, profile_id: object) -> None:
        try:
            tool = self._current_record().tool
            profile = next(
                (item for item in tool.program_profiles if item.profile_id == profile_id),
                None,
            )
            if action == "add":
                labels = [
                    ui_text(schema.display_name_vi)
                    for schema in DEFAULT_TOOL_PROFILE_REGISTRY.schemas
                ]
                label, accepted = QInputDialog.getItem(
                    self,
                    ui_text("Add strategy profile"),
                    ui_text("Strategy"),
                    labels,
                    0,
                    False,
                )
                if not accepted:
                    return
                index = labels.index(label)
                schema = DEFAULT_TOOL_PROFILE_REGISTRY.schemas[index]
                self._edit_profile(tool, schema.strategy_id, None)
            elif action == "edit" and profile is not None:
                self._edit_profile(tool, profile.strategy_id, profile)
            elif action == "copy" and profile is not None:
                self._execute(
                    lambda app: app.duplicate_tool_program_profile_entry(
                        tool.tool_id,
                        profile.profile_id,
                        expected_configuration_revision=tool.configuration_revision,
                    )
                )
            elif action == "toggle" and profile is not None:
                self._execute(
                    lambda app: app.set_tool_program_profile_enabled(
                        tool.tool_id,
                        profile.profile_id,
                        not profile.enabled,
                        expected_configuration_revision=tool.configuration_revision,
                    )
                )
            elif action == "reset" and profile is not None:
                self._execute(
                    lambda app: app.reset_tool_program_profile(
                        tool.tool_id,
                        profile.profile_id,
                        expected_configuration_revision=tool.configuration_revision,
                    )
                )
            elif action == "delete" and profile is not None:
                self._execute(
                    lambda app: app.delete_tool_program_profile(
                        tool.tool_id,
                        profile.profile_id,
                        expected_configuration_revision=tool.configuration_revision,
                    )
                )
        except (KeyError, RuntimeError, TypeError, ValueError) as error:
            self._show_error(str(error))

    def _edit_profile(
        self,
        tool: ToolDefinition,
        strategy_id: str,
        profile: ToolProgramProfile | None,
    ) -> None:
        schema = DEFAULT_TOOL_PROFILE_REGISTRY.schema(strategy_id)
        dialog = ToolProfileEditorDialog(schema, profile, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.profile_values()
        preview = preview_tool_profile_capture(
            tool,
            strategy_id,
            dialog.display_name,
            values,
            overridden_field_ids=frozenset(values),
            mode=ToolProfileSaveMode.OVERRIDES_ONLY,
            profile_id=profile.profile_id if profile is not None else None,
            registry=DEFAULT_TOOL_PROFILE_REGISTRY,
        )
        requested_enabled = dialog.profile_enabled
        if (
            profile is None
            and requested_enabled
            and any(
                item.strategy_id == strategy_id and item.enabled
                for item in tool.program_profiles
            )
        ):
            self._show_error(
                ui_text(
                    "Disable the existing strategy profile before enabling another one."
                )
            )
            return

        def command(app):
            changed = app.save_tool_program_profile(
                preview,
                expected_configuration_revision=tool.configuration_revision,
                holder_fingerprint=(
                    profile.source_holder_fingerprint if profile is not None else None
                ),
            )
            saved_tool = next(
                item for item in changed.tool_definitions if item.tool_id == tool.tool_id
            )
            saved_profile = next(
                item
                for item in saved_tool.program_profiles
                if item.profile_id == preview.profile_id
                or (
                    preview.profile_id is None
                    and item.strategy_id == strategy_id
                    and item.display_name == dialog.display_name
                )
            )
            if not requested_enabled:
                return app.set_tool_program_profile_enabled(
                    tool.tool_id,
                    saved_profile.profile_id,
                    False,
                    expected_configuration_revision=saved_tool.configuration_revision,
                )
            return changed

        self._execute(command)

    def apply_available_geometry(self, available: QRect, scale: float) -> None:
        """Clamp four audited surfaces for 100–200% display scales."""
        if not available.isValid():
            return
        logical_scale = max(1.0, float(scale))
        compact = logical_scale >= 1.5 or available.width() <= 1366
        self.table.setColumnHidden(1, compact)
        self.table.setColumnHidden(4, compact and logical_scale >= 2.0)
        self.table.setColumnHidden(5, compact)
        splitter = self.findChild(QSplitter, "ToolLibrarySplitter")
        if splitter is not None:
            splitter.setSizes(
                [int(available.width() * 0.6), int(available.width() * 0.4)]
            )
        width = min(available.width(), max(900, 1120))
        height = min(available.height(), max(600, 760))
        self.setMaximumSize(available.size())
        self.setMinimumSize(0, 0)
        self.resize(width, height)

    def _show_error(self, message: str) -> None:
        QMessageBox.warning(self, ui_text("Tool Library"), ui_text(message))

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
            return
        super().keyPressEvent(event)


__all__ = [
    "ToolCommonDefaultsDialog",
    "ToolDefinitionDialog",
    "ToolLibraryDialog",
]
