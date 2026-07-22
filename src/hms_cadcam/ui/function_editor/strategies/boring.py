"""Production Boring Function Editor schema."""

from hms_cadcam.ui.function_editor.schema import FunctionEditorSchema
from hms_cadcam.ui.function_editor.strategies.common_drilling import (
    DrillingFamilyEditorContext,
    DrillingFamilyEditorKind,
    build_drilling_family_schema,
)


def build_boring_schema(
    context: DrillingFamilyEditorContext,
) -> FunctionEditorSchema:
    """Build the Boring v1 production editor."""
    if context.kind is not DrillingFamilyEditorKind.BORING:
        raise ValueError("Boring schema requires a Boring context")
    return build_drilling_family_schema(context)
