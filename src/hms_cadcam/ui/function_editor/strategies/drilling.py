"""Production Drilling Function Editor schema."""

from hms_cadcam.ui.function_editor.schema import FunctionEditorSchema
from hms_cadcam.ui.function_editor.strategies.common_drilling import (
    DrillingFamilyEditorContext,
    DrillingFamilyEditorKind,
    build_drilling_family_schema,
)


def build_drilling_schema(
    context: DrillingFamilyEditorContext,
) -> FunctionEditorSchema:
    """Build the Drilling v1 production editor."""
    if context.kind is not DrillingFamilyEditorKind.DRILLING:
        raise ValueError("Drilling schema requires a Drilling context")
    return build_drilling_family_schema(context)
