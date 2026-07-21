"""Unified Function Editor framework introduced in Stage 9A.4."""

from hms_cadcam.ui.function_editor.host import FunctionEditorHost
from hms_cadcam.ui.function_editor.legacy_adapter import LegacyFunctionEditorAdapter
from hms_cadcam.ui.function_editor.model import (
    ApplicabilityOperator,
    FunctionEditorAction,
    FunctionEditorApplicability,
    FunctionEditorDiagnostic,
    FunctionEditorDiagnosticSeverity,
    FunctionEditorDraftStatus,
    FunctionEditorField,
    FunctionEditorFieldKind,
    FunctionEditorFooter,
    FunctionEditorPreviewRequest,
    FunctionEditorSection,
    FunctionEditorStrategyKey,
    FunctionEditorSummary,
    FunctionEditorValidationKind,
    FunctionEditorValidationRule,
    FunctionEditorValueSource,
    ParameterDisclosureLevel,
)
from hms_cadcam.ui.function_editor.reference import build_contour_reference_schema
from hms_cadcam.ui.function_editor.schema import (
    FunctionEditorRegistry,
    FunctionEditorSchema,
)
from hms_cadcam.ui.function_editor.sections import FunctionEditorSectionWidget
from hms_cadcam.ui.function_editor.fields import FunctionEditorFieldWidget
from hms_cadcam.ui.function_editor.state import (
    FUNCTION_EDITOR_STATE_VERSION,
    FunctionEditorDraftState,
    FunctionEditorStateStore,
    FunctionEditorUserState,
)
from hms_cadcam.ui.function_editor.widgets import (
    FunctionEditorDiagnosticView,
    FunctionEditorFooterWidget,
    FunctionEditorPage,
    FunctionEditorSummaryWidget,
)

__all__ = [
    "ApplicabilityOperator",
    "FUNCTION_EDITOR_STATE_VERSION",
    "FunctionEditorAction",
    "FunctionEditorApplicability",
    "FunctionEditorDiagnostic",
    "FunctionEditorDiagnosticSeverity",
    "FunctionEditorDiagnosticView",
    "FunctionEditorDraftState",
    "FunctionEditorDraftStatus",
    "FunctionEditorField",
    "FunctionEditorFieldKind",
    "FunctionEditorFieldWidget",
    "FunctionEditorFooter",
    "FunctionEditorFooterWidget",
    "FunctionEditorHost",
    "FunctionEditorPage",
    "FunctionEditorPreviewRequest",
    "FunctionEditorRegistry",
    "FunctionEditorSchema",
    "FunctionEditorSection",
    "FunctionEditorSectionWidget",
    "FunctionEditorStateStore",
    "FunctionEditorStrategyKey",
    "FunctionEditorSummary",
    "FunctionEditorSummaryWidget",
    "FunctionEditorUserState",
    "FunctionEditorValidationKind",
    "FunctionEditorValidationRule",
    "FunctionEditorValueSource",
    "LegacyFunctionEditorAdapter",
    "ParameterDisclosureLevel",
    "build_contour_reference_schema",
]
