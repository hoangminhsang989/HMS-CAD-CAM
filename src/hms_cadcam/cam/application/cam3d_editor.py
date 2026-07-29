"""Pure CAM 3D editor draft and validation foundation for Stage 9A.8 WP2B-A.

The module deliberately has no Qt, persistence, database, CAD-kernel or worker
dependency.  It keeps a deterministic, immutable editor draft and exposes
typed application results that can be projected by a later UI slice.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
import math
from typing import Final
from uuid import UUID

from hms_cadcam.cad.models import CadDocumentId
from hms_cadcam.cam.application.cam3d_selection import (
    Cam3DSelectionIssue,
    Cam3DSelectionRole,
    Cam3DSelectionState,
    Cam3DSelectionStatus,
    Cam3DSelectionValidity,
)
from hms_cadcam.cam.domain import (
    DiagnosticSeverity,
    HolderDefinition,
    LengthUnit,
    ToolAssembly,
    ToolAssemblyEvidence,
    ToolAssemblyId,
    ToolAssemblyStatus,
    ToolDefinition,
    ToolProgramProfile,
    ToolProgramProfileId,
    ToolProfileValidationState,
    assess_tool_assembly,
)


class Cam3DEditorDiagnosticCode(StrEnum):
    """Stable, localization-neutral editor validation codes."""

    PROJECT_CLOSED = "cam3d.editor.project_closed"
    PROJECT_SWITCHED = "cam3d.editor.project_switched"
    PROJECT_GENERATION_STALE = "cam3d.editor.project_generation_stale"
    READ_ONLY = "cam3d.editor.read_only"
    PART_MISSING = "cam3d.editor.part_missing"
    PART_INVALID = "cam3d.editor.part_invalid"
    CHECK_INVALID = "cam3d.editor.check_invalid"
    FIXTURE_INVALID = "cam3d.editor.fixture_invalid"
    TOOL_ASSEMBLY_MISSING = "cam3d.editor.tool_assembly_missing"
    TOOL_ASSEMBLY_NOT_OWNED = "cam3d.editor.tool_assembly_not_owned"
    TOOL_ASSEMBLY_STALE = "cam3d.editor.tool_assembly_stale"
    TOOL_ASSEMBLY_INCOMPATIBLE = "cam3d.editor.tool_assembly_incompatible"
    TOOL_PROFILE_MISSING = "cam3d.editor.tool_profile_missing"
    TOOL_PROFILE_NOT_OWNED = "cam3d.editor.tool_profile_not_owned"
    TOOL_PROFILE_STALE = "cam3d.editor.tool_profile_stale"
    TOOL_PROFILE_DISABLED = "cam3d.editor.tool_profile_disabled"
    TOOL_PROFILE_NEEDS_REVIEW = "cam3d.editor.tool_profile_needs_review"
    TOOL_PROFILE_INCOMPATIBLE = "cam3d.editor.tool_profile_incompatible"
    INVALID_NUMERIC_TYPE = "cam3d.editor.invalid_numeric_type"
    NON_FINITE_VALUE = "cam3d.editor.non_finite_value"
    VALUE_BELOW_MINIMUM = "cam3d.editor.value_below_minimum"
    VALUE_ABOVE_MAXIMUM = "cam3d.editor.value_above_maximum"


class Cam3DEditorField(StrEnum):
    """Typed editor fields accepted by the application service."""

    TOLERANCE_MM = "tolerance_mm"
    ALLOWANCE_MM = "allowance_mm"
    CLEARANCE_Z_MM = "clearance_z_mm"
    RETRACT_Z_MM = "retract_z_mm"
    APPROACH_DISTANCE_MM = "approach_distance_mm"
    LINK_CLEARANCE_MM = "link_clearance_mm"


class Cam3DEditorReadiness(StrEnum):
    """Readiness states, explicitly distinct from calculation readiness."""

    EMPTY = "empty"
    PARTIAL = "partial"
    INVALID = "invalid"
    STALE = "stale"
    READ_ONLY = "read_only"
    READY_FOR_EDITOR_BINDING = "ready_for_editor_binding"


_TOLERANCE_MIN_MM: Final[float] = 1.0e-6
_TOLERANCE_MAX_MM: Final[float] = 10.0
_ALLOWANCE_MAX_MM: Final[float] = 1_000.0


@dataclass(frozen=True, slots=True)
class Cam3DEditorDiagnostic:
    """Structured diagnostic without visible text or raw exception strings."""

    code: Cam3DEditorDiagnosticCode
    severity: DiagnosticSeverity
    field: Cam3DEditorField | None = None
    parameters: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.code, Cam3DEditorDiagnosticCode):
            raise TypeError("diagnostic code must be Cam3DEditorDiagnosticCode")
        if not isinstance(self.severity, DiagnosticSeverity):
            raise TypeError("diagnostic severity must be DiagnosticSeverity")
        if self.field is not None and not isinstance(self.field, Cam3DEditorField):
            raise TypeError("diagnostic field must be Cam3DEditorField or None")
        if not isinstance(self.parameters, tuple) or any(
            not isinstance(item, tuple)
            or len(item) != 2
            or not isinstance(item[0], str)
            or not isinstance(item[1], str)
            for item in self.parameters
        ):
            raise TypeError("diagnostic parameters must be string pairs")
        if len({key for key, _value in self.parameters}) != len(self.parameters):
            raise ValueError("diagnostic parameter keys must be unique")
        object.__setattr__(self, "parameters", tuple(sorted(self.parameters)))


def _ordered_diagnostics(
    diagnostics: tuple[Cam3DEditorDiagnostic, ...] | list[Cam3DEditorDiagnostic],
) -> tuple[Cam3DEditorDiagnostic, ...]:
    """Return one canonical, duplicate-free diagnostic sequence."""

    if any(not isinstance(item, Cam3DEditorDiagnostic) for item in diagnostics):
        raise TypeError("diagnostics must contain Cam3DEditorDiagnostic values")
    return tuple(
        sorted(
            set(diagnostics),
            key=lambda item: (
                item.code.value,
                item.field.value if item.field is not None else "",
                item.severity.value,
                item.parameters,
            ),
        )
    )


@dataclass(frozen=True, slots=True)
class Cam3DProjectContext:
    """Immutable project/document identity used to guard every mutation."""

    project_id: UUID | None
    document_id: CadDocumentId | None
    source_id: UUID | None
    project_generation: int | None
    read_only: bool = False
    is_open: bool = True

    def __post_init__(self) -> None:
        if self.project_id is not None and not isinstance(self.project_id, UUID):
            raise TypeError("project_id must be a UUID or None")
        if self.project_id is not None and self.project_id.int == 0:
            raise ValueError("project_id must be a non-nil UUID")
        if self.document_id is not None and not isinstance(
            self.document_id, CadDocumentId
        ):
            raise TypeError("document_id must be CadDocumentId or None")
        if self.source_id is not None and not isinstance(self.source_id, UUID):
            raise TypeError("source_id must be a UUID or None")
        if self.source_id is not None and self.source_id.int == 0:
            raise ValueError("source_id must be a non-nil UUID")
        if self.project_generation is not None and (
            type(self.project_generation) is not int or self.project_generation <= 0
        ):
            raise ValueError("project_generation must be a positive int or None")
        if type(self.read_only) is not bool or type(self.is_open) is not bool:
            raise TypeError("read_only and is_open must be bool")
        if self.is_open and (
            self.project_id is None or self.project_generation is None
        ):
            raise ValueError("an open project requires project identity and generation")
        if not self.is_open and any(
            value is not None
            for value in (
                self.project_id,
                self.document_id,
                self.source_id,
                self.project_generation,
            )
        ):
            raise ValueError("a closed project cannot retain live identity")
        if not self.is_open and self.read_only:
            raise ValueError("a closed project cannot be read-only")

    @classmethod
    def closed(cls) -> "Cam3DProjectContext":
        return cls(None, None, None, None, is_open=False)

    @classmethod
    def open(
        cls,
        project_id: UUID,
        project_generation: int,
        *,
        document_id: CadDocumentId | None = None,
        source_id: UUID | None = None,
        read_only: bool = False,
    ) -> "Cam3DProjectContext":
        return cls(
            project_id,
            document_id,
            source_id,
            project_generation,
            read_only,
            True,
        )

    def same_identity(self, other: "Cam3DProjectContext") -> bool:
        if not isinstance(other, Cam3DProjectContext):
            raise TypeError("context must be Cam3DProjectContext")
        return self == other


def _selection_binding_diagnostics(
    context: Cam3DProjectContext,
    selection: Cam3DSelectionState,
) -> tuple[Cam3DEditorDiagnostic, ...]:
    """Validate that a WP2A selection belongs to exactly one editor context."""

    diagnostics: list[Cam3DEditorDiagnostic] = []
    if context.is_open != (selection.project_id is not None):
        diagnostics.append(_diagnostic(Cam3DEditorDiagnosticCode.PROJECT_SWITCHED))
    elif context.is_open:
        if selection.project_id != context.project_id:
            diagnostics.append(_diagnostic(Cam3DEditorDiagnosticCode.PROJECT_SWITCHED))
        elif selection.project_generation != context.project_generation:
            diagnostics.append(
                _diagnostic(Cam3DEditorDiagnosticCode.PROJECT_GENERATION_STALE)
            )
        if selection.read_only != context.read_only:
            diagnostics.append(_diagnostic(Cam3DEditorDiagnosticCode.READ_ONLY))
        for _role, items in selection.role_items:
            if context.document_id is not None and any(
                item.provenance.document_id != context.document_id for item in items
            ):
                diagnostics.append(
                    _diagnostic(Cam3DEditorDiagnosticCode.PROJECT_SWITCHED)
                )
            if context.source_id is not None and any(
                item.provenance.source_id != context.source_id for item in items
            ):
                diagnostics.append(
                    _diagnostic(Cam3DEditorDiagnosticCode.PROJECT_SWITCHED)
                )
    return _ordered_diagnostics(diagnostics)


@dataclass(frozen=True, slots=True)
class Cam3DToolAssemblyChoice:
    """Tool Assembly plus the current immutable Tool/Holder library snapshots."""

    assembly: ToolAssembly
    tool: ToolDefinition
    holder: HolderDefinition | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.assembly, ToolAssembly):
            raise TypeError("assembly must be ToolAssembly")
        if not isinstance(self.tool, ToolDefinition):
            raise TypeError("tool must be ToolDefinition")
        if self.holder is not None and not isinstance(self.holder, HolderDefinition):
            raise TypeError("holder must be HolderDefinition or None")

    @property
    def assembly_id(self) -> ToolAssemblyId:
        return self.assembly.assembly_id

    @property
    def ownership_valid(self) -> bool:
        if self.assembly.tool_id != self.tool.tool_id:
            return False
        if self.assembly.holder_id is None:
            return self.holder is None
        return self.holder is not None and self.assembly.holder_id == self.holder.holder_id

    @property
    def status(self) -> ToolAssemblyStatus:
        if not self.ownership_valid:
            return ToolAssemblyStatus.MISSING_TOOL
        evidence = ToolAssemblyEvidence(
            tool_exists=True,
            tool_revision=self.tool.revision,
            tool_fingerprint=self.tool.content_fingerprint,
            tool_unit=self.tool.unit,
            holder_exists=self.holder is not None,
            holder_revision=self.holder.revision if self.holder is not None else None,
            holder_fingerprint=(
                self.holder.content_fingerprint if self.holder is not None else None
            ),
            holder_unit=self.holder.unit if self.holder is not None else None,
        )
        return assess_tool_assembly(self.assembly, evidence)

    @property
    def current(self) -> bool:
        return self.ownership_valid and self.status is ToolAssemblyStatus.VALID


@dataclass(frozen=True, slots=True)
class Cam3DToolProfileChoice:
    """One immutable Tool Program Profile and its current Tool snapshot."""

    profile: ToolProgramProfile
    tool: ToolDefinition

    def __post_init__(self) -> None:
        if not isinstance(self.profile, ToolProgramProfile):
            raise TypeError("profile must be ToolProgramProfile")
        if not isinstance(self.tool, ToolDefinition):
            raise TypeError("tool must be ToolDefinition")

    @property
    def profile_id(self) -> ToolProgramProfileId:
        return self.profile.profile_id

    def owned_by(self, assembly: Cam3DToolAssemblyChoice) -> bool:
        if not isinstance(assembly, Cam3DToolAssemblyChoice):
            raise TypeError("assembly must be Cam3DToolAssemblyChoice")
        return (
            self.profile.tool_id == assembly.tool.tool_id
            and self.tool.tool_id == assembly.tool.tool_id
        )

    @property
    def current(self) -> bool:
        return (
            self.profile.tool_id == self.tool.tool_id
            and self.profile.source_tool_revision == self.tool.revision
            and self.profile.source_tool_fingerprint == self.tool.content_fingerprint
        )

    def current_for(self, assembly: Cam3DToolAssemblyChoice) -> bool:
        """Check Tool and optional Holder snapshot identity for one assembly."""

        if not isinstance(assembly, Cam3DToolAssemblyChoice):
            raise TypeError("assembly must be Cam3DToolAssemblyChoice")
        holder_fingerprint = (
            assembly.holder.content_fingerprint
            if assembly.holder is not None
            else None
        )
        return self.current and (
            self.profile.source_holder_fingerprint is None
            or self.profile.source_holder_fingerprint == holder_fingerprint
        )

    @property
    def enabled(self) -> bool:
        return self.profile.enabled


def _finite_number(
    value: object,
    field: Cam3DEditorField,
) -> tuple[float | None, Cam3DEditorDiagnostic | None]:
    if type(value) not in {int, float}:
        return None, Cam3DEditorDiagnostic(
            Cam3DEditorDiagnosticCode.INVALID_NUMERIC_TYPE,
            DiagnosticSeverity.ERROR,
            field,
        )
    try:
        normalized = float(value)
    except OverflowError:
        return None, Cam3DEditorDiagnostic(
            Cam3DEditorDiagnosticCode.NON_FINITE_VALUE,
            DiagnosticSeverity.ERROR,
            field,
        )
    if not math.isfinite(normalized):
        return None, Cam3DEditorDiagnostic(
            Cam3DEditorDiagnosticCode.NON_FINITE_VALUE,
            DiagnosticSeverity.ERROR,
            field,
        )
    return (0.0 if normalized == 0.0 else normalized), None


def _range_diagnostic(
    field: Cam3DEditorField,
    value: float,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> Cam3DEditorDiagnostic | None:
    if minimum is not None and value < minimum:
        return Cam3DEditorDiagnostic(
            Cam3DEditorDiagnosticCode.VALUE_BELOW_MINIMUM,
            DiagnosticSeverity.ERROR,
            field,
            (("minimum", str(minimum)),),
        )
    if maximum is not None and value > maximum:
        return Cam3DEditorDiagnostic(
            Cam3DEditorDiagnosticCode.VALUE_ABOVE_MAXIMUM,
            DiagnosticSeverity.ERROR,
            field,
            (("maximum", str(maximum)),),
        )
    return None


@dataclass(frozen=True, slots=True)
class Cam3DParameterDraft:
    """Validated numeric MM draft; invalid input never enters this value object."""

    tolerance_mm: float = 0.01
    allowance_mm: float = 0.0
    clearance_z_mm: float | None = None
    retract_z_mm: float | None = None
    approach_distance_mm: float = 2.0
    link_clearance_mm: float = 0.0
    unit: LengthUnit = LengthUnit.MM

    def __post_init__(self) -> None:
        if self.unit is not LengthUnit.MM:
            raise ValueError("CAM 3D editor draft supports MM only")
        for field in Cam3DEditorField:
            value = getattr(self, field.value)
            if value is None:
                if field not in {
                    Cam3DEditorField.CLEARANCE_Z_MM,
                    Cam3DEditorField.RETRACT_Z_MM,
                }:
                    raise ValueError(f"{field.value} cannot be None")
                continue
            normalized, diagnostic = _finite_number(value, field)
            if diagnostic is not None:
                if diagnostic.code is Cam3DEditorDiagnosticCode.INVALID_NUMERIC_TYPE:
                    raise TypeError(f"{field.value} has an invalid numeric type")
                raise ValueError(f"{field.value} is invalid")
            assert normalized is not None
            minimum = (
                _TOLERANCE_MIN_MM
                if field is Cam3DEditorField.TOLERANCE_MM
                else 0.0
                if field
                in {
                    Cam3DEditorField.ALLOWANCE_MM,
                    Cam3DEditorField.APPROACH_DISTANCE_MM,
                    Cam3DEditorField.LINK_CLEARANCE_MM,
                }
                else None
            )
            maximum = (
                _TOLERANCE_MAX_MM
                if field is Cam3DEditorField.TOLERANCE_MM
                else _ALLOWANCE_MAX_MM
                if field is Cam3DEditorField.ALLOWANCE_MM
                else None
            )
            diagnostic = _range_diagnostic(
                field,
                normalized,
                minimum=minimum,
                maximum=maximum,
            )
            if diagnostic is not None:
                raise ValueError(f"{field.value} is outside the editor contract")
            object.__setattr__(self, field.value, normalized)

    def replace_field(
        self,
        field: Cam3DEditorField,
        value: object,
    ) -> tuple["Cam3DParameterDraft", tuple[Cam3DEditorDiagnostic, ...]]:
        if not isinstance(field, Cam3DEditorField):
            raise TypeError("field must be Cam3DEditorField")
        if value is None and field in {
            Cam3DEditorField.CLEARANCE_Z_MM,
            Cam3DEditorField.RETRACT_Z_MM,
        }:
            return replace(self, **{field.value: None}), ()
        normalized, diagnostic = _finite_number(value, field)
        if diagnostic is not None:
            return self, (diagnostic,)
        assert normalized is not None
        minimum = (
            _TOLERANCE_MIN_MM
            if field is Cam3DEditorField.TOLERANCE_MM
            else 0.0
            if field
            in {
                Cam3DEditorField.ALLOWANCE_MM,
                Cam3DEditorField.APPROACH_DISTANCE_MM,
                Cam3DEditorField.LINK_CLEARANCE_MM,
            }
            else None
        )
        maximum = (
            _TOLERANCE_MAX_MM
            if field is Cam3DEditorField.TOLERANCE_MM
            else _ALLOWANCE_MAX_MM
            if field is Cam3DEditorField.ALLOWANCE_MM
            else None
        )
        diagnostic = _range_diagnostic(
            field,
            normalized,
            minimum=minimum,
            maximum=maximum,
        )
        if diagnostic is not None:
            return self, (diagnostic,)
        return replace(self, **{field.value: normalized}), ()


@dataclass(frozen=True, slots=True)
class Cam3DEditorEvaluation:
    """Read-only validation result used by a later presentation layer."""

    readiness: Cam3DEditorReadiness
    diagnostics: tuple[Cam3DEditorDiagnostic, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.readiness, Cam3DEditorReadiness):
            raise TypeError("readiness must be Cam3DEditorReadiness")
        if not isinstance(self.diagnostics, tuple) or any(
            not isinstance(item, Cam3DEditorDiagnostic) for item in self.diagnostics
        ):
            raise TypeError("diagnostics must be typed tuple")
        object.__setattr__(self, "diagnostics", _ordered_diagnostics(self.diagnostics))

    @property
    def valid(self) -> bool:
        return self.readiness is Cam3DEditorReadiness.READY_FOR_EDITOR_BINDING


@dataclass(frozen=True, slots=True)
class Cam3DEditorState:
    """Immutable editor state containing only typed, runtime-safe values."""

    context: Cam3DProjectContext
    selection: Cam3DSelectionState
    parameters: Cam3DParameterDraft = Cam3DParameterDraft()
    tool_assembly: Cam3DToolAssemblyChoice | None = None
    tool_profile: Cam3DToolProfileChoice | None = None
    diagnostics: tuple[Cam3DEditorDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.context, Cam3DProjectContext):
            raise TypeError("context must be Cam3DProjectContext")
        if not isinstance(self.selection, Cam3DSelectionState):
            raise TypeError("selection must be Cam3DSelectionState")
        if _selection_binding_diagnostics(self.context, self.selection):
            raise ValueError("context and selection binding are inconsistent")
        if not isinstance(self.parameters, Cam3DParameterDraft):
            raise TypeError("parameters must be Cam3DParameterDraft")
        if self.tool_assembly is not None and not isinstance(
            self.tool_assembly, Cam3DToolAssemblyChoice
        ):
            raise TypeError("tool_assembly must be a typed choice or None")
        if self.tool_profile is not None and not isinstance(
            self.tool_profile, Cam3DToolProfileChoice
        ):
            raise TypeError("tool_profile must be a typed choice or None")
        if not isinstance(self.diagnostics, tuple) or any(
            not isinstance(item, Cam3DEditorDiagnostic) for item in self.diagnostics
        ):
            raise TypeError("diagnostics must be typed tuple")
        object.__setattr__(self, "diagnostics", _ordered_diagnostics(self.diagnostics))

    def with_diagnostics(
        self,
        diagnostics: tuple[Cam3DEditorDiagnostic, ...],
    ) -> "Cam3DEditorState":
        return replace(self, diagnostics=diagnostics)


@dataclass(frozen=True, slots=True)
class Cam3DEditorMutationResult:
    """Typed result for all mutating application operations."""

    state: Cam3DEditorState
    accepted: bool
    diagnostics: tuple[Cam3DEditorDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.state, Cam3DEditorState):
            raise TypeError("mutation state must be Cam3DEditorState")
        if type(self.accepted) is not bool:
            raise TypeError("mutation accepted must be bool")
        if not isinstance(self.diagnostics, tuple) or any(
            not isinstance(item, Cam3DEditorDiagnostic) for item in self.diagnostics
        ):
            raise TypeError("mutation diagnostics must be typed tuple")
        object.__setattr__(self, "diagnostics", _ordered_diagnostics(self.diagnostics))


def _diagnostic(
    code: Cam3DEditorDiagnosticCode,
    field: Cam3DEditorField | None = None,
) -> Cam3DEditorDiagnostic:
    return Cam3DEditorDiagnostic(code, DiagnosticSeverity.ERROR, field)


class Cam3DEditorApplicationService:
    """Runtime-only editor draft service with explicit live-context guards."""

    def __init__(
        self,
        context: Cam3DProjectContext,
        selection: Cam3DSelectionState,
        *,
        parameters: Cam3DParameterDraft | None = None,
    ) -> None:
        if not isinstance(context, Cam3DProjectContext):
            raise TypeError("context must be Cam3DProjectContext")
        if not isinstance(selection, Cam3DSelectionState):
            raise TypeError("selection must be Cam3DSelectionState")
        if parameters is not None and not isinstance(parameters, Cam3DParameterDraft):
            raise TypeError("parameters must be Cam3DParameterDraft or None")
        self._state = Cam3DEditorState(
            context,
            selection,
            parameters or Cam3DParameterDraft(),
        )

    @classmethod
    def create_empty_draft(cls) -> "Cam3DEditorApplicationService":
        """Create the closed, project-neutral editor state."""

        return cls(Cam3DProjectContext.closed(), Cam3DSelectionState.closed())

    @property
    def state(self) -> Cam3DEditorState:
        return self._state

    def reset(self) -> Cam3DEditorState:
        self._state = Cam3DEditorState(
            Cam3DProjectContext.closed(),
            Cam3DSelectionState.closed(),
        )
        return self._state

    def bind(
        self,
        context: Cam3DProjectContext,
        selection: Cam3DSelectionState,
    ) -> Cam3DEditorState:
        if not isinstance(context, Cam3DProjectContext):
            raise TypeError("context must be Cam3DProjectContext")
        if not isinstance(selection, Cam3DSelectionState):
            raise TypeError("selection must be Cam3DSelectionState")
        self._state = Cam3DEditorState(context, selection)
        return self._state

    def evaluate(
        self,
        live_context: Cam3DProjectContext | None = None,
    ) -> Cam3DEditorEvaluation:
        state = self._state
        diagnostics = list(state.diagnostics)
        diagnostics.extend(self._lifecycle_diagnostics(live_context))
        if state.context.read_only or state.selection.read_only or (
            live_context is not None and live_context.read_only
        ):
            diagnostics.append(_diagnostic(Cam3DEditorDiagnosticCode.READ_ONLY))
        diagnostics.extend(_selection_binding_diagnostics(state.context, state.selection))
        diagnostics.extend(self._selection_diagnostics(state.selection))
        diagnostics.extend(self._tool_diagnostics(state))
        ordered = _ordered_diagnostics(diagnostics)
        readiness = self._readiness(ordered)
        return Cam3DEditorEvaluation(readiness, ordered)

    def replace_selection(
        self,
        selection: Cam3DSelectionState,
        *,
        live_context: Cam3DProjectContext | None,
    ) -> Cam3DEditorMutationResult:
        if not isinstance(selection, Cam3DSelectionState):
            raise TypeError("selection must be Cam3DSelectionState")
        guard = self._mutation_guard(live_context)
        if guard:
            return self._rejected(guard)
        binding = _selection_binding_diagnostics(self._state.context, selection)
        if binding:
            return self._rejected(binding)
        self._state = replace(self._state, selection=selection, diagnostics=())
        return Cam3DEditorMutationResult(self._state, True)

    def assign_tool_assembly(
        self,
        choice: Cam3DToolAssemblyChoice,
        *,
        live_context: Cam3DProjectContext | None,
    ) -> Cam3DEditorMutationResult:
        if not isinstance(choice, Cam3DToolAssemblyChoice):
            raise TypeError("choice must be Cam3DToolAssemblyChoice")
        guard = self._mutation_guard(live_context)
        if guard:
            return self._rejected(guard)
        profile = (
            self._state.tool_profile
            if self._state.tool_profile is not None
            and self._state.tool_profile.owned_by(choice)
            else None
        )
        self._state = replace(
            self._state,
            tool_assembly=choice,
            tool_profile=profile,
            diagnostics=(),
        )
        return Cam3DEditorMutationResult(self._state, True)

    def clear_tool_assembly(
        self,
        *,
        live_context: Cam3DProjectContext | None,
    ) -> Cam3DEditorMutationResult:
        guard = self._mutation_guard(live_context)
        if guard:
            return self._rejected(guard)
        self._state = replace(
            self._state,
            tool_assembly=None,
            tool_profile=None,
            diagnostics=(),
        )
        return Cam3DEditorMutationResult(self._state, True)

    def assign_tool_profile(
        self,
        choice: Cam3DToolProfileChoice,
        *,
        live_context: Cam3DProjectContext | None,
    ) -> Cam3DEditorMutationResult:
        if not isinstance(choice, Cam3DToolProfileChoice):
            raise TypeError("choice must be Cam3DToolProfileChoice")
        guard = self._mutation_guard(live_context)
        if guard:
            return self._rejected(guard)
        assembly = self._state.tool_assembly
        if assembly is None:
            return self._rejected(
                (_diagnostic(Cam3DEditorDiagnosticCode.TOOL_ASSEMBLY_MISSING),),
                persist=True,
            )
        if not choice.owned_by(assembly):
            return self._rejected(
                (_diagnostic(Cam3DEditorDiagnosticCode.TOOL_PROFILE_NOT_OWNED),),
                persist=True,
            )
        self._state = replace(self._state, tool_profile=choice, diagnostics=())
        return Cam3DEditorMutationResult(self._state, True)

    def clear_tool_profile(
        self,
        *,
        live_context: Cam3DProjectContext | None,
    ) -> Cam3DEditorMutationResult:
        guard = self._mutation_guard(live_context)
        if guard:
            return self._rejected(guard)
        self._state = replace(self._state, tool_profile=None, diagnostics=())
        return Cam3DEditorMutationResult(self._state, True)

    def replace_numeric_field(
        self,
        field: Cam3DEditorField,
        value: object,
        *,
        live_context: Cam3DProjectContext | None,
    ) -> Cam3DEditorMutationResult:
        if not isinstance(field, Cam3DEditorField):
            raise TypeError("field must be Cam3DEditorField")
        guard = self._mutation_guard(live_context)
        if guard:
            return self._rejected(guard)
        parameters, diagnostics = self._state.parameters.replace_field(field, value)
        if diagnostics:
            return self._rejected(diagnostics, persist=True)
        self._state = replace(self._state, parameters=parameters, diagnostics=())
        return Cam3DEditorMutationResult(self._state, True)

    def _rejected(
        self,
        diagnostics: tuple[Cam3DEditorDiagnostic, ...],
        *,
        persist: bool = False,
    ) -> Cam3DEditorMutationResult:
        ordered = _ordered_diagnostics(diagnostics)
        state = self._state.with_diagnostics(ordered) if persist else self._state
        if persist:
            self._state = state
        return Cam3DEditorMutationResult(state, False, ordered)

    def _mutation_guard(
        self,
        live_context: Cam3DProjectContext | None,
    ) -> tuple[Cam3DEditorDiagnostic, ...]:
        diagnostics = self._lifecycle_diagnostics(live_context)
        if diagnostics:
            return diagnostics
        assert live_context is not None
        if self._state.context.read_only or live_context.read_only:
            return (_diagnostic(Cam3DEditorDiagnosticCode.READ_ONLY),)
        return ()

    def _lifecycle_diagnostics(
        self,
        live_context: Cam3DProjectContext | None,
    ) -> tuple[Cam3DEditorDiagnostic, ...]:
        if live_context is not None and not isinstance(
            live_context, Cam3DProjectContext
        ):
            raise TypeError("live_context must be Cam3DProjectContext or None")
        if not self._state.context.is_open:
            return (_diagnostic(Cam3DEditorDiagnosticCode.PROJECT_CLOSED),)
        if live_context is None or not live_context.is_open:
            return (_diagnostic(Cam3DEditorDiagnosticCode.PROJECT_CLOSED),)
        if live_context.project_id != self._state.context.project_id:
            return (_diagnostic(Cam3DEditorDiagnosticCode.PROJECT_SWITCHED),)
        if live_context.project_generation != self._state.context.project_generation:
            return (_diagnostic(Cam3DEditorDiagnosticCode.PROJECT_GENERATION_STALE),)
        if live_context.document_id != self._state.context.document_id or (
            live_context.source_id != self._state.context.source_id
        ):
            return (_diagnostic(Cam3DEditorDiagnosticCode.PROJECT_SWITCHED),)
        return ()

    @staticmethod
    def _selection_diagnostics(
        selection: Cam3DSelectionState,
    ) -> tuple[Cam3DEditorDiagnostic, ...]:
        diagnostics: list[Cam3DEditorDiagnostic] = []
        if not selection.part:
            diagnostics.append(_diagnostic(Cam3DEditorDiagnosticCode.PART_MISSING))
        issue_code = {
            Cam3DSelectionIssue.NO_PROJECT: Cam3DEditorDiagnosticCode.PROJECT_CLOSED,
            Cam3DSelectionIssue.FOREIGN_DOCUMENT: Cam3DEditorDiagnosticCode.PROJECT_SWITCHED,
            Cam3DSelectionIssue.FOREIGN_PROJECT: Cam3DEditorDiagnosticCode.PROJECT_SWITCHED,
            Cam3DSelectionIssue.STALE_PROJECT: Cam3DEditorDiagnosticCode.PROJECT_GENERATION_STALE,
            Cam3DSelectionIssue.STALE_IDENTITY: Cam3DEditorDiagnosticCode.PROJECT_GENERATION_STALE,
            Cam3DSelectionIssue.READ_ONLY: Cam3DEditorDiagnosticCode.READ_ONLY,
            Cam3DSelectionIssue.INVALID_GEOMETRY_KIND: Cam3DEditorDiagnosticCode.PART_INVALID,
            Cam3DSelectionIssue.DUPLICATE_SURFACE: Cam3DEditorDiagnosticCode.PART_INVALID,
            Cam3DSelectionIssue.SOURCE_UNAVAILABLE: Cam3DEditorDiagnosticCode.PART_INVALID,
            Cam3DSelectionIssue.NO_SELECTION: Cam3DEditorDiagnosticCode.PART_MISSING,
        }.get(selection.issue)
        if issue_code is not None:
            diagnostics.append(_diagnostic(issue_code))
        for role, items in selection.role_items:
            if any(item.validity is Cam3DSelectionValidity.STALE for item in items):
                diagnostics.append(
                    _diagnostic(Cam3DEditorDiagnosticCode.PROJECT_GENERATION_STALE)
                )
            if any(item.validity is Cam3DSelectionValidity.INVALID for item in items):
                code = {
                    Cam3DSelectionRole.PART: Cam3DEditorDiagnosticCode.PART_INVALID,
                    Cam3DSelectionRole.CHECK: Cam3DEditorDiagnosticCode.CHECK_INVALID,
                    Cam3DSelectionRole.FIXTURE: Cam3DEditorDiagnosticCode.FIXTURE_INVALID,
                }[role]
                diagnostics.append(_diagnostic(code))
        if selection.status is Cam3DSelectionStatus.INVALID and issue_code is None:
            diagnostics.append(_diagnostic(Cam3DEditorDiagnosticCode.PART_INVALID))
        if selection.status is Cam3DSelectionStatus.STALE:
            diagnostics.append(
                _diagnostic(Cam3DEditorDiagnosticCode.PROJECT_GENERATION_STALE)
            )
        return _ordered_diagnostics(diagnostics)

    @staticmethod
    def _tool_diagnostics(
        state: Cam3DEditorState,
    ) -> tuple[Cam3DEditorDiagnostic, ...]:
        diagnostics: list[Cam3DEditorDiagnostic] = []
        assembly = state.tool_assembly
        if assembly is None:
            diagnostics.append(_diagnostic(Cam3DEditorDiagnosticCode.TOOL_ASSEMBLY_MISSING))
        elif not assembly.ownership_valid:
            diagnostics.append(
                _diagnostic(Cam3DEditorDiagnosticCode.TOOL_ASSEMBLY_NOT_OWNED)
            )
        elif assembly.status is ToolAssemblyStatus.INCOMPATIBLE_UNIT:
            diagnostics.append(
                _diagnostic(Cam3DEditorDiagnosticCode.TOOL_ASSEMBLY_INCOMPATIBLE)
            )
        elif assembly.status is not ToolAssemblyStatus.VALID:
            diagnostics.append(
                _diagnostic(Cam3DEditorDiagnosticCode.TOOL_ASSEMBLY_STALE)
            )
        profile = state.tool_profile
        if profile is None:
            diagnostics.append(_diagnostic(Cam3DEditorDiagnosticCode.TOOL_PROFILE_MISSING))
        elif assembly is None or not profile.owned_by(assembly):
            diagnostics.append(
                _diagnostic(Cam3DEditorDiagnosticCode.TOOL_PROFILE_NOT_OWNED)
            )
        elif not profile.current_for(assembly):
            diagnostics.append(
                _diagnostic(Cam3DEditorDiagnosticCode.TOOL_PROFILE_STALE)
            )
        elif not profile.enabled:
            diagnostics.append(
                _diagnostic(Cam3DEditorDiagnosticCode.TOOL_PROFILE_DISABLED)
            )
        elif profile.profile.validation_state is ToolProfileValidationState.NEEDS_REVIEW:
            diagnostics.append(
                _diagnostic(Cam3DEditorDiagnosticCode.TOOL_PROFILE_NEEDS_REVIEW)
            )
        elif profile.profile.validation_state is ToolProfileValidationState.INCOMPATIBLE:
            diagnostics.append(
                _diagnostic(Cam3DEditorDiagnosticCode.TOOL_PROFILE_INCOMPATIBLE)
            )
        return _ordered_diagnostics(diagnostics)

    @staticmethod
    def _readiness(
        diagnostics: tuple[Cam3DEditorDiagnostic, ...],
    ) -> Cam3DEditorReadiness:
        codes = {item.code for item in diagnostics}
        if Cam3DEditorDiagnosticCode.READ_ONLY in codes:
            return Cam3DEditorReadiness.READ_ONLY
        if Cam3DEditorDiagnosticCode.PROJECT_CLOSED in codes:
            return Cam3DEditorReadiness.EMPTY
        if {
            Cam3DEditorDiagnosticCode.PROJECT_SWITCHED,
            Cam3DEditorDiagnosticCode.PROJECT_GENERATION_STALE,
            Cam3DEditorDiagnosticCode.TOOL_ASSEMBLY_STALE,
            Cam3DEditorDiagnosticCode.TOOL_PROFILE_STALE,
            Cam3DEditorDiagnosticCode.TOOL_PROFILE_NEEDS_REVIEW,
        } & codes:
            return Cam3DEditorReadiness.STALE
        if {
            Cam3DEditorDiagnosticCode.PART_INVALID,
            Cam3DEditorDiagnosticCode.CHECK_INVALID,
            Cam3DEditorDiagnosticCode.FIXTURE_INVALID,
            Cam3DEditorDiagnosticCode.TOOL_ASSEMBLY_NOT_OWNED,
            Cam3DEditorDiagnosticCode.TOOL_ASSEMBLY_INCOMPATIBLE,
            Cam3DEditorDiagnosticCode.TOOL_PROFILE_NOT_OWNED,
            Cam3DEditorDiagnosticCode.TOOL_PROFILE_DISABLED,
            Cam3DEditorDiagnosticCode.TOOL_PROFILE_INCOMPATIBLE,
            Cam3DEditorDiagnosticCode.INVALID_NUMERIC_TYPE,
            Cam3DEditorDiagnosticCode.NON_FINITE_VALUE,
            Cam3DEditorDiagnosticCode.VALUE_BELOW_MINIMUM,
            Cam3DEditorDiagnosticCode.VALUE_ABOVE_MAXIMUM,
        } & codes:
            return Cam3DEditorReadiness.INVALID
        if diagnostics:
            return Cam3DEditorReadiness.PARTIAL
        return Cam3DEditorReadiness.READY_FOR_EDITOR_BINDING


__all__ = [
    "Cam3DEditorApplicationService",
    "Cam3DEditorDiagnostic",
    "Cam3DEditorDiagnosticCode",
    "Cam3DEditorEvaluation",
    "Cam3DEditorField",
    "Cam3DEditorReadiness",
    "Cam3DEditorState",
    "Cam3DEditorMutationResult",
    "Cam3DParameterDraft",
    "Cam3DProjectContext",
    "Cam3DToolAssemblyChoice",
    "Cam3DToolProfileChoice",
]
