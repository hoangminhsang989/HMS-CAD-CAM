"""Three-locale production UI for the HMS storage architecture."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont, QFontDatabase
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTabWidget,
    QTableView,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from hms_cadcam.core.paths import (
    AppPathKind,
    ApplicationPathsService,
    PathStatus,
    STORAGE_LAYOUT_VERSION,
)
from hms_cadcam.core.storage_layout import (
    BootstrapResult,
    StorageBootstrapService,
    StorageLayoutInspection,
    StorageLayoutStatus,
)
from hms_cadcam.core.storage_maintenance import UserStorageMaintenanceService
from hms_cadcam.ui.i18n import apply_widget_font_tree, translation_service
from hms_cadcam.ui.localization import localize_widget_tree, ui_text
from hms_cadcam.ui.localized_dialogs import QMessageBox


@dataclass(frozen=True, slots=True)
class DataLocationRow:
    name_key: str
    kind: AppPathKind | None
    description_key: str
    boundary_key: str


INSTALL_ROWS = (
    DataLocationRow("Installation root", AppPathKind.INSTALL_ROOT, "Read-only while HMS is running", "Created by the installer"),
    DataLocationRow("Executable", AppPathKind.EXECUTABLE, "Production executable", "Created by the installer"),
    DataLocationRow("Runtime", AppPathKind.RUNTIME, "Read-only runtime files", "Created by the installer"),
    DataLocationRow("Resources", AppPathKind.RESOURCES, "Built-in read-only resources", "Created by the installer"),
    DataLocationRow("Plugins", AppPathKind.PLUGINS, "Installed plugins", "Created by the installer"),
    DataLocationRow("Translations", AppPathKind.TRANSLATIONS, "Installed translation resources", "Created by the installer"),
    DataLocationRow("Licenses", AppPathKind.LICENSES, "License files without secrets in diagnostics", "Created by the installer"),
)
MACHINE_ROWS = (
    DataLocationRow("Shared data root", AppPathKind.PROGRAM_DATA_ROOT, "Machine-wide data", "Managed by an administrator"),
    DataLocationRow("Tool Library", AppPathKind.TOOL_LIBRARY, "Shared Tool and Holder library", "Never stores project Tool snapshots"),
    DataLocationRow("Program Templates", AppPathKind.PROGRAM_TEMPLATES, "Storage location only in this stage", "No Program Templates behavior yet"),
    DataLocationRow("Posts", AppPathKind.POSTS, "Shared Post definitions", "Post safety remains fail-closed"),
    DataLocationRow("Machines", AppPathKind.MACHINES, "Shared machine definitions", "Does not certify machine-ready state"),
    DataLocationRow("Materials", AppPathKind.MATERIALS, "Shared material library", "Machine-wide resource"),
    DataLocationRow("Config", AppPathKind.MACHINE_CONFIG, "Locked machine policies and layout manifest", "Not project-specific state"),
    DataLocationRow("Schemas", AppPathKind.SCHEMAS, "Shared schemas and catalogs", "Does not change project SQLite schema"),
    DataLocationRow("Backups", AppPathKind.MACHINE_BACKUPS, "Backups of machine-wide resources only", "Never stores project autosave"),
)
USER_ROWS = (
    DataLocationRow("Roaming AppData root", AppPathKind.USER_ROAMING_ROOT, "Per-user roaming preferences", "Never stores project data"),
    DataLocationRow("User Config", AppPathKind.USER_CONFIG, "Language, recent files and preferences", "Current user only"),
    DataLocationRow("UI State", AppPathKind.USER_UI_STATE, "Window and workspace layout", "Current user only"),
    DataLocationRow("User Profiles", AppPathKind.USER_PROFILES, "Per-user HMS interface profiles", "Current Windows user only"),
    DataLocationRow("Local AppData root", AppPathKind.USER_LOCAL_ROOT, "Per-user local runtime data", "Never stores shared libraries"),
    DataLocationRow("Cache", AppPathKind.CACHE, "Rebuildable local cache", "Can be cleared safely"),
    DataLocationRow("Logs", AppPathKind.LOGS, "Application logs", "Current user only"),
    DataLocationRow("Temp", AppPathKind.TEMP, "Short-lived session files", "Not production project data"),
    DataLocationRow("Crash", AppPathKind.CRASH, "Crash diagnostics", "Current user only"),
)
DOCUMENT_ROWS = (
    DataLocationRow("HMS document", None, "The user chooses each .HMS file location", "Never moved to ProgramData or AppData"),
    DataLocationRow("CAM project root", None, "The user chooses the parent folder", "The project root remains the data boundary"),
)


class DataLocationsTableModel(QAbstractTableModel):
    """Render typed resolved paths without translating physical path data."""

    HEADERS = ("Location", "Physical path", "Status", "Access / boundary")

    def __init__(
        self,
        rows: tuple[DataLocationRow, ...],
        actual_paths: ApplicationPathsService,
        display_paths: ApplicationPathsService,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.rows = rows
        self.actual_paths = actual_paths
        self.display_paths = display_paths
        translation_service().language_changed.connect(self.retranslate)

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if orientation is Qt.Orientation.Horizontal and role in {
            Qt.ItemDataRole.DisplayRole,
            Qt.ItemDataRole.AccessibleTextRole,
        } and 0 <= section < len(self.HEADERS):
            return ui_text(self.HEADERS[section])
        return None

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self.rows):
            return None
        row = self.rows[index.row()]
        display = None if row.kind is None else self.display_paths.resolve(row.kind)
        actual = None if row.kind is None else self.actual_paths.resolve(row.kind)
        if role in {Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.AccessibleTextRole}:
            if index.column() == 0:
                return ui_text(row.name_key)
            if index.column() == 1:
                return ui_text("User-selected location") if display is None else display.display_path
            if index.column() == 2:
                return ui_text("User selected") if actual is None else ui_text(_status_key(actual.status))
            if index.column() == 3:
                return ui_text(row.boundary_key)
        if role in {Qt.ItemDataRole.ToolTipRole, Qt.ItemDataRole.AccessibleDescriptionRole}:
            detail = ui_text(row.description_key)
            if display is not None and actual is not None and display.physical_path != actual.physical_path:
                return f"{detail}\n{ui_text('Sandbox path')}: {actual.physical_path}"
            return detail
        if role is Qt.ItemDataRole.UserRole:
            return row.kind
        if role == Qt.ItemDataRole.FontRole and index.column() == 1:
            # Malgun Gothic follows the Korean legacy glyph convention where
            # U+005C may look like Won. Physical Windows paths must keep a
            # visually unambiguous backslash, so use an installed Latin system
            # font for the path column only.
            families = set(QFontDatabase.families())
            if "Segoe UI" in families:
                return QFont("Segoe UI")
        return None

    def retranslate(self, _language: object = None) -> None:
        if self.rows:
            self.dataChanged.emit(
                self.index(0, 0),
                self.index(len(self.rows) - 1, len(self.HEADERS) - 1),
            )
        self.headerDataChanged.emit(Qt.Orientation.Horizontal, 0, len(self.HEADERS) - 1)

    def localization_audit_value(self, index: QModelIndex, role: int):
        """Exclude physical path data while retaining every presentation role."""
        if index.column() == 1 and role in {
            Qt.ItemDataRole.DisplayRole,
            Qt.ItemDataRole.AccessibleTextRole,
            Qt.ItemDataRole.AccessibleDescriptionRole,
            Qt.ItemDataRole.ToolTipRole,
        }:
            return None
        return self.data(index, role)


class DataLocationsDialog(QDialog):
    """Inspect fixed production targets without exposing root editors."""

    inspection_changed = Signal(object)

    def __init__(
        self,
        paths: ApplicationPathsService,
        bootstrap: StorageBootstrapService,
        *,
        production_preview: ApplicationPathsService | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("DataLocationsDialog")
        self.setModal(False)
        self.resize(1120, 760)
        self.setMinimumSize(900, 620)
        self._paths = paths
        self._bootstrap = bootstrap
        self._display_paths = production_preview or paths
        self._maintenance = UserStorageMaintenanceService(paths)
        self._models: list[DataLocationsTableModel] = []
        self._diagnostic_title_key = "Storage diagnostics"
        self._diagnostic_rows: tuple[tuple[str, str, str], ...] = ()

        self.title_label = QLabel()
        self.title_label.setObjectName("DataLocationsTitle")
        self.title_label.setStyleSheet("font-size: 20px; font-weight: 600;")
        self.description_label = QLabel()
        self.description_label.setWordWrap(True)
        self.summary_label = QLabel()
        self.summary_label.setObjectName("StorageSummary")
        self.summary_label.setWordWrap(True)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("DataLocationGroups")
        for rows in (INSTALL_ROWS, MACHINE_ROWS, USER_ROWS, DOCUMENT_ROWS):
            page = QWidget()
            table = QTableView()
            table.setObjectName(f"DataLocationsTable{len(self._models)}")
            model = DataLocationsTableModel(rows, paths, self._display_paths, table)
            self._models.append(model)
            table.setModel(model)
            table.setAlternatingRowColors(True)
            table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
            table.verticalHeader().setVisible(False)
            table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
            table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
            table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
            table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
            table.setAccessibleName("Data locations")
            table.setAccessibleDescription("Storage paths and their current permission status")
            page_layout = QVBoxLayout(page)
            page_layout.setContentsMargins(8, 10, 8, 8)
            page_layout.addWidget(table)
            self.tabs.addTab(page, "")

        self.diagnostic_title = QLabel()
        self.diagnostic_title.setStyleSheet("font-weight: 600;")
        self.diagnostic_table = QTableWidget(0, 3)
        self.diagnostic_table.setObjectName("StorageDiagnosticTable")
        self.diagnostic_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.diagnostic_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.diagnostic_table.verticalHeader().setVisible(False)
        self.diagnostic_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.diagnostic_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.diagnostic_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.diagnostic_table.setMinimumHeight(145)
        self.diagnostic_table.setAccessibleName("Storage diagnostics")
        self.diagnostic_table.setAccessibleDescription(
            "Storage checks, results and details"
        )

        self.check_button = QPushButton()
        self.check_button.setObjectName("CheckStorageButton")
        self.check_button.clicked.connect(self.refresh_inspection)
        self.initialize_button = QPushButton()
        self.initialize_button.setObjectName("InitializeStorageButton")
        self.initialize_button.clicked.connect(self._initialize_missing)
        self.open_button = QPushButton()
        self.open_button.setObjectName("OpenStorageFolderButton")
        self.open_button.clicked.connect(self._open_current_folder)
        self.clear_cache_button = QPushButton()
        self.clear_cache_button.setObjectName("ClearCacheButton")
        self.clear_cache_button.clicked.connect(self._clear_cache)
        self.close_button = QPushButton()
        self.close_button.clicked.connect(self.close)
        buttons = QHBoxLayout()
        buttons.addWidget(self.check_button)
        buttons.addWidget(self.initialize_button)
        buttons.addWidget(self.open_button)
        buttons.addWidget(self.clear_cache_button)
        buttons.addStretch(1)
        buttons.addWidget(self.close_button)

        self.footer_label = QLabel()
        self.footer_label.setObjectName("StorageBoundaryFooter")
        self.footer_label.setWordWrap(True)
        self.footer_label.setStyleSheet("color: #5b6570;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(10)
        layout.addWidget(self.title_label)
        layout.addWidget(self.description_label)
        layout.addWidget(self.summary_label)
        layout.addWidget(self.tabs, 1)
        layout.addWidget(self.diagnostic_title)
        layout.addWidget(self.diagnostic_table)
        layout.addLayout(buttons)
        layout.addWidget(self.footer_label)

        translation_service().language_changed.connect(self.retranslate_ui)
        self.retranslate_ui()
        self.refresh_inspection()

    @property
    def inspection(self) -> StorageLayoutInspection:
        return self._bootstrap.inspect()

    def refresh_inspection(self) -> None:
        inspection = self._bootstrap.inspect()
        for model in self._models:
            model.retranslate()
        self._update_summary(inspection)
        rows = (
            ("Install root", "PASS" if inspection.install_ready else "WARNING", "Read-only runtime contract"),
            ("Shared data", "PASS" if inspection.program_data_ready else "WARNING", _diagnostic_display_key(inspection.diagnostic_code)),
            ("Roaming AppData", "PASS" if inspection.user_roaming_ready else "WARNING", "User preference scope"),
            ("Local AppData", "PASS" if inspection.user_local_ready else "WARNING", "Cache, Logs, Temp and Crash scope"),
            ("Project boundary", "PASS", ".HMS and CAM projects remain user-selected"),
        )
        self.show_diagnostics("Storage diagnostics", rows)
        self.initialize_button.setEnabled(not inspection.ready)
        self.inspection_changed.emit(inspection)

    def _update_summary(self, inspection: StorageLayoutInspection) -> None:
        self.summary_label.setText(
            f"{ui_text('Storage status')}: {ui_text(_layout_status_key(inspection.status))}  ·  "
            f"{ui_text('Layout version')}: {inspection.layout_version or STORAGE_LAYOUT_VERSION}  ·  "
            f"{ui_text('Missing directories')}: {len(inspection.missing_directories)}"
        )

    def show_diagnostics(
        self,
        title_key: str,
        rows: Iterable[tuple[str, str, str]],
    ) -> None:
        self._diagnostic_title_key = title_key
        self._diagnostic_rows = tuple(rows)
        self._render_diagnostics()

    def _render_diagnostics(self) -> None:
        materialized = self._diagnostic_rows
        self.diagnostic_title.setText(ui_text(self._diagnostic_title_key))
        self.diagnostic_table.setRowCount(len(materialized))
        for row_index, (check, result, detail) in enumerate(materialized):
            for column, value in enumerate((ui_text(check), ui_text(result), ui_text(detail))):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.diagnostic_table.setItem(row_index, column, item)
        self.diagnostic_table.resizeRowsToContents()

    def retranslate_ui(self, _language: object = None) -> None:
        apply_widget_font_tree(self, translation_service().language)
        self.setWindowTitle(ui_text("Data locations"))
        self.setAccessibleName(ui_text("Data locations"))
        self.setAccessibleDescription(ui_text("Inspect fixed HMS storage roots and permissions"))
        self.title_label.setText(ui_text("Data locations"))
        self.description_label.setText(
            ui_text("Review installation, shared, user and project storage boundaries. Production roots cannot be changed here.")
        )
        for index, key in enumerate(("Program installation", "Shared machine data", "User data", "Documents and projects")):
            self.tabs.setTabText(index, ui_text(key))
            self.tabs.setTabToolTip(index, ui_text(key))
        self.diagnostic_table.setHorizontalHeaderLabels(
            [ui_text("Check"), ui_text("Result"), ui_text("Detail")]
        )
        self.check_button.setText(ui_text("Check again"))
        self.check_button.setToolTip(ui_text("Recheck all storage locations and permissions"))
        self.initialize_button.setText(ui_text("Initialize missing folders"))
        self.initialize_button.setToolTip(ui_text("Create only permitted missing folders; never create a project"))
        self.open_button.setText(ui_text("Open folder"))
        self.open_button.setToolTip(ui_text("Open the selected storage folder"))
        self.clear_cache_button.setText(ui_text("Clear cache safely"))
        self.clear_cache_button.setToolTip(ui_text("Delete only validated files below the user Cache folder"))
        self.close_button.setText(ui_text("Close"))
        self.footer_label.setText(
            ui_text("Physical paths, hashes, IDs, schema versions and executable names are never translated. No project data is moved by this page.")
        )
        for model in self._models:
            model.retranslate()
        localize_widget_tree(self)
        self._update_summary(self._bootstrap.inspect())
        self._render_diagnostics()

    def _initialize_missing(self) -> None:
        result = self._bootstrap.bootstrap()
        self._show_bootstrap_result(result)
        self.refresh_inspection()

    def _show_bootstrap_result(self, result: BootstrapResult) -> None:
        self.show_diagnostics(
            "Bootstrap result",
            (
                ("Outcome", result.outcome.value, _diagnostic_display_key(result.diagnostic_code)),
                ("Created folders", str(len(result.created_directories)), "Atomic directory transaction"),
                ("Rolled back folders", str(len(result.rolled_back_directories)), "Existing data is never deleted"),
                ("Layout manifest", "PASS" if result.manifest_written else "WARNING", "storage-layout.json"),
            ),
        )

    def _open_current_folder(self) -> None:
        model = self._models[self.tabs.currentIndex()]
        table = self.tabs.currentWidget().findChild(QTableView)
        row = 0 if table is None or not table.currentIndex().isValid() else table.currentIndex().row()
        if not 0 <= row < len(model.rows) or model.rows[row].kind is None:
            return
        path = self._paths.path(model.rows[row].kind)
        if path.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _clear_cache(self) -> None:
        response = QMessageBox.question(
            self,
            ui_text("Clear cache safely"),
            ui_text("Only rebuildable files below the user Cache folder will be removed."),
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if response != QMessageBox.StandardButton.Ok:
            return
        result = self._maintenance.clear_cache()
        self.show_diagnostics(
            "Cache cleanup result",
            (
                ("Removed files", str(result.removed_file_count), "User-local Cache only"),
                ("Removed folders", str(result.removed_directory_count), "Validated root containment"),
                ("Blocked paths", str(len(result.blocked_paths)), "Unsafe paths are preserved"),
            ),
        )


class StorageNotificationBar(QFrame):
    """Non-modal startup warning that blocks only dependent operations."""

    details_requested = Signal()
    recheck_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("StorageNotificationBar")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet("QFrame#StorageNotificationBar { background: #fff4d6; border: 1px solid #d9a441; }")
        self.message_label = QLabel()
        self.message_label.setWordWrap(True)
        self.details_button = QPushButton()
        self.details_button.clicked.connect(self.details_requested)
        self.recheck_button = QPushButton()
        self.recheck_button.clicked.connect(self.recheck_requested)
        self.close_button = QPushButton()
        self.close_button.clicked.connect(self.hide)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 5, 8, 5)
        layout.addWidget(self.message_label, 1)
        layout.addWidget(self.details_button)
        layout.addWidget(self.recheck_button)
        layout.addWidget(self.close_button)
        translation_service().language_changed.connect(self.retranslate_ui)
        self.retranslate_ui()

    def update_inspection(self, inspection: StorageLayoutInspection) -> None:
        self.setProperty("storageDiagnosticCode", inspection.diagnostic_code)
        self.setVisible(not inspection.ready)
        if inspection.status is StorageLayoutStatus.READ_ONLY:
            key = "Shared data is read-only. Editing machine-wide libraries is disabled."
        else:
            key = "Shared data is not ready. Some libraries are available in read-only mode."
        self.message_label.setProperty("storageMessageKey", key)
        self.message_label.setText(ui_text(key))

    def retranslate_ui(self, _language: object = None) -> None:
        key = str(
            self.message_label.property("storageMessageKey")
            or "Shared data is not ready. Some libraries are available in read-only mode."
        )
        self.message_label.setText(ui_text(key))
        self.message_label.setAccessibleName(ui_text("Storage warning"))
        self.message_label.setAccessibleDescription(ui_text(key))
        self.details_button.setText(ui_text("View details"))
        self.details_button.setAccessibleName(ui_text("View storage details"))
        self.recheck_button.setText(ui_text("Check again"))
        self.recheck_button.setAccessibleName(ui_text("Recheck storage"))
        self.close_button.setText(ui_text("Close"))
        self.close_button.setAccessibleName(ui_text("Close storage warning"))
        localize_widget_tree(self)


def _status_key(status: PathStatus) -> str:
    return {
        PathStatus.READY: "Ready",
        PathStatus.MISSING: "Not found",
        PathStatus.READ_ONLY: "Read-only",
        PathStatus.READ_DENIED: "No read permission",
        PathStatus.NOT_CREATABLE: "Cannot be created",
        PathStatus.UNSAFE: "Unsafe path",
        PathStatus.USER_SELECTION_REQUIRED: "User selected",
    }[status]


def _layout_status_key(status: StorageLayoutStatus) -> str:
    return {
        StorageLayoutStatus.READY: "Ready",
        StorageLayoutStatus.INCOMPLETE: "Incomplete layout",
        StorageLayoutStatus.READ_ONLY: "Read-only",
        StorageLayoutStatus.PERMISSION_DENIED: "Permission denied",
        StorageLayoutStatus.UNSUPPORTED_VERSION: "Unsupported version",
        StorageLayoutStatus.UNSAFE_PATH: "Unsafe path",
        StorageLayoutStatus.ADMIN_INSTALL_REQUIRED: "Administrator installation required",
        StorageLayoutStatus.FAILED: "Failed",
    }[status]


def _diagnostic_display_key(code: str) -> str:
    return {
        "READY": "READY",
        "INCOMPLETE_LAYOUT": "INCOMPLETE_LAYOUT",
        "ADMIN_INSTALL_REQUIRED": "ADMIN_INSTALL_REQUIRED",
        "UNSAFE_PATH": "UNSAFE_PATH",
        "FILE_DIRECTORY_COLLISION": "FILE_DIRECTORY_COLLISION",
        "UNSUPPORTED_LAYOUT_MANIFEST": "UNSUPPORTED_LAYOUT_MANIFEST",
        "READ_ONLY": "READ_ONLY",
    }.get(str(code), str(code))


__all__ = [
    "DataLocationRow",
    "DataLocationsDialog",
    "DataLocationsTableModel",
    "StorageNotificationBar",
]
