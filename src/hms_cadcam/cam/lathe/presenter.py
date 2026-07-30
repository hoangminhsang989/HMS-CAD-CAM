"""Qt-free Stage 9A.9 presenter-neutral Lathe facade and immutable DTOs."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from hms_cadcam.cam.domain.ids import OperationId
from hms_cadcam.cam.domain.revision import Revision
from hms_cadcam.cam.lathe.application import (
    LatheCommandOutcome,
    LatheOperationService,
)
from hms_cadcam.cam.lathe.capabilities import LatheToolReference
from hms_cadcam.cam.lathe.commands import (
    BindLatheGeometry,
    BindLatheTool,
    ChangeLatheStrategy,
    ClearLatheGeometry,
    ClearLatheTool,
    CreateLatheOperation,
    DeleteLatheOperation,
    SetLatheOperationEnabled,
    UpdateLatheParameters,
    ValidateLatheOperation,
)
from hms_cadcam.cam.lathe.domain import (
    LatheGeometryBinding,
    LatheOperationEvaluation,
    LatheOperationState,
    LatheOwnershipKey,
    LatheToolBinding,
)
from hms_cadcam.cam.lathe.parameters import (
    LatheParameterDescriptor,
    LatheParameterUpdate,
    LatheParameterValue,
    build_lathe_v1_defaults,
)
from hms_cadcam.cam.lathe.readiness import (
    LatheWorkspaceReadiness,
    STAGE12_LATHE_WORKSPACE_READINESS,
)
from hms_cadcam.cam.lathe.strategies import (
    LATHE_STRATEGY_REGISTRY,
    LatheStrategyDefinition,
    lathe_strategy_definition,
)
from hms_cadcam.cam.lathe.types import (
    LatheDiagnostic,
    LatheGeometryKind,
    LatheOperationReadiness,
    LatheStrategyFamily,
    LatheStrategyId,
    LatheToolCapability,
)


@dataclass(frozen=True, slots=True)
class LatheStrategyDescriptor:
    strategy_id: LatheStrategyId
    family_id: LatheStrategyFamily
    allowed_geometry_kinds: tuple[LatheGeometryKind, ...]
    required_tool_capabilities: frozenset[LatheToolCapability]
    parameters: tuple[LatheParameterDescriptor, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.strategy_id, LatheStrategyId):
            raise TypeError("Presenter strategy ID is invalid")
        if not isinstance(self.family_id, LatheStrategyFamily):
            raise TypeError("Presenter strategy family is invalid")
        if not isinstance(self.allowed_geometry_kinds, tuple) or any(
            not isinstance(item, LatheGeometryKind)
            for item in self.allowed_geometry_kinds
        ):
            raise TypeError("Presenter geometry kinds must be a typed tuple")
        if not isinstance(self.required_tool_capabilities, frozenset) or any(
            not isinstance(item, LatheToolCapability)
            for item in self.required_tool_capabilities
        ):
            raise TypeError("Presenter tool capabilities must be a typed frozenset")
        if not isinstance(self.parameters, tuple) or any(
            not isinstance(item, LatheParameterDescriptor) for item in self.parameters
        ):
            raise TypeError("Presenter parameters must be a typed tuple")

    @classmethod
    def from_definition(
        cls, definition: LatheStrategyDefinition
    ) -> "LatheStrategyDescriptor":
        if not isinstance(definition, LatheStrategyDefinition):
            raise TypeError("definition must be LatheStrategyDefinition")
        return cls(
            definition.strategy_id,
            definition.family_id,
            definition.allowed_geometry_kinds,
            definition.required_tool_capabilities,
            definition.parameter_descriptors,
        )


@dataclass(frozen=True, slots=True)
class LatheOperationSnapshot:
    ownership: LatheOwnershipKey
    strategy_id: LatheStrategyId
    parameter_values: tuple[tuple[str, LatheParameterValue], ...]
    geometry_binding: LatheGeometryBinding | None
    tool_binding: LatheToolBinding | None
    enabled: bool
    readiness: LatheOperationReadiness
    diagnostics: tuple[LatheDiagnostic, ...]
    revision: Revision

    def __post_init__(self) -> None:
        if not isinstance(self.ownership, LatheOwnershipKey):
            raise TypeError("Presenter operation ownership is invalid")
        if not isinstance(self.strategy_id, LatheStrategyId):
            raise TypeError("Presenter operation strategy is invalid")
        if not isinstance(self.parameter_values, tuple) or any(
            not isinstance(item, tuple)
            or len(item) != 2
            or not isinstance(item[0], str)
            or not item[0]
            or not (
                item[1] is None
                or type(item[1]) in {int, float}
                or isinstance(item[1], StrEnum)
            )
            for item in self.parameter_values
        ):
            raise TypeError("Presenter parameter values must be immutable typed pairs")
        if self.geometry_binding is not None and not isinstance(
            self.geometry_binding, LatheGeometryBinding
        ):
            raise TypeError("Presenter geometry binding is invalid")
        if self.tool_binding is not None and not isinstance(
            self.tool_binding, LatheToolBinding
        ):
            raise TypeError("Presenter tool binding is invalid")
        if type(self.enabled) is not bool:
            raise TypeError("Presenter enabled flag must be bool")
        if not isinstance(self.readiness, LatheOperationReadiness):
            raise TypeError("Presenter operation readiness is invalid")
        if not isinstance(self.diagnostics, tuple) or any(
            not isinstance(item, LatheDiagnostic) for item in self.diagnostics
        ):
            raise TypeError("Presenter diagnostics must be a typed tuple")
        if not isinstance(self.revision, Revision):
            raise TypeError("Presenter revision is invalid")

    @classmethod
    def from_state(
        cls,
        state: LatheOperationState,
        evaluation: LatheOperationEvaluation,
    ) -> "LatheOperationSnapshot":
        if not isinstance(state, LatheOperationState):
            raise TypeError("state must be LatheOperationState")
        if not isinstance(evaluation, LatheOperationEvaluation):
            raise TypeError("evaluation must be LatheOperationEvaluation")
        return cls(
            state.ownership,
            state.strategy_id,
            state.parameter_state.values,
            state.geometry_binding,
            state.tool_binding,
            state.enabled,
            evaluation.readiness,
            evaluation.diagnostics,
            state.revision,
        )


@dataclass(frozen=True, slots=True)
class LathePresenterSnapshot:
    strategies: tuple[LatheStrategyDescriptor, ...]
    operations: tuple[LatheOperationSnapshot, ...]
    active_operation_id: OperationId | None
    workspace_readiness: LatheWorkspaceReadiness
    read_only: bool
    closed: bool

    def __post_init__(self) -> None:
        if not isinstance(self.strategies, tuple) or any(
            not isinstance(item, LatheStrategyDescriptor) for item in self.strategies
        ):
            raise TypeError("Presenter strategies must be a typed tuple")
        if not isinstance(self.operations, tuple) or any(
            not isinstance(item, LatheOperationSnapshot) for item in self.operations
        ):
            raise TypeError("Presenter operations must be a typed tuple")
        if self.active_operation_id is not None and not isinstance(
            self.active_operation_id, OperationId
        ):
            raise TypeError("active_operation_id must be OperationId or None")
        if not isinstance(self.workspace_readiness, LatheWorkspaceReadiness):
            raise TypeError("Presenter workspace readiness is invalid")
        if type(self.read_only) is not bool or type(self.closed) is not bool:
            raise TypeError("Presenter lifecycle flags must be bool")


class LathePresenterFacade:
    """Thin Stage 9A.9 boundary that delegates to LatheOperationService."""

    def __init__(self, service: LatheOperationService) -> None:
        if not isinstance(service, LatheOperationService):
            raise TypeError("service must be LatheOperationService")
        self._service = service
        self._active_operation_id: OperationId | None = None

    @property
    def service(self) -> LatheOperationService:
        return self._service

    def list_strategies(self) -> tuple[LatheStrategyDescriptor, ...]:
        """Return exactly 11 descriptors in canonical registry order."""

        return tuple(
            LatheStrategyDescriptor.from_definition(item)
            for item in LATHE_STRATEGY_REGISTRY
        )

    def strategy_metadata(
        self, strategy_id: LatheStrategyId
    ) -> LatheStrategyDescriptor:
        return LatheStrategyDescriptor.from_definition(
            lathe_strategy_definition(strategy_id)
        )

    def create_operation(
        self,
        operation_id: OperationId,
        strategy_id: LatheStrategyId,
        *,
        enabled: bool = True,
    ) -> LatheCommandOutcome:
        ownership = self._service.ownership_for(operation_id)
        outcome = self._service.execute(
            CreateLatheOperation(
                ownership,
                strategy_id,
                build_lathe_v1_defaults(strategy_id),
                enabled=enabled,
            )
        )
        if outcome.accepted:
            self._active_operation_id = operation_id
        return outcome

    def select_active_operation(self, operation_id: OperationId | None) -> None:
        if operation_id is not None:
            self._service.query(operation_id)
        self._active_operation_id = operation_id

    def operation_snapshot(
        self, operation_id: OperationId
    ) -> LatheOperationSnapshot:
        state = self._service.query(operation_id)
        return LatheOperationSnapshot.from_state(
            state, self._service.evaluate(operation_id)
        )

    def snapshot(self) -> LathePresenterSnapshot:
        operations = tuple(
            self.operation_snapshot(item.ownership.operation_id)
            for item in self._service.list_operations()
        )
        active = self._active_operation_id
        if active is not None and all(
            item.ownership.operation_id != active for item in operations
        ):
            active = None
            self._active_operation_id = None
        session = self._service.session
        return LathePresenterSnapshot(
            self.list_strategies(),
            operations,
            active,
            STAGE12_LATHE_WORKSPACE_READINESS,
            session.read_only,
            session.closed,
        )

    def apply_parameter_changes(
        self,
        operation_id: OperationId,
        updates: tuple[LatheParameterUpdate, ...],
        expected_revision: Revision,
    ) -> LatheCommandOutcome:
        state = self._service.query(operation_id)
        return self._service.execute(
            UpdateLatheParameters(state.ownership, updates, expected_revision)
        )

    def apply_parameter_change(
        self,
        operation_id: OperationId,
        parameter_id: str,
        value: object,
        expected_revision: Revision,
    ) -> LatheCommandOutcome:
        return self.apply_parameter_changes(
            operation_id,
            (LatheParameterUpdate(parameter_id, value),),
            expected_revision,
        )

    def change_strategy(
        self,
        operation_id: OperationId,
        strategy_id: LatheStrategyId,
        expected_revision: Revision,
    ) -> LatheCommandOutcome:
        state = self._service.query(operation_id)
        return self._service.execute(
            ChangeLatheStrategy(state.ownership, strategy_id, expected_revision)
        )

    def bind_geometry(
        self,
        operation_id: OperationId,
        binding: LatheGeometryBinding,
        expected_revision: Revision,
    ) -> LatheCommandOutcome:
        state = self._service.query(operation_id)
        return self._service.execute(
            BindLatheGeometry(state.ownership, binding, expected_revision)
        )

    def clear_geometry(
        self, operation_id: OperationId, expected_revision: Revision
    ) -> LatheCommandOutcome:
        state = self._service.query(operation_id)
        return self._service.execute(
            ClearLatheGeometry(state.ownership, expected_revision)
        )

    def bind_tool(
        self,
        operation_id: OperationId,
        reference: LatheToolReference,
        expected_revision: Revision,
    ) -> LatheCommandOutcome:
        state = self._service.query(operation_id)
        return self._service.execute(
            BindLatheTool(state.ownership, reference, expected_revision)
        )

    def clear_tool(
        self, operation_id: OperationId, expected_revision: Revision
    ) -> LatheCommandOutcome:
        state = self._service.query(operation_id)
        return self._service.execute(
            ClearLatheTool(state.ownership, expected_revision)
        )

    def set_enabled(
        self,
        operation_id: OperationId,
        enabled: bool,
        expected_revision: Revision,
    ) -> LatheCommandOutcome:
        state = self._service.query(operation_id)
        return self._service.execute(
            SetLatheOperationEnabled(
                state.ownership, enabled, expected_revision
            )
        )

    def delete_operation(
        self, operation_id: OperationId, expected_revision: Revision
    ) -> LatheCommandOutcome:
        state = self._service.query(operation_id)
        outcome = self._service.execute(
            DeleteLatheOperation(state.ownership, expected_revision)
        )
        if outcome.accepted and self._active_operation_id == operation_id:
            self._active_operation_id = None
        return outcome

    def validate_operation(
        self, operation_id: OperationId, expected_revision: Revision
    ) -> LatheCommandOutcome:
        state = self._service.query(operation_id)
        return self._service.execute(
            ValidateLatheOperation(state.ownership, expected_revision)
        )

    def query_diagnostics(
        self, operation_id: OperationId
    ) -> tuple[LatheDiagnostic, ...]:
        return self._service.evaluate(operation_id).diagnostics

    def query_workspace_readiness(self) -> LatheWorkspaceReadiness:
        return STAGE12_LATHE_WORKSPACE_READINESS


__all__ = [
    "LatheOperationSnapshot",
    "LathePresenterFacade",
    "LathePresenterSnapshot",
    "LatheStrategyDescriptor",
]
