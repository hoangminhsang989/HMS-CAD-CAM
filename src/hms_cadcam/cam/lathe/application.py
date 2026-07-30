"""Qt-free atomic Lathe operation service and runtime lifecycle."""

from __future__ import annotations

from dataclasses import dataclass, replace
from uuid import UUID

from hms_cadcam.cad.models import CadDocumentId
from hms_cadcam.cam.domain.ids import OperationId, SetupId
from hms_cadcam.cam.domain.revision import Revision
from hms_cadcam.cam.lathe.capabilities import (
    FailClosedLatheToolCapabilityResolver,
    LatheToolCapabilityResolution,
    LatheToolCapabilityResolver,
)
from hms_cadcam.cam.lathe.commands import (
    BindLatheGeometry,
    BindLatheTool,
    ChangeLatheStrategy,
    ClearLatheGeometry,
    ClearLatheTool,
    CreateLatheOperation,
    DeleteLatheOperation,
    LatheCommand,
    SetLatheOperationEnabled,
    UpdateLatheParameters,
    ValidateLatheOperation,
)
from hms_cadcam.cam.lathe.domain import (
    LatheOperationEvaluation,
    LatheOperationState,
    LatheOwnershipKey,
    LatheToolBinding,
    evaluate_lathe_operation,
)
from hms_cadcam.cam.lathe.parameters import (
    COMMON_PARAMETER_IDS,
    LatheParameterUpdate,
    LatheParameterValidationError,
    build_lathe_v1_defaults,
)
from hms_cadcam.cam.lathe.strategies import lathe_strategy_definition
from hms_cadcam.cam.lathe.types import (
    LatheDiagnostic,
    LatheDiagnosticCode,
    ordered_lathe_diagnostics,
)


def _non_nil_uuid(value: object, subject: str) -> UUID:
    if not isinstance(value, UUID) or value.int == 0:
        raise ValueError(f"{subject} must be a non-nil UUID")
    return value


@dataclass(frozen=True, slots=True)
class LatheServiceSession:
    """One explicit runtime ownership/lifecycle context."""

    project_id: UUID
    document_id: CadDocumentId
    source_id: UUID
    generation: int
    setup_id: SetupId | None
    read_only: bool = False
    closed: bool = False

    def __post_init__(self) -> None:
        _non_nil_uuid(self.project_id, "project_id")
        if not isinstance(self.document_id, CadDocumentId) or not str(
            self.document_id
        ).strip():
            raise ValueError("document_id must be a non-blank CadDocumentId")
        _non_nil_uuid(self.source_id, "source_id")
        if type(self.generation) is not int or self.generation < 0:
            raise ValueError("generation must be a non-negative integer")
        if self.setup_id is not None and not isinstance(self.setup_id, SetupId):
            raise TypeError("setup_id must be SetupId or None")
        if type(self.read_only) is not bool or type(self.closed) is not bool:
            raise TypeError("read_only and closed must be bool")
        if self.closed and self.read_only:
            raise ValueError("A closed Lathe session cannot remain read-only")


@dataclass(frozen=True, slots=True)
class LatheCommandOutcome:
    """Typed deterministic outcome shared with the presenter-neutral facade."""

    accepted: bool
    changed: bool
    operation: LatheOperationState | None
    evaluation: LatheOperationEvaluation | None
    diagnostics: tuple[LatheDiagnostic, ...] = ()
    deleted: bool = False

    def __post_init__(self) -> None:
        if type(self.accepted) is not bool or type(self.changed) is not bool:
            raise TypeError("Lathe command outcome flags must be bool")
        if self.operation is not None and not isinstance(
            self.operation, LatheOperationState
        ):
            raise TypeError("Lathe command outcome operation is invalid")
        if self.evaluation is not None and not isinstance(
            self.evaluation, LatheOperationEvaluation
        ):
            raise TypeError("Lathe command outcome evaluation is invalid")
        if not isinstance(self.diagnostics, tuple) or any(
            not isinstance(item, LatheDiagnostic) for item in self.diagnostics
        ):
            raise TypeError("Lathe command outcome diagnostics are invalid")
        object.__setattr__(
            self, "diagnostics", ordered_lathe_diagnostics(self.diagnostics)
        )
        if type(self.deleted) is not bool:
            raise TypeError("Lathe command outcome deleted flag must be bool")
        if self.deleted and (not self.accepted or not self.changed):
            raise ValueError("A Lathe delete outcome must be accepted and changed")


class LatheOperationService:
    """In-memory service for exactly one live Lathe session.

    The service deliberately has no notification hooks, locks, workers, persistence,
    database, filesystem, Qt, OCP, toolpath, Post, or simulation dependency.
    """

    def __init__(
        self,
        session: LatheServiceSession,
        *,
        capability_resolver: LatheToolCapabilityResolver | None = None,
    ) -> None:
        if not isinstance(session, LatheServiceSession):
            raise TypeError("session must be LatheServiceSession")
        self._session = session
        self._capability_resolver = (
            FailClosedLatheToolCapabilityResolver()
            if capability_resolver is None
            else capability_resolver
        )
        if not callable(getattr(self._capability_resolver, "resolve", None)):
            raise TypeError("capability_resolver must implement resolve")
        self._operations: dict[OperationId, LatheOperationState] = {}

    @property
    def session(self) -> LatheServiceSession:
        return self._session

    def ownership_for(self, operation_id: OperationId) -> LatheOwnershipKey:
        """Build ownership for the current active setup."""

        if not isinstance(operation_id, OperationId):
            raise TypeError("operation_id must be OperationId")
        if self._session.setup_id is None:
            raise ValueError("Cannot build Lathe ownership without an active setup")
        return LatheOwnershipKey(
            self._session.project_id,
            self._session.document_id,
            self._session.source_id,
            self._session.generation,
            self._session.setup_id,
            operation_id,
        )

    def query(self, operation_id: OperationId) -> LatheOperationState:
        """Return one immutable operation or raise a deterministic KeyError."""

        if not isinstance(operation_id, OperationId):
            raise TypeError("operation_id must be OperationId")
        try:
            return self._operations[operation_id]
        except KeyError as error:
            raise KeyError(str(operation_id)) from error

    def list_operations(self) -> tuple[LatheOperationState, ...]:
        """Return operations in deterministic creation order."""

        return tuple(self._operations.values())

    def evaluate(self, operation_id: OperationId) -> LatheOperationEvaluation:
        """Evaluate one operation against the current lifecycle context."""

        return self._evaluate(self.query(operation_id))

    def execute(self, command: LatheCommand) -> LatheCommandOutcome:
        """Execute one exact typed command atomically."""

        if isinstance(command, CreateLatheOperation):
            return self._create(command)
        if not isinstance(
            command,
            (
                UpdateLatheParameters,
                ChangeLatheStrategy,
                BindLatheGeometry,
                ClearLatheGeometry,
                BindLatheTool,
                ClearLatheTool,
                SetLatheOperationEnabled,
                DeleteLatheOperation,
                ValidateLatheOperation,
            ),
        ):
            raise TypeError("command must be an exact Lathe command")
        operation = self._operations.get(command.ownership.operation_id)
        if operation is None:
            return self._rejected(
                None, (LatheDiagnostic(LatheDiagnosticCode.OPERATION_NOT_FOUND),)
            )
        if operation.ownership != command.ownership:
            return self._rejected(
                operation, (LatheDiagnostic(LatheDiagnosticCode.STALE_OWNERSHIP),)
            )
        if operation.revision != command.expected_revision:
            return self._rejected(
                operation,
                (
                    LatheDiagnostic(
                        LatheDiagnosticCode.REVISION_MISMATCH,
                        parameters=(
                            ("actual", str(operation.revision.value)),
                            ("expected", str(command.expected_revision.value)),
                        ),
                    ),
                ),
            )
        if isinstance(command, ValidateLatheOperation):
            evaluation = self._evaluate(operation)
            return LatheCommandOutcome(
                True, False, operation, evaluation, evaluation.diagnostics
            )
        guard = self._mutation_guard(command.ownership)
        if guard:
            return self._rejected(operation, guard)

        if isinstance(command, UpdateLatheParameters):
            try:
                parameters = operation.parameter_state.with_updates(command.updates)
            except LatheParameterValidationError as error:
                return self._rejected(operation, error.diagnostics)
            candidate = replace(
                operation,
                parameter_state=parameters,
                diagnostics=(),
                revision=operation.revision.next(),
            )
        elif isinstance(command, ChangeLatheStrategy):
            defaults = build_lathe_v1_defaults(command.strategy_id)
            retained = tuple(
                LatheParameterUpdate(parameter_id, operation.parameter_state.value(parameter_id))
                for parameter_id in COMMON_PARAMETER_IDS
            )
            parameters = defaults.with_updates(retained)
            candidate = replace(
                operation,
                strategy_id=command.strategy_id,
                parameter_state=parameters,
                diagnostics=(),
                revision=operation.revision.next(),
            )
        elif isinstance(command, BindLatheGeometry):
            binding = command.binding
            definition = lathe_strategy_definition(operation.strategy_id)
            if (
                binding.source_id != operation.ownership.source_id
                or binding.generation != operation.ownership.generation
            ):
                return self._rejected(
                    operation,
                    (LatheDiagnostic(LatheDiagnosticCode.STALE_OWNERSHIP),),
                )
            if binding.kind not in definition.allowed_geometry_kinds:
                return self._rejected(
                    operation,
                    (LatheDiagnostic(LatheDiagnosticCode.INCOMPATIBLE_GEOMETRY),),
                )
            candidate = replace(
                operation,
                geometry_binding=binding,
                diagnostics=(),
                revision=operation.revision.next(),
            )
        elif isinstance(command, ClearLatheGeometry):
            candidate = replace(
                operation,
                geometry_binding=None,
                diagnostics=(),
                revision=operation.revision.next(),
            )
        elif isinstance(command, BindLatheTool):
            resolution = self._capability_resolver.resolve(command.reference)
            if not isinstance(resolution, LatheToolCapabilityResolution):
                return self._rejected(
                    operation, (LatheDiagnostic(LatheDiagnosticCode.MISSING_TOOL),)
                )
            if resolution.reference != command.reference or not resolution.exists:
                return self._rejected(
                    operation, (LatheDiagnostic(LatheDiagnosticCode.MISSING_TOOL),)
                )
            required = lathe_strategy_definition(
                operation.strategy_id
            ).required_tool_capabilities
            if not resolution.current or not required.issubset(resolution.capabilities):
                return self._rejected(
                    operation,
                    (LatheDiagnostic(LatheDiagnosticCode.INCOMPATIBLE_TOOL),),
                )
            binding = LatheToolBinding.from_resolution(resolution)
            candidate = replace(
                operation,
                tool_binding=binding,
                diagnostics=(),
                revision=operation.revision.next(),
            )
        elif isinstance(command, ClearLatheTool):
            candidate = replace(
                operation,
                tool_binding=None,
                diagnostics=(),
                revision=operation.revision.next(),
            )
        elif isinstance(command, SetLatheOperationEnabled):
            candidate = replace(
                operation,
                enabled=command.enabled,
                diagnostics=(),
                revision=operation.revision.next(),
            )
        elif isinstance(command, DeleteLatheOperation):
            tombstone = replace(operation, revision=operation.revision.next())
            del self._operations[operation.ownership.operation_id]
            evaluation = self._evaluate(tombstone)
            return LatheCommandOutcome(
                True,
                True,
                tombstone,
                evaluation,
                evaluation.diagnostics,
                deleted=True,
            )
        else:  # exhaustive command guard above
            raise TypeError("Unsupported Lathe command")

        self._operations[candidate.ownership.operation_id] = candidate
        evaluation = self._evaluate(candidate)
        return LatheCommandOutcome(
            True, True, candidate, evaluation, evaluation.diagnostics
        )

    def set_read_only(self, read_only: bool) -> LatheServiceSession:
        """Apply an explicit read-only transition without touching operations."""

        if type(read_only) is not bool:
            raise TypeError("read_only must be bool")
        if self._session.closed:
            return self._session
        self._session = replace(self._session, read_only=read_only)
        return self._session

    def switch_setup(self, setup_id: SetupId | None) -> LatheServiceSession:
        """Switch active setup; prior operations remain stale and queryable."""

        if setup_id is not None and not isinstance(setup_id, SetupId):
            raise TypeError("setup_id must be SetupId or None")
        if self._session.closed:
            return self._session
        self._session = replace(self._session, setup_id=setup_id)
        return self._session

    def switch_source(
        self, source_id: UUID, generation: int
    ) -> LatheServiceSession:
        """Switch source and explicit generation without rebinding operations."""

        _non_nil_uuid(source_id, "source_id")
        if type(generation) is not int or generation < 0:
            raise ValueError("generation must be a non-negative integer")
        if self._session.closed:
            return self._session
        self._session = replace(
            self._session, source_id=source_id, generation=generation
        )
        return self._session

    def increment_generation(self) -> LatheServiceSession:
        """Increment generation exactly once; prior operations become stale."""

        if self._session.closed:
            return self._session
        self._session = replace(
            self._session, generation=self._session.generation + 1
        )
        return self._session

    def close(self) -> LatheServiceSession:
        """Close idempotently without persistence or background cleanup."""

        if self._session.closed:
            return self._session
        self._session = replace(self._session, read_only=False, closed=True)
        return self._session

    def _create(self, command: CreateLatheOperation) -> LatheCommandOutcome:
        existing = self._operations.get(command.ownership.operation_id)
        if existing is not None:
            return self._rejected(
                existing, (LatheDiagnostic(LatheDiagnosticCode.DUPLICATE_OPERATION),)
            )
        guard = self._mutation_guard(command.ownership)
        if guard:
            return self._rejected(None, guard)
        operation = LatheOperationState(
            command.ownership,
            command.strategy_id,
            command.parameter_state,
            enabled=command.enabled,
        )
        self._operations[operation.ownership.operation_id] = operation
        evaluation = self._evaluate(operation)
        return LatheCommandOutcome(
            True, True, operation, evaluation, evaluation.diagnostics
        )

    def _mutation_guard(
        self, ownership: LatheOwnershipKey
    ) -> tuple[LatheDiagnostic, ...]:
        if self._session.closed:
            return (LatheDiagnostic(LatheDiagnosticCode.CLOSED),)
        if self._session.read_only:
            return (LatheDiagnostic(LatheDiagnosticCode.READ_ONLY),)
        if self._session.setup_id is None:
            return (LatheDiagnostic(LatheDiagnosticCode.MISSING_SETUP),)
        if (
            ownership.project_id != self._session.project_id
            or ownership.document_id != self._session.document_id
            or ownership.source_id != self._session.source_id
            or ownership.generation != self._session.generation
            or ownership.setup_id != self._session.setup_id
        ):
            return (LatheDiagnostic(LatheDiagnosticCode.STALE_OWNERSHIP),)
        return ()

    def _evaluate(self, operation: LatheOperationState) -> LatheOperationEvaluation:
        return evaluate_lathe_operation(
            operation,
            project_id=self._session.project_id,
            document_id=self._session.document_id,
            source_id=self._session.source_id,
            generation=self._session.generation,
            setup_id=self._session.setup_id,
            read_only=self._session.read_only,
            closed=self._session.closed,
        )

    def _rejected(
        self,
        operation: LatheOperationState | None,
        diagnostics: tuple[LatheDiagnostic, ...],
    ) -> LatheCommandOutcome:
        evaluation = self._evaluate(operation) if operation is not None else None
        return LatheCommandOutcome(
            False, False, operation, evaluation, diagnostics
        )


__all__ = [
    "LatheCommandOutcome",
    "LatheOperationService",
    "LatheServiceSession",
]
