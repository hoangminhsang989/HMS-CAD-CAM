from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime
from decimal import Decimal
from enum import IntEnum
from fractions import Fraction
import importlib
import importlib.util
import math
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from hms_cadcam.cad.models import CadDocumentId
from hms_cadcam.cam.application.defaults import basic_parallel_resources
from hms_cadcam.cam.application.cam3d_editor import (
    Cam3DEditorApplicationService,
    Cam3DEditorDiagnostic,
    Cam3DEditorDiagnosticCode,
    Cam3DEditorField,
    Cam3DEditorReadiness,
    Cam3DParameterDraft,
    Cam3DProjectContext,
    Cam3DToolAssemblyChoice,
    Cam3DToolProfileChoice,
)
from hms_cadcam.cam.application.cam3d_selection import (
    Cam3DSelectedSurface,
    Cam3DSelectionProvenance,
    Cam3DSelectionRole,
    Cam3DSelectionState,
)
from hms_cadcam.cam.domain import (
    ContentFingerprint,
    DEFAULT_TOOL_PROFILE_REGISTRY,
    DiagnosticSeverity,
    Length,
    LengthUnit,
    Revision,
    ToolAssembly,
    ToolAssemblyId,
    ToolDefinitionId,
    ToolProgramProfile,
    ToolProgramProfileId,
    ToolProfileValidationState,
)
from tests.unit._cam3d_fixtures import surface, tool


def _context(
    project_id: UUID,
    generation: int = 4,
    *,
    read_only: bool = False,
    document_id: CadDocumentId | None = None,
    source_id: UUID | None = None,
) -> Cam3DProjectContext:
    return Cam3DProjectContext.open(
        project_id,
        generation,
        document_id=document_id or CadDocumentId("document-1"),
        source_id=source_id or uuid4(),
        read_only=read_only,
    )


def _selection(
    project_id: UUID,
    generation: int = 4,
    *,
    source_id: UUID | None = None,
    document_id: CadDocumentId | None = None,
    read_only: bool = False,
) -> Cam3DSelectionState:
    source_id = source_id or uuid4()
    document_id = document_id or CadDocumentId("document-1")
    state = Cam3DSelectionState.for_project(
        project_id,
        generation,
        read_only=read_only,
    )
    for role, selector in (
        (Cam3DSelectionRole.PART, "part"),
        (Cam3DSelectionRole.CHECK, "check"),
        (Cam3DSelectionRole.FIXTURE, "fixture"),
    ):
        item = Cam3DSelectedSurface(
            role,
            surface(
                project_id,
                source_id,
                selector,
                role.cam_role,
                revision=Revision(0),
            ),
            Cam3DSelectionProvenance(
                project_id,
                generation,
                document_id,
                source_id,
            ),
            f"CAD surface {selector}",
        )
        state = state.assign(role, (item,))
    return state


def _tool_choice(*, profile: bool = False) -> tuple[Cam3DToolAssemblyChoice, ToolDefinition, ToolProgramProfile | None]:
    value = tool(ball=False)
    assembly = ToolAssembly.create(
        ToolAssemblyId.new(),
        "Editor assembly",
        value,
        Length(20.0, LengthUnit.MM),
        Length(40.0, LengthUnit.MM),
    )
    profile_value = None
    if profile:
        schema = DEFAULT_TOOL_PROFILE_REGISTRY.schema("parallel_finishing_3d")
        now = datetime(2026, 7, 29, tzinfo=UTC)
        profile_value = ToolProgramProfile(
            ToolProgramProfileId.new(),
            value.tool_id,
            schema.strategy_id,
            schema.display_name_vi,
            True,
            schema.profile_schema_version,
            schema.normalize_values({}),
            now,
            now,
            value.revision,
            value.content_fingerprint,
        )
    return Cam3DToolAssemblyChoice(assembly, value), value, profile_value


def _ready_service(*, read_only: bool = False) -> tuple[
    Cam3DEditorApplicationService,
    Cam3DProjectContext,
    Cam3DToolAssemblyChoice,
    Cam3DToolProfileChoice,
]:
    project_id = uuid4()
    context = _context(project_id, read_only=read_only)
    selection = _selection(
        project_id,
        source_id=context.source_id,
        document_id=context.document_id,
        read_only=read_only,
    )
    assembly, value, profile = _tool_choice(profile=True)
    assert profile is not None
    service = Cam3DEditorApplicationService(context, selection)
    service.assign_tool_assembly(assembly, live_context=context)
    profile_choice = Cam3DToolProfileChoice(profile, value)
    service.assign_tool_profile(profile_choice, live_context=context)
    return service, context, assembly, profile_choice


def test_project_context_is_frozen_deterministic_and_rejects_raw_identity() -> None:
    project_id = uuid4()
    left = _context(project_id)
    right = _context(
        project_id,
        document_id=left.document_id,
        source_id=left.source_id,
    )
    assert left == right
    assert hash(left) == hash(right)
    assert Cam3DProjectContext.closed() == Cam3DProjectContext.closed()
    with pytest.raises(FrozenInstanceError):
        left.read_only = True  # type: ignore[misc]
    with pytest.raises(TypeError):
        Cam3DProjectContext.open("raw", 1)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        Cam3DProjectContext.open(project_id, True)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        Cam3DProjectContext.open(project_id, 1, source_id="raw")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        Cam3DProjectContext(None, None, None, None)


def test_parameter_draft_is_mm_only_immutable_and_uses_domain_limits() -> None:
    draft = Cam3DParameterDraft()
    assert draft.unit is LengthUnit.MM
    assert draft.tolerance_mm == 0.01
    with pytest.raises(FrozenInstanceError):
        draft.tolerance_mm = 0.2  # type: ignore[misc]
    with pytest.raises(ValueError):
        Cam3DParameterDraft(unit=LengthUnit.INCH)
    with pytest.raises(ValueError):
        Cam3DParameterDraft(tolerance_mm=0.0)
    with pytest.raises(ValueError):
        Cam3DParameterDraft(allowance_mm=-1.0)


@pytest.mark.parametrize(
    ("value", "code"),
    [
        (True, Cam3DEditorDiagnosticCode.INVALID_NUMERIC_TYPE),
        ("10 mm", Cam3DEditorDiagnosticCode.INVALID_NUMERIC_TYPE),
        (float("nan"), Cam3DEditorDiagnosticCode.NON_FINITE_VALUE),
        (float("inf"), Cam3DEditorDiagnosticCode.NON_FINITE_VALUE),
        (-1.0, Cam3DEditorDiagnosticCode.VALUE_BELOW_MINIMUM),
        (11.0, Cam3DEditorDiagnosticCode.VALUE_ABOVE_MAXIMUM),
    ],
)
def test_numeric_mutation_returns_structured_diagnostic_without_clamping(
    value: object,
    code: Cam3DEditorDiagnosticCode,
) -> None:
    project_id = uuid4()
    context = _context(project_id)
    service = Cam3DEditorApplicationService(
        context,
        Cam3DSelectionState.for_project(project_id, context.project_generation),
    )
    before = service.state
    result = service.replace_numeric_field(
        Cam3DEditorField.TOLERANCE_MM,
        value,
        live_context=context,
    )
    assert not result.accepted
    assert result.diagnostics[0].code is code
    assert result.diagnostics[0].severity is DiagnosticSeverity.ERROR
    assert result.state.parameters == before.parameters


def test_tool_choice_reports_current_and_stale_snapshots_without_raw_identity() -> None:
    choice, value, _profile = _tool_choice()
    assert choice.current
    stale = replace(
        choice.assembly,
        expected_tool_revision=Revision(1),
    )
    stale_choice = Cam3DToolAssemblyChoice(stale, value)
    assert not stale_choice.current
    assert stale_choice.assembly_id == choice.assembly_id
    with pytest.raises(TypeError):
        Cam3DToolAssemblyChoice("raw", value)  # type: ignore[arg-type]


def test_profile_choice_requires_typed_tool_and_exposes_ownership_and_current() -> None:
    assembly, value, profile = _tool_choice(profile=True)
    assert profile is not None
    choice = Cam3DToolProfileChoice(profile, value)
    assert choice.owned_by(assembly)
    assert choice.current
    other_tool = tool(ball=True)
    foreign = Cam3DToolProfileChoice(profile, other_tool)
    assert not foreign.owned_by(assembly)
    with pytest.raises(TypeError):
        Cam3DToolProfileChoice("raw", value)  # type: ignore[arg-type]


def test_service_empty_draft_is_partial_and_never_ready_to_calculate() -> None:
    project_id = uuid4()
    context = _context(project_id)
    service = Cam3DEditorApplicationService(
        context,
        Cam3DSelectionState.for_project(project_id, context.project_generation),
    )
    evaluation = service.evaluate(context)
    assert evaluation.readiness is Cam3DEditorReadiness.PARTIAL
    assert {
        item.code
        for item in evaluation.diagnostics
    } == {
        Cam3DEditorDiagnosticCode.PART_MISSING,
        Cam3DEditorDiagnosticCode.TOOL_ASSEMBLY_MISSING,
        Cam3DEditorDiagnosticCode.TOOL_PROFILE_MISSING,
    }


def test_service_ready_requires_part_tool_and_owned_enabled_profile() -> None:
    service, context, _assembly, _profile = _ready_service()
    evaluation = service.evaluate(context)
    assert evaluation.valid
    assert evaluation.readiness is Cam3DEditorReadiness.READY_FOR_EDITOR_BINDING
    assert evaluation.diagnostics == ()


def test_profile_from_another_tool_fails_closed_and_keeps_previous_state() -> None:
    service, context, assembly, _profile = _ready_service()
    before = service.state
    foreign_tool = tool(ball=True)
    foreign_profile = replace(
        before.tool_profile.profile,  # type: ignore[union-attr]
        tool_id=foreign_tool.tool_id,
        source_tool_revision=foreign_tool.revision,
        source_tool_fingerprint=foreign_tool.content_fingerprint,
    )
    result = service.assign_tool_profile(
        Cam3DToolProfileChoice(foreign_profile, foreign_tool),
        live_context=context,
    )
    assert not result.accepted
    assert result.diagnostics[0].code is Cam3DEditorDiagnosticCode.TOOL_PROFILE_NOT_OWNED
    assert result.state.tool_assembly == assembly
    assert result.state.tool_profile == before.tool_profile


def test_profile_missing_and_disabled_are_structured_diagnostics() -> None:
    service, context, assembly, profile = _ready_service()
    cleared = service.clear_tool_profile(live_context=context)
    assert cleared.accepted
    assert service.evaluate(context).readiness is Cam3DEditorReadiness.PARTIAL
    assert service.evaluate(context).diagnostics[-1].code is (
        Cam3DEditorDiagnosticCode.TOOL_PROFILE_MISSING
    )
    disabled = replace(
        profile.profile,
        enabled=False,
        validation_state=ToolProfileValidationState.CONFIGURED,
    )
    result = service.assign_tool_profile(
        Cam3DToolProfileChoice(disabled, profile.tool),
        live_context=context,
    )
    assert result.accepted
    evaluation = service.evaluate(context)
    assert evaluation.readiness is Cam3DEditorReadiness.INVALID
    assert Cam3DEditorDiagnosticCode.TOOL_PROFILE_DISABLED in {
        item.code for item in evaluation.diagnostics
    }
    assert service.state.tool_assembly == assembly


def test_lifecycle_switch_and_generation_stale_fail_closed() -> None:
    service, context, _assembly, _profile = _ready_service()
    before = service.state
    switched = _context(uuid4(), document_id=context.document_id, source_id=context.source_id)
    result = service.replace_numeric_field(
        Cam3DEditorField.ALLOWANCE_MM,
        0.5,
        live_context=switched,
    )
    assert not result.accepted
    assert result.diagnostics[0].code is Cam3DEditorDiagnosticCode.PROJECT_SWITCHED
    assert result.state is before
    assert service.state is before
    stale = _context(
        context.project_id,
        context.project_generation + 1,  # type: ignore[operator]
        document_id=context.document_id,
        source_id=context.source_id,
    )
    result = service.clear_tool_assembly(live_context=stale)
    assert not result.accepted
    assert result.diagnostics[0].code is (
        Cam3DEditorDiagnosticCode.PROJECT_GENERATION_STALE
    )
    assert result.state is before
    assert service.state is before


def test_closed_project_and_read_only_mutation_are_fail_closed_but_inspectable() -> None:
    project_id = uuid4()
    closed_service = Cam3DEditorApplicationService(
        Cam3DProjectContext.closed(),
        Cam3DSelectionState.closed(),
    )
    result = closed_service.replace_numeric_field(
        Cam3DEditorField.TOLERANCE_MM,
        0.2,
        live_context=Cam3DProjectContext.closed(),
    )
    assert not result.accepted
    assert result.diagnostics[0].code is Cam3DEditorDiagnosticCode.PROJECT_CLOSED
    assert closed_service.evaluate().readiness is Cam3DEditorReadiness.EMPTY

    service, context, _assembly, _profile = _ready_service(read_only=True)
    result = service.clear_tool_assembly(live_context=context)
    assert not result.accepted
    assert result.diagnostics[0].code is Cam3DEditorDiagnosticCode.READ_ONLY
    assert result.state.tool_assembly is None
    evaluation = service.evaluate(context)
    assert evaluation.readiness is Cam3DEditorReadiness.READ_ONLY


def test_selection_replace_and_reset_are_typed_and_deterministic() -> None:
    project_id = uuid4()
    context = _context(project_id)
    service = Cam3DEditorApplicationService(
        context,
        Cam3DSelectionState.for_project(project_id, context.project_generation),
    )
    selection = _selection(
        project_id,
        source_id=context.source_id,
        document_id=context.document_id,
    )
    result = service.replace_selection(selection, live_context=context)
    assert result.accepted
    assert service.state.selection == selection
    with pytest.raises(TypeError):
        service.replace_selection("raw", live_context=context)  # type: ignore[arg-type]
    reset = service.reset()
    assert reset.context == Cam3DProjectContext.closed()
    assert reset.selection == Cam3DSelectionState.closed()
    assert service.evaluate().readiness is Cam3DEditorReadiness.EMPTY


def test_service_has_no_qt_projectservice_persistence_or_candidate_dependencies() -> None:
    source = Path("src/hms_cadcam/cam/application/cam3d_editor.py").read_text(
        encoding="utf-8"
    )
    assert "PySide6" not in source
    assert "ProjectService" not in source
    assert "sqlite" not in source.casefold()
    assert "MachiningZone3D" not in source

class _NumericEnum(IntEnum):
    ONE = 1


class _Floatable:
    def __float__(self) -> float:
        return 1.0


@pytest.mark.parametrize(
    "value",
    [Decimal("1.0"), Fraction(1, 2), _NumericEnum.ONE, _Floatable(), 10**1_000],
)
def test_numeric_contract_rejects_non_builtin_scalars(value: object) -> None:
    project_id = uuid4()
    context = _context(project_id)
    service = Cam3DEditorApplicationService(
        context,
        Cam3DSelectionState.for_project(project_id, context.project_generation),
    )

    result = service.replace_numeric_field(
        Cam3DEditorField.TOLERANCE_MM,
        value,
        live_context=context,
    )

    assert not result.accepted
    expected_code = (
        Cam3DEditorDiagnosticCode.NON_FINITE_VALUE
        if type(value) is int
        else Cam3DEditorDiagnosticCode.INVALID_NUMERIC_TYPE
    )
    assert result.diagnostics == (
        Cam3DEditorDiagnostic(
            expected_code,
            DiagnosticSeverity.ERROR,
            Cam3DEditorField.TOLERANCE_MM,
        ),
    )


def test_numpy_scalar_is_rejected_when_numpy_is_available() -> None:
    if importlib.util.find_spec("numpy") is None:
        return
    numpy = importlib.import_module("numpy")
    project_id = uuid4()
    context = _context(project_id)
    service = Cam3DEditorApplicationService(
        context,
        Cam3DSelectionState.for_project(project_id, context.project_generation),
    )

    result = service.replace_numeric_field(
        Cam3DEditorField.TOLERANCE_MM,
        numpy.float64(0.5),
        live_context=context,
    )

    assert not result.accepted
    assert result.diagnostics[0].code is (
        Cam3DEditorDiagnosticCode.INVALID_NUMERIC_TYPE
    )


def test_numeric_zero_is_canonical_and_extreme_limits_are_explicit() -> None:
    project_id = uuid4()
    context = _context(project_id)
    service = Cam3DEditorApplicationService(
        context,
        Cam3DSelectionState.for_project(project_id, context.project_generation),
    )

    zero = service.replace_numeric_field(
        Cam3DEditorField.ALLOWANCE_MM,
        -0.0,
        live_context=context,
    )
    huge_safe_z = service.replace_numeric_field(
        Cam3DEditorField.CLEARANCE_Z_MM,
        1.0e308,
        live_context=context,
    )
    huge_tolerance = service.replace_numeric_field(
        Cam3DEditorField.TOLERANCE_MM,
        1.0e308,
        live_context=context,
    )

    assert zero.accepted
    assert math.copysign(1.0, zero.state.parameters.allowance_mm) == 1.0
    assert huge_safe_z.accepted
    assert huge_safe_z.state.parameters.clearance_z_mm == 1.0e308
    assert not huge_tolerance.accepted
    assert huge_tolerance.diagnostics[0].code is (
        Cam3DEditorDiagnosticCode.VALUE_ABOVE_MAXIMUM
    )


def test_diagnostic_parameters_are_canonical_and_reject_duplicate_keys() -> None:
    diagnostic = Cam3DEditorDiagnostic(
        Cam3DEditorDiagnosticCode.VALUE_ABOVE_MAXIMUM,
        DiagnosticSeverity.ERROR,
        Cam3DEditorField.TOLERANCE_MM,
        (("maximum", "10.0"), ("actual", "11.0")),
    )
    assert diagnostic.parameters == (("actual", "11.0"), ("maximum", "10.0"))
    with pytest.raises(ValueError):
        Cam3DEditorDiagnostic(
            Cam3DEditorDiagnosticCode.VALUE_ABOVE_MAXIMUM,
            DiagnosticSeverity.ERROR,
            parameters=(("maximum", "10.0"), ("maximum", "11.0")),
        )


def test_bound_read_only_cannot_be_bypassed_and_inspection_keeps_field_diagnostics() -> None:
    project_id = uuid4()
    context = _context(project_id, read_only=True)
    selection = Cam3DSelectionState.for_project(
        project_id,
        context.project_generation,
        read_only=True,
    )
    service = Cam3DEditorApplicationService(context, selection)
    before = service.state
    mutable_live_context = replace(context, read_only=False)

    result = service.clear_tool_profile(live_context=mutable_live_context)
    evaluation = service.evaluate(mutable_live_context)

    assert not result.accepted
    assert result.diagnostics[0].code is Cam3DEditorDiagnosticCode.READ_ONLY
    assert result.state is before
    assert service.state is before
    assert evaluation.readiness is Cam3DEditorReadiness.READ_ONLY
    assert Cam3DEditorDiagnosticCode.PART_MISSING in {
        item.code for item in evaluation.diagnostics
    }


def test_cross_project_selection_replacement_is_rejected_atomically() -> None:
    service, context, _assembly, _profile = _ready_service()
    before = service.state
    foreign = Cam3DSelectionState.for_project(uuid4(), context.project_generation)

    result = service.replace_selection(foreign, live_context=context)

    assert not result.accepted
    assert result.diagnostics[0].code is Cam3DEditorDiagnosticCode.PROJECT_SWITCHED
    assert result.state is before
    assert service.state is before


def test_lifecycle_blocker_does_not_hide_numeric_validation() -> None:
    service, context, _assembly, _profile = _ready_service()
    invalid = service.replace_numeric_field(
        Cam3DEditorField.TOLERANCE_MM,
        "invalid",
        live_context=context,
    )
    stale = replace(context, project_generation=context.project_generation + 1)

    evaluation = service.evaluate(stale)
    codes = {item.code for item in evaluation.diagnostics}

    assert not invalid.accepted
    assert Cam3DEditorDiagnosticCode.INVALID_NUMERIC_TYPE in codes
    assert Cam3DEditorDiagnosticCode.PROJECT_GENERATION_STALE in codes
    assert evaluation.readiness is Cam3DEditorReadiness.STALE
    assert evaluation.diagnostics == service.evaluate(stale).diagnostics


def test_profile_holder_identity_and_validation_states_are_distinct() -> None:
    service, context, _assembly, profile = _ready_service()
    holder_stale = replace(
        profile.profile,
        source_holder_fingerprint=ContentFingerprint.from_payload(
            {"holder": "foreign"}
        ),
    )
    assert service.assign_tool_profile(
        Cam3DToolProfileChoice(holder_stale, profile.tool),
        live_context=context,
    ).accepted
    assert Cam3DEditorDiagnosticCode.TOOL_PROFILE_STALE in {
        item.code for item in service.evaluate(context).diagnostics
    }

    needs_review = replace(
        profile.profile,
        validation_state=ToolProfileValidationState.NEEDS_REVIEW,
    )
    assert service.assign_tool_profile(
        Cam3DToolProfileChoice(needs_review, profile.tool),
        live_context=context,
    ).accepted
    review_evaluation = service.evaluate(context)
    assert review_evaluation.readiness is Cam3DEditorReadiness.STALE
    assert Cam3DEditorDiagnosticCode.TOOL_PROFILE_NEEDS_REVIEW in {
        item.code for item in review_evaluation.diagnostics
    }

    incompatible = replace(
        profile.profile,
        validation_state=ToolProfileValidationState.INCOMPATIBLE,
    )
    assert service.assign_tool_profile(
        Cam3DToolProfileChoice(incompatible, profile.tool),
        live_context=context,
    ).accepted
    incompatible_evaluation = service.evaluate(context)
    assert incompatible_evaluation.readiness is Cam3DEditorReadiness.INVALID
    assert Cam3DEditorDiagnosticCode.TOOL_PROFILE_INCOMPATIBLE in {
        item.code for item in incompatible_evaluation.diagnostics
    }


def test_tool_assembly_unit_incompatibility_is_not_reported_as_stale() -> None:
    value, holder, assembly, _machine = basic_parallel_resources(LengthUnit.INCH)
    incompatible = replace(
        assembly,
        unit=LengthUnit.MM,
        expected_tool_unit=LengthUnit.MM,
        expected_holder_unit=LengthUnit.MM,
        stickout=Length(20.0, LengthUnit.MM),
        gauge_length=Length(40.0, LengthUnit.MM),
    )
    choice = Cam3DToolAssemblyChoice(incompatible, value, holder)
    project_id = uuid4()
    context = _context(project_id)
    service = Cam3DEditorApplicationService(
        context,
        Cam3DSelectionState.for_project(project_id, context.project_generation),
    )

    assert service.assign_tool_assembly(choice, live_context=context).accepted
    evaluation = service.evaluate(context)

    assert evaluation.readiness is Cam3DEditorReadiness.INVALID
    assert Cam3DEditorDiagnosticCode.TOOL_ASSEMBLY_INCOMPATIBLE in {
        item.code for item in evaluation.diagnostics
    }


def test_empty_factory_bind_reset_and_equivalent_operations_are_deterministic() -> None:
    empty = Cam3DEditorApplicationService.create_empty_draft()
    assert empty.evaluate().readiness is Cam3DEditorReadiness.EMPTY

    service, _context_value, _assembly, _profile = _ready_service()
    project_id = uuid4()
    context = _context(project_id)
    selection = _selection(
        project_id,
        source_id=context.source_id,
        document_id=context.document_id,
    )
    first = service.bind(context, selection)
    second = service.clear_tool_profile(live_context=context).state
    third = service.clear_tool_profile(live_context=context).state

    assert first.tool_assembly is None
    assert first.tool_profile is None
    assert second == third
    with pytest.raises(ValueError):
        service.bind(context, Cam3DSelectionState.for_project(uuid4(), 4))
    assert service.reset() == Cam3DEditorApplicationService.create_empty_draft().state
