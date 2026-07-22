"""Production Tapping Function Editor schema."""

from hms_cadcam.ui.function_editor.schema import FunctionEditorSchema
from hms_cadcam.ui.function_editor.strategies.common_drilling import (
    DrillingFamilyEditorContext,
    DrillingFamilyEditorKind,
    build_drilling_family_schema,
)


def build_tapping_schema(
    context: DrillingFamilyEditorContext,
) -> FunctionEditorSchema:
    """Build the Tapping v1 production editor."""
    if context.kind is not DrillingFamilyEditorKind.TAPPING:
        raise ValueError("Tapping schema requires a Tapping context")
    return build_drilling_family_schema(context)
