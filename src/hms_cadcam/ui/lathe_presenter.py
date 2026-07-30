"""Qt adapter over the immutable presenter-neutral Stage 9A.9 facade."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QObject, Signal

from hms_cadcam.cam.domain.ids import OperationId
from hms_cadcam.cam.domain.revision import Revision
from hms_cadcam.cam.lathe.capabilities import LatheToolReference
from hms_cadcam.cam.lathe.parameters import LatheParameterUpdate
from hms_cadcam.cam.lathe.presenter import (
    LatheOperationSnapshot,
    LathePresenterFacade,
    LathePresenterSnapshot,
)
from hms_cadcam.cam.lathe.types import (
    LatheDiagnostic,
    LatheDiagnosticCode,
    LatheStrategyId,
)
from hms_cadcam.ui.lathe_adapters import (
    LatheGeometrySelectionError,
    LatheSelectionContext,
    LatheToolChoice,
    ProjectLatheToolCatalog,
    lathe_geometry_from_selection,
)


@dataclass(frozen=True, slots=True)
class LatheQtDiagnostic:
    """Presentation-safe structured diagnostic without exception text."""

    code: str
    field_id: str | None = None
    parameters: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.code, str) or not self.code.strip():
            raise ValueError("Lathe Qt diagnostic code must be non-blank")
        if self.field_id is not None and (
            not isinstance(self.field_id, str) or not self.field_id.strip()
        ):
            raise ValueError("Lathe Qt diagnostic field_id must be non-blank")
        if not isinstance(self.parameters, tuple) or any(
            not isinstance(item, tuple)
            or len(item) != 2
            or not isinstance(item[0], str)
            or not isinstance(item[1], str)
            for item in self.parameters
        ):
            raise TypeError("Lathe Qt diagnostic parameters are invalid")

    @classmethod
    def from_domain(cls, diagnostic: LatheDiagnostic) -> "LatheQtDiagnostic":
        """Project one typed domain diagnostic without changing its meaning."""

        if not isinstance(diagnostic, LatheDiagnostic):
            raise TypeError("diagnostic must be LatheDiagnostic")
        return cls(
            diagnostic.code.value,
            diagnostic.field_id,
            diagnostic.parameters,
        )


@dataclass(frozen=True, slots=True)
class LatheQtCommandResult:
    """Immutable Qt command outcome rendered by the workspace."""

    action: str
    accepted: bool
    changed: bool
    diagnostics: tuple[LatheQtDiagnostic, ...]
    snapshot: LathePresenterSnapshot

    def __post_init__(self) -> None:
        if not isinstance(self.action, str) or not self.action:
            raise ValueError("Lathe Qt command action is empty")
        if type(self.accepted) is not bool or type(self.changed) is not bool:
            raise TypeError("Lathe Qt command flags must be bool")
        if not isinstance(self.diagnostics, tuple) or any(
            not isinstance(item, LatheQtDiagnostic) for item in self.diagnostics
        ):
            raise TypeError("Lathe Qt command diagnostics are invalid")
        if not isinstance(self.snapshot, LathePresenterSnapshot):
            raise TypeError("Lathe Qt command snapshot is invalid")


class LatheQtPresenter(QObject):
    """Qt-safe coordinator that owns no mutable Lathe aggregate shadow."""

    snapshot_changed = Signal(object)
    command_completed = Signal(object)
    revision_conflict = Signal(object)

    def __init__(
        self,
        facade: LathePresenterFacade,
        tool_catalog: ProjectLatheToolCatalog,
        selection_provider: Callable[[], LatheSelectionContext | None],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        if not isinstance(facade, LathePresenterFacade):
            raise TypeError("facade must be LathePresenterFacade")
        if not isinstance(tool_catalog, ProjectLatheToolCatalog):
            raise TypeError("tool_catalog must be ProjectLatheToolCatalog")
        if not callable(selection_provider):
            raise TypeError("selection_provider must be callable")
        self.setObjectName("LatheQtPresenter")
        self._facade = facade
        self._tool_catalog = tool_catalog
        self._selection_provider = selection_provider
        self._alive = True
        self._snapshot = facade.snapshot()

    @property
    def facade(self) -> LathePresenterFacade:
        """Expose the typed facade for lifecycle composition and audit tests."""

        return self._facade

    @property
    def snapshot(self) -> LathePresenterSnapshot:
        """Return the last immutable authoritative snapshot."""

        return self._snapshot

    @property
    def active_operation(self) -> LatheOperationSnapshot | None:
        """Return the immutable active operation projection, if any."""

        active_id = self._snapshot.active_operation_id
        return next(
            (
                item
                for item in self._snapshot.operations
                if item.ownership.operation_id == active_id
            ),
            None,
        )

    @property
    def is_alive(self) -> bool:
        return self._alive

    def refresh(self) -> LathePresenterSnapshot:
        """Refresh deterministically from the presenter-neutral facade."""

        if not self._alive:
            return self._snapshot
        self._snapshot = self._facade.snapshot()
        self.snapshot_changed.emit(self._snapshot)
        return self._snapshot

    def select_operation(self, operation_id: OperationId | None) -> LatheQtCommandResult:
        """Select one exact operation identity without a semantic mutation."""

        return self._execute_ui(
            "select_operation",
            lambda: self._select(operation_id),
        )

    def create_operation(self, strategy_id: LatheStrategyId) -> LatheQtCommandResult:
        """Create revision-zero defaults through the facade."""

        return self._execute_domain(
            "create_operation",
            lambda: self._facade.create_operation(OperationId.new(), strategy_id),
        )

    def apply_parameter_changes(
        self,
        operation_id: OperationId,
        updates: tuple[LatheParameterUpdate, ...],
        expected_revision: Revision,
    ) -> LatheQtCommandResult:
        """Submit one typed atomic parameter update set."""

        return self._execute_domain(
            "apply_parameters",
            lambda: self._facade.apply_parameter_changes(
                operation_id, updates, expected_revision
            ),
        )

    def change_strategy(
        self,
        operation_id: OperationId,
        strategy_id: LatheStrategyId,
        expected_revision: Revision,
    ) -> LatheQtCommandResult:
        """Change strategy under the exact expected revision."""

        return self._execute_domain(
            "change_strategy",
            lambda: self._facade.change_strategy(
                operation_id, strategy_id, expected_revision
            ),
        )

    def bind_current_geometry(
        self,
        operation_id: OperationId,
        expected_revision: Revision,
    ) -> LatheQtCommandResult:
        """Bind the current exact OCP-free viewer selection atomically."""

        def command():
            operation = self._facade.operation_snapshot(operation_id)
            context = self._selection_provider()
            if context is None:
                raise LatheGeometrySelectionError(
                    "lathe.geometry.selection_unavailable"
                )
            binding = lathe_geometry_from_selection(
                context,
                operation.strategy_id,
                expected_document_id=operation.ownership.document_id,
                expected_source_id=operation.ownership.source_id,
                expected_generation=operation.ownership.generation,
            )
            return self._facade.bind_geometry(
                operation_id, binding, expected_revision
            )

        return self._execute_domain("bind_geometry", command)

    def clear_geometry(
        self,
        operation_id: OperationId,
        expected_revision: Revision,
    ) -> LatheQtCommandResult:
        return self._execute_domain(
            "clear_geometry",
            lambda: self._facade.clear_geometry(operation_id, expected_revision),
        )

    def bind_tool(
        self,
        operation_id: OperationId,
        reference: LatheToolReference,
        expected_revision: Revision,
    ) -> LatheQtCommandResult:
        return self._execute_domain(
            "bind_tool",
            lambda: self._facade.bind_tool(
                operation_id, reference, expected_revision
            ),
        )

    def clear_tool(
        self,
        operation_id: OperationId,
        expected_revision: Revision,
    ) -> LatheQtCommandResult:
        return self._execute_domain(
            "clear_tool",
            lambda: self._facade.clear_tool(operation_id, expected_revision),
        )

    def set_enabled(
        self,
        operation_id: OperationId,
        enabled: bool,
        expected_revision: Revision,
    ) -> LatheQtCommandResult:
        return self._execute_domain(
            "set_enabled",
            lambda: self._facade.set_enabled(
                operation_id, enabled, expected_revision
            ),
        )

    def validate_operation(
        self,
        operation_id: OperationId,
        expected_revision: Revision,
    ) -> LatheQtCommandResult:
        return self._execute_domain(
            "validate_operation",
            lambda: self._facade.validate_operation(
                operation_id, expected_revision
            ),
        )

    def delete_operation(
        self,
        operation_id: OperationId,
        expected_revision: Revision,
    ) -> LatheQtCommandResult:
        """Delete exactly the requested operation and select a deterministic peer."""

        before = tuple(
            item.ownership.operation_id for item in self._snapshot.operations
        )
        try:
            index = before.index(operation_id)
        except ValueError:
            index = 0

        def command():
            outcome = self._facade.delete_operation(
                operation_id, expected_revision
            )
            if outcome.accepted:
                remaining = tuple(
                    item.ownership.operation_id
                    for item in self._facade.snapshot().operations
                )
                selected = (
                    remaining[min(index, len(remaining) - 1)]
                    if remaining
                    else None
                )
                self._facade.select_active_operation(selected)
            return outcome

        return self._execute_domain("delete_operation", command)

    def tool_choices_for(
        self, strategy_id: LatheStrategyId
    ) -> tuple[LatheToolChoice, ...]:
        """Return all current choices; compatibility stays explicit per row."""

        if not isinstance(strategy_id, LatheStrategyId):
            raise TypeError("strategy_id must be LatheStrategyId")
        return self._tool_catalog.choices()

    def teardown(self) -> None:
        """Make queued commands inert; repeated teardown is safe."""

        if not self._alive:
            return
        self._alive = False

    def _select(self, operation_id: OperationId | None) -> None:
        self._facade.select_active_operation(operation_id)

    def _execute_ui(
        self,
        action: str,
        command: Callable[[], None],
    ) -> LatheQtCommandResult:
        if not self._alive:
            return self._local_rejection(action, "closed")
        try:
            command()
        except KeyError:
            return self._local_rejection(action, "operation_not_found")
        self._snapshot = self._facade.snapshot()
        result = LatheQtCommandResult(action, True, False, (), self._snapshot)
        self.snapshot_changed.emit(self._snapshot)
        self.command_completed.emit(result)
        return result

    def _execute_domain(self, action: str, command: Callable[[], object]) -> LatheQtCommandResult:
        if not self._alive:
            return self._local_rejection(action, "closed")
        try:
            outcome = command()
        except LatheGeometrySelectionError as error:
            return self._local_rejection(action, str(error))
        except KeyError:
            return self._local_rejection(action, "operation_not_found")
        except ValueError as error:
            code = (
                "missing_setup"
                if "active setup" in str(error).casefold()
                else "invalid_parameter"
            )
            return self._local_rejection(action, code)
        except TypeError:
            return self._local_rejection(action, "invalid_parameter")
        accepted = bool(getattr(outcome, "accepted", False))
        changed = bool(getattr(outcome, "changed", False))
        domain_diagnostics = getattr(outcome, "diagnostics", ())
        diagnostics = tuple(
            LatheQtDiagnostic.from_domain(item) for item in domain_diagnostics
        )
        self._snapshot = self._facade.snapshot()
        result = LatheQtCommandResult(
            action, accepted, changed, diagnostics, self._snapshot
        )
        self.snapshot_changed.emit(self._snapshot)
        self.command_completed.emit(result)
        if any(
            item.code == LatheDiagnosticCode.REVISION_MISMATCH.value
            for item in diagnostics
        ):
            self.revision_conflict.emit(result)
        return result

    def _local_rejection(self, action: str, code: str) -> LatheQtCommandResult:
        self._snapshot = self._facade.snapshot()
        result = LatheQtCommandResult(
            action,
            False,
            False,
            (LatheQtDiagnostic(code),),
            self._snapshot,
        )
        self.snapshot_changed.emit(self._snapshot)
        self.command_completed.emit(result)
        return result


__all__ = [
    "LatheQtCommandResult",
    "LatheQtDiagnostic",
    "LatheQtPresenter",
]
