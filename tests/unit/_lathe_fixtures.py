"""Deterministic typed fixtures for Stage 12 Lathe Foundation V1 tests."""

from __future__ import annotations

from uuid import NAMESPACE_URL, UUID, uuid5

from hms_cadcam.cad.models import CadDocumentId
from hms_cadcam.cam.domain.ids import (
    OperationId,
    SetupId,
    ToolAssemblyId,
    ToolDefinitionId,
    ToolProgramProfileId,
)
from hms_cadcam.cam.domain.revision import Revision
from hms_cadcam.cam.lathe.application import LatheOperationService, LatheServiceSession
from hms_cadcam.cam.lathe.capabilities import (
    LatheToolCapabilityResolution,
    LatheToolReference,
    StaticLatheToolCapabilityResolver,
)
from hms_cadcam.cam.lathe.commands import (
    BindLatheGeometry,
    BindLatheTool,
    CreateLatheOperation,
)
from hms_cadcam.cam.lathe.domain import (
    LatheGeometryBinding,
    LatheOperationState,
    LatheOwnershipKey,
)
from hms_cadcam.cam.lathe.parameters import build_lathe_v1_defaults
from hms_cadcam.cam.lathe.strategies import lathe_strategy_definition
from hms_cadcam.cam.lathe.types import LatheStrategyId, LatheToolCapability


def stable_uuid(label: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"https://hms.local/stage12/{label}")


def operation_id(index: int = 1) -> OperationId:
    return OperationId(stable_uuid(f"operation/{index}"))


def setup_id(index: int = 1) -> SetupId:
    return SetupId(stable_uuid(f"setup/{index}"))


def session(
    *,
    setup: SetupId | None = None,
    generation: int = 3,
    read_only: bool = False,
) -> LatheServiceSession:
    return LatheServiceSession(
        stable_uuid("project/1"),
        CadDocumentId("cad-document-stage12"),
        stable_uuid("source/1"),
        generation,
        setup if setup is not None else setup_id(),
        read_only=read_only,
    )


def ownership(index: int = 1, *, live_session: LatheServiceSession | None = None) -> LatheOwnershipKey:
    active = live_session or session()
    if active.setup_id is None:
        raise ValueError("Fixture session requires a setup")
    return LatheOwnershipKey(
        active.project_id,
        active.document_id,
        active.source_id,
        active.generation,
        active.setup_id,
        operation_id(index),
    )


def tool_reference(index: int = 1, *, with_profile: bool = False) -> LatheToolReference:
    return LatheToolReference(
        ToolDefinitionId(stable_uuid(f"tool/{index}")),
        (
            ToolProgramProfileId(stable_uuid(f"profile/{index}"))
            if with_profile
            else None
        ),
        ToolAssemblyId(stable_uuid(f"assembly/{index}")),
    )


def capability_resolution(
    capability: LatheToolCapability,
    *,
    reference: LatheToolReference | None = None,
    current: bool = True,
) -> LatheToolCapabilityResolution:
    selected = reference or tool_reference()
    return LatheToolCapabilityResolution(
        selected,
        True,
        current,
        frozenset({capability}),
        Revision(2),
        Revision(1) if selected.profile_id is not None else None,
        Revision(4),
    )


def service_for(
    strategy_id: LatheStrategyId = LatheStrategyId.FACE,
    *,
    live_session: LatheServiceSession | None = None,
    reference: LatheToolReference | None = None,
    capability: LatheToolCapability | None = None,
    current: bool = True,
) -> tuple[LatheOperationService, LatheToolReference]:
    selected_reference = reference or tool_reference()
    selected_capability = capability or next(
        iter(lathe_strategy_definition(strategy_id).required_tool_capabilities)
    )
    resolver = StaticLatheToolCapabilityResolver(
        (
            capability_resolution(
                selected_capability,
                reference=selected_reference,
                current=current,
            ),
        )
    )
    return (
        LatheOperationService(
            live_session or session(), capability_resolver=resolver
        ),
        selected_reference,
    )


def create_operation(
    service: LatheOperationService,
    strategy_id: LatheStrategyId = LatheStrategyId.FACE,
    *,
    index: int = 1,
) -> LatheOperationState:
    key = service.ownership_for(operation_id(index))
    outcome = service.execute(
        CreateLatheOperation(key, strategy_id, build_lathe_v1_defaults(strategy_id))
    )
    if not outcome.accepted or outcome.operation is None:
        raise AssertionError("Fixture operation creation failed")
    return outcome.operation


def complete_operation(
    service: LatheOperationService,
    reference: LatheToolReference,
    strategy_id: LatheStrategyId = LatheStrategyId.FACE,
    *,
    index: int = 1,
) -> LatheOperationState:
    state = create_operation(service, strategy_id, index=index)
    geometry_kind = lathe_strategy_definition(strategy_id).allowed_geometry_kinds[0]
    geometry = LatheGeometryBinding(
        geometry_kind,
        (f"entity-{index}",),
        state.ownership.source_id,
        state.ownership.generation,
    )
    geometry_outcome = service.execute(
        BindLatheGeometry(state.ownership, geometry, state.revision)
    )
    if not geometry_outcome.accepted or geometry_outcome.operation is None:
        raise AssertionError("Fixture geometry binding failed")
    state = geometry_outcome.operation
    tool_outcome = service.execute(
        BindLatheTool(state.ownership, reference, state.revision)
    )
    if not tool_outcome.accepted or tool_outcome.operation is None:
        raise AssertionError("Fixture tool binding failed")
    return tool_outcome.operation


__all__ = [
    "capability_resolution",
    "complete_operation",
    "create_operation",
    "operation_id",
    "ownership",
    "service_for",
    "session",
    "setup_id",
    "stable_uuid",
    "tool_reference",
]
