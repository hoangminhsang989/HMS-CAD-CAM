"""Schema validation and strategy registry for Function Editors."""

from __future__ import annotations

from dataclasses import dataclass

from hms_cadcam.ui.function_editor.model import (
    FunctionEditorField,
    FunctionEditorFooter,
    FunctionEditorSection,
    FunctionEditorStrategyKey,
    FunctionEditorSummary,
    ParameterDisclosureLevel,
    PresentationValue,
    require_stable_id,
)


@dataclass(frozen=True, slots=True)
class FunctionEditorSchema:
    """One deterministic presentation schema for a strategy or function."""

    editor_id: str
    strategy: FunctionEditorStrategyKey
    summary: FunctionEditorSummary
    sections: tuple[FunctionEditorSection, ...]
    footer: FunctionEditorFooter = FunctionEditorFooter()

    def __post_init__(self) -> None:
        require_stable_id(self.editor_id, label="editor ID")
        section_ids: set[str] = set()
        field_ids: set[str] = set()
        for section in self.sections:
            if section.section_id in section_ids:
                raise ValueError(f"Duplicate section ID: {section.section_id}")
            section_ids.add(section.section_id)
            for field in section.fields:
                if field.field_id in field_ids:
                    raise ValueError(f"Duplicate field ID: {field.field_id}")
                field_ids.add(field.field_id)
        for section in self.sections:
            dependency = section.applicable_when
            if dependency is not None and dependency.field_id not in field_ids:
                raise ValueError(
                    f"Unknown section applicability field: {dependency.field_id}"
                )
            for field in section.fields:
                dependency = field.applicable_when
                if dependency is not None and dependency.field_id not in field_ids:
                    raise ValueError(
                        f"Unknown field applicability field: {dependency.field_id}"
                    )
                for validator in field.validators:
                    if (
                        isinstance(validator.operand, str)
                        and validator.operand not in field_ids
                    ):
                        raise ValueError(
                            f"Unknown validation field: {validator.operand}"
                        )

    @property
    def ordered_sections(self) -> tuple[FunctionEditorSection, ...]:
        """Return a stable order independent of input collection behavior."""
        return tuple(sorted(self.sections, key=lambda item: (item.order, item.section_id)))

    @property
    def fields(self) -> tuple[FunctionEditorField, ...]:
        """Return all fields in deterministic section and field order."""
        return tuple(
            field
            for section in self.ordered_sections
            for field in sorted(section.fields, key=lambda item: (item.order, item.field_id))
        )

    def field(self, field_id: str) -> FunctionEditorField:
        """Resolve a field by stable ID."""
        return next(item for item in self.fields if item.field_id == field_id)

    def section(self, section_id: str) -> FunctionEditorSection:
        """Resolve a section by stable ID."""
        return next(
            item for item in self.ordered_sections if item.section_id == section_id
        )

    def section_for_field(self, field_id: str) -> FunctionEditorSection:
        """Resolve the owning section for diagnostic aggregation/focus."""
        return next(
            section
            for section in self.ordered_sections
            if any(item.field_id == field_id for item in section.fields)
        )

    def visible_sections(
        self,
        values: dict[str, PresentationValue],
        maximum_level: ParameterDisclosureLevel,
    ) -> tuple[FunctionEditorSection, ...]:
        """Return applicable sections allowed by the selected disclosure ceiling."""
        return tuple(
            section
            for section in self.ordered_sections
            if section.disclosure_level <= maximum_level
            and section.is_applicable(values)
            and any(
                field.disclosure_level <= maximum_level
                and field.is_applicable(values)
                for field in section.fields
            )
        )

    def visible_fields(
        self,
        section_id: str,
        values: dict[str, PresentationValue],
        maximum_level: ParameterDisclosureLevel,
    ) -> tuple[FunctionEditorField, ...]:
        """Return only applicable fields; irrelevant controls are not constructed."""
        section = self.section(section_id)
        if (
            section.disclosure_level > maximum_level
            or not section.is_applicable(values)
        ):
            return ()
        return tuple(
            field
            for field in sorted(section.fields, key=lambda item: (item.order, item.field_id))
            if field.disclosure_level <= maximum_level
            and field.is_applicable(values)
        )


class FunctionEditorRegistry:
    """Typed strategy-to-schema mapping with explicit migration boundaries."""

    def __init__(self) -> None:
        self._schemas: dict[FunctionEditorStrategyKey, FunctionEditorSchema] = {}

    def register(self, schema: FunctionEditorSchema) -> None:
        """Register exactly one schema per typed strategy key."""
        if schema.strategy in self._schemas:
            raise ValueError(f"Strategy already registered: {schema.strategy}")
        self._schemas[schema.strategy] = schema

    def unregister(self, strategy: FunctionEditorStrategyKey) -> None:
        """Remove a presentation migration without touching domain state."""
        self._schemas.pop(strategy, None)

    def resolve(
        self, strategy: FunctionEditorStrategyKey
    ) -> FunctionEditorSchema | None:
        """Return the migrated schema or ``None`` for the legacy adapter."""
        return self._schemas.get(strategy)

    @property
    def strategies(self) -> tuple[FunctionEditorStrategyKey, ...]:
        """Return registered keys deterministically for tests and diagnostics."""
        return tuple(sorted(self._schemas, key=lambda item: item.value))
