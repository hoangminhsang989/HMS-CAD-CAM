"""Focused native-free certification for the Stage16A creation session."""

from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

import pytest

from _cam3d_fixtures import tool
from hms_cadcam.cam.domain import (
    CamJobId,
    CamNodeId,
    Length,
    LengthUnit,
    Revision,
    SetupId,
    ToolAssembly,
    ToolAssemblyId,
    ToolProfileListState,
    ToolProfileValueSource,
)
from hms_cadcam.cam.operation_creation import (
    OperationCreationSession,
    OperationCreationState,
    OperationCreationStep,
    OperationToolChoice,
    Stage16AStrategyRegistry,
    Stage16AToolSelectionService,
)
from hms_cadcam.cam.persistence import CamProjectSnapshot


def _session() -> OperationCreationSession:
    return OperationCreationSession.start(
        project_id=uuid4(),
        project_generation=3,
        job_id=CamJobId.new(),
        setup_id=SetupId.new(),
        parent_node_id=CamNodeId.new(),
    )


def _choice(*, compatible: bool = True) -> OperationToolChoice:
    return OperationToolChoice(
        ToolAssemblyId.new(),
        tool(ball=True).tool_id,
        "Ball D10",
        "Assembly",
        "ball_end_mill",
        "D10 mm / R5",
        "No holder",
        compatible,
        "Tool tương thích." if compatible else "Sai họ Tool.",
        ToolProfileListState.NOT_CONFIGURED,
        None,
        (ToolProfileValueSource.AUTOMATIC_POLICY,),
        Revision(0),
        Revision(0),
        Revision(0),
    )


def _ready_session() -> tuple[OperationCreationSession, OperationToolChoice]:
    choice = _choice()
    session = (
        _session()
        .select_strategy("parallel_finishing_3d")
        .select_tool(choice)
        .configure({"operation_name": "Terminal", "stepover_mm": "1.25"})
    )
    return session, choice


def _terminal_snapshot(session: OperationCreationSession) -> tuple[object, ...]:
    """Capture every semantic identity and mutable working-copy field."""
    return (
        session.state,
        session.current_step,
        session.session_id,
        session.project_id,
        session.project_generation,
        session.job_id,
        session.setup_id,
        session.parent_node_id,
        session.strategy_id,
        session.tool_assembly_id,
        session.tool_id,
        session.profile_id,
        session.tool_configuration_revision,
        session.resolved_provenance,
        session.working_values,
        session.validation_errors,
    )


def _invoke_public_mutator(
    session: OperationCreationSession,
    mutator: str,
    choice: OperationToolChoice,
) -> OperationCreationSession:
    if mutator == "select_strategy":
        return session.select_strategy("drilling_v1")
    if mutator == "select_tool":
        return session.select_tool(choice)
    if mutator == "configure":
        return session.configure({"operation_name": "Resurrected"})
    if mutator == "back":
        return session.back()
    if mutator == "cancel":
        return session.cancel()
    if mutator == "mark_created":
        return session.mark_created()
    raise AssertionError(f"Unmapped public mutator: {mutator}")


def _library():
    ball = tool(ball=True)
    flat = tool(ball=False)
    ball_assembly = ToolAssembly.create(
        ToolAssemblyId.new(),
        "Ball assembly",
        ball,
        Length(25, LengthUnit.MM),
        Length(40, LengthUnit.MM),
    )
    flat_assembly = ToolAssembly.create(
        ToolAssemblyId.new(),
        "Flat assembly",
        flat,
        Length(25, LengthUnit.MM),
        Length(40, LengthUnit.MM),
    )
    snapshot = CamProjectSnapshot(
        tool_definitions=(ball, flat),
        tool_assemblies=(ball_assembly, flat_assembly),
    )
    return snapshot, ball, flat, ball_assembly, flat_assembly


def test_start_has_stable_project_program_and_no_working_operation() -> None:
    session = _session()
    assert session.state is OperationCreationState.SELECT_OPERATION
    assert session.current_step is OperationCreationStep.SELECT_OPERATION
    assert session.strategy_id is None
    assert session.tool_id is None
    assert str(session.job_id) in session.program_context_id
    assert str(session.setup_id) in session.program_context_id


@pytest.mark.parametrize(
    ("strategy_id", "families"),
    (
        ("drilling_v1", ("drill", "center_drill")),
        ("parallel_finishing_3d", ("ball_end_mill",)),
        ("z_level_finishing_3d", ("ball_end_mill",)),
    ),
)
def test_strategy_registry_is_existing_profile_registry_driven(
    strategy_id: str, families: tuple[str, ...]
) -> None:
    choice = Stage16AStrategyRegistry().choice(strategy_id)
    assert choice.strategy_id == strategy_id
    assert choice.supported_tool_families == families
    assert choice.display_name
    assert choice.description


def test_strategy_selection_advances_without_creating_tool_or_values() -> None:
    selected = _session().select_strategy("parallel_finishing_3d")
    assert selected.state is OperationCreationState.SELECT_TOOL
    assert selected.current_step is OperationCreationStep.SELECT_TOOL
    assert selected.tool_assembly_id is None
    assert selected.working_values == ()


def test_incompatible_tool_cannot_advance() -> None:
    selected = _session().select_strategy("parallel_finishing_3d")
    with pytest.raises(ValueError, match="Sai họ Tool"):
        selected.select_tool(_choice(compatible=False))


def test_tool_selection_advances_and_records_profile_provenance() -> None:
    choice = _choice()
    selected = _session().select_strategy("parallel_finishing_3d").select_tool(choice)
    assert selected.current_step is OperationCreationStep.CONFIGURE_OPERATION
    assert selected.tool_assembly_id == choice.assembly_id
    assert selected.tool_id == choice.tool_id
    assert selected.resolved_provenance == (ToolProfileValueSource.AUTOMATIC_POLICY,)


def test_valid_configuration_becomes_ready_and_terminal_create_is_exact() -> None:
    ready = (
        _session()
        .select_strategy("parallel_finishing_3d")
        .select_tool(_choice())
        .configure({"operation_name": "Finish", "stepover_mm": "1"})
    )
    assert ready.state is OperationCreationState.READY_TO_CREATE
    created = ready.mark_created()
    assert created.state is OperationCreationState.CREATED
    with pytest.raises(RuntimeError, match="terminal"):
        created.select_strategy("drilling_v1")


def test_invalid_configuration_blocks_ready_state() -> None:
    invalid = (
        _session()
        .select_strategy("parallel_finishing_3d")
        .select_tool(_choice())
        .configure({"stepover_mm": "0"}, validation_errors=("must be positive",))
    )
    assert invalid.state is OperationCreationState.CONFIGURE_OPERATION
    assert invalid.validation_errors == ("must be positive",)
    with pytest.raises(RuntimeError, match="not ready"):
        invalid.mark_created()


def test_back_from_step3_discards_only_working_values_and_preserves_tool() -> None:
    configured = (
        _session()
        .select_strategy("parallel_finishing_3d")
        .select_tool(_choice())
        .configure({"manual": 1})
    )
    backed = configured.back()
    assert backed.current_step is OperationCreationStep.SELECT_TOOL
    assert backed.tool_id == configured.tool_id
    assert backed.working_values == ()


def test_strategy_change_keeps_tool_only_when_registry_confirms_compatibility() -> None:
    selected = _session().select_strategy("parallel_finishing_3d").select_tool(_choice())
    kept = selected.select_strategy(
        "z_level_finishing_3d", selected_tool_remains_compatible=True
    )
    dropped = selected.select_strategy(
        "drilling_v1", selected_tool_remains_compatible=False
    )
    assert kept.tool_id == selected.tool_id
    assert kept.profile_id == selected.profile_id
    assert dropped.tool_id is None
    assert dropped.resolved_provenance == ()


def test_cancel_clears_working_copy_and_is_terminal() -> None:
    cancelled = (
        _session()
        .select_strategy("parallel_finishing_3d")
        .select_tool(_choice())
        .configure({"manual": 1})
        .cancel()
    )
    assert cancelled.state is OperationCreationState.CANCELLED
    assert cancelled.working_values == ()
    with pytest.raises(RuntimeError, match="terminal"):
        cancelled.select_strategy("drilling_v1")


@pytest.mark.parametrize(
    "terminal_state",
    (OperationCreationState.CANCELLED, OperationCreationState.CREATED),
)
@pytest.mark.parametrize(
    "mutator",
    (
        "select_strategy",
        "select_tool",
        "configure",
        "back",
        "cancel",
        "mark_created",
    ),
)
def test_terminal_state_public_mutator_matrix_is_monotonic_and_immutable(
    terminal_state: OperationCreationState,
    mutator: str,
) -> None:
    ready, choice = _ready_session()
    terminal = (
        ready.cancel()
        if terminal_state is OperationCreationState.CANCELLED
        else ready.mark_created()
    )
    before = _terminal_snapshot(terminal)

    if terminal_state is OperationCreationState.CANCELLED and mutator == "cancel":
        result = _invoke_public_mutator(terminal, mutator, choice)
        assert result is terminal
        assert _terminal_snapshot(result) == before
    else:
        with pytest.raises(RuntimeError):
            _invoke_public_mutator(terminal, mutator, choice)

    assert _terminal_snapshot(terminal) == before


def test_exact_r169_terminal_resurrection_paths_are_rejected() -> None:
    ready, choice = _ready_session()
    cancelled = ready.cancel()
    created = ready.mark_created()

    for attempt in (
        lambda: cancelled.configure({"operation_name": "Cancelled configure"}),
        lambda: cancelled.select_tool(choice),
        cancelled.back,
        lambda: created.configure({"operation_name": "Created configure"}),
        lambda: created.select_tool(choice),
        created.back,
    ):
        with pytest.raises(RuntimeError, match="terminal"):
            attempt()

    assert cancelled.state is OperationCreationState.CANCELLED
    assert created.state is OperationCreationState.CREATED


def test_tool_service_prioritizes_compatible_and_explains_negative_pair() -> None:
    snapshot, _ball, _flat, ball_assembly, flat_assembly = _library()
    choices = Stage16AToolSelectionService(
        snapshot, setup_unit=LengthUnit.MM
    ).choices("parallel_finishing_3d")
    assert tuple(item.assembly_id for item in choices) == (
        ball_assembly.assembly_id,
        flat_assembly.assembly_id,
    )
    assert choices[0].compatible
    assert not choices[1].compatible
    assert "family" in choices[1].reason


def test_tool_service_searches_name_family_and_stable_identity() -> None:
    snapshot, _ball, _flat, ball_assembly, _flat_assembly = _library()
    service = Stage16AToolSelectionService(snapshot, setup_unit=LengthUnit.MM)
    assert service.choices("parallel_finishing_3d", "Ball")
    assert service.choices("parallel_finishing_3d", str(ball_assembly.assembly_id))
    assert service.choices("parallel_finishing_3d", "no-match") == ()


def test_tool_service_detects_deleted_tool_and_stale_assembly() -> None:
    snapshot, ball, _flat, ball_assembly, _flat_assembly = _library()
    deleted = replace(snapshot, tool_definitions=tuple(
        item for item in snapshot.tool_definitions if item.tool_id != ball.tool_id
    ))
    deleted_choice = next(
        item
        for item in Stage16AToolSelectionService(
            deleted, setup_unit=LengthUnit.MM
        ).choices("parallel_finishing_3d")
        if item.assembly_id == ball_assembly.assembly_id
    )
    assert not deleted_choice.compatible
    assert "missing" in deleted_choice.reason

    stale_tool = replace(ball, revision=ball.revision.next())
    stale = replace(
        snapshot,
        tool_definitions=tuple(
            stale_tool if item.tool_id == ball.tool_id else item
            for item in snapshot.tool_definitions
        ),
    )
    stale_choice = next(
        item
        for item in Stage16AToolSelectionService(
            stale, setup_unit=LengthUnit.MM
        ).choices("parallel_finishing_3d")
        if item.assembly_id == ball_assembly.assembly_id
    )
    assert not stale_choice.compatible
    assert "revision" in stale_choice.reason


def test_tool_service_detects_configuration_revision_change_before_finish() -> None:
    snapshot, ball, _flat, ball_assembly, _flat_assembly = _library()
    service = Stage16AToolSelectionService(snapshot, setup_unit=LengthUnit.MM)
    choice = service.require_current(
        "parallel_finishing_3d",
        ball_assembly.assembly_id,
        tool_id=ball.tool_id,
        configuration_revision=ball.configuration_revision,
    )
    assert choice.compatible
    with pytest.raises(ValueError, match="profile"):
        service.require_current(
            "parallel_finishing_3d",
            ball_assembly.assembly_id,
            tool_id=ball.tool_id,
            configuration_revision=Revision(99),
        )


def test_tool_service_fails_closed_on_setup_unit_mismatch() -> None:
    snapshot, _ball, _flat, _ball_assembly, _flat_assembly = _library()
    choices = Stage16AToolSelectionService(
        snapshot, setup_unit=LengthUnit.INCH
    ).choices("parallel_finishing_3d")
    assert all(not item.compatible for item in choices)
    assert all("unit" in item.reason.casefold() for item in choices)
