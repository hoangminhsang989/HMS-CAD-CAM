"""Stage 9A.7 WP2 unified Post/Program Assembly presentation.

The panel is deliberately limited to presentation and explicit operation-list
management.  It does not calculate, simulate, generate, save, export, or
write directly to a project database.  Domain evidence crosses this module
through :class:`PostAssemblyProjectionAdapter`; all UI state is keyed by the
stable operation ID rather than a table row.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any, Protocol

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QObject,
    QSignalBlocker,
    Qt,
    Signal,
)
from PySide6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from hms_cadcam.cam.domain import ArtifactStatus
from hms_cadcam.ui.i18n import UiLanguage, translation_service
from hms_cadcam.ui.localization import (
    operation_display_name,
    operation_type_display_name,
    setup_display_name,
    translate_status,
    ui_text,
)
from hms_cadcam.ui.post_assembly_projection import (
    DiagnosticSeverity,
    OperationArtifactState,
    PostAssemblyProjection,
    PostAssemblyProjectionInput,
    PostAssemblyProjector,
    ReadinessState,
    project_post_assembly,
)


class PostAssemblyOperationAction(StrEnum):
    ADD = "add"
    REMOVE = "remove"
    MOVE_UP = "move_up"
    MOVE_DOWN = "move_down"
    CLEAR = "clear"


class PostAssemblyColumn(StrEnum):
    """Stable operation-table columns."""

    ORDER = "order"
    OPERATION = "operation"
    STRATEGY = "strategy"
    TOOL = "tool"
    SETUP = "setup"
    STATUS = "status"


@dataclass(frozen=True, slots=True)
class PostAssemblyOperationRow:
    """Typed evidence shown by one operation-table row."""

    operation_id: str
    execution_order: int
    operation_name: str
    operation_type: str
    strategy: str
    tool: str
    setup: str
    status: str
    enabled: bool = True
    missing: bool = False
    artifact_state: OperationArtifactState = OperationArtifactState.CURRENT
    diagnostic_severity: DiagnosticSeverity = DiagnosticSeverity.INFO
    selected: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.operation_id, str) or not self.operation_id.strip():
            raise ValueError("operation_id must not be empty")
        if not isinstance(self.execution_order, int) or isinstance(
            self.execution_order, bool
        ):
            raise TypeError("execution_order must be int")
        if self.execution_order < 0:
            raise ValueError("execution_order must not be negative")
        for field in (
            "operation_name",
            "operation_type",
            "strategy",
            "tool",
            "setup",
            "status",
        ):
            if not isinstance(getattr(self, field), str):
                raise TypeError(f"{field} must be str")
        if type(self.enabled) is not bool or type(self.missing) is not bool:
            raise TypeError("enabled and missing must be bool")
        if not isinstance(self.artifact_state, OperationArtifactState):
            raise TypeError("artifact_state must be OperationArtifactState")
        if not isinstance(self.diagnostic_severity, DiagnosticSeverity):
            raise TypeError("diagnostic_severity must be DiagnosticSeverity")
        if type(self.selected) is not bool:
            raise TypeError("selected must be bool")

    @property
    def currentness(self) -> OperationArtifactState:
        """Alias used by presentation clients without exposing row indexes."""

        return self.artifact_state

    @property
    def identity(self) -> str:
        return self.operation_id


@dataclass(frozen=True, slots=True)
class PostAssemblyProjectionInputEvidence:
    """Boundary payload accepted by the WP2 adapter."""

    projection_input: PostAssemblyProjectionInput
    operation_rows: tuple[PostAssemblyOperationRow, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.projection_input, PostAssemblyProjectionInput):
            raise TypeError("projection_input must be PostAssemblyProjectionInput")
        rows = tuple(self.operation_rows)
        if any(not isinstance(row, PostAssemblyOperationRow) for row in rows):
            raise TypeError("operation_rows must contain PostAssemblyOperationRow")
        if len({row.operation_id for row in rows}) != len(rows):
            raise ValueError("operation_rows must not contain duplicate operation IDs")
        object.__setattr__(self, "operation_rows", rows)


class PostAssemblyProjectionAdapter:
    """Map existing application evidence into the WP1 typed projector.

    ``build`` accepts an already typed input for deterministic tests.  When a
    ProjectService/ProjectSession pair is supplied, only the in-memory CAM
    snapshot is inspected; SQLite and downstream executors are intentionally
    outside this boundary.
    """

    def __init__(
        self,
        service: object | None = None,
        session: object | None = None,
        *,
        projector: PostAssemblyProjector | None = None,
        evidence_provider: Callable[[], PostAssemblyProjectionInput] | None = None,
    ) -> None:
        self._service = service
        self._session = session
        self._projector = projector or PostAssemblyProjector()
        self._evidence_provider = evidence_provider

    def build_input(
        self,
        evidence: PostAssemblyProjectionInput | None = None,
    ) -> PostAssemblyProjectionInput:
        if evidence is not None:
            if not isinstance(evidence, PostAssemblyProjectionInput):
                raise TypeError("evidence must be PostAssemblyProjectionInput")
            return evidence
        if self._evidence_provider is not None:
            value = self._evidence_provider()
            if not isinstance(value, PostAssemblyProjectionInput):
                raise TypeError("evidence provider returned an invalid input")
            return value
        return self._from_application_state()

    def set_session(self, session: object | None) -> None:
        """Replace the in-memory project-session evidence source."""

        self._session = session

    def project(
        self,
        evidence: PostAssemblyProjectionInput | None = None,
    ) -> PostAssemblyProjection:
        return self._projector.project(self.build_input(evidence))

    def capture(
        self,
        evidence: PostAssemblyProjectionInput | None = None,
    ) -> PostAssemblyProjectionInputEvidence:
        typed = self.build_input(evidence)
        return PostAssemblyProjectionInputEvidence(typed, self.operation_rows())

    def adapt(
        self, evidence: PostAssemblyProjectionInput | None = None
    ) -> PostAssemblyProjectionInputEvidence:
        """Compatibility alias for the typed capture boundary."""
        return self.capture(evidence)

    def build_projection(
        self, evidence: PostAssemblyProjectionInput | None = None
    ) -> PostAssemblyProjection:
        """Return the deterministic WP1 projection for current evidence."""
        return self.project(evidence)

    def operation_rows(self) -> tuple[PostAssemblyOperationRow, ...]:
        service = self._service
        if service is None or not bool(getattr(service, "has_project", False)):
            return ()
        snapshot = getattr(service, "cam_snapshot", None)
        if snapshot is None:
            return ()
        rows: list[PostAssemblyOperationRow] = []
        order = 0
        for job in tuple(getattr(snapshot, "jobs", ())):
            for setup in tuple(getattr(job, "setups", ())):
                tree = getattr(setup, "operation_tree", None)
                operations = tuple(getattr(tree, "operations", ()))
                for operation in operations:
                    operation_id = str(getattr(operation, "operation_id", ""))
                    if not operation_id:
                        continue
                    artifact = getattr(operation, "artifact_state", None)
                    artifact_status = getattr(artifact, "status", None)
                    state = _artifact_state(artifact_status)
                    enabled = bool(getattr(operation, "enabled", True))
                    missing = state in {
                        OperationArtifactState.MISSING,
                        OperationArtifactState.CALCULATION_REQUIRED,
                    }
                    severity = (
                        DiagnosticSeverity.ERROR
                        if missing or not enabled
                        else DiagnosticSeverity.INFO
                    )
                    strategy = str(getattr(operation, "strategy_key", ""))
                    name = str(getattr(operation, "name", strategy or "Operation"))
                    tool_reference = getattr(operation, "tool_assembly", None)
                    tool = str(
                        getattr(tool_reference, "assembly_id", "")
                        or getattr(tool_reference, "name", "")
                        or "—"
                    )
                    rows.append(
                        PostAssemblyOperationRow(
                            operation_id=operation_id,
                            execution_order=order,
                            operation_name=name,
                            operation_type=str(
                                getattr(operation, "kind", "operation")
                            ),
                            strategy=strategy,
                            tool=tool,
                            setup=str(getattr(setup, "name", "—")),
                            status=str(
                                getattr(artifact_status, "value", state.value)
                            ).upper(),
                            enabled=enabled,
                            missing=missing,
                            artifact_state=state,
                            diagnostic_severity=severity,
                        )
                    )
                    order += 1
        return tuple(rows)

    def _from_application_state(self) -> PostAssemblyProjectionInput:
        rows = self.operation_rows()
        operation_ids = tuple(row.operation_id for row in rows)
        project = getattr(self._session, "manifest", None)
        project_id = getattr(project, "project_id", None)
        has_project = bool(getattr(self._service, "has_project", False))
        generation = (
            getattr(self._service, "cam_generation", None)
            if has_project
            else None
        )
        order_fingerprint = _fingerprint(operation_ids) if operation_ids else None
        operation_state = (
            OperationArtifactState.MISSING
            if not rows
            else (
                OperationArtifactState.CALCULATION_REQUIRED
                if any(row.missing for row in rows)
                else OperationArtifactState.CURRENT
            )
        )
        return PostAssemblyProjectionInput(
            project_id=project_id,
            project_generation=generation,
            operation_ids=operation_ids,
            operation_order_fingerprint=order_fingerprint,
            operation_enabled=all(row.enabled for row in rows) if rows else False,
            operation_missing=any(row.missing for row in rows) if rows else True,
            operation_artifact_state=operation_state,
            simulation_status=getattr(
                getattr(self._service, "post_assembly_projection", None),
                "simulation_status",
                PostAssemblyProjectionInput().simulation_status,
            ),
        )


class PostAssemblyTableRole:
    """Custom roles kept stable for selection/evidence consumers."""

    OPERATION_ID = int(Qt.ItemDataRole.UserRole) + 1
    ROW_EVIDENCE = int(Qt.ItemDataRole.UserRole) + 2
    COLUMN_KEY = int(Qt.ItemDataRole.UserRole) + 3


class PostAssemblyOperationTableModel(QAbstractTableModel):
    """Production model/view table with explicit order and stable identity."""

    OPERATION_ID = PostAssemblyTableRole.OPERATION_ID
    ROW_EVIDENCE = PostAssemblyTableRole.ROW_EVIDENCE
    HEADERS: tuple[PostAssemblyColumn, ...] = (
        PostAssemblyColumn.ORDER,
        PostAssemblyColumn.OPERATION,
        PostAssemblyColumn.STRATEGY,
        PostAssemblyColumn.TOOL,
        PostAssemblyColumn.SETUP,
        PostAssemblyColumn.STATUS,
    )
    COLUMN_NAMES = HEADERS
    HEADER_SOURCES = {
        PostAssemblyColumn.ORDER: "Order",
        PostAssemblyColumn.OPERATION: "Operation",
        PostAssemblyColumn.STRATEGY: "Strategy",
        PostAssemblyColumn.TOOL: "Tool",
        PostAssemblyColumn.SETUP: "Setup",
        PostAssemblyColumn.STATUS: "Status",
    }

    def __init__(
        self,
        rows: Iterable[PostAssemblyOperationRow] = (),
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._rows: tuple[PostAssemblyOperationRow, ...] = ()
        self._locale = translation_service().language
        self.set_rows(rows)

    @property
    def rows(self) -> tuple[PostAssemblyOperationRow, ...]:
        return self._rows

    def row_for_operation_id(self, operation_id: str | None) -> int:
        if not operation_id:
            return -1
        return next(
            (
                index
                for index, row in enumerate(self._rows)
                if row.operation_id == operation_id
            ),
            -1,
        )

    def operation_id_at(self, row: int) -> str | None:
        if 0 <= row < len(self._rows):
            return self._rows[row].operation_id
        return None

    def set_rows(self, rows: Iterable[PostAssemblyOperationRow]) -> None:
        values = tuple(rows)
        if any(not isinstance(row, PostAssemblyOperationRow) for row in values):
            raise TypeError("rows must contain PostAssemblyOperationRow")
        if len({row.operation_id for row in values}) != len(values):
            raise ValueError("duplicate operation_id")
        selected = next((row.operation_id for row in values if row.selected), None)
        previous = next((row.operation_id for row in self._rows if row.selected), None)
        selected_id = selected or previous
        if selected_id is not None:
            values = tuple(
                replace(row, selected=row.operation_id == selected_id) for row in values
            )
        self.beginResetModel()
        self._rows = values
        self.endResetModel()

    def set_selected_operation(self, operation_id: str | None) -> None:
        if operation_id is not None and self.row_for_operation_id(operation_id) < 0:
            operation_id = None
        values = tuple(
            replace(row, selected=row.operation_id == operation_id) for row in self._rows
        )
        if values == self._rows:
            return
        self._rows = values
        if values:
            self.dataChanged.emit(
                self.index(0, 0),
                self.index(len(values) - 1, len(self.HEADERS) - 1),
                [Qt.ItemDataRole.DisplayRole, PostAssemblyTableRole.ROW_EVIDENCE],
            )

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.HEADERS)

    def data(
        self,
        index: QModelIndex,
        role: Qt.ItemDataRole = Qt.ItemDataRole.DisplayRole,
    ) -> object:
        if not index.isValid() or not (0 <= index.row() < len(self._rows)):
            return None
        row = self._rows[index.row()]
        column = self.HEADERS[index.column()]
        if role == Qt.ItemDataRole.DisplayRole:
            return self._display_value(row, column)
        if role == Qt.ItemDataRole.ToolTipRole:
            return ui_text(row.status) if row.status else ui_text("No status")
        if role == PostAssemblyTableRole.OPERATION_ID:
            return row.operation_id
        if role == PostAssemblyTableRole.ROW_EVIDENCE:
            return row
        if role == PostAssemblyTableRole.COLUMN_KEY:
            return column.value
        if role == Qt.ItemDataRole.ForegroundRole and not row.enabled:
            return Qt.GlobalColor.gray
        return None

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: Qt.ItemDataRole = Qt.ItemDataRole.DisplayRole,
    ) -> object:
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            if 0 <= section < len(self.HEADERS):
                return ui_text(self.HEADER_SOURCES[self.HEADERS[section]])
        if role == Qt.ItemDataRole.ToolTipRole and orientation == Qt.Orientation.Horizontal:
            if 0 <= section < len(self.HEADERS):
                return ui_text(self.HEADER_SOURCES[self.HEADERS[section]])
        return super().headerData(section, orientation, role)

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def _display_value(
        self,
        row: PostAssemblyOperationRow,
        column: PostAssemblyColumn,
    ) -> str:
        if column is PostAssemblyColumn.ORDER:
            return str(row.execution_order + 1)
        if column is PostAssemblyColumn.OPERATION:
            return operation_display_name(row.operation_name)
        if column is PostAssemblyColumn.STRATEGY:
            return operation_type_display_name(row.strategy or row.operation_type)
        if column is PostAssemblyColumn.TOOL:
            return row.tool
        if column is PostAssemblyColumn.SETUP:
            return setup_display_name(row.setup)
        return translate_status(row.status)

    def retranslate_ui(self, language: object = None) -> None:
        self._locale = UiLanguage.coerce(language or self._locale)
        if self.columnCount() > 0:
            self.headerDataChanged.emit(
                Qt.Orientation.Horizontal, 0, self.columnCount() - 1
            )
        if self.rowCount() > 0 and self.columnCount() > 0:
            self.dataChanged.emit(
                self.index(0, 0),
                self.index(self.rowCount() - 1, self.columnCount() - 1),
                [Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole],
            )


class PostAssemblyOperationController(Protocol):
    """Optional application service boundary for operation-list actions."""

    def add_operation(self, operation_id: str) -> bool: ...
    def remove_operation(self, operation_id: str) -> bool: ...
    def move_operation(self, operation_id: str, delta: int) -> bool: ...
    def clear_assembly(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class PostAssemblyPanelState:
    """State that must survive close/reopen and feature-flag presentation."""

    project_id: str | None = None
    project_generation: int | None = None
    document_title: str = ""
    dirty_state: bool = False
    operation_ids: tuple[str, ...] = ()
    selected_operation_id: str | None = None
    accepted_result_id: str | None = None
    managed_artifact_id: str | None = None
    external_artifact_id: str | None = None
    worker_identity: str | None = None


class UnifiedPostAssemblyPanel(QWidget):
    """WP2 panel shell with explicit action/capability boundaries."""

    state_changed = Signal()
    operation_action_requested = Signal(str, str)

    def __init__(
        self,
        adapter: PostAssemblyProjectionAdapter | None = None,
        *,
        operation_controller: PostAssemblyOperationController | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("UnifiedPostAssemblyPanel")
        self.setAccessibleName(ui_text("Post / Program Assembly"))
        self._adapter = adapter or PostAssemblyProjectionAdapter()
        self._operation_controller = operation_controller
        self._projection: PostAssemblyProjection | None = None
        self._available_rows: tuple[PostAssemblyOperationRow, ...] = ()
        self._assembly_ids: list[str] = []
        self._selected_operation_id: str | None = None
        self._source_selected_operation_id: str | None = None
        self._state = PostAssemblyPanelState()
        self.model = PostAssemblyOperationTableModel(parent=self)
        self.operation_model = self.model
        self.table_model = self.model
        self._build_ui()
        self._set_capability_placeholders()
        self._refresh_action_state()
        translation_service().language_changed.connect(self.retranslate_ui)

    @property
    def projection(self) -> PostAssemblyProjection | None:
        return self._projection

    @property
    def operation_ids(self) -> tuple[str, ...]:
        return tuple(self._assembly_ids)

    @property
    def selected_operation_id(self) -> str | None:
        return self._selected_operation_id

    @property
    def panel_state(self) -> PostAssemblyPanelState:
        return self._state

    def open(self) -> None:
        """Show/focus the panel without starting any downstream action."""
        self.show()
        self.raise_()
        self.activateWindow()

    def close_panel(self) -> PostAssemblyPanelState:
        """Return presentation state for a host that is about to close."""
        self._state = self.snapshot_state()
        self.hide()
        return self._state

    def set_projection(
        self,
        projection: PostAssemblyProjection,
        *,
        rows: Iterable[PostAssemblyOperationRow] | None = None,
    ) -> None:
        if not isinstance(projection, PostAssemblyProjection):
            raise TypeError("projection must be PostAssemblyProjection")
        self._projection = projection
        if rows is not None:
            self.set_available_operations(rows)
        self._render_readiness()
        self._refresh_action_state()

    def refresh_from_adapter(self) -> None:
        evidence = self._adapter.capture()
        projection = self._adapter.project(evidence.projection_input)
        self.set_available_operations(evidence.operation_rows)
        self.set_projection(projection)

    def set_available_operations(
        self,
        rows: Iterable[PostAssemblyOperationRow],
    ) -> None:
        values = tuple(rows)
        if len({row.operation_id for row in values}) != len(values):
            raise ValueError("duplicate operation IDs")
        self._available_rows = values
        known = {row.operation_id for row in values}
        self._assembly_ids = [item for item in self._assembly_ids if item in known]
        if self._state.operation_ids:
            self._assembly_ids = [
                item for item in self._state.operation_ids if item in known
            ]
        self._sync_source_picker()
        self._sync_table()

    def set_operation_rows(self, rows: Iterable[PostAssemblyOperationRow]) -> None:
        """Set the visible assembly rows directly for review harnesses/tests."""

        values = tuple(rows)
        if len({row.operation_id for row in values}) != len(values):
            raise ValueError("duplicate operation IDs")
        self._available_rows = values
        self._assembly_ids = [row.operation_id for row in values]
        self._sync_source_picker()
        self._sync_table()

    def select_operation(self, operation_id: str | None) -> None:
        if operation_id is not None and operation_id not in self._assembly_ids:
            if any(row.operation_id == operation_id for row in self._available_rows):
                self._source_selected_operation_id = operation_id
            operation_id = None
        self._selected_operation_id = operation_id
        self.model.set_selected_operation(operation_id)
        row = self.model.row_for_operation_id(operation_id)
        if row >= 0:
            self.operation_table.selectRow(row)
        else:
            self.operation_table.clearSelection()
        self._state = replace(self._state, selected_operation_id=operation_id)
        self._refresh_action_state()
        self.state_changed.emit()

    def set_selected_available_operation(self, operation_id: str | None) -> None:
        """Select a source operation without adding it to the assembly."""
        if operation_id is not None and not any(
            row.operation_id == operation_id for row in self._available_rows
        ):
            raise KeyError(operation_id)
        self._source_selected_operation_id = operation_id
        self._set_source_picker_selection(operation_id)
        if operation_id in self._assembly_ids:
            self.select_operation(operation_id)
            return
        self._selected_operation_id = None
        self.model.set_selected_operation(None)
        self.operation_table.clearSelection()
        self._refresh_action_state()

    def snapshot_state(self) -> PostAssemblyPanelState:
        return replace(self._state, operation_ids=tuple(self._assembly_ids))

    def restore_state(self, state: PostAssemblyPanelState) -> None:
        if not isinstance(state, PostAssemblyPanelState):
            raise TypeError("state must be PostAssemblyPanelState")
        self._state = state
        self._assembly_ids = [
            item
            for item in state.operation_ids
            if any(row.operation_id == item for row in self._available_rows)
        ]
        self._sync_table()
        self.select_operation(state.selected_operation_id)

    def add_selected_operation(self) -> bool:
        source_id = self._source_selected_operation_id or self._selected_operation_id
        if source_id is None:
            return False
        if source_id in self._assembly_ids:
            return False
        source_row = next(
            (row for row in self._available_rows if row.operation_id == source_id),
            None,
        )
        if source_row is None or not source_row.enabled or source_row.missing:
            return False
        if self._operation_controller is not None and not self._operation_controller.add_operation(
            source_id
        ):
            return False
        self._assembly_ids.append(source_id)
        self._source_selected_operation_id = None
        self._set_source_picker_selection(None)
        self._sync_table(select_id=source_id)
        self.operation_action_requested.emit(PostAssemblyOperationAction.ADD.value, source_id)
        return True

    def remove_selected_operation(self) -> bool:
        operation_id = self._selected_operation_id
        if operation_id is None or operation_id not in self._assembly_ids:
            return False
        if self._operation_controller is not None and not self._operation_controller.remove_operation(
            operation_id
        ):
            return False
        index = self._assembly_ids.index(operation_id)
        self._assembly_ids.pop(index)
        next_id = (
            self._assembly_ids[min(index, len(self._assembly_ids) - 1)]
            if self._assembly_ids
            else None
        )
        self._sync_table(select_id=next_id)
        self.operation_action_requested.emit(PostAssemblyOperationAction.REMOVE.value, operation_id)
        return True

    def move_selected_operation(self, delta: int) -> bool:
        if delta not in {-1, 1}:
            raise ValueError("operation move must be one row")
        operation_id = self._selected_operation_id
        if operation_id is None or operation_id not in self._assembly_ids:
            return False
        index = self._assembly_ids.index(operation_id)
        target = index + delta
        if not 0 <= target < len(self._assembly_ids):
            return False
        if self._operation_controller is not None and not self._operation_controller.move_operation(
            operation_id, delta
        ):
            return False
        self._assembly_ids[index], self._assembly_ids[target] = (
            self._assembly_ids[target],
            self._assembly_ids[index],
        )
        self._sync_table(select_id=operation_id)
        self.operation_action_requested.emit(
            (
                PostAssemblyOperationAction.MOVE_UP.value
                if delta < 0
                else PostAssemblyOperationAction.MOVE_DOWN.value
            ),
            operation_id,
        )
        return True

    def clear_operation_list(self) -> bool:
        if not self._assembly_ids:
            return False
        if self._operation_controller is not None and not self._operation_controller.clear_assembly():
            return False
        previous = tuple(self._assembly_ids)
        self._assembly_ids.clear()
        self._sync_table()
        self.operation_action_requested.emit(
            PostAssemblyOperationAction.CLEAR.value, ",".join(previous)
        )
        return True

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)
        header = QHBoxLayout()
        self.title_label = QLabel(ui_text("Post / Program Assembly"))
        self.title_label.setObjectName("PostAssemblyTitle")
        self.title_label.setAccessibleName(ui_text("Post / Program Assembly"))
        header.addWidget(self.title_label)
        header.addStretch(1)
        self.readiness_label = QLabel()
        self.readiness_label.setObjectName("PostAssemblyReadiness")
        self.readiness_label.setWordWrap(True)
        header.addWidget(self.readiness_label)
        root.addLayout(header)

        self.summary_label = QLabel()
        self.summary_label.setObjectName("PostAssemblyReadinessSummary")
        self.summary_label.setWordWrap(True)
        root.addWidget(self.summary_label)

        self.operation_table_group = QGroupBox(ui_text("Operation table"))
        self.operation_table_group.setObjectName(
            "PostAssemblyOperationTableGroup"
        )
        table_layout = QVBoxLayout(self.operation_table_group)
        self.operation_table = QTableView()
        self.operation_table.setObjectName("PostAssemblyOperationTable")
        self.operation_table.setAccessibleName(ui_text("Operation table"))
        self.operation_table.setModel(self.model)
        self.operation_table.setSelectionBehavior(
            QTableView.SelectionBehavior.SelectRows
        )
        self.operation_table.setSelectionMode(
            QTableView.SelectionMode.SingleSelection
        )
        self.operation_table.setAlternatingRowColors(True)
        self.operation_table.horizontalHeader().setStretchLastSection(True)
        self.operation_table.selectionModel().currentChanged.connect(
            self._current_changed
        )
        table_layout.addWidget(self.operation_table)
        root.addWidget(self.operation_table_group, 1)

        source_bar = QHBoxLayout()
        self.source_operation_label = QLabel(ui_text("Operation"))
        self.source_operation_label.setObjectName("PostAssemblySourceOperationLabel")
        self.source_operation_picker = QComboBox()
        self.source_operation_picker.setObjectName("PostAssemblySourceOperationPicker")
        self.source_operation_picker.setAccessibleName(ui_text("Operation"))
        self.source_operation_picker.setPlaceholderText(ui_text("Operation"))
        self.source_operation_picker.currentIndexChanged.connect(
            self._source_operation_changed
        )
        source_bar.addWidget(self.source_operation_label)
        source_bar.addWidget(self.source_operation_picker, 1)
        root.insertLayout(2, source_bar)

        action_bar = QHBoxLayout()
        self.add_button = self._button("Add", "PostAssemblyAddOperationButton")
        self.remove_button = self._button("Remove", "PostAssemblyRemoveOperationButton")
        self.move_up_button = self._button("Move Up", "PostAssemblyMoveUpButton")
        self.move_down_button = self._button("Move Down", "PostAssemblyMoveDownButton")
        self.clear_button = self._button("Clear", "PostAssemblyClearButton")
        for button in (
            self.add_button,
            self.remove_button,
            self.move_up_button,
            self.move_down_button,
            self.clear_button,
        ):
            action_bar.addWidget(button)
        action_bar.addStretch(1)
        root.addLayout(action_bar)
        self.add_button.clicked.connect(self.add_selected_operation)
        self.remove_button.clicked.connect(self.remove_selected_operation)
        self.move_up_button.clicked.connect(lambda: self.move_selected_operation(-1))
        self.move_down_button.clicked.connect(lambda: self.move_selected_operation(1))
        self.clear_button.clicked.connect(self.clear_operation_list)

        self.artifact_summary = self._placeholder_group(
            "Artifact summary",
            "WP4 artifact host is unavailable in WP2.",
            "PostAssemblyArtifactSummary",
        )
        self.preview_placeholder = self._placeholder_group(
            "Preview",
            "Preview is not available in WP2.",
            "PostAssemblyPreviewPlaceholder",
        )
        self.diagnostics_placeholder = self._placeholder_group(
            "Diagnostics",
            "Diagnostics drawer is not available in WP2.",
            "PostAssemblyDiagnosticsPlaceholder",
        )
        root.addWidget(self.artifact_summary)
        root.addWidget(self.preview_placeholder)
        root.addWidget(self.diagnostics_placeholder)

        footer = QHBoxLayout()
        self.generate_button = self._button(
            "Generate", "PostAssemblyGenerateButton"
        )
        self.save_managed_button = self._button(
            "Save Managed", "PostAssemblySaveManagedButton"
        )
        self.export_external_button = self._button(
            "Export External", "PostAssemblyExportExternalButton"
        )
        for button in (
            self.generate_button,
            self.save_managed_button,
            self.export_external_button,
        ):
            footer.addWidget(button)
        footer.addStretch(1)
        root.addLayout(footer)

    def _button(self, source: str, object_name: str) -> QPushButton:
        button = QPushButton(ui_text(source))
        button.setObjectName(object_name)
        button.setAccessibleName(ui_text(source))
        button.setToolTip(ui_text(source))
        return button

    def _placeholder_group(
        self,
        title: str,
        message: str,
        object_name: str,
    ) -> QGroupBox:
        group = QGroupBox(ui_text(title))
        group.setObjectName(object_name)
        group.setAccessibleName(ui_text(title))
        layout = QVBoxLayout(group)
        label = QLabel(ui_text(message))
        label.setObjectName(f"{object_name}Label")
        label.setWordWrap(True)
        label.setEnabled(False)
        layout.addWidget(label)
        group.setEnabled(False)
        group.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        return group

    def _set_capability_placeholders(self) -> None:
        disabled_reasons = {
            self.generate_button: "wp4_generation_executor_unavailable",
            self.save_managed_button: "wp4_managed_publish_unavailable",
            self.export_external_button: "wp4_external_export_unavailable",
        }
        for button, reason in disabled_reasons.items():
            button.setEnabled(False)
            button.setToolTip(f"{ui_text('Unavailable')}: {reason}")
            button.setStatusTip(button.toolTip())

    def retranslate_ui(self, language: object = None) -> None:
        """Retranslate every visible WP2 label without changing panel state."""

        if language is not None:
            UiLanguage.coerce(language)
        self.setAccessibleName(ui_text("Post / Program Assembly"))
        self.title_label.setText(ui_text("Post / Program Assembly"))
        self.title_label.setAccessibleName(ui_text("Post / Program Assembly"))
        self.operation_table_group.setTitle(ui_text("Operation table"))
        self.operation_table_group.setAccessibleName(ui_text("Operation table"))
        self.operation_table.setAccessibleName(ui_text("Operation table"))
        self.source_operation_label.setText(ui_text("Operation"))
        self.source_operation_picker.setAccessibleName(ui_text("Operation"))
        self.source_operation_picker.setPlaceholderText(ui_text("Operation"))
        for index, row in enumerate(self._available_rows):
            if index < self.source_operation_picker.count():
                self.source_operation_picker.setItemText(
                    index, self._source_display_text(row)
                )
        for button, source in (
            (self.add_button, "Add"),
            (self.remove_button, "Remove"),
            (self.move_up_button, "Move Up"),
            (self.move_down_button, "Move Down"),
            (self.clear_button, "Clear"),
            (self.generate_button, "Generate"),
            (self.save_managed_button, "Save Managed"),
            (self.export_external_button, "Export External"),
        ):
            button.setText(ui_text(source))
            button.setAccessibleName(ui_text(source))
            button.setToolTip(ui_text(source))
        for group, title, message in (
            (
                self.artifact_summary,
                "Artifact summary",
                "WP4 artifact host is unavailable in WP2.",
            ),
            (
                self.preview_placeholder,
                "Preview",
                "Preview is not available in WP2.",
            ),
            (
                self.diagnostics_placeholder,
                "Diagnostics",
                "Diagnostics drawer is not available in WP2.",
            ),
        ):
            group.setTitle(ui_text(title))
            group.setAccessibleName(ui_text(title))
            label = group.findChild(QLabel, f"{group.objectName()}Label")
            if label is not None:
                label.setText(ui_text(message))
        self._set_capability_placeholders()
        self.model.retranslate_ui(translation_service().language)
        self._render_readiness()

    def _source_operation_changed(self, index: int) -> None:
        operation_id = self.source_operation_picker.itemData(index)
        self._source_selected_operation_id = (
            str(operation_id) if operation_id else None
        )
        if self._source_selected_operation_id in self._assembly_ids:
            self.select_operation(self._source_selected_operation_id)
        else:
            self._refresh_action_state()
            self.state_changed.emit()

    def _source_display_text(self, row: PostAssemblyOperationRow) -> str:
        return operation_display_name(row.operation_name)

    def _sync_source_picker(self) -> None:
        selected = self._source_selected_operation_id
        with QSignalBlocker(self.source_operation_picker):
            self.source_operation_picker.clear()
            for row in self._available_rows:
                self.source_operation_picker.addItem(
                    self._source_display_text(row), row.operation_id
                )
            self._set_source_picker_selection(selected)

    def _set_source_picker_selection(self, operation_id: str | None) -> None:
        index = -1
        if operation_id is not None:
            index = self.source_operation_picker.findData(operation_id)
        with QSignalBlocker(self.source_operation_picker):
            self.source_operation_picker.setCurrentIndex(index)

    def _sync_table(self, *, select_id: str | None = None) -> None:
        by_id = {row.operation_id: row for row in self._available_rows}
        rows = tuple(
            replace(by_id[item], execution_order=index)
            for index, item in enumerate(self._assembly_ids)
            if item in by_id
        )
        self.model.set_rows(rows)
        self.select_operation(select_id or self._selected_operation_id)
        self._state = replace(self._state, operation_ids=tuple(self._assembly_ids))
        self._refresh_action_state()
        self.state_changed.emit()

    def _current_changed(self, current: QModelIndex, _previous: QModelIndex) -> None:
        self.select_operation(self.model.operation_id_at(current.row()))

    def _refresh_action_state(self) -> None:
        selected = self._selected_operation_id
        index = self._assembly_ids.index(selected) if selected in self._assembly_ids else -1
        source_id = self._source_selected_operation_id or selected
        source_row = next(
            (row for row in self._available_rows if row.operation_id == source_id),
            None,
        )
        self.add_button.setEnabled(
            source_row is not None
            and source_row.enabled
            and not source_row.missing
            and source_id not in self._assembly_ids
        )
        self.remove_button.setEnabled(selected is not None)
        self.move_up_button.setEnabled(index > 0)
        self.move_down_button.setEnabled(0 <= index < len(self._assembly_ids) - 1)
        self.clear_button.setEnabled(bool(self._assembly_ids))

    def _render_readiness(self) -> None:
        projection = self._projection
        if projection is None:
            self.readiness_label.setText(ui_text("Readiness unavailable"))
            self.summary_label.setText(ui_text("No projection evidence."))
            return
        headline = projection.headline_state.value
        self.readiness_label.setText(translate_status(headline))
        self.summary_label.setText(
            projection.diagnostic_summary
            or ui_text("Presentation only; no downstream action was started.")
        )


class PostAssemblyPanel(UnifiedPostAssemblyPanel):
    """Compatibility alias for callers that use the shorter panel name."""


def _fingerprint(values: Iterable[str]) -> str:
    payload = json.dumps(tuple(values), ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _artifact_state(value: object) -> OperationArtifactState:
    raw = getattr(value, "value", value)
    if raw in {ArtifactStatus.VALID, "valid", "CURRENT", "current"}:
        return OperationArtifactState.CURRENT
    if raw in {ArtifactStatus.MISSING, "missing", "MISSING"}:
        return OperationArtifactState.MISSING
    if raw in {ArtifactStatus.DIRTY, "dirty", ArtifactStatus.STALE, "stale", "STALE", "DIRTY"}:
        return OperationArtifactState.STALE
    if raw in {ArtifactStatus.COMPUTING, "computing", ArtifactStatus.FAILED, "failed", "FAILED"}:
        return OperationArtifactState.CALCULATION_REQUIRED
    return OperationArtifactState.CALCULATION_REQUIRED


__all__ = [
    "PostAssemblyOperationAction",
    "PostAssemblyColumn",
    "PostAssemblyOperationRow",
    "PostAssemblyProjectionInputEvidence",
    "PostAssemblyProjectionAdapter",
    "PostAssemblyTableRole",
    "PostAssemblyOperationTableModel",
    "PostAssemblyOperationController",
    "PostAssemblyPanelState",
    "UnifiedPostAssemblyPanel",
    "PostAssemblyPanel",
]
