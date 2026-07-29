from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime
from decimal import Decimal
from fractions import Fraction
import math
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from hms_cadcam.cad.models import CadDocumentId
from hms_cadcam.cam.application.cam3d_editor import (
    Cam3DEditorDiagnosticCode,
    Cam3DEditorField,
    Cam3DEditorReadiness,
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
    Length,
    LengthUnit,
    Revision,
    ToolAssembly,
    ToolAssemblyId,
    ToolDefinition,
    ToolProgramProfile,
    ToolProgramProfileId,
    ToolProfileValidationState,
)
from hms_cadcam.ui.cam3d_editor_binding import (
    DIAGNOSTIC_SOURCE_KEYS,
    Cam3DEditorBindingController,
)
from tests.unit._cam3d_fixtures import surface, tool


def _context(
    project_id: UUID | None = None,
    generation: int = 7,
    *,
    document_id: CadDocumentId | None = None,
    source_id: UUID | None = None,
    read_only: bool = False,
) -> Cam3DProjectContext:
    return Cam3DProjectContext.open(
        project_id or uuid4(),
        generation,
        document_id=document_id or CadDocumentId("wp2b-document"),
        source_id=source_id or uuid4(),
        read_only=read_only,
    )


def _selection(
    context: Cam3DProjectContext,
    *,
    with_part: bool = True,
    read_only: bool | None = None,
) -> Cam3DSelectionState:
    assert context.project_id is not None
    assert context.project_generation is not None
    state = Cam3DSelectionState.for_project(
        context.project_id,
        context.project_generation,
        read_only=context.read_only if read_only is None else read_only,
    )
    if not with_part:
        return state
    assert context.document_id is not None
    assert context.source_id is not None
    item = Cam3DSelectedSurface(
        Cam3DSelectionRole.PART,
        surface(
            context.project_id,
            context.source_id,
            "part",
            Cam3DSelectionRole.PART.cam_role,
            revision=Revision(0),
        ),
        Cam3DSelectionProvenance(
            context.project_id,
            context.project_generation,
            context.document_id,
            context.source_id,
        ),
        "Part surface",
    )
    return state.assign(Cam3DSelectionRole.PART, (item,))


def _resource(
    name: str = "Editor assembly",
    *,
    ball: bool = False,
    enabled: bool = True,
    validation_state: ToolProfileValidationState = (
        ToolProfileValidationState.CONFIGURED
    ),
    stale_holder: bool = False,
) -> tuple[ToolDefinition, ToolAssembly, ToolProgramProfile]:
    base_tool = tool(ball=ball)
    schema = DEFAULT_TOOL_PROFILE_REGISTRY.schema("parallel_finishing_3d")
    now = datetime(2026, 7, 29, tzinfo=UTC)
    profile = ToolProgramProfile(
        ToolProgramProfileId.new(),
        base_tool.tool_id,
        schema.strategy_id,
        f"{name} profile",
        enabled,
        schema.profile_schema_version,
        schema.normalize_values({}),
        now,
        now,
        base_tool.revision,
        base_tool.content_fingerprint,
        source_holder_fingerprint=(
            ContentFingerprint.from_payload({"holder": "stale"})
            if stale_holder
            else None
        ),
        validation_state=validation_state,
    )
    configured_tool = replace(base_tool, program_profiles=(profile,))
    assembly = ToolAssembly.create(
        ToolAssemblyId.new(),
        name,
        configured_tool,
        Length(20.0, LengthUnit.MM),
        Length(40.0, LengthUnit.MM),
    )
    return configured_tool, assembly, profile


def _bound_controller(
    *,
    context: Cam3DProjectContext | None = None,
    resource: tuple[ToolDefinition, ToolAssembly, ToolProgramProfile] | None = None,
) -> tuple[
    Cam3DEditorBindingController,
    Cam3DProjectContext,
    ToolDefinition,
    ToolAssembly,
    ToolProgramProfile,
]:
    context = context or _context()
    value, assembly, profile = resource or _resource()
    controller = Cam3DEditorBindingController()
    controller.bind(
        context,
        _selection(context),
        tools=(value,),
        assemblies=(assembly,),
    )
    return controller, context, value, assembly, profile


def _select_ready(
    controller: Cam3DEditorBindingController,
) -> None:
    tool_state = controller.render()
    controller.assign_tool_assembly(tool_state.tool_options[0].choice)
    profile_state = controller.render()
    controller.assign_tool_profile(profile_state.profile_options[0].choice)


def test_empty_binding_is_immutable_closed_and_deterministic() -> None:
    controller = Cam3DEditorBindingController()
    first = controller.render()
    second = controller.render()

    assert first == second
    assert first.readiness is Cam3DEditorReadiness.EMPTY
    assert not first.editable
    assert first.tool_options == first.profile_options == ()
    with pytest.raises(FrozenInstanceError):
        first.parameters.tolerance_mm = 1.0  # type: ignore[misc]


def test_resource_projection_is_typed_sorted_and_does_not_mutate_inputs() -> None:
    context = _context()
    zulu = _resource("Zulu", ball=True)
    alpha = _resource("Alpha")
    tools = (zulu[0], alpha[0])
    assemblies = (zulu[1], alpha[1])
    before = (tools, assemblies)
    controller = Cam3DEditorBindingController()

    render = controller.bind(
        context,
        _selection(context),
        tools=tools,
        assemblies=assemblies,
    )

    assert tuple(option.label for option in render.tool_options) == (
        "Alpha",
        "Zulu",
    )
    assert all(
        isinstance(option.choice, Cam3DToolAssemblyChoice)
        for option in render.tool_options
    )
    assert (tools, assemblies) == before
    with pytest.raises(TypeError):
        controller.bind(
            context,
            _selection(context),
            tools=("raw",),  # type: ignore[arg-type]
        )


def test_assembly_and_profile_binding_use_typed_identity_and_clear_foreign_profile() -> None:
    controller, _context_value, _tool, _assembly, _profile = _bound_controller()
    assembly_render = controller.render()
    selected = controller.assign_tool_assembly(
        assembly_render.tool_options[0].choice
    )

    assert selected.selected_tool_assembly is not None
    assert len(selected.profile_options) == 1
    assert isinstance(
        selected.profile_options[0].choice,
        Cam3DToolProfileChoice,
    )
    ready = controller.assign_tool_profile(selected.profile_options[0].choice)
    assert ready.readiness is Cam3DEditorReadiness.READY_FOR_EDITOR_BINDING

    other = _resource("Other", ball=True)
    switched_context = _context()
    controller.bind(
        switched_context,
        _selection(switched_context),
        tools=(other[0],),
        assemblies=(other[1],),
    )
    before = controller.state
    controller.assign_tool_assembly(assembly_render.tool_options[0].choice)
    assert controller.state is before
    assert controller.state.tool_assembly is None
    assert controller.state.tool_profile is None


@pytest.mark.parametrize(
    ("enabled", "validation_state", "stale_holder"),
    (
        (False, ToolProfileValidationState.CONFIGURED, False),
        (True, ToolProfileValidationState.NEEDS_REVIEW, False),
        (True, ToolProfileValidationState.INCOMPATIBLE, False),
        (True, ToolProfileValidationState.CONFIGURED, True),
    ),
)
def test_profile_candidates_fail_closed_when_not_selectable(
    enabled: bool,
    validation_state: ToolProfileValidationState,
    stale_holder: bool,
) -> None:
    resource = _resource(
        enabled=enabled,
        validation_state=validation_state,
        stale_holder=stale_holder,
    )
    controller, _context_value, _tool, _assembly, _profile = _bound_controller(
        resource=resource
    )
    tool_render = controller.render()
    controller.assign_tool_assembly(tool_render.tool_options[0].choice)
    profile_render = controller.render()
    option = profile_render.profile_options[0]
    before = controller.state

    assert not option.selectable
    controller.assign_tool_profile(option.choice)
    assert controller.state is before
    assert controller.state.tool_profile is None
    assert profile_render.readiness is Cam3DEditorReadiness.PARTIAL


def test_numeric_text_routes_all_six_fields_through_wp2b_a_without_clamping() -> None:
    controller, _context_value, _tool, _assembly, _profile = _bound_controller()
    values = {
        Cam3DEditorField.TOLERANCE_MM: "0.5",
        Cam3DEditorField.ALLOWANCE_MM: "1.25",
        Cam3DEditorField.CLEARANCE_Z_MM: "-10.5",
        Cam3DEditorField.RETRACT_Z_MM: "",
        Cam3DEditorField.APPROACH_DISTANCE_MM: "3",
        Cam3DEditorField.LINK_CLEARANCE_MM: "-0.0",
    }
    for field, raw in values.items():
        controller.replace_numeric_text(field, raw)

    parameters = controller.state.parameters
    assert parameters.tolerance_mm == 0.5
    assert parameters.allowance_mm == 1.25
    assert parameters.clearance_z_mm == -10.5
    assert parameters.retract_z_mm is None
    assert parameters.approach_distance_mm == 3.0
    assert math.copysign(1.0, parameters.link_clearance_mm) == 1.0

    before = controller.state.parameters
    rejected = controller.replace_numeric_text(
        Cam3DEditorField.TOLERANCE_MM,
        "11",
    )
    assert rejected.parameters == before
    assert rejected.field_diagnostics[0][0] is Cam3DEditorField.TOLERANCE_MM
    assert rejected.field_diagnostics[0][1][0].code is (
        Cam3DEditorDiagnosticCode.VALUE_ABOVE_MAXIMUM
    )


@pytest.mark.parametrize(
    "value",
    (True, Decimal("0.5"), Fraction(1, 2), object()),
)
def test_non_builtin_numeric_values_cannot_bypass_service(value: object) -> None:
    controller, _context_value, _tool, _assembly, _profile = _bound_controller()
    before = controller.state.parameters

    render = controller.replace_numeric_field(
        Cam3DEditorField.ALLOWANCE_MM,
        value,
    )

    assert render.parameters == before
    assert render.field_diagnostics[0][1][0].code is (
        Cam3DEditorDiagnosticCode.INVALID_NUMERIC_TYPE
    )


def test_rebind_is_idempotent_but_generation_document_and_source_changes_reset() -> None:
    controller, context, value, assembly, _profile = _bound_controller()
    controller.replace_numeric_text(Cam3DEditorField.ALLOWANCE_MM, "8")
    same = controller.bind(
        context,
        _selection(context),
        tools=(value,),
        assemblies=(assembly,),
    )
    assert same.parameters.allowance_mm == 8.0

    for changed in (
        replace(context, project_generation=context.project_generation + 1),
        replace(context, document_id=CadDocumentId("other-document")),
        replace(context, source_id=uuid4()),
    ):
        reset = controller.bind(
            changed,
            _selection(changed),
            tools=(value,),
            assemblies=(assembly,),
        )
        assert reset.parameters.allowance_mm == 0.0
        assert reset.selected_tool_assembly is None


def test_stale_live_context_and_read_only_are_inspectable_but_not_editable() -> None:
    controller, context, _tool, _assembly, _profile = _bound_controller()
    before = controller.state.parameters
    stale_context = replace(
        context,
        project_generation=context.project_generation + 1,
    )
    stale = controller.set_live_context(stale_context)

    assert stale.readiness is Cam3DEditorReadiness.STALE
    assert not stale.editable
    controller.replace_numeric_text(Cam3DEditorField.ALLOWANCE_MM, "9")
    assert controller.state.parameters == before

    read_only_context = _context(read_only=True)
    read_only_controller = Cam3DEditorBindingController()
    read_only = read_only_controller.bind(
        read_only_context,
        _selection(read_only_context),
    )
    assert read_only.readiness is Cam3DEditorReadiness.READ_ONLY
    assert not read_only.editable
    assert read_only.global_diagnostics


def test_diagnostic_mapping_is_complete_and_module_is_qt_persistence_free() -> None:
    assert set(DIAGNOSTIC_SOURCE_KEYS) == set(Cam3DEditorDiagnosticCode)
    source = Path(
        "src/hms_cadcam/ui/cam3d_editor_binding.py"
    ).read_text(encoding="utf-8")
    assert "PySide6" not in source
    assert "ProjectService" not in source
    assert "cam.persistence" not in source
    assert "sqlite" not in source.casefold()
