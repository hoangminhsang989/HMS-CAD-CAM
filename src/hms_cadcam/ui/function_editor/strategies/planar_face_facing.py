"""Production schema for persistent planar-FACE Facing."""

from hms_cadcam.ui.function_editor.model import (
    FunctionEditorStrategyKey,
    FunctionEditorSummary,
)
from hms_cadcam.ui.function_editor.schema import FunctionEditorSchema
from hms_cadcam.ui.function_editor.strategies.common_milling import (
    FacingEditorContext,
    FacingEditorVariant,
    build_facing_sections,
    facing_footer,
    validate_facing_schema_contract,
)


def build_planar_face_facing_schema(
    context: FacingEditorContext,
) -> FunctionEditorSchema:
    """Build the deterministic Planar Face Facing production schema."""
    geometry = (
        "Planar FACE · RESOLVED"
        if context.geometry_reference is not None and context.geometry_resolved
        else "Planar FACE · NEEDS INPUT"
    )
    schema = FunctionEditorSchema(
        "planar_face_facing_production_9a5_1",
        FunctionEditorStrategyKey("planar_face_facing_9a5_1"),
        FunctionEditorSummary(
            context.operation_name,
            "Planar Face Facing 2.5D",
            tool="Tool Assembly đã bind",
            geometry=geometry,
            operation_status=context.operation.artifact_state.status.value.upper(),
        ),
        build_facing_sections(context, FacingEditorVariant.PLANAR_FACE),
        facing_footer(),
    )
    validate_facing_schema_contract(schema, FacingEditorVariant.PLANAR_FACE)
    return schema
