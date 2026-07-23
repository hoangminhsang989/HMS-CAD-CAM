"""Production schema for Stock BOX Facing."""

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


def build_facing_schema(context: FacingEditorContext) -> FunctionEditorSchema:
    """Build the deterministic Stock BOX Facing production schema."""
    schema = FunctionEditorSchema(
        "facing_production_9a5_1",
        FunctionEditorStrategyKey("facing_stock_box_9a5_1"),
        FunctionEditorSummary(
            context.operation_name,
            "Phay mặt 2.5D · Phôi dạng hộp",
            tool="Tool Assembly đã bind",
            geometry="Mặt trên Stock BOX",
            operation_status=context.operation.artifact_state.status.value.upper(),
        ),
        build_facing_sections(context, FacingEditorVariant.STOCK),
        facing_footer(),
    )
    validate_facing_schema_contract(schema, FacingEditorVariant.STOCK)
    return schema
