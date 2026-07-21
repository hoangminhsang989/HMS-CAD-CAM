"""Typed presentation models for the Stage 9A.4 Function Editor."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
import re
from typing import TypeAlias


PresentationScalar: TypeAlias = str | int | float | bool | None
PresentationValue: TypeAlias = PresentationScalar | tuple[PresentationScalar, ...]

_STABLE_ID = re.compile(r"^[a-z][a-z0-9_.-]*$")


def require_stable_id(value: str, *, label: str) -> str:
    """Return a normalized stable ID or reject an ambiguous presentation key."""
    if not isinstance(value, str) or _STABLE_ID.fullmatch(value) is None:
        raise ValueError(
            f"{label} must match {_STABLE_ID.pattern!r}; received {value!r}"
        )
    return value


class ParameterDisclosureLevel(IntEnum):
    """Maximum presentation detail selected by the user."""

    BASIC = 0
    ADVANCED = 1
    EXPERT = 2

    @classmethod
    def parse(cls, value: object) -> ParameterDisclosureLevel:
        """Parse persisted names and integer values conservatively."""
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            normalized = value.strip().upper()
            if normalized in cls.__members__:
                return cls[normalized]
        if isinstance(value, int) and not isinstance(value, bool):
            return cls(value)
        raise ValueError(f"Unsupported disclosure level: {value!r}")


class FunctionEditorValueSource(StrEnum):
    """Provenance displayed beside inherited or recommended values."""

    USER = "user"
    TOOL = "tool"
    SETUP = "setup"
    STOCK = "stock"
    MACHINE = "machine"
    PROFILE = "profile"
    GEOMETRY = "geometry"
    PROJECT = "project"
    DEFAULT = "default"
    DERIVED = "derived"


class FunctionEditorValueConversion(StrEnum):
    """Declared UI-to-binding conversion; execution stays in the presenter."""

    IDENTITY = "identity"
    TEXT = "text"
    FLOAT = "float"
    BOOLEAN = "boolean"


class FunctionEditorResetBehavior(StrEnum):
    """Stable reset policy declared by a production field schema."""

    APPLIED = "applied"
    RECOMMENDED = "recommended"
    INHERITED = "inherited"


class FunctionEditorDiagnosticSeverity(IntEnum):
    """Severity ordering used for section and header aggregation."""

    INFO = 1
    WARNING = 2
    ERROR = 3


class FunctionEditorDraftStatus(StrEnum):
    """User-facing draft lifecycle without domain persistence state."""

    NO_CHANGES = "no_changes"
    MODIFIED = "modified"
    INVALID = "invalid"
    APPLYING = "applying"
    APPLIED = "applied"
    STALE = "stale"


class FunctionEditorAction(StrEnum):
    """Standard footer action identities."""

    RESET_DRAFT = "reset_draft"
    PREVIEW = "preview"
    VALIDATE = "validate"
    CALCULATE = "calculate"
    APPLY = "apply"
    CLOSE = "close"


class FunctionEditorFieldKind(StrEnum):
    """Finite widget choices supported by the framework."""

    TEXT = "text"
    NUMBER = "number"
    CHOICE = "choice"
    CHECKBOX = "checkbox"
    READ_ONLY = "read_only"


class ApplicabilityOperator(StrEnum):
    """Declarative operators; arbitrary expressions are intentionally excluded."""

    EQUALS = "equals"
    NOT_EQUALS = "not_equals"
    TRUTHY = "truthy"
    FALSY = "falsy"
    IN = "in"
    NOT_IN = "not_in"


class FunctionEditorValidationKind(StrEnum):
    """Declarative validation rules evaluated by the draft state."""

    MINIMUM = "minimum"
    MAXIMUM = "maximum"
    GREATER_THAN_FIELD = "greater_than_field"
    LESS_THAN_FIELD = "less_than_field"


@dataclass(frozen=True, slots=True)
class FunctionEditorStrategyKey:
    """Typed registry key that prevents accidental row/text based lookup."""

    value: str

    def __post_init__(self) -> None:
        require_stable_id(self.value, label="strategy key")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class FunctionEditorApplicability:
    """One safe dependency rule for field or section visibility."""

    field_id: str
    operator: ApplicabilityOperator
    operand: PresentationValue = None

    def __post_init__(self) -> None:
        require_stable_id(self.field_id, label="applicability field ID")
        if self.operator in {ApplicabilityOperator.IN, ApplicabilityOperator.NOT_IN}:
            if not isinstance(self.operand, tuple):
                raise TypeError("IN applicability operands must be tuples")

    def evaluate(self, values: dict[str, PresentationValue]) -> bool:
        """Evaluate the finite rule set against presentation primitives."""
        value = values.get(self.field_id)
        if self.operator is ApplicabilityOperator.EQUALS:
            return value == self.operand
        if self.operator is ApplicabilityOperator.NOT_EQUALS:
            return value != self.operand
        if self.operator is ApplicabilityOperator.TRUTHY:
            return bool(value)
        if self.operator is ApplicabilityOperator.FALSY:
            return not bool(value)
        assert isinstance(self.operand, tuple)
        if self.operator is ApplicabilityOperator.IN:
            return value in self.operand
        return value not in self.operand


@dataclass(frozen=True, slots=True)
class FunctionEditorValidationRule:
    """One typed rule with a stable diagnostic code and localized message."""

    kind: FunctionEditorValidationKind
    operand: float | str
    message: str
    code: str
    severity: FunctionEditorDiagnosticSeverity = (
        FunctionEditorDiagnosticSeverity.ERROR
    )

    def __post_init__(self) -> None:
        require_stable_id(self.code, label="validation code")
        if self.kind in {
            FunctionEditorValidationKind.GREATER_THAN_FIELD,
            FunctionEditorValidationKind.LESS_THAN_FIELD,
        }:
            if not isinstance(self.operand, str):
                raise TypeError("Cross-field validation operands must be field IDs")
            require_stable_id(self.operand, label="validation field ID")
        elif isinstance(self.operand, bool) or not isinstance(self.operand, (int, float)):
            raise TypeError("Numeric validation operands must be numbers")


@dataclass(frozen=True, slots=True)
class FunctionEditorDiagnostic:
    """A presentation diagnostic that can focus a field or section."""

    code: str
    message: str
    severity: FunctionEditorDiagnosticSeverity
    field_id: str | None = None
    section_id: str | None = None

    def __post_init__(self) -> None:
        require_stable_id(self.code, label="diagnostic code")
        if self.field_id is not None:
            require_stable_id(self.field_id, label="diagnostic field ID")
        if self.section_id is not None:
            require_stable_id(self.section_id, label="diagnostic section ID")


@dataclass(frozen=True, slots=True)
class FunctionEditorField:
    """Declarative field metadata; it never owns a Qt editor widget."""

    field_id: str
    label: str
    kind: FunctionEditorFieldKind = FunctionEditorFieldKind.TEXT
    value: PresentationValue = None
    unit: str = ""
    source: FunctionEditorValueSource = FunctionEditorValueSource.USER
    default: PresentationValue = None
    default_label: str = ""
    applicable_when: FunctionEditorApplicability | None = None
    required: bool = False
    disclosure_level: ParameterDisclosureLevel = ParameterDisclosureLevel.BASIC
    choices: tuple[PresentationScalar, ...] = ()
    validators: tuple[FunctionEditorValidationRule, ...] = ()
    tooltip: str = ""
    help_text: str = ""
    help_key: str = ""
    order: int = 0
    binding_key: str = ""
    conversion: FunctionEditorValueConversion = FunctionEditorValueConversion.IDENTITY
    reset_behavior: FunctionEditorResetBehavior = FunctionEditorResetBehavior.APPLIED
    choice_labels: tuple[tuple[PresentationScalar, str], ...] = ()
    action_id: str = ""
    action_label: str = ""

    def __post_init__(self) -> None:
        require_stable_id(self.field_id, label="field ID")
        if not self.label.strip():
            raise ValueError("Field label must not be empty")
        if self.kind is FunctionEditorFieldKind.CHOICE and not self.choices:
            raise ValueError(f"Choice field {self.field_id!r} requires choices")
        if self.help_key:
            require_stable_id(self.help_key, label="help key")
        if self.binding_key:
            require_stable_id(self.binding_key, label="binding key")
        if self.action_id:
            require_stable_id(self.action_id, label="field action ID")
            if not self.action_label.strip():
                raise ValueError("Field action label must not be empty")
        elif self.action_label:
            raise ValueError("Field action label requires an action ID")
        if self.choice_labels:
            if self.kind is not FunctionEditorFieldKind.CHOICE:
                raise ValueError("Choice labels require a choice field")
            labels = dict(self.choice_labels)
            if len(labels) != len(self.choice_labels) or set(labels) != set(self.choices):
                raise ValueError("Choice labels must map every choice exactly once")

    def is_applicable(self, values: dict[str, PresentationValue]) -> bool:
        """Return whether the field should exist in the current presentation."""
        return self.applicable_when is None or self.applicable_when.evaluate(values)


@dataclass(frozen=True, slots=True)
class FunctionEditorSection:
    """Ordered, collapsible group of fields in one disclosure tier."""

    section_id: str
    title: str
    fields: tuple[FunctionEditorField, ...] = ()
    summary: str = ""
    disclosure_level: ParameterDisclosureLevel = ParameterDisclosureLevel.BASIC
    default_expanded: bool = True
    help_text: str = ""
    help_key: str = ""
    enabled: bool = True
    applicable_when: FunctionEditorApplicability | None = None
    order: int = 0

    def __post_init__(self) -> None:
        require_stable_id(self.section_id, label="section ID")
        if not self.title.strip():
            raise ValueError("Section title must not be empty")
        if self.help_key:
            require_stable_id(self.help_key, label="help key")

    def is_applicable(self, values: dict[str, PresentationValue]) -> bool:
        """Return whether the section is relevant for the current draft."""
        return (
            self.enabled
            and (
                self.applicable_when is None
                or self.applicable_when.evaluate(values)
            )
        )


@dataclass(frozen=True, slots=True)
class FunctionEditorSummary:
    """Compact sticky header state."""

    title: str
    strategy: str
    tool: str = "Chưa chọn dao"
    geometry: str = "Chưa chọn hình học"
    operation_status: str = "DRAFT"
    reference_only: bool = False


@dataclass(frozen=True, slots=True)
class FunctionEditorFooter:
    """Contextual action policy for a Function Editor page."""

    actions: tuple[FunctionEditorAction, ...] = field(
        default_factory=lambda: tuple(FunctionEditorAction)
    )
    preview_supported: bool = False
    calculate_supported: bool = False
    apply_supported: bool = True


@dataclass(frozen=True, slots=True)
class FunctionEditorPreviewRequest:
    """Immutable, stale-checkable request passed to a preview adapter."""

    project_key: str
    operation_key: str
    generation: int
    fingerprint: str
    values: tuple[tuple[str, PresentationValue], ...]
