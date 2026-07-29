"""Qt-free presentation binding for the Stage 9A.8 WP2B-B editor slice.

The controller is deliberately a small adapter around the committed
``Cam3DEditorApplicationService``.  It owns no project/session objects and
never mutates the immutable CAM snapshot supplied by the composition root.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Iterable, Mapping, TypeVar

from hms_cadcam.cam.application.cam3d_editor import (
    Cam3DEditorApplicationService,
    Cam3DEditorDiagnostic,
    Cam3DEditorDiagnosticCode,
    Cam3DEditorField,
    Cam3DEditorMutationResult,
    Cam3DEditorReadiness,
    Cam3DEditorState,
    Cam3DParameterDraft,
    Cam3DProjectContext,
    Cam3DToolAssemblyChoice,
    Cam3DToolProfileChoice,
)
from hms_cadcam.cam.application.cam3d_selection import Cam3DSelectionState
from hms_cadcam.cam.domain import (
    HolderDefinition,
    ToolAssembly,
    ToolAssemblyStatus,
    ToolDefinition,
    ToolProfileValidationState,
)


_ResourceT = TypeVar("_ResourceT")


@dataclass(frozen=True, slots=True)
class Cam3DEditorToolOption:
    """One deterministic, typed Tool Assembly combo-box option."""

    choice: Cam3DToolAssemblyChoice
    label: str
    selectable: bool
    current: bool

    def __post_init__(self) -> None:
        if not isinstance(self.choice, Cam3DToolAssemblyChoice):
            raise TypeError("choice must be Cam3DToolAssemblyChoice")
        if not isinstance(self.label, str) or not self.label.strip():
            raise ValueError("tool option label must not be empty")
        if type(self.selectable) is not bool or type(self.current) is not bool:
            raise TypeError("tool option flags must be bool")


@dataclass(frozen=True, slots=True)
class Cam3DEditorProfileOption:
    """One profile option projected from the selected Tool only."""

    choice: Cam3DToolProfileChoice
    label: str
    selectable: bool
    current: bool
    enabled: bool
    validation_state: ToolProfileValidationState

    def __post_init__(self) -> None:
        if not isinstance(self.choice, Cam3DToolProfileChoice):
            raise TypeError("choice must be Cam3DToolProfileChoice")
        if not isinstance(self.label, str) or not self.label.strip():
            raise ValueError("profile option label must not be empty")
        if any(type(value) is not bool for value in (self.selectable, self.current, self.enabled)):
            raise TypeError("profile option flags must be bool")
        if not isinstance(self.validation_state, ToolProfileValidationState):
            raise TypeError("validation_state must be ToolProfileValidationState")


@dataclass(frozen=True, slots=True)
class Cam3DEditorRenderState:
    """Complete immutable projection consumed by Qt."""

    context: Cam3DProjectContext
    readiness: Cam3DEditorReadiness
    tool_options: tuple[Cam3DEditorToolOption, ...]
    profile_options: tuple[Cam3DEditorProfileOption, ...]
    selected_tool_assembly: Cam3DToolAssemblyChoice | None
    selected_tool_profile: Cam3DToolProfileChoice | None
    parameters: Cam3DParameterDraft
    diagnostics: tuple[Cam3DEditorDiagnostic, ...]
    editable: bool

    def __post_init__(self) -> None:
        if not isinstance(self.context, Cam3DProjectContext):
            raise TypeError("context must be Cam3DProjectContext")
        if not isinstance(self.readiness, Cam3DEditorReadiness):
            raise TypeError("readiness must be Cam3DEditorReadiness")
        if not isinstance(self.parameters, Cam3DParameterDraft):
            raise TypeError("parameters must be Cam3DParameterDraft")
        if any(not isinstance(item, Cam3DEditorToolOption) for item in self.tool_options):
            raise TypeError("tool_options must be typed")
        if any(not isinstance(item, Cam3DEditorProfileOption) for item in self.profile_options):
            raise TypeError("profile_options must be typed")
        if any(not isinstance(item, Cam3DEditorDiagnostic) for item in self.diagnostics):
            raise TypeError("diagnostics must be typed")
        if type(self.editable) is not bool:
            raise TypeError("editable must be bool")
        object.__setattr__(self, "tool_options", tuple(self.tool_options))
        object.__setattr__(self, "profile_options", tuple(self.profile_options))
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics))
    @property
    def tool_assembly_options(self) -> tuple[Cam3DEditorToolOption, ...]:
        """Return the typed Tool Assembly options in deterministic order."""

        return self.tool_options

    @property
    def tool_profile_options(self) -> tuple[Cam3DEditorProfileOption, ...]:
        """Return the typed Tool Profile options in deterministic order."""

        return self.profile_options

    @property
    def field_diagnostics(
        self,
    ) -> tuple[tuple[Cam3DEditorField, tuple[Cam3DEditorDiagnostic, ...]], ...]:
        """Return numeric diagnostics grouped by their typed editor field."""

        return tuple(
            (
                field,
                tuple(item for item in self.diagnostics if item.field is field),
            )
            for field in Cam3DEditorField
            if any(item.field is field for item in self.diagnostics)
        )

    @property
    def global_diagnostics(self) -> tuple[Cam3DEditorDiagnostic, ...]:
        """Return lifecycle, selection and tooling diagnostics."""

        return tuple(item for item in self.diagnostics if item.field is None)


# Values are source keys, not visible diagnostic codes.
DIAGNOSTIC_SOURCE_KEYS: Mapping[Cam3DEditorDiagnosticCode, str] = MappingProxyType(
    {
        Cam3DEditorDiagnosticCode.PROJECT_CLOSED: "CAM 3D editor: project is closed",
        Cam3DEditorDiagnosticCode.PROJECT_SWITCHED: "CAM 3D editor: project or document changed",
        Cam3DEditorDiagnosticCode.PROJECT_GENERATION_STALE: "CAM 3D editor: project generation is stale",
        Cam3DEditorDiagnosticCode.READ_ONLY: "CAM 3D editor: project is read-only",
        Cam3DEditorDiagnosticCode.PART_MISSING: "CAM 3D editor: Part is required",
        Cam3DEditorDiagnosticCode.PART_INVALID: "CAM 3D editor: Part selection is invalid",
        Cam3DEditorDiagnosticCode.CHECK_INVALID: "CAM 3D editor: Check selection is invalid",
        Cam3DEditorDiagnosticCode.FIXTURE_INVALID: "CAM 3D editor: Fixture selection is invalid",
        Cam3DEditorDiagnosticCode.TOOL_ASSEMBLY_MISSING: "CAM 3D editor: select Tool Assembly",
        Cam3DEditorDiagnosticCode.TOOL_ASSEMBLY_NOT_OWNED: "CAM 3D editor: Tool Assembly ownership is invalid",
        Cam3DEditorDiagnosticCode.TOOL_ASSEMBLY_STALE: "CAM 3D editor: Tool Assembly is stale",
        Cam3DEditorDiagnosticCode.TOOL_ASSEMBLY_INCOMPATIBLE: "CAM 3D editor: Tool Assembly units are incompatible",
        Cam3DEditorDiagnosticCode.TOOL_PROFILE_MISSING: "CAM 3D editor: select Tool Profile",
        Cam3DEditorDiagnosticCode.TOOL_PROFILE_NOT_OWNED: "CAM 3D editor: Tool Profile ownership is invalid",
        Cam3DEditorDiagnosticCode.TOOL_PROFILE_STALE: "CAM 3D editor: Tool Profile is stale",
        Cam3DEditorDiagnosticCode.TOOL_PROFILE_DISABLED: "CAM 3D editor: Tool Profile is disabled",
        Cam3DEditorDiagnosticCode.TOOL_PROFILE_NEEDS_REVIEW: "CAM 3D editor: Tool Profile needs review",
        Cam3DEditorDiagnosticCode.TOOL_PROFILE_INCOMPATIBLE: "CAM 3D editor: Tool Profile is incompatible",
        Cam3DEditorDiagnosticCode.INVALID_NUMERIC_TYPE: "CAM 3D editor: numeric value is invalid",
        Cam3DEditorDiagnosticCode.NON_FINITE_VALUE: "CAM 3D editor: value must be finite",
        Cam3DEditorDiagnosticCode.VALUE_BELOW_MINIMUM: "CAM 3D editor: value is below minimum ({minimum})",
        Cam3DEditorDiagnosticCode.VALUE_ABOVE_MAXIMUM: "CAM 3D editor: value is above maximum ({maximum})",
    }
)



class Cam3DEditorBindingController:
    """Typed binding controller; all mutations are lifecycle-guarded."""

    def __init__(
        self,
        service: Cam3DEditorApplicationService | None = None,
    ) -> None:
        self._service = service or Cam3DEditorApplicationService.create_empty_draft()
        self._context = self._service.state.context
        self._assemblies: tuple[ToolAssembly, ...] = ()
        self._tools: tuple[ToolDefinition, ...] = ()
        self._holders: tuple[HolderDefinition, ...] = ()

    @property
    def service(self) -> Cam3DEditorApplicationService:
        return self._service

    @property
    def state(self) -> Cam3DEditorState:
        return self._service.state

    def reset(self) -> Cam3DEditorRenderState:
        self._service.reset()
        self._context = self._service.state.context
        self._assemblies = self._tools = self._holders = ()
        return self.render()

    def bind(
        self,
        context: Cam3DProjectContext,
        selection: Cam3DSelectionState,
        *,
        tools: Iterable[ToolDefinition] = (),
        holders: Iterable[HolderDefinition] = (),
        assemblies: Iterable[ToolAssembly] = (),
    ) -> Cam3DEditorRenderState:
        if not isinstance(context, Cam3DProjectContext):
            raise TypeError("context must be Cam3DProjectContext")
        if not isinstance(selection, Cam3DSelectionState):
            raise TypeError("selection must be Cam3DSelectionState")
        self._context = context
        self._tools = self._typed_resources(tools, ToolDefinition, "tools")
        self._holders = self._typed_resources(holders, HolderDefinition, "holders")
        self._assemblies = self._typed_resources(
            assemblies,
            ToolAssembly,
            "assemblies",
        )
        bound = self._service.state.context
        same_runtime_identity = (
            bound.is_open
            and context.is_open
            and bound.project_id == context.project_id
            and bound.project_generation == context.project_generation
            and bound.document_id == context.document_id
            and bound.source_id == context.source_id
        )
        if same_runtime_identity:
            if self._service.state.selection.read_only == selection.read_only:
                self._service.replace_selection(selection, live_context=context)
        else:
            self._service.bind(context, selection)
        return self.render()

    # Explicit alias used by composition roots that separate context/resource binding.
    bind_context = bind

    def set_live_context(self, context: Cam3DProjectContext) -> Cam3DEditorRenderState:
        """Update only the live lifecycle facts used by the next command."""
        if not isinstance(context, Cam3DProjectContext):
            raise TypeError("context must be Cam3DProjectContext")
        self._context = context
        return self.render()

    def set_selection(self, selection: Cam3DSelectionState) -> Cam3DEditorRenderState:
        result = self._service.replace_selection(selection, live_context=self._context)
        return self._after(result)

    def assign_tool_assembly(
        self,
        choice: Cam3DToolAssemblyChoice,
    ) -> Cam3DEditorRenderState:
        if not isinstance(choice, Cam3DToolAssemblyChoice):
            raise TypeError("choice must be Cam3DToolAssemblyChoice")
        option = next(
            (item for item in self._tool_options(self.state) if item.choice == choice),
            None,
        )
        if option is None or not option.selectable:
            return self.render()
        return self._after(
            self._service.assign_tool_assembly(choice, live_context=self._context)
        )

    def clear_tool_assembly(self) -> Cam3DEditorRenderState:
        return self._after(self._service.clear_tool_assembly(live_context=self._context))

    def assign_tool_profile(
        self,
        choice: Cam3DToolProfileChoice,
    ) -> Cam3DEditorRenderState:
        if not isinstance(choice, Cam3DToolProfileChoice):
            raise TypeError("choice must be Cam3DToolProfileChoice")
        option = next(
            (item for item in self._profile_options(self.state) if item.choice == choice),
            None,
        )
        if option is None or not option.selectable:
            return self.render()
        return self._after(
            self._service.assign_tool_profile(choice, live_context=self._context)
        )

    def clear_tool_profile(self) -> Cam3DEditorRenderState:
        return self._after(self._service.clear_tool_profile(live_context=self._context))

    def replace_numeric_field(self, field: Cam3DEditorField, value: object) -> Cam3DEditorRenderState:
        return self._after(self._service.replace_numeric_field(field, value, live_context=self._context))

    def replace_numeric_text(self, field: Cam3DEditorField, text: str) -> Cam3DEditorRenderState:
        if not isinstance(field, Cam3DEditorField):
            raise TypeError("field must be Cam3DEditorField")
        if not isinstance(text, str):
            raise TypeError("numeric text must be str")
        if not text.strip() and field in {
            Cam3DEditorField.CLEARANCE_Z_MM,
            Cam3DEditorField.RETRACT_Z_MM,
        }:
            return self.replace_numeric_field(field, None)
        if not text.strip():
            return self.replace_numeric_field(field, "")
        try:
            value: object = float(text.strip())
        except ValueError:
            value = text
        return self.replace_numeric_field(field, value)

    set_tool_assembly = assign_tool_assembly
    set_tool_profile = assign_tool_profile
    set_numeric_field = replace_numeric_field

    def render(self) -> Cam3DEditorRenderState:
        evaluation = self._service.evaluate(self._context)
        state = self._service.state
        return Cam3DEditorRenderState(
            context=state.context,
            readiness=evaluation.readiness,
            tool_options=self._tool_options(state),
            profile_options=self._profile_options(state),
            selected_tool_assembly=state.tool_assembly,
            selected_tool_profile=state.tool_profile,
            parameters=state.parameters,
            diagnostics=evaluation.diagnostics,
            editable=(
                state.context.is_open
                and not state.context.read_only
                and self._context == state.context
                and evaluation.readiness is not Cam3DEditorReadiness.READ_ONLY
            ),
        )

    def _after(self, result: Cam3DEditorMutationResult) -> Cam3DEditorRenderState:
        # Mutation diagnostics are persisted by WP2B-A for numeric/ownership
        # failures; evaluate adds lifecycle and selection diagnostics.
        return self.render()

    @staticmethod
    def _typed_resources(
        values: Iterable[_ResourceT],
        expected: type[_ResourceT],
        label: str,
    ) -> tuple[_ResourceT, ...]:
        materialized = tuple(values)
        if any(not isinstance(item, expected) for item in materialized):
            raise TypeError(f"{label} must contain only {expected.__name__}")
        return materialized

    def _tool_options(self, state: Cam3DEditorState) -> tuple[Cam3DEditorToolOption, ...]:
        tools = {item.tool_id: item for item in self._tools}
        holders = {item.holder_id: item for item in self._holders}
        options: list[Cam3DEditorToolOption] = []
        for assembly in sorted(self._assemblies, key=lambda item: (item.name.casefold(), str(item.assembly_id))):
            tool = tools.get(assembly.tool_id)
            if tool is None:
                continue
            holder = holders.get(assembly.holder_id) if assembly.holder_id is not None else None
            choice = Cam3DToolAssemblyChoice(assembly, tool, holder)
            selectable = choice.ownership_valid and choice.status is ToolAssemblyStatus.VALID
            options.append(Cam3DEditorToolOption(choice, assembly.name, selectable, choice == state.tool_assembly))
        return tuple(options)

    def _profile_options(self, state: Cam3DEditorState) -> tuple[Cam3DEditorProfileOption, ...]:
        assembly = state.tool_assembly
        if assembly is None:
            return ()
        options: list[Cam3DEditorProfileOption] = []
        for profile in sorted(assembly.tool.program_profiles, key=lambda item: (item.display_name.casefold(), str(item.profile_id))):
            choice = Cam3DToolProfileChoice(profile, assembly.tool)
            current = choice.current_for(assembly)
            selectable = (
                choice.owned_by(assembly)
                and current
                and choice.enabled
                and profile.validation_state not in {
                    ToolProfileValidationState.NEEDS_REVIEW,
                    ToolProfileValidationState.INCOMPATIBLE,
                }
            )
            options.append(
                Cam3DEditorProfileOption(
                    choice,
                    profile.display_name,
                    selectable,
                    choice == state.tool_profile,
                    choice.enabled,
                    profile.validation_state,
                )
            )
        return tuple(options)


__all__ = [
    "DIAGNOSTIC_SOURCE_KEYS",
    "Cam3DEditorBindingController",
    "Cam3DEditorProfileOption",
    "Cam3DEditorRenderState",
    "Cam3DEditorToolOption",
]
