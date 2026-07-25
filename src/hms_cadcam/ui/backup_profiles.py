"""Production backup/restore wizards and per-user profile management UI."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
import logging
from pathlib import Path

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QTableView,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from hms_cadcam.core.hms_backup import (
    BACKUP_EXTENSION,
    BackupCategory,
    BackupCreationResult,
    BackupError,
    BackupInspection,
    BackupSelectionModel,
    BackupScope,
    CategoryEstimate,
    CompatibilityState,
    ConflictAction,
    HmsBackupService,
    HmsRestoreService,
    RestorePlan,
    RestoreResult,
    SelectionState,
)
from hms_cadcam.core.user_profiles import (
    ProfileError,
    ProfileSwitchReport,
    UserProfile,
    UserProfileService,
)
from hms_cadcam.ui.i18n import apply_widget_font_tree, translation_service
from hms_cadcam.ui.localization import localize_widget_tree, ui_text
from hms_cadcam.ui.localization_audit import LOCALIZATION_AUDIT_EXCLUDE_ROLE


LOGGER = logging.getLogger(__name__)


BACKUP_CATEGORY_LABELS = {
    BackupCategory.USER_PROFILES: "User profiles",
    BackupCategory.USER_INTERFACE: "User interface",
    BackupCategory.USER_SETTINGS: "Settings",
    BackupCategory.KEYBOARD_SHORTCUTS: "Keyboard shortcuts",
    BackupCategory.QUICK_ACCESS: "Quick Access",
    BackupCategory.RECENT_FILES: "Recent files",
    BackupCategory.TOOL_LIBRARY: "Tool/cutter library",
    BackupCategory.HOLDER_LIBRARY: "Holder library",
    BackupCategory.PROGRAM_TEMPLATES: "Program templates",
    BackupCategory.POSTS: "Post",
    BackupCategory.MACHINES: "Machines",
    BackupCategory.MATERIALS: "Materials",
    BackupCategory.MACHINE_CONFIG: "Shared configuration",
    BackupCategory.EXPORTABLE_SCHEMAS: "Exportable schemas and catalogs",
}
BACKUP_CATEGORY_DESCRIPTIONS = {
    BackupCategory.USER_PROFILES: "Profile identity, locale and layout description",
    BackupCategory.USER_INTERFACE: "Window, dock, ribbon and toolbar state",
    BackupCategory.USER_SETTINGS: "Per-profile preferences and appearance",
    BackupCategory.KEYBOARD_SHORTCUTS: "Validated command IDs and shortcuts",
    BackupCategory.QUICK_ACCESS: "Quick Access command IDs",
    BackupCategory.RECENT_FILES: "Optional private recent-file paths",
    BackupCategory.TOOL_LIBRARY: "Shared Tool resources without project snapshots",
    BackupCategory.HOLDER_LIBRARY: "Holder resources inside the shared Tool library",
    BackupCategory.PROGRAM_TEMPLATES: "Stored program-template data only",
    BackupCategory.POSTS: "Post library data; never executed during backup or restore",
    BackupCategory.MACHINES: "Shared machine definitions without machine-ready claims",
    BackupCategory.MATERIALS: "Shared material definitions",
    BackupCategory.MACHINE_CONFIG: "Exportable machine-wide configuration",
    BackupCategory.EXPORTABLE_SCHEMAS: "Compatible exportable schemas and catalogs",
}
PROFILE_FILE_FILTER = f"HMS Backup (*{BACKUP_EXTENSION})"


class BackupCategoryTableModel(QAbstractTableModel):
    """Single source of truth for category check state and its summaries."""

    selection_changed = Signal()
    HEADERS = ("", "Category", "Scope", "Availability", "Size", "Resources", "Description")

    def __init__(
        self,
        estimates: tuple[CategoryEstimate, ...],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.estimates = estimates
        self._selection = BackupSelectionModel(estimates)

    @property
    def selected(self) -> tuple[BackupCategory, ...]:
        return self._selection.selected

    @property
    def state(self) -> SelectionState:
        return self._selection.state

    @property
    def estimated_size(self) -> int:
        return self._selection.estimated_size

    @property
    def resource_count(self) -> int:
        return self._selection.resource_count

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.estimates)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> object:
        if (
            orientation is Qt.Orientation.Horizontal
            and role in {
                Qt.ItemDataRole.DisplayRole,
                Qt.ItemDataRole.ToolTipRole,
                Qt.ItemDataRole.AccessibleTextRole,
            }
            and 0 <= section < len(self.HEADERS)
        ):
            return ui_text(self.HEADERS[section])
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> object:
        if not index.isValid() or not 0 <= index.row() < len(self.estimates):
            return None
        estimate = self.estimates[index.row()]
        if index.column() == 0:
            if role == Qt.ItemDataRole.CheckStateRole:
                return (
                    Qt.CheckState.Checked
                    if estimate.category in self._selection.selected
                    else Qt.CheckState.Unchecked
                )
            if role in {
                Qt.ItemDataRole.AccessibleTextRole,
                Qt.ItemDataRole.AccessibleDescriptionRole,
                Qt.ItemDataRole.ToolTipRole,
            }:
                return ui_text(BACKUP_CATEGORY_LABELS[estimate.category])
            return None
        values = (
            BACKUP_CATEGORY_LABELS[estimate.category],
            _scope_label(estimate.scope),
            _diagnostic_label(estimate.diagnostic_code),
            _format_size(estimate.estimated_size),
            str(estimate.resource_count),
            BACKUP_CATEGORY_DESCRIPTIONS[estimate.category],
        )
        value = values[index.column() - 1]
        if role in {
            Qt.ItemDataRole.DisplayRole,
            Qt.ItemDataRole.ToolTipRole,
            Qt.ItemDataRole.AccessibleTextRole,
        }:
            return ui_text(value)
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        flags = super().flags(index)
        if not index.isValid():
            return flags
        estimate = self.estimates[index.row()]
        if index.column() == 0 and estimate.selectable:
            return flags | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled
        if not estimate.selectable:
            return flags & ~Qt.ItemFlag.ItemIsEnabled
        return flags

    def setData(self, index: QModelIndex, value: object, role: int) -> bool:
        if (
            not index.isValid()
            or index.column() != 0
            or role != Qt.ItemDataRole.CheckStateRole
        ):
            return False
        estimate = self.estimates[index.row()]
        if not estimate.selectable:
            return False
        self.set_selected(
            estimate.category,
            (
                value is Qt.CheckState.Checked
                or getattr(value, "value", value) == Qt.CheckState.Checked.value
            ),
        )
        return True

    def set_selected(self, category: BackupCategory, selected: bool) -> None:
        before = self._selection.selected
        self._selection.set_selected(category, selected)
        if self._selection.selected != before:
            self._emit_selection_change()

    def select_all(self) -> None:
        before = self._selection.selected
        self._selection.select_all()
        if self._selection.selected != before:
            self._emit_selection_change()

    def select_none(self) -> None:
        before = self._selection.selected
        self._selection.select_none()
        if self._selection.selected != before:
            self._emit_selection_change()

    def retranslate(self) -> None:
        if self.rowCount():
            self.dataChanged.emit(
                self.index(0, 0),
                self.index(self.rowCount() - 1, self.columnCount() - 1),
                [
                    Qt.ItemDataRole.DisplayRole,
                    Qt.ItemDataRole.ToolTipRole,
                    Qt.ItemDataRole.AccessibleTextRole,
                    Qt.ItemDataRole.AccessibleDescriptionRole,
                ],
            )
        self.headerDataChanged.emit(
            Qt.Orientation.Horizontal,
            0,
            self.columnCount() - 1,
        )

    def _emit_selection_change(self) -> None:
        if self.rowCount():
            self.dataChanged.emit(
                self.index(0, 0),
                self.index(self.rowCount() - 1, 0),
                [Qt.ItemDataRole.CheckStateRole],
            )
        self.selection_changed.emit()


class _BackupThread(QThread):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        service: HmsBackupService,
        destination: Path,
        categories: tuple[BackupCategory, ...],
        profile_ids: tuple[str, ...],
        locale: str,
        overwrite_confirmed: bool,
        parent: QWidget,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.destination = destination
        self.categories = categories
        self.profile_ids = profile_ids
        self.locale = locale
        self.overwrite_confirmed = overwrite_confirmed
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        try:
            result = self.service.create(
                self.destination,
                self.categories,
                profile_ids=self.profile_ids,
                created_locale=self.locale,
                overwrite_confirmed=self.overwrite_confirmed,
                cancelled=lambda: self._cancelled,
            )
            self.completed.emit(result)
        except (OSError, RuntimeError, ValueError, BackupError) as exc:
            self.failed.emit(str(exc))


class _RestoreThread(QThread):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, service: HmsRestoreService, plan: RestorePlan, parent: QWidget) -> None:
        super().__init__(parent)
        self.service = service
        self.plan = plan

    def run(self) -> None:
        try:
            self.completed.emit(self.service.restore(self.plan))
        except (OSError, RuntimeError, ValueError, BackupError) as exc:
            self.failed.emit(str(exc))


class _WizardDialog(QDialog):
    """Shared compact stacked-page navigation with accessible step titles."""

    def __init__(self, page_keys: tuple[str, ...], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setModal(False)
        self.resize(1120, 760)
        self.setMinimumSize(920, 640)
        self._page_keys = page_keys
        self.step_label = QLabel()
        self.step_label.setObjectName("WizardStepTitle")
        self.step_label.setStyleSheet("font-size: 20px; font-weight: 600;")
        self.step_label.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Minimum,
        )
        self.step_label.setAccessibleName(ui_text("Current wizard step"))
        self.description_label = QLabel()
        self.description_label.setWordWrap(True)
        self.description_label.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Minimum,
        )
        self.stack = QStackedWidget()
        self.back_button = QPushButton()
        self.next_button = QPushButton()
        self.action_button = QPushButton()
        self.cancel_button = QPushButton()
        self.back_button.setObjectName("wizard.back")
        self.next_button.setObjectName("wizard.next")
        self.action_button.setObjectName("wizard.action")
        self.cancel_button.setObjectName("wizard.close")
        self.back_button.setProperty("wizardSemanticKey", "wizard.back")
        self.next_button.setProperty("wizardSemanticKey", "wizard.next")
        self.cancel_button.setProperty("wizardSemanticKey", "wizard.close")
        self.back_button.clicked.connect(lambda: self.set_page(self.stack.currentIndex() - 1))
        self.next_button.clicked.connect(lambda: self.set_page(self.stack.currentIndex() + 1))
        self.cancel_button.clicked.connect(self._cancel_or_close)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.back_button)
        buttons.addWidget(self.next_button)
        buttons.addWidget(self.action_button)
        buttons.addWidget(self.cancel_button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.addWidget(self.step_label)
        layout.addWidget(self.description_label)
        layout.addWidget(self.stack, 1)
        layout.addLayout(buttons)
        self.setTabOrder(self.back_button, self.next_button)
        self.setTabOrder(self.next_button, self.action_button)
        self.setTabOrder(self.action_button, self.cancel_button)
        self._running_thread: QThread | None = None

    def add_page(self, widget: QWidget) -> None:
        self.stack.addWidget(widget)

    def set_page(self, index: int) -> None:
        bounded = min(max(index, 0), self.stack.count() - 1)
        self.stack.setCurrentIndex(bounded)
        self._refresh_navigation()

    def _refresh_navigation(self) -> None:
        index = self.stack.currentIndex()
        self.step_label.setText(ui_text(self._page_keys[index]))
        self.step_label.setAccessibleDescription(
            f"{ui_text('Step')} {index + 1}/{self.stack.count()}"
        )
        self.back_button.setEnabled(index > 0 and self._running_thread is None)
        self.next_button.setVisible(index < self.stack.count() - 2)
        self.action_button.setVisible(index == self.stack.count() - 2)
        self.next_button.setEnabled(self._running_thread is None)
        self.action_button.setEnabled(self._running_thread is None)

    def _cancel_or_close(self) -> None:
        if self._running_thread is not None:
            cancel = getattr(self._running_thread, "cancel", None)
            if callable(cancel):
                cancel()
            return
        self.close()

    def retranslate_ui(self, _language: object = None) -> None:
        apply_widget_font_tree(self, translation_service().language)
        self._set_semantic_button(
            self.back_button,
            "Back",
            "Go to the previous wizard step",
        )
        self._set_semantic_button(
            self.next_button,
            "Next",
            "Go to the next wizard step",
        )
        self._set_semantic_button(
            self.cancel_button,
            "Cancel" if self._running_thread else "Close",
            "Cancel the running task" if self._running_thread else "Close the wizard",
        )
        self._refresh_navigation()
        localize_widget_tree(self)

    @staticmethod
    def _set_semantic_button(
        button: QPushButton,
        text_key: str,
        description_key: str,
    ) -> None:
        text = ui_text(text_key)
        description = ui_text(description_key)
        button.setText(text)
        button.setToolTip(description)
        button.setAccessibleName(text)
        button.setAccessibleDescription(description)


class BackupWizardDialog(_WizardDialog):
    """Five-step production wizard for selective `.BAKUPHMS` creation."""

    PAGE_KEYS = (
        "Choose backup content",
        "Choose profiles",
        "Choose destination",
        "Confirmation",
        "Result",
    )

    def __init__(
        self,
        service: HmsBackupService,
        profile_service: UserProfileService,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(self.PAGE_KEYS, parent)
        self.setObjectName("HmsBackupWizard")
        self.service = service
        self.profile_service = profile_service
        self.profile_service.bootstrap(locale=translation_service().language.value)
        self.estimates = self.service.estimate_categories()
        self.selection = BackupCategoryTableModel(tuple(self.estimates), self)
        self._profile_checks: dict[str, QCheckBox] = {}
        self._result: BackupCreationResult | None = None

        categories_page = QWidget()
        categories_layout = QVBoxLayout(categories_page)
        self.select_all = QCheckBox()
        self.select_all.setTristate(True)
        self.select_all.clicked.connect(self._toggle_all)
        self.category_table = QTableView()
        self.category_table.setObjectName("BackupCategoryTable")
        self.category_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.category_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.category_table.verticalHeader().setVisible(False)
        self.category_table.setWordWrap(True)
        self.category_table.setModel(self.selection)
        self.category_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        for column, width in ((1, 215), (2, 190), (3, 105), (4, 90), (5, 100)):
            self.category_table.horizontalHeader().setSectionResizeMode(
                column, QHeaderView.ResizeMode.Fixed
            )
            self.category_table.setColumnWidth(column, width)
        self.category_table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        self.category_table.setAccessibleName(ui_text("Backup categories"))
        self.category_table.setAccessibleDescription(ui_text("Select exportable user and shared HMS resources"))
        categories_layout.addWidget(self.select_all)
        categories_layout.addWidget(self.category_table)
        self.category_summary = QLabel()
        categories_layout.addWidget(self.category_summary)
        self.add_page(categories_page)

        profiles_page = QWidget()
        profiles_layout = QVBoxLayout(profiles_page)
        self.profile_table = QTableWidget(0, 5)
        self.profile_table.verticalHeader().setVisible(False)
        self.profile_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.profile_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.profile_table.setAccessibleName(ui_text("Profiles included in backup"))
        self.profile_table.setAccessibleDescription(ui_text("Select one or more per-user HMS profiles"))
        profiles_layout.addWidget(self.profile_table)
        self.recent_privacy_label = QLabel()
        self.recent_privacy_label.setWordWrap(True)
        profiles_layout.addWidget(self.recent_privacy_label)
        self.add_page(profiles_page)

        destination_page = QWidget()
        destination_layout = QGridLayout(destination_page)
        self.destination_edit = QLineEdit()
        self.destination_edit.setReadOnly(True)
        self.destination_edit.setAccessibleName(ui_text("Backup destination"))
        self.browse_button = QPushButton()
        self.browse_button.clicked.connect(self._browse_destination)
        destination_layout.addWidget(QLabel(ui_text("Backup file")), 0, 0)
        destination_layout.addWidget(self.destination_edit, 0, 1)
        destination_layout.addWidget(self.browse_button, 0, 2)
        self.destination_hint = QLabel()
        self.destination_hint.setWordWrap(True)
        destination_layout.addWidget(self.destination_hint, 1, 0, 1, 3)
        destination_layout.setRowStretch(2, 1)
        self.add_page(destination_page)

        confirmation_page = QWidget()
        confirmation_layout = QVBoxLayout(confirmation_page)
        self.confirmation_label = QLabel()
        self.confirmation_label.setWordWrap(True)
        self.confirmation_table = QTableWidget(0, 2)
        self.confirmation_table.verticalHeader().setVisible(False)
        self.confirmation_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.confirmation_table.setColumnWidth(0, 210)
        self.confirmation_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.confirmation_table.setAccessibleName(ui_text("Confirmation"))
        self.confirmation_table.setAccessibleDescription(
            ui_text("Review the selected categories and destination before creating the backup.")
        )
        confirmation_layout.addWidget(self.confirmation_label)
        confirmation_layout.addWidget(self.confirmation_table)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setAccessibleName(ui_text("Backup progress"))
        confirmation_layout.addWidget(self.progress)
        self.add_page(confirmation_page)

        result_page = QWidget()
        result_layout = QVBoxLayout(result_page)
        self.result_title = QLabel()
        self.result_title.setStyleSheet("font-size: 18px; font-weight: 600;")
        self.result_detail = QLabel()
        self.result_detail.setWordWrap(True)
        result_layout.addWidget(self.result_title)
        result_layout.addWidget(self.result_detail)
        result_layout.addStretch(1)
        self.add_page(result_page)

        self.action_button.clicked.connect(self._start_backup)
        self.selection.selection_changed.connect(self._refresh_selection_summary)
        translation_service().language_changed.connect(self.retranslate_ui)
        self._populate_profiles()
        self.set_destination(
            self.service.suggested_directory()
            / self.service.suggested_filename()
        )
        self.retranslate_ui()
        self.set_page(0)

    @property
    def selected_profile_ids(self) -> tuple[str, ...]:
        return tuple(key for key, checkbox in self._profile_checks.items() if checkbox.isChecked())

    def set_destination(self, path: Path) -> None:
        self.destination_edit.setText(str(Path(path)))

    def set_page(self, index: int) -> None:
        if index == 3:
            self._populate_confirmation()
        super().set_page(index)

    def select_category(self, category: BackupCategory, selected: bool) -> None:
        self.selection.set_selected(category, selected)

    def _populate_profiles(self) -> None:
        profiles = self.profile_service.profiles()
        index = self.profile_service.load_index()
        self.profile_table.setRowCount(len(profiles))
        for row, profile in enumerate(profiles):
            checkbox = QCheckBox()
            checkbox.setAccessibleName(
                f"{ui_text('Profile name')} {row + 1}"
            )
            checkbox.setChecked(True)
            self._profile_checks[profile.profile_id] = checkbox
            self.profile_table.setCellWidget(row, 0, checkbox)
            values = (
                profile.display_name,
                ui_text(_language_label(profile.locale)),
                ui_text("Active") if profile.profile_id == index.active_profile_id else "",
                ui_text("Default") if profile.profile_id == index.default_profile_id else "",
            )
            for column, value in enumerate(values, start=1):
                item = QTableWidgetItem(value)
                if column == 1:
                    item.setData(LOCALIZATION_AUDIT_EXCLUDE_ROLE, True)
                self.profile_table.setItem(row, column, item)

    def _toggle_all(self, checked: bool) -> None:
        if checked:
            self.selection.select_all()
        else:
            self.selection.select_none()

    def _refresh_selection_summary(self) -> None:
        state = self.selection.state
        self.select_all.blockSignals(True)
        self.select_all.setCheckState({
            SelectionState.NONE: Qt.CheckState.Unchecked,
            SelectionState.PARTIAL: Qt.CheckState.PartiallyChecked,
            SelectionState.ALL: Qt.CheckState.Checked,
        }[state])
        self.select_all.blockSignals(False)
        self.category_summary.setText(
            ui_text("Selected: {categories} categories, {resources} resources, estimated {size}").format(
                categories=len(self.selection.selected),
                resources=self.selection.resource_count,
                size=_format_size(self.selection.estimated_size),
            )
        )

    def _browse_destination(self) -> None:
        selected, _filter = QFileDialog.getSaveFileName(
            self,
            ui_text("Choose backup destination"),
            self.destination_edit.text(),
            PROFILE_FILE_FILTER,
        )
        if selected:
            path = Path(selected)
            if path.suffix.casefold() != BACKUP_EXTENSION.casefold():
                path = path.with_suffix(BACKUP_EXTENSION)
            self.set_destination(path)

    def _populate_confirmation(self) -> None:
        rows = (
            ("Selected categories", str(len(self.selection.selected))),
            ("Selected profiles", str(len(self.selected_profile_ids))),
            ("Resources", str(self.selection.resource_count)),
            ("Estimated size", _format_size(self.selection.estimated_size)),
            ("Destination", self.destination_edit.text()),
        )
        self.confirmation_table.setRowCount(len(rows))
        self.confirmation_table.setHorizontalHeaderLabels(
            [ui_text("Property"), ui_text("Value")]
        )
        for row, (key, value) in enumerate(rows):
            self.confirmation_table.setItem(row, 0, QTableWidgetItem(ui_text(key)))
            item = QTableWidgetItem(value)
            if key == "Destination":
                item.setData(LOCALIZATION_AUDIT_EXCLUDE_ROLE, True)
            self.confirmation_table.setItem(row, 1, item)

    def _start_backup(self) -> None:
        destination = Path(self.destination_edit.text())
        overwrite_confirmed = destination.exists()
        if overwrite_confirmed:
            answer = QMessageBox.question(
                self,
                ui_text("Confirm replacement"),
                ui_text("The selected backup file already exists. Replace it?"),
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.progress.setRange(0, 0)
        thread = _BackupThread(
            self.service,
            destination,
            self.selection.selected,
            self.selected_profile_ids,
            translation_service().language.value,
            overwrite_confirmed,
            self,
        )
        self._running_thread = thread
        thread.completed.connect(self.show_success)
        thread.failed.connect(self.show_failure)
        thread.finished.connect(lambda: setattr(self, "_running_thread", None))
        thread.finished.connect(self.retranslate_ui)
        thread.start()
        self._refresh_navigation()

    def execute_synchronously(self) -> BackupCreationResult:
        result = self.service.create(
            Path(self.destination_edit.text()),
            self.selection.selected,
            profile_ids=self.selected_profile_ids,
            created_locale=translation_service().language.value,
        )
        self.show_success(result)
        return result

    def show_success(self, result: BackupCreationResult) -> None:
        self._result = result
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        self.result_title.setText(ui_text("Backup completed"))
        self.result_detail.setText(
            f"{result.path}\n{result.manifest.resource_count} {ui_text('resources')} · {_format_size(result.file_size)}"
        )
        self.set_page(4)

    def show_failure(self, detail: str) -> None:
        LOGGER.error("HMS backup failed: %s", detail)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.result_title.setText(ui_text("Backup failed"))
        self.result_detail.setText(
            ui_text("The backup operation failed; source data was not changed.")
        )
        self.set_page(4)

    def retranslate_ui(self, _language: object = None) -> None:
        self.setWindowTitle(ui_text("Back up HMS"))
        self.setAccessibleName(ui_text("HMS backup wizard"))
        self.setAccessibleDescription(ui_text("Back up HMS settings, profiles and library data"))
        self.description_label.setText(ui_text("Back up HMS settings, profiles and library data. Executables and the installer are not included."))
        self.select_all.setText(ui_text("Select all eligible categories"))
        self.profile_table.setHorizontalHeaderLabels([
            "", ui_text("Profile name"), ui_text("Language"), ui_text("Active"), ui_text("Default"),
        ])
        self.recent_privacy_label.setText(ui_text("Recent files are not selected by default to protect private paths."))
        self.browse_button.setText(ui_text("Browse…"))
        self.destination_hint.setText(ui_text("The destination is selected by the user and is never forced into ProgramData or AppData."))
        self.confirmation_label.setText(ui_text("Review the selected categories and destination before creating the backup."))
        self.action_button.setProperty("wizardSemanticKey", "wizard.backup")
        self._set_semantic_button(
            self.action_button,
            "Back up",
            "Create the selected HMS backup",
        )
        self.selection.retranslate()
        super().retranslate_ui(_language)
        self._refresh_selection_summary()


class RestoreWizardDialog(_WizardDialog):
    """Six-step validation-first selective restore wizard."""

    PAGE_KEYS = (
        "Choose backup file",
        "Validate backup",
        "Choose restore content",
        "Resolve conflicts",
        "Confirmation",
        "Result",
    )

    def __init__(
        self,
        service: HmsRestoreService,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(self.PAGE_KEYS, parent)
        self.setObjectName("HmsRestoreWizard")
        self.service = service
        self.inspection: BackupInspection | None = None
        self.plan: RestorePlan | None = None
        self._result: RestoreResult | None = None

        choose_page = QWidget()
        choose_layout = QGridLayout(choose_page)
        self.source_edit = QLineEdit()
        self.source_edit.setReadOnly(True)
        self.source_edit.setAccessibleName(ui_text("Backup file to restore"))
        self.source_button = QPushButton()
        self.source_button.clicked.connect(self._browse_source)
        choose_layout.addWidget(QLabel(ui_text("Backup file")), 0, 0)
        choose_layout.addWidget(self.source_edit, 0, 1)
        choose_layout.addWidget(self.source_button, 0, 2)
        choose_layout.setRowStretch(1, 1)
        self.add_page(choose_page)

        validation_page = QWidget()
        validation_layout = QVBoxLayout(validation_page)
        self.validation_title = QLabel()
        self.validation_title.setStyleSheet("font-size: 18px; font-weight: 600;")
        self.validation_detail = QLabel()
        self.validation_detail.setWordWrap(True)
        self.validation_table = QTableWidget(0, 2)
        self.validation_table.verticalHeader().setVisible(False)
        self.validation_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.validation_table.setColumnWidth(0, 210)
        self.validation_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.validation_table.setAccessibleName(ui_text("Validate backup"))
        self.validation_table.setAccessibleDescription(
            ui_text("The backup is validated before any destination is changed. Existing data is kept by default.")
        )
        validation_layout.addWidget(self.validation_title)
        validation_layout.addWidget(self.validation_detail)
        validation_layout.addWidget(self.validation_table)
        self.add_page(validation_page)

        content_page = QWidget()
        content_layout = QVBoxLayout(content_page)
        self.restore_table = QTableWidget(0, 6)
        self.restore_table.verticalHeader().setVisible(False)
        self.restore_table.setWordWrap(True)
        self.restore_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.restore_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for column, width in ((2, 160), (3, 300), (4, 95), (5, 145)):
            self.restore_table.horizontalHeader().setSectionResizeMode(
                column, QHeaderView.ResizeMode.Fixed
            )
            self.restore_table.setColumnWidth(column, width)
        self.restore_table.setAccessibleName(ui_text("Restore resources"))
        self.restore_table.setAccessibleDescription(ui_text("Select validated resources to restore"))
        content_layout.addWidget(self.restore_table)
        self.add_page(content_page)

        conflict_page = QWidget()
        conflict_layout = QVBoxLayout(conflict_page)
        self.conflict_table = QTableWidget(0, 5)
        self.conflict_table.verticalHeader().setVisible(False)
        self.conflict_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column, width in ((1, 190), (2, 160), (3, 145), (4, 190)):
            self.conflict_table.horizontalHeader().setSectionResizeMode(
                column, QHeaderView.ResizeMode.Fixed
            )
            self.conflict_table.setColumnWidth(column, width)
        self.conflict_table.setAccessibleName(ui_text("Resolve conflicts"))
        self.conflict_table.setAccessibleDescription(
            ui_text("The backup is validated before any destination is changed. Existing data is kept by default.")
        )
        conflict_layout.addWidget(self.conflict_table)
        self.permission_label = QLabel()
        self.permission_label.setWordWrap(True)
        conflict_layout.addWidget(self.permission_label)
        self.add_page(conflict_page)

        confirm_page = QWidget()
        confirm_layout = QVBoxLayout(confirm_page)
        self.restore_summary = QLabel()
        self.restore_summary.setWordWrap(True)
        self.restore_progress = QProgressBar()
        self.restore_progress.setRange(0, 100)
        self.restore_progress.setAccessibleName(ui_text("Restore progress"))
        confirm_layout.addWidget(self.restore_summary)
        confirm_layout.addWidget(self.restore_progress)
        confirm_layout.addStretch(1)
        self.add_page(confirm_page)

        result_page = QWidget()
        result_layout = QVBoxLayout(result_page)
        self.restore_result_title = QLabel()
        self.restore_result_title.setStyleSheet("font-size: 18px; font-weight: 600;")
        self.restore_result_detail = QLabel()
        self.restore_result_detail.setWordWrap(True)
        result_layout.addWidget(self.restore_result_title)
        result_layout.addWidget(self.restore_result_detail)
        result_layout.addStretch(1)
        self.add_page(result_page)

        self.action_button.clicked.connect(self._start_restore)
        translation_service().language_changed.connect(self.retranslate_ui)
        self.retranslate_ui()
        self.set_page(0)

    def load_backup(self, path: Path) -> BackupInspection:
        self.source_edit.setText(str(Path(path)))
        self.inspection = self.service.backup_service.inspect(path)
        if self.inspection.valid:
            self.plan = self.service.preview(path)
            manifest = self.inspection.manifest
            assert manifest is not None
            self.validation_title.setText(ui_text("Backup is compatible"))
            self.validation_detail.setProperty("localizationAuditDomainText", True)
            self.validation_detail.setText(
                f"{Path(path).name} · {manifest.created_at_utc} · {manifest.source_application_version}"
            )
            self._set_validation_rows((
                ("Compatibility", ui_text(_compatibility_label(self.inspection.compatibility))),
                ("Resources", str(manifest.resource_count)),
                ("Uncompressed size", _format_size(manifest.uncompressed_size)),
                ("Checksum", "SHA-256"),
            ))
            self._populate_plan()
        else:
            self.plan = None
            self.validation_title.setText(ui_text("Backup is invalid or incompatible"))
            self.validation_detail.setProperty("localizationAuditDomainText", False)
            self.validation_detail.setText(
                ui_text("The selected backup failed validation; no destination was changed.")
            )
            self._set_validation_rows(((
                "Compatibility",
                ui_text(_compatibility_label(self.inspection.compatibility)),
            ),))
        return self.inspection

    def set_conflict_action(self, logical_id: str, action: ConflictAction) -> None:
        if self.plan is None:
            return
        action_map = {
            item.entry.logical_resource_id: (
                ConflictAction(action) if item.entry.logical_resource_id == logical_id else item.action
            )
            for item in self.plan.items
        }
        selected = tuple(item.entry.category for item in self.plan.items if item.selected)
        self.plan = self.service.preview(
            self.plan.source_path,
            selected_categories=selected,
            actions=action_map,
        )
        self._populate_plan()

    def _browse_source(self) -> None:
        selected, _filter = QFileDialog.getOpenFileName(
            self,
            ui_text("Choose HMS backup"),
            "",
            PROFILE_FILE_FILTER,
        )
        if selected:
            self.load_backup(Path(selected))
            self.set_page(1)

    def _set_validation_rows(self, rows: tuple[tuple[str, str], ...]) -> None:
        self.validation_table.setRowCount(len(rows))
        for row, (key, value) in enumerate(rows):
            self.validation_table.setItem(row, 0, QTableWidgetItem(ui_text(key)))
            self.validation_table.setItem(row, 1, QTableWidgetItem(value))

    def _populate_plan(self) -> None:
        self.restore_table.clearContents()
        self.conflict_table.clearContents()
        if self.plan is None:
            self.restore_table.setRowCount(0)
            self.conflict_table.setRowCount(0)
            return
        self.restore_table.setRowCount(len(self.plan.items))
        conflicts = tuple(
            item
            for item in self.plan.items
            if item.selected and (item.conflict or item.permission_blocked)
        )
        self.conflict_table.setRowCount(len(conflicts))
        for row, item in enumerate(self.plan.items):
            checkbox = QCheckBox()
            checkbox.setAccessibleName(
                f"{ui_text('Resource')} {row + 1}"
            )
            checkbox.setChecked(item.selected)
            checkbox.setEnabled(not item.permission_blocked)
            checkbox.toggled.connect(
                lambda checked, logical=item.entry.logical_resource_id: self._set_item_selected(logical, checked)
            )
            self.restore_table.setCellWidget(row, 0, checkbox)
            values = (
                BACKUP_CATEGORY_LABELS[item.entry.category],
                _scope_label(item.entry.scope),
                item.entry.relative_path,
                _format_size(item.entry.size),
                _diagnostic_label(item.diagnostic_code),
            )
            for column, value in enumerate(values, start=1):
                table_item = QTableWidgetItem(ui_text(value))
                if column == 3:
                    table_item.setData(LOCALIZATION_AUDIT_EXCLUDE_ROLE, True)
                self.restore_table.setItem(row, column, table_item)
        for row, item in enumerate(conflicts):
            values = (
                item.entry.relative_path,
                BACKUP_CATEGORY_LABELS[item.entry.category],
                _scope_label(item.entry.scope),
                _diagnostic_label(item.diagnostic_code),
            )
            for column, value in enumerate(values):
                table_item = QTableWidgetItem(ui_text(value))
                if column == 0:
                    table_item.setData(LOCALIZATION_AUDIT_EXCLUDE_ROLE, True)
                self.conflict_table.setItem(row, column, table_item)
            combo = QComboBox()
            combo.setAccessibleName(
                f"{ui_text('Action')} {row + 1}"
            )
            actions = (
                (ConflictAction.SKIP,)
                if item.permission_blocked
                else _actions_for(item.entry.category)
            )
            for action in actions:
                label = (
                    "Skip — no write permission"
                    if item.permission_blocked
                    else _conflict_label(action)
                )
                combo.addItem(ui_text(label), action)
            combo.setCurrentIndex(max(0, combo.findData(item.action)))
            combo.setEnabled(not item.permission_blocked)
            combo.currentIndexChanged.connect(
                lambda _index, logical=item.entry.logical_resource_id, widget=combo: self.set_conflict_action(logical, widget.currentData())
            )
            self.conflict_table.setCellWidget(row, 4, combo)
        self.restore_table.resizeRowsToContents()
        self.conflict_table.resizeRowsToContents()
        blocked_count = sum(
            item.selected and item.permission_blocked
            for item in self.plan.items
        )
        eligible_profile_count = sum(
            item.selected
            and not item.permission_blocked
            and item.entry.category is BackupCategory.USER_PROFILES
            and item.action is not ConflictAction.SKIP
            for item in self.plan.items
        )
        self.permission_label.setText(
            ui_text(
                "{blocked} machine-wide items skipped for write permission; "
                "{profiles} user profiles can be restored."
            ).format(
                blocked=blocked_count,
                profiles=eligible_profile_count,
            )
            if blocked_count
            else ui_text(
                "Machine-wide items without write permission stay blocked; "
                "eligible user-profile items can still be restored."
            )
        )
        self.restore_summary.setText(
            ui_text("Restore plan: {0} selected, {1} conflicts, {2} permission-blocked.").format(
                sum(item.selected for item in self.plan.items),
                self.plan.conflict_count,
                self.plan.permission_blocked_count,
            )
        )
        self._refresh_navigation()

    def _refresh_navigation(self) -> None:
        super()._refresh_navigation()
        if not hasattr(self, "plan") or self.plan is None:
            return
        if self.stack.currentIndex() not in {2, 3, 4}:
            return
        has_publishable = any(
            item.selected
            and not item.permission_blocked
            and item.action is not ConflictAction.SKIP
            for item in self.plan.items
        )
        self.next_button.setEnabled(
            self.next_button.isEnabled() and has_publishable
        )
        self.action_button.setEnabled(
            self.action_button.isEnabled() and has_publishable
        )

    def _set_item_selected(self, logical_id: str, selected: bool) -> None:
        if self.plan is None:
            return
        self.plan = replace(
            self.plan,
            items=tuple(
                replace(item, selected=selected)
                if item.entry.logical_resource_id == logical_id
                else item
                for item in self.plan.items
            ),
        )

    def _start_restore(self) -> None:
        if self.plan is None:
            return
        self.restore_progress.setRange(0, 0)
        thread = _RestoreThread(self.service, self.plan, self)
        self._running_thread = thread
        thread.completed.connect(self.show_result)
        thread.failed.connect(self.show_failure)
        thread.finished.connect(lambda: setattr(self, "_running_thread", None))
        thread.finished.connect(self.retranslate_ui)
        thread.start()
        self._refresh_navigation()

    def execute_synchronously(self) -> RestoreResult:
        if self.plan is None:
            raise BackupError("No validated restore plan")
        result = self.service.restore(self.plan)
        self.show_result(result)
        return result

    def show_result(self, result: RestoreResult) -> None:
        self._result = result
        self.restore_progress.setRange(0, 100)
        self.restore_progress.setValue(100 if result.success else 0)
        self.restore_result_title.setText(
            ui_text("Restore completed") if result.success else ui_text("Restore failed and rollback was applied")
        )
        if result.success:
            detail = ui_text(
                "Safety backups created: {backups}\n"
                "Restored: {restored}\n"
                "Permission-blocked: {blocked}\n"
                "Rollback failures: {rollback}"
            ).format(
                backups=result.backup_before_restore_count,
                restored=result.restored_count,
                blocked=result.permission_blocked_count,
                rollback=result.rollback_failure_count,
            )
        else:
            detail = ui_text(
                "Published before failure: {published}\n"
                "Rolled back: {rolled_back}\n"
                "Previous-data checksum: {checksum}\n"
                "Rollback failures: {rollback}"
            ).format(
                published=result.resource_published_before_failure_count,
                rolled_back=result.rollback_restored_resource_count,
                checksum=ui_text(
                    "Correct"
                    if result.previous_data_preserved
                    and not result.rollback_restored_checksum_mismatch_count
                    else "Mismatch"
                ),
                rollback=result.rollback_failure_count,
            )
        self.restore_result_detail.setText(detail)
        self.set_page(5)

    def show_failure(self, detail: str) -> None:
        LOGGER.error("HMS restore failed: %s", detail)
        self.restore_result_title.setText(ui_text("Restore failed and rollback was applied"))
        self.restore_result_detail.setText(
            ui_text("Restore failed; changed resources were rolled back.")
        )
        self.set_page(5)

    def retranslate_ui(self, _language: object = None) -> None:
        self.setWindowTitle(ui_text("Restore HMS"))
        self.setAccessibleName(ui_text("HMS restore wizard"))
        self.setAccessibleDescription(ui_text("Validate, preview and selectively restore an HMS backup"))
        self.description_label.setText(ui_text("The backup is validated before any destination is changed. Existing data is kept by default."))
        self.source_button.setText(ui_text("Browse…"))
        self.validation_table.setHorizontalHeaderLabels([ui_text("Property"), ui_text("Value")])
        self.restore_table.setHorizontalHeaderLabels([
            "", ui_text("Category"), ui_text("Scope"), ui_text("Relative path"), ui_text("Size"), ui_text("Status"),
        ])
        self.conflict_table.setHorizontalHeaderLabels([
            ui_text("Resource"), ui_text("Category"), ui_text("Scope"), ui_text("Conflict"), ui_text("Action"),
        ])
        self.action_button.setProperty("wizardSemanticKey", "wizard.restore")
        self._set_semantic_button(
            self.action_button,
            "Restore",
            "Restore the selected HMS resources",
        )
        super().retranslate_ui(_language)


class UserProfilesDialog(QDialog):
    """Manage stable per-user profile IDs without Windows authentication."""

    backup_requested = Signal(tuple)
    restore_requested = Signal()

    def __init__(
        self,
        service: UserProfileService,
        *,
        switch_callback: Callable[[str], ProfileSwitchReport] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("UserProfilesDialog")
        self.setModal(False)
        self.resize(1060, 680)
        self.setMinimumSize(880, 580)
        self.service = service
        self.switch_callback = switch_callback
        self.service.bootstrap(locale=translation_service().language.value)
        self.title_label = QLabel()
        self.title_label.setStyleSheet("font-size: 20px; font-weight: 600;")
        self.description_label = QLabel()
        self.description_label.setWordWrap(True)
        self.table = QTableWidget(0, 7)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column, width in ((1, 120), (2, 235), (3, 100), (4, 100), (5, 155), (6, 220)):
            self.table.horizontalHeader().setSectionResizeMode(
                column, QHeaderView.ResizeMode.Fixed
            )
            self.table.setColumnWidth(column, width)
        self.table.setAccessibleName(ui_text("User profiles"))
        self.table.setAccessibleDescription(ui_text("Per-user HMS interface profiles in Roaming AppData"))
        self.use_button = QPushButton()
        self.create_button = QPushButton()
        self.copy_button = QPushButton()
        self.rename_button = QPushButton()
        self.default_button = QPushButton()
        self.delete_button = QPushButton()
        self.export_button = QPushButton()
        self.import_button = QPushButton()
        self.close_button = QPushButton()
        self.close_button.clicked.connect(self.close)
        self.use_button.clicked.connect(self._use_selected)
        self.create_button.clicked.connect(self._prompt_create)
        self.copy_button.clicked.connect(self._prompt_copy)
        self.rename_button.clicked.connect(self._prompt_rename)
        self.default_button.clicked.connect(self._set_selected_default)
        self.delete_button.clicked.connect(self._delete_selected)
        self.export_button.clicked.connect(self._export_selected)
        self.import_button.clicked.connect(self.restore_requested)
        buttons = QHBoxLayout()
        for button in (
            self.use_button, self.create_button, self.copy_button,
            self.rename_button, self.default_button, self.delete_button,
            self.export_button, self.import_button,
        ):
            buttons.addWidget(button)
        buttons.addStretch(1)
        buttons.addWidget(self.close_button)
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.addWidget(self.title_label)
        layout.addWidget(self.description_label)
        layout.addWidget(self.table, 1)
        layout.addLayout(buttons)
        layout.addWidget(self.status_label)
        translation_service().language_changed.connect(self.retranslate_ui)
        self.refresh()
        self.retranslate_ui()

    @property
    def selected_profile_id(self) -> str | None:
        row = self.table.currentRow()
        item = self.table.item(row, 0) if row >= 0 else None
        return None if item is None else str(item.data(Qt.ItemDataRole.UserRole))

    def refresh(self) -> None:
        profiles = self.service.profiles()
        index = self.service.load_index()
        self.table.setRowCount(len(profiles))
        for row, profile in enumerate(profiles):
            values = (
                profile.display_name,
                ui_text(_language_label(profile.locale)),
                profile.updated_at_utc,
                ui_text("Active") if profile.profile_id == index.active_profile_id else "",
                ui_text("Default") if profile.profile_id == index.default_profile_id else "",
                str(len(profile.shortcuts)),
                profile.layout_description or ui_text("No layout description"),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, profile.profile_id)
                if column in {0, 2, 6}:
                    item.setData(LOCALIZATION_AUDIT_EXCLUDE_ROLE, True)
                self.table.setItem(row, column, item)
        if profiles:
            active_row = next(
                (row for row, item in enumerate(profiles) if item.profile_id == index.active_profile_id),
                0,
            )
            self.table.selectRow(active_row)

    def create_profile(self, display_name: str, *, locale: str = "VI_VN") -> UserProfile:
        profile = self.service.create(display_name, locale=locale)
        self.refresh()
        return profile

    def copy_profile(self, profile_id: str, display_name: str) -> UserProfile:
        profile = self.service.copy(profile_id, display_name)
        self.refresh()
        return profile

    def rename_profile(self, profile_id: str, display_name: str) -> UserProfile:
        profile = self.service.rename(profile_id, display_name)
        self.refresh()
        return profile

    def set_default_profile(self, profile_id: str) -> None:
        self.service.set_default(profile_id)
        self.refresh()

    def delete_profile(self, profile_id: str, *, replacement: str | None = None) -> None:
        self.service.delete(profile_id, replacement_active_id=replacement)
        self.refresh()

    def _use_selected(self) -> None:
        identifier = self.selected_profile_id
        if identifier is None or self.switch_callback is None:
            return
        report = self.switch_callback(identifier)
        self.status_label.setText(
            ui_text("Profile switched without changing the workspace or project.")
            if report.success
            else ui_text("Profile switch failed; the previous profile was restored.")
        )
        self.refresh()

    def _prompt_create(self) -> None:
        name, accepted = QInputDialog.getText(
            self,
            ui_text("Create profile"),
            ui_text("Profile name"),
        )
        if accepted and name.strip():
            self.create_profile(name, locale=translation_service().language.value)

    def _prompt_copy(self) -> None:
        identifier = self.selected_profile_id
        if identifier is None:
            return
        name, accepted = QInputDialog.getText(
            self,
            ui_text("Copy profile"),
            ui_text("New profile name"),
        )
        if accepted and name.strip():
            self.copy_profile(identifier, name)

    def _prompt_rename(self) -> None:
        identifier = self.selected_profile_id
        if identifier is None:
            return
        name, accepted = QInputDialog.getText(
            self,
            ui_text("Rename profile"),
            ui_text("Profile name"),
        )
        if accepted and name.strip():
            self.rename_profile(identifier, name)

    def _set_selected_default(self) -> None:
        if self.selected_profile_id is not None:
            self.set_default_profile(self.selected_profile_id)

    def _delete_selected(self) -> None:
        identifier = self.selected_profile_id
        if identifier is None:
            return
        try:
            self.delete_profile(identifier)
        except ProfileError as exc:
            LOGGER.error("HMS profile operation failed: %s", exc)
            self.status_label.setText(
                ui_text("Profile operation failed; no changes were applied.")
            )

    def _export_selected(self) -> None:
        if self.selected_profile_id is not None:
            self.backup_requested.emit((self.selected_profile_id,))

    def retranslate_ui(self, _language: object = None) -> None:
        apply_widget_font_tree(self, translation_service().language)
        self.setWindowTitle(ui_text("User profiles"))
        self.setAccessibleName(ui_text("User profile settings"))
        self.setAccessibleDescription(ui_text("Manage HMS interface profiles for the current Windows user"))
        self.title_label.setText(ui_text("User profiles"))
        self.description_label.setText(ui_text("Profiles store interface settings only. They are not Windows accounts, authentication or authorization."))
        self.table.setHorizontalHeaderLabels([
            ui_text("Profile name"), ui_text("Language"), ui_text("Updated"),
            ui_text("Active"), ui_text("Default"), ui_text("Custom shortcuts"),
            ui_text("Layout description"),
        ])
        labels = (
            (self.use_button, "Use profile"), (self.create_button, "Create"),
            (self.copy_button, "Copy"), (self.rename_button, "Rename"),
            (self.default_button, "Set as default"), (self.delete_button, "Delete"),
            (self.export_button, "Export profile"), (self.import_button, "Import profile"),
            (self.close_button, "Close"),
        )
        for button, key in labels:
            button.setText(ui_text(key))
            button.setAccessibleName(ui_text(key))
        localize_widget_tree(self)


def _actions_for(category: BackupCategory) -> tuple[ConflictAction, ...]:
    actions = [
        ConflictAction.KEEP_EXISTING,
        ConflictAction.REPLACE,
        ConflictAction.IMPORT_AS_COPY,
        ConflictAction.SKIP,
    ]
    if category in {
        BackupCategory.USER_SETTINGS,
        BackupCategory.QUICK_ACCESS,
        BackupCategory.RECENT_FILES,
        BackupCategory.MACHINE_CONFIG,
    }:
        actions.insert(2, ConflictAction.MERGE)
    return tuple(actions)


def _conflict_label(action: ConflictAction) -> str:
    return {
        ConflictAction.KEEP_EXISTING: "Keep existing",
        ConflictAction.REPLACE: "Replace",
        ConflictAction.MERGE: "Merge",
        ConflictAction.IMPORT_AS_COPY: "Import as copy",
        ConflictAction.SKIP: "Skip",
    }[action]


def _scope_label(scope: BackupScope) -> str:
    return {
        BackupScope.USER_ROAMING: "User roaming data",
        BackupScope.MACHINE_SHARED: "Machine-shared data",
    }[scope]


def _diagnostic_label(code: str) -> str:
    return {
        "READY": "Ready",
        "NO_DATA": "No data",
        "READ_DENIED": "Read permission denied",
        "PERMISSION_DENIED": "Write permission denied",
        "CONFLICT": "Conflict",
        "MERGE_NOT_SUPPORTED": "Merge is not supported",
        "RESTORED": "Restored",
        "ROLLED_BACK": "Rolled back",
        "RESTORE_FAILED": "Restore failed",
    }.get(str(code), str(code))


def _compatibility_label(state: CompatibilityState) -> str:
    return {
        CompatibilityState.COMPATIBLE: "Compatible",
        CompatibilityState.PARTIAL: "Partially compatible",
        CompatibilityState.MIGRATION_REQUIRED: "Migration required",
        CompatibilityState.NEWER_UNSUPPORTED: "Newer version is not supported",
        CompatibilityState.WRONG_PRODUCT: "Different product family",
        CompatibilityState.CORRUPT: "Corrupt",
        CompatibilityState.MISSING_REQUIRED: "Required data is missing",
    }[state]


def _language_label(locale: str) -> str:
    return {
        "VI_VN": "Vietnamese",
        "EN_US": "English",
        "KO_KR": "Korean",
    }.get(str(locale), str(locale))


def _format_size(value: int) -> str:
    size = float(max(0, value))
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


__all__ = [
    "BACKUP_CATEGORY_DESCRIPTIONS",
    "BACKUP_CATEGORY_LABELS",
    "BackupWizardDialog",
    "RestoreWizardDialog",
    "UserProfilesDialog",
]
