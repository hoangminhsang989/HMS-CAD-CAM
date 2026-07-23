"""Typed production-session boundary for migrated Function Editors."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from hms_cadcam.ui.function_editor.model import (
    FunctionEditorDiagnostic,
    FunctionEditorPreviewRequest,
    PresentationValue,
)
from hms_cadcam.ui.function_editor.schema import FunctionEditorSchema


ProductionApplyCallback = Callable[[Mapping[str, PresentationValue]], object]
ProductionValidationCallback = Callable[
    [Mapping[str, PresentationValue]], tuple[FunctionEditorDiagnostic, ...]
]
ProductionPreviewCallback = Callable[[FunctionEditorPreviewRequest], object]
ProductionCalculateCallback = Callable[[Mapping[str, PresentationValue]], object]
ProductionFieldActionCallback = Callable[
    [str, Mapping[str, PresentationValue]], Mapping[str, PresentationValue] | None
]
ProductionDraftTransformCallback = Callable[
    [Mapping[str, PresentationValue]], Mapping[str, PresentationValue]
]


@dataclass(frozen=True, slots=True)
class FunctionEditorProductionSession:
    """One operation-bound editor session with no Qt or native CAD identity."""

    selection_key: tuple[str, str]
    schema: FunctionEditorSchema
    applied_values: tuple[tuple[str, PresentationValue], ...]
    project_key: str
    operation_key: str
    generation: int
    apply_callback: ProductionApplyCallback
    validation_callback: ProductionValidationCallback
    preview_callback: ProductionPreviewCallback
    calculate_callback: ProductionCalculateCallback
    field_action_callback: ProductionFieldActionCallback | None = None
    draft_transform_callback: ProductionDraftTransformCallback | None = None

    def __post_init__(self) -> None:
        if not self.selection_key[0] or not self.selection_key[1]:
            raise ValueError("Production editor selection identity is invalid")
        keys = tuple(key for key, _value in self.applied_values)
        if len(set(keys)) != len(keys):
            raise ValueError("Production editor applied field IDs must be unique")
        if set(keys) != {field.field_id for field in self.schema.fields}:
            raise ValueError("Production editor applied fields do not match its schema")
        if self.generation <= 0:
            raise ValueError("Production editor generation must be positive")

    def applied_mapping(self) -> dict[str, PresentationValue]:
        """Return a detached mapping suitable for a new draft state."""
        return dict(self.applied_values)
