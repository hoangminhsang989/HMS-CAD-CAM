"""Synthetic representative Stage 12.4C programs; no owner NC content."""

from __future__ import annotations

from hms_cadcam.cam.lathe.lathe_post import (
    BasicPostMetadata,
    BasicToolMapping,
    LatheProgramAssemblerV1,
    LatheProgramBlockKind,
    LatheProgramIdentity,
    LatheProgramIRV1,
)
from hms_cadcam.cam.lathe.types import LatheStrategyId
from tests.unit._lathe_toolpath_fixtures import generate, ready_request, stock_snapshot


SCENARIO_A_STRATEGIES = (
    LatheStrategyId.OD_ROUGH,
    LatheStrategyId.OD_FINISH,
)
SCENARIO_B_STRATEGIES = (
    LatheStrategyId.FACE,
    LatheStrategyId.AXIAL_DRILL,
    LatheStrategyId.ID_ROUGH,
    LatheStrategyId.ID_FINISH,
    LatheStrategyId.OD_GROOVE,
    LatheStrategyId.ID_GROOVE,
    LatheStrategyId.PART_OFF,
)
SCENARIO_C_STRATEGIES = (
    LatheStrategyId.OD_THREAD,
    LatheStrategyId.ID_THREAD,
)


def representative_program(
    scenario: str,
) -> tuple[LatheProgramIRV1, tuple[BasicToolMapping, ...], BasicPostMetadata]:
    """Build one deterministic multi-operation IR through production services."""

    strategies = {
        "A": SCENARIO_A_STRATEGIES,
        "B": SCENARIO_B_STRATEGIES,
        "C": SCENARIO_C_STRATEGIES,
    }.get(scenario)
    if strategies is None:
        raise ValueError("scenario must be A, B, or C")
    operations = []
    results: dict[str, object] = {}
    first_owner = None
    for index, strategy in enumerate(strategies, start=1):
        stock = (
            stock_snapshot(inner_diameter_mm=12.0, identity="stage12-4c-bore")
            if strategy in {
                LatheStrategyId.ID_ROUGH,
                LatheStrategyId.ID_FINISH,
                LatheStrategyId.ID_GROOVE,
                LatheStrategyId.ID_THREAD,
            }
            else stock_snapshot(identity="stage12-4c-solid")
        )
        parameters = {"face_z_mm": -1.0} if strategy is LatheStrategyId.FACE else None
        _, operation, request = ready_request(
            strategy,
            parameters=parameters,
            stock=stock,
            operation_index=index,
            tool_index=index + (0 if scenario == "A" else 10 if scenario == "B" else 30),
            request_sequence=index,
        )
        result = generate(request)
        if not result.succeeded:
            raise AssertionError(f"synthetic scenario {scenario} generation failed")
        owner = operation.ownership
        first_owner = first_owner or owner
        operations.append(operation)
        results[str(owner.operation_id)] = result
    assert first_owner is not None
    identity = LatheProgramIdentity(
        first_owner.project_id,
        first_owner.document_id,
        first_owner.source_id,
        first_owner.generation,
        first_owner.setup_id,
        f"hms-stage12-4c-scenario-{scenario.casefold()}",
        0,
    )
    assembled = LatheProgramAssemblerV1().assemble(
        identity,
        tuple(operations),
        accepted_results=results,
    )
    if not assembled.accepted or assembled.program is None:
        raise AssertionError(f"synthetic scenario {scenario} assembly failed: {assembled.diagnostics}")
    tool_ids = tuple(
        dict.fromkeys(
            block.payload.tool_id
            for block in assembled.program.blocks
            if block.kind is LatheProgramBlockKind.TOOL_INTENT
        )
    )
    mappings = tuple(
        BasicToolMapping(tool_id, index, index, description=f"HMS SYNTHETIC TOOL {index}")
        for index, tool_id in enumerate(tool_ids, start=1)
    )
    return assembled.program, mappings, BasicPostMetadata(f"HMS_SCENARIO_{scenario}")


__all__ = [
    "SCENARIO_A_STRATEGIES",
    "SCENARIO_B_STRATEGIES",
    "SCENARIO_C_STRATEGIES",
    "representative_program",
]
