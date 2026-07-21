"""Pure draft, validation, preview and user-preference state for Stage 9A.4."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import math
from types import MappingProxyType

from PySide6.QtCore import QSettings

from hms_cadcam.ui.function_editor.model import (
    FunctionEditorDiagnostic,
    FunctionEditorDiagnosticSeverity,
    FunctionEditorDraftStatus,
    FunctionEditorField,
    FunctionEditorFieldKind,
    FunctionEditorPreviewRequest,
    FunctionEditorValidationKind,
    ParameterDisclosureLevel,
    PresentationValue,
)
from hms_cadcam.ui.function_editor.schema import FunctionEditorSchema


FUNCTION_EDITOR_STATE_VERSION = 1
_SETTINGS_GROUP = "function_editor_9a4"


@dataclass(frozen=True, slots=True)
class FunctionEditorUserState:
    """User-only layout preference, never project or draft data."""

    disclosure_level: ParameterDisclosureLevel = ParameterDisclosureLevel.BASIC
    expanded_sections: tuple[str, ...] = ()
    has_expansion_state: bool = False
    help_visible: bool = False


class FunctionEditorStateStore:
    """Versioned QSettings adapter keyed by editor type and strategy only."""

    def __init__(self, settings: QSettings) -> None:
        self._settings = settings
        self._ensure_version()

    def _ensure_version(self) -> None:
        self._settings.beginGroup(_SETTINGS_GROUP)
        try:
            version = self._settings.value("version", type=int)
            if version != FUNCTION_EDITOR_STATE_VERSION:
                self._settings.remove("")
                self._settings.setValue("version", FUNCTION_EDITOR_STATE_VERSION)
        finally:
            self._settings.endGroup()
        self._settings.sync()

    @staticmethod
    def _key(schema: FunctionEditorSchema) -> str:
        return f"{schema.editor_id}/{schema.strategy.value}"

    def load(self, schema: FunctionEditorSchema) -> FunctionEditorUserState:
        """Load presentation-only state; malformed values fail to safe defaults."""
        self._settings.beginGroup(f"{_SETTINGS_GROUP}/{self._key(schema)}")
        try:
            try:
                level = ParameterDisclosureLevel.parse(
                    self._settings.value("disclosure", "BASIC")
                )
            except (TypeError, ValueError):
                level = ParameterDisclosureLevel.BASIC
            raw_expanded = self._settings.value("expanded", [])
            if isinstance(raw_expanded, str):
                raw_expanded = [raw_expanded]
            valid_sections = {item.section_id for item in schema.sections}
            expanded = tuple(
                sorted(
                    item
                    for item in raw_expanded
                    if isinstance(item, str) and item in valid_sections
                )
            )
            return FunctionEditorUserState(
                disclosure_level=level,
                expanded_sections=expanded,
                has_expansion_state=bool(
                    self._settings.value("has_expansion", False, type=bool)
                ),
                help_visible=bool(
                    self._settings.value("help_visible", False, type=bool)
                ),
            )
        finally:
            self._settings.endGroup()

    def save(
        self, schema: FunctionEditorSchema, state: FunctionEditorUserState
    ) -> None:
        """Persist only disclosure/collapse/help preferences."""
        self._settings.beginGroup(f"{_SETTINGS_GROUP}/{self._key(schema)}")
        try:
            self._settings.setValue("disclosure", state.disclosure_level.name)
            self._settings.setValue("expanded", list(state.expanded_sections))
            self._settings.setValue("has_expansion", state.has_expansion_state)
            self._settings.setValue("help_visible", state.help_visible)
        finally:
            self._settings.endGroup()
        self._settings.sync()


ApplyCallback = Callable[[Mapping[str, PresentationValue]], object]
ValidationCallback = Callable[
    [Mapping[str, PresentationValue]], tuple[FunctionEditorDiagnostic, ...]
]


class FunctionEditorDraftState:
    """Presentation draft separated from its last atomically applied snapshot."""

    def __init__(
        self,
        schema: FunctionEditorSchema,
        applied_values: Mapping[str, PresentationValue] | None = None,
        *,
        project_key: str = "reference-project",
        operation_key: str = "reference-operation",
        generation: int = 0,
        validation_callback: ValidationCallback | None = None,
    ) -> None:
        self.schema = schema
        defaults = {field.field_id: deepcopy(field.value) for field in schema.fields}
        supplied = dict(applied_values or {})
        unknown = set(supplied).difference(defaults)
        if unknown:
            raise KeyError(f"Unknown applied fields: {sorted(unknown)}")
        defaults.update({key: deepcopy(value) for key, value in supplied.items()})
        self._ensure_values_safe(defaults)
        self._applied: dict[str, PresentationValue] = defaults
        self._draft: dict[str, PresentationValue] = deepcopy(defaults)
        self._diagnostics: tuple[FunctionEditorDiagnostic, ...] = ()
        self._status = FunctionEditorDraftStatus.NO_CHANGES
        self.project_key = str(project_key)
        self.operation_key = str(operation_key)
        self.generation = int(generation)
        self._validation_callback = validation_callback
        self._last_apply_result: object = None

    @staticmethod
    def _ensure_values_safe(values: Mapping[str, PresentationValue]) -> None:
        for key, value in values.items():
            if callable(value):
                raise TypeError(f"Field {key!r} cannot contain callbacks")
            if isinstance(value, tuple):
                if any(
                    callable(item)
                    or not isinstance(item, (str, int, float, bool, type(None)))
                    for item in value
                ):
                    raise TypeError(f"Field {key!r} contains a non-primitive value")
            elif not isinstance(value, (str, int, float, bool, type(None))):
                raise TypeError(f"Field {key!r} contains a non-serializable value")

    @property
    def values(self) -> Mapping[str, PresentationValue]:
        """Read-only copy of the current UI draft."""
        return MappingProxyType(deepcopy(self._draft))

    @property
    def applied_values(self) -> Mapping[str, PresentationValue]:
        """Read-only copy of the last applied UI snapshot."""
        return MappingProxyType(deepcopy(self._applied))

    @property
    def diagnostics(self) -> tuple[FunctionEditorDiagnostic, ...]:
        return self._diagnostics

    @property
    def status(self) -> FunctionEditorDraftStatus:
        return self._status

    @property
    def is_dirty(self) -> bool:
        return self._draft != self._applied

    @property
    def last_apply_result(self) -> object:
        """Return the last successful application callback result."""
        return self._last_apply_result

    def edit(self, field_id: str, value: PresentationValue) -> None:
        """Update one draft primitive without mutating domain or applied state."""
        self.edit_many({field_id: value})

    def edit_many(self, changes: Mapping[str, PresentationValue]) -> None:
        """Atomically merge validated presentation primitives into the draft."""
        detached = dict(changes)
        known = {field.field_id for field in self.schema.fields}
        unknown = set(detached).difference(known)
        if unknown:
            raise KeyError(f"Unknown draft fields: {sorted(unknown)}")
        self._ensure_values_safe(detached)
        changed_ids = set(detached)
        candidate = deepcopy(self._draft)
        candidate.update(
            {field_id: deepcopy(value) for field_id, value in detached.items()}
        )
        self._draft = candidate
        self._diagnostics = tuple(
            item for item in self._diagnostics if item.field_id not in changed_ids
        )
        self._status = (
            FunctionEditorDraftStatus.MODIFIED
            if self.is_dirty
            else FunctionEditorDraftStatus.NO_CHANGES
        )

    def reset_field(self, field_id: str) -> None:
        """Restore one field to its last applied value."""
        self.edit(field_id, deepcopy(self._applied[field_id]))

    def reset_section(self, section_id: str) -> None:
        """Restore all fields in one section to the applied snapshot."""
        section = self.schema.section(section_id)
        for field in section.fields:
            self._draft[field.field_id] = deepcopy(self._applied[field.field_id])
        self._diagnostics = tuple(
            item for item in self._diagnostics if item.section_id != section_id
        )
        self._status = (
            FunctionEditorDraftStatus.MODIFIED
            if self.is_dirty
            else FunctionEditorDraftStatus.NO_CHANGES
        )

    def reset_draft(self) -> None:
        """Restore the complete draft to the last applied snapshot."""
        self._draft = deepcopy(self._applied)
        self._diagnostics = ()
        self._status = FunctionEditorDraftStatus.NO_CHANGES

    def set_diagnostics(
        self, diagnostics: tuple[FunctionEditorDiagnostic, ...]
    ) -> None:
        """Replace presentation diagnostics from a typed UI action boundary."""
        if not isinstance(diagnostics, tuple) or any(
            not isinstance(item, FunctionEditorDiagnostic) for item in diagnostics
        ):
            raise TypeError("Function Editor diagnostics are invalid")
        self._diagnostics = diagnostics
        self._status = (
            FunctionEditorDraftStatus.INVALID
            if any(
                item.severity is FunctionEditorDiagnosticSeverity.ERROR
                for item in diagnostics
            )
            else FunctionEditorDraftStatus.MODIFIED
            if self.is_dirty
            else FunctionEditorDraftStatus.NO_CHANGES
        )

    def restore_recommended_defaults(self, section_id: str | None = None) -> None:
        """Load declared recommendations into the draft without applying them."""
        fields = (
            self.schema.fields
            if section_id is None
            else self.schema.section(section_id).fields
        )
        for field in fields:
            if field.default is not None:
                self._draft[field.field_id] = deepcopy(field.default)
        self._diagnostics = ()
        self._status = (
            FunctionEditorDraftStatus.MODIFIED
            if self.is_dirty
            else FunctionEditorDraftStatus.NO_CHANGES
        )

    def applicable_field_ids(
        self, *, applied: bool = False
    ) -> tuple[str, ...]:
        """Return applicable IDs; hidden stale values are deliberately excluded."""
        values = self._applied if applied else self._draft
        return tuple(
            field.field_id for field in self.schema.fields if field.is_applicable(values)
        )

    def applicable_snapshot(
        self, *, applied: bool = False
    ) -> Mapping[str, PresentationValue]:
        """Create an immutable calculation/apply snapshot of applicable fields only."""
        values = self._applied if applied else self._draft
        keys = self.applicable_field_ids(applied=applied)
        return MappingProxyType({key: deepcopy(values[key]) for key in keys})

    def validate(self) -> tuple[FunctionEditorDiagnostic, ...]:
        """Validate the complete applicable draft without mutation or calculation."""
        diagnostics: list[FunctionEditorDiagnostic] = []
        applicable = set(self.applicable_field_ids())
        for field in self.schema.fields:
            if field.field_id not in applicable:
                continue
            section_id = self.schema.section_for_field(field.field_id).section_id
            value = self._draft[field.field_id]
            if field.required and (
                value is None or (isinstance(value, str) and not value.strip())
            ):
                diagnostics.append(
                    FunctionEditorDiagnostic(
                        code="field.required",
                        message=f"{field.label} là bắt buộc.",
                        severity=FunctionEditorDiagnosticSeverity.ERROR,
                        field_id=field.field_id,
                        section_id=section_id,
                    )
                )
                continue
            numeric = self._numeric_value(field, value)
            if field.kind is FunctionEditorFieldKind.NUMBER and numeric is None:
                diagnostics.append(
                    FunctionEditorDiagnostic(
                        code="field.invalid_number",
                        message=f"{field.label} phải là số hữu hạn.",
                        severity=FunctionEditorDiagnosticSeverity.ERROR,
                        field_id=field.field_id,
                        section_id=section_id,
                    )
                )
                continue
            for rule in field.validators:
                if numeric is None:
                    continue
                other: float | None = None
                if isinstance(rule.operand, str):
                    other_field = self.schema.field(rule.operand)
                    other = self._numeric_value(
                        other_field, self._draft[rule.operand]
                    )
                else:
                    other = float(rule.operand)
                failed = other is None or self._rule_failed(rule.kind, numeric, other)
                if failed:
                    diagnostics.append(
                        FunctionEditorDiagnostic(
                            code=rule.code,
                            message=rule.message,
                            severity=rule.severity,
                            field_id=field.field_id,
                            section_id=section_id,
                        )
                    )
        if not any(
            item.severity is FunctionEditorDiagnosticSeverity.ERROR
            for item in diagnostics
        ) and self._validation_callback is not None:
            try:
                external = self._validation_callback(self.applicable_snapshot())
                if not isinstance(external, tuple) or any(
                    not isinstance(item, FunctionEditorDiagnostic) for item in external
                ):
                    raise TypeError("Production validator returned invalid diagnostics")
                diagnostics.extend(external)
            except (KeyError, RuntimeError, TypeError, ValueError) as error:
                diagnostics.append(
                    FunctionEditorDiagnostic(
                        code="validation.failed",
                        message=str(error) or "Không thể kiểm tra bản nháp.",
                        severity=FunctionEditorDiagnosticSeverity.ERROR,
                    )
                )
        self._diagnostics = tuple(
            sorted(
                diagnostics,
                key=lambda item: (-int(item.severity), item.section_id or "", item.field_id or "", item.code),
            )
        )
        if any(
            item.severity is FunctionEditorDiagnosticSeverity.ERROR
            for item in self._diagnostics
        ):
            self._status = FunctionEditorDraftStatus.INVALID
        else:
            self._status = (
                FunctionEditorDraftStatus.MODIFIED
                if self.is_dirty
                else FunctionEditorDraftStatus.NO_CHANGES
            )
        return self._diagnostics

    @staticmethod
    def _numeric_value(
        field: FunctionEditorField, value: PresentationValue
    ) -> float | None:
        if field.kind is not FunctionEditorFieldKind.NUMBER:
            return None
        try:
            parsed = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
        return parsed if math.isfinite(parsed) else None

    @staticmethod
    def _rule_failed(
        kind: FunctionEditorValidationKind, value: float, operand: float
    ) -> bool:
        if kind is FunctionEditorValidationKind.MINIMUM:
            return value < operand
        if kind is FunctionEditorValidationKind.MAXIMUM:
            return value > operand
        if kind is FunctionEditorValidationKind.GREATER_THAN_FIELD:
            return value <= operand
        return value >= operand

    def apply(self, callback: ApplyCallback | None = None) -> bool:
        """Validate then atomically publish one immutable applicable snapshot."""
        previous_status = self._status
        if any(
            item.severity is FunctionEditorDiagnosticSeverity.ERROR
            for item in self.validate()
        ):
            return False
        snapshot = self.applicable_snapshot()
        self._status = FunctionEditorDraftStatus.APPLYING
        try:
            result = callback(snapshot) if callback is not None else True
            if result is False:
                raise RuntimeError("Application service rejected the draft")
        except (KeyError, RuntimeError, TypeError, ValueError) as error:
            self._diagnostics = self._diagnostics + (
                FunctionEditorDiagnostic(
                    code="apply.failed",
                    message=str(error) or "Không thể áp dụng bản nháp.",
                    severity=FunctionEditorDiagnosticSeverity.ERROR,
                ),
            )
            self._status = (
                FunctionEditorDraftStatus.INVALID
                if self.is_dirty
                else previous_status
            )
            return False
        self._last_apply_result = result
        for key, value in snapshot.items():
            self._applied[key] = deepcopy(value)
        self._draft = deepcopy(self._applied)
        self._diagnostics = ()
        self._status = FunctionEditorDraftStatus.APPLIED
        return True

    @property
    def can_calculate(self) -> bool:
        """Calculate is allowed only from current, valid, applied state."""
        if self._draft.get("enabled") is False:
            return False
        if self.is_dirty or self._status in {
            FunctionEditorDraftStatus.INVALID,
            FunctionEditorDraftStatus.APPLYING,
            FunctionEditorDraftStatus.STALE,
        }:
            return False
        old_status = self._status
        diagnostics = self.validate()
        if not diagnostics:
            self._status = old_status
        return not any(
            item.severity is FunctionEditorDiagnosticSeverity.ERROR
            for item in diagnostics
        )

    def calculation_snapshot(self) -> Mapping[str, PresentationValue]:
        """Return applied values only; an unapplied draft can never leak here."""
        if not self.can_calculate:
            raise RuntimeError("Calculate requires a current valid applied state")
        return self.applicable_snapshot(applied=True)

    def mark_stale(self) -> None:
        """Invalidate preview/calculation eligibility after an upstream change."""
        self._status = FunctionEditorDraftStatus.STALE
        self.generation += 1

    def preview_request(self) -> FunctionEditorPreviewRequest:
        """Capture a transient draft preview request with a stale-safe fingerprint."""
        snapshot = self.applicable_snapshot()
        encoded = json.dumps(
            dict(snapshot), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return FunctionEditorPreviewRequest(
            project_key=self.project_key,
            operation_key=self.operation_key,
            generation=self.generation,
            fingerprint=hashlib.sha256(encoded).hexdigest(),
            values=tuple(snapshot.items()),
        )

    def accepts_preview(self, request: FunctionEditorPreviewRequest) -> bool:
        """Reject callbacks from a previous project, selection or draft."""
        current = self.preview_request()
        return (
            request.project_key == current.project_key
            and request.operation_key == current.operation_key
            and request.generation == current.generation
            and request.fingerprint == current.fingerprint
        )
