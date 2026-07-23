"""Shared visual and sizing tokens for the Stage 9A workspace shell."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QRect, QSize


OPERATION_MANAGER_MIN_WIDTH = 260
OPERATION_MANAGER_DEFAULT_WIDTH = 300
OPERATION_MANAGER_MAX_WIDTH = 340

FUNCTION_EDITOR_MIN_WIDTH = 300
FUNCTION_EDITOR_DEFAULT_WIDTH = 460
FUNCTION_EDITOR_MAX_WIDTH = 520

SECONDARY_PANEL_MIN_WIDTH = 360
SECONDARY_PANEL_DEFAULT_WIDTH = 460
SECONDARY_PANEL_MAX_WIDTH = 620

DIAGNOSTICS_DEFAULT_HEIGHT = 118
DIAGNOSTICS_MAX_HEIGHT = 280

VIEWPORT_MIN_WIDTH = 520
VIEWPORT_MIN_HEIGHT = 360


@dataclass(frozen=True, slots=True)
class CAMPopupMetrics:
    """Logical-pixel metrics for the shared compact CAM popup surface.

    Qt already exposes screen work areas in device-independent logical pixels.
    Keeping layout metrics logical avoids applying Windows display scaling twice;
    point-sized native fonts remain responsible for 100/125/150% text scaling.
    """

    popup_width: int
    popup_height: int
    display_scale_factor: float
    minimum_width: int
    minimum_height: int
    maximum_width: int
    maximum_height: int
    content_margin: int
    section_spacing: int
    row_spacing: int
    label_spacing: int
    control_height: int
    button_height: int
    compact_button_height: int
    toolbar_height: int
    footer_height: int
    regular_font_point_size: float
    heading_font_point_size: float
    operation_title_font_point_size: float
    status_font_point_size: float
    table_row_height: int
    tree_row_height: int
    illustration_collapsed_height: int
    illustration_expanded_height: int
    illustration_canvas_collapsed_height: int
    illustration_canvas_expanded_height: int
    field_reflow_width: int
    grid_min_column_width: int
    grid_gap: int
    illustration_auto_collapse_height: int
    child_margin: int
    tool_selector_size: QSize
    diagnostics_size: QSize
    illustration_dialog_size: QSize


class CAMPopupDensityPolicy:
    """Responsive density policy shared by all nine production CAM editors."""

    def metrics_for(
        self,
        available: QRect | QSize,
        *,
        native_font_point_size: float = 9.0,
        display_scale_factor: float = 1.0,
    ) -> CAMPopupMetrics:
        """Return compact metrics for one monitor's logical work area.

        ``display_scale_factor`` is retained as audit evidence. It is not
        multiplied into logical dimensions because Qt has already converted the
        work area and point fonts for the monitor.
        """
        width = max(1, available.width())
        height = max(1, available.height())
        if width <= 1450:
            preferred_width = round(min(600, max(540, width * 0.43)))
            preferred_height = round(min(650, max(560, height * 0.82)))
            maximum_width = round(width * 0.45)
            maximum_height = round(height * 0.84)
        elif width <= 1750:
            preferred_width = round(min(660, max(580, width * 0.39)))
            preferred_height = round(min(720, max(620, height * 0.78)))
            maximum_width = round(width * 0.43)
            maximum_height = round(height * 0.82)
        else:
            preferred_width = round(min(700, max(620, width * 0.35)))
            preferred_height = round(min(800, max(680, height * 0.72)))
            maximum_width = round(width * 0.42)
            maximum_height = round(height * 0.80)

        maximum_width = min(width, max(1, maximum_width))
        maximum_height = min(height, max(1, maximum_height))
        if display_scale_factor > 1.0 and height <= 720:
            preferred_height = maximum_height
        minimum_width = min(520, maximum_width)
        minimum_height = min(480, maximum_height)
        popup_width = min(maximum_width, max(minimum_width, preferred_width))
        popup_height = min(maximum_height, max(minimum_height, preferred_height))
        regular_font = min(10.0, max(9.0, native_font_point_size))
        if width <= 1450:
            illustration_height = 110
        elif width <= 1750:
            illustration_height = min(140, max(125, round(popup_height * 0.19)))
        else:
            illustration_height = min(150, max(130, round(popup_height * 0.18)))
        expanded_illustration_height = min(
            max(illustration_height, 220), round(maximum_height * 0.38)
        )
        child_width = min(maximum_width, max(380, round(popup_width * 0.72)))
        child_height = min(maximum_height, max(300, round(popup_height * 0.50)))
        diagnostics_width = min(width - 24, max(760, round(width * 0.68)))
        diagnostics_height = min(height - 24, max(420, round(height * 0.58)))
        illustration_dialog_width = min(width - 24, max(520, popup_width))
        illustration_dialog_height = min(
            height - 24, max(360, round(popup_height * 0.68))
        )
        return CAMPopupMetrics(
            popup_width=popup_width,
            popup_height=popup_height,
            display_scale_factor=max(1.0, float(display_scale_factor)),
            minimum_width=minimum_width,
            minimum_height=minimum_height,
            maximum_width=maximum_width,
            maximum_height=maximum_height,
            content_margin=8,
            section_spacing=6,
            row_spacing=3,
            label_spacing=7,
            control_height=27,
            button_height=29,
            compact_button_height=27,
            toolbar_height=28,
            footer_height=43,
            regular_font_point_size=regular_font,
            heading_font_point_size=max(10.0, regular_font + 1.0),
            operation_title_font_point_size=max(11.0, regular_font + 2.0),
            status_font_point_size=max(8.5, regular_font - 0.5),
            table_row_height=26,
            tree_row_height=26,
            illustration_collapsed_height=illustration_height,
            illustration_expanded_height=expanded_illustration_height,
            illustration_canvas_collapsed_height=max(54, illustration_height - 58),
            illustration_canvas_expanded_height=max(
                126, expanded_illustration_height - 62
            ),
            field_reflow_width=500,
            grid_min_column_width=244,
            grid_gap=6,
            illustration_auto_collapse_height=620,
            child_margin=8,
            tool_selector_size=QSize(child_width, child_height),
            diagnostics_size=QSize(diagnostics_width, diagnostics_height),
            illustration_dialog_size=QSize(
                illustration_dialog_width, illustration_dialog_height
            ),
        )


CAM_POPUP_DENSITY = CAMPopupDensityPolicy()


class CAMResponsiveGridPolicy:
    """Choose one or two columns from usable width and real widget hints.

    The policy works in Qt logical pixels, so it remains stable at 100–200%
    display scaling.  A size hint may raise the minimum column width, but is
    capped so long Vietnamese labels can wrap instead of disabling the grid.
    """

    def columns_for(
        self,
        content_width: int,
        metrics: CAMPopupMetrics,
        *,
        minimum_size_hint: int = 0,
    ) -> int:
        """Return two columns only when both remain comfortably readable."""
        hinted_width = min(260, max(0, int(minimum_size_hint)))
        column_width = max(metrics.grid_min_column_width, hinted_width)
        usable_width = max(0, int(content_width) - 2 * metrics.content_margin)
        return 2 if usable_width >= 2 * column_width + metrics.grid_gap else 1


CAM_RESPONSIVE_GRID = CAMResponsiveGridPolicy()


def cam_popup_style(metrics: CAMPopupMetrics) -> str:
    """Build popup-scoped QSS without replacing the Windows native font family."""
    return f"""
QDialog#CAMFunctionPopupHost {{ background: #f7f9fb; }}
QDialog#CAMFunctionPopupHost QWidget {{
    font-size: {metrics.regular_font_point_size:g}pt;
}}
QDialog#CAMFunctionPopupHost QLabel#FunctionEditorSummaryTitle {{
    font-size: {metrics.operation_title_font_point_size:g}pt;
    font-weight: 600;
}}
QDialog#CAMFunctionPopupHost QLabel#FunctionEditorSectionTitle,
QDialog#CAMFunctionPopupHost QLabel#CAMIllustrationTitle {{
    font-size: {metrics.heading_font_point_size:g}pt;
    font-weight: 600;
}}
QDialog#CAMFunctionPopupHost QLabel#FunctionEditorReferenceBadge,
QDialog#CAMFunctionPopupHost QLabel#FunctionEditorHostMode,
QDialog#CAMFunctionPopupHost QLabel#FunctionEditorFieldSource,
QDialog#CAMFunctionPopupHost QLabel#FunctionEditorDefaultIndicator,
QDialog#CAMFunctionPopupHost QLabel#FunctionEditorUnit {{
    font-size: {metrics.status_font_point_size:g}pt;
}}
QDialog#CAMFunctionPopupHost QPushButton {{
    min-height: {metrics.button_height}px;
    padding: 1px 8px;
}}
QDialog#CAMFunctionPopupHost QToolButton {{
    min-height: {metrics.compact_button_height}px;
    padding: 0px 4px;
}}
QDialog#CAMFunctionPopupHost QLineEdit,
QDialog#CAMFunctionPopupHost QComboBox {{
    min-height: {metrics.control_height}px;
}}
QDialog#CAMFunctionPopupHost QAbstractItemView::item {{
    min-height: {metrics.table_row_height}px;
}}
"""


WORKSPACE_STYLE = """
QToolBar#WorkspaceBar {
    background: #172f47;
    border: 0;
    border-bottom: 1px solid #0e2235;
    spacing: 3px;
    padding: 3px 8px;
}
QToolBar#WorkspaceBar QToolButton {
    background: transparent;
    color: #eaf1f7;
    border: 1px solid transparent;
    border-radius: 3px;
    min-height: 25px;
    padding: 2px 11px;
    font-weight: 600;
}
QToolBar#WorkspaceBar QToolButton:hover {
    background: #244b6d;
    border-color: #4b7599;
}
QToolBar#WorkspaceBar QToolButton:checked {
    background: #e7f1fb;
    color: #123e65;
    border-color: #8cb4d8;
}
QToolBar#WorkspaceBar QToolButton:disabled { color: #91a4b5; }
QWidget#OperationManagerHost,
QWidget#FunctionEditorHost,
QWidget#DiagnosticsHost,
QWidget#SecondaryPanelHost { background: #f7f9fb; }
QDialog#CAMFunctionPopupHost { background: #f7f9fb; }
QWidget#CAMIllustrationPanel {
    background: #f8fbfd;
    border-top: 1px solid #d5dde4;
    border-bottom: 1px solid #d5dde4;
}
QLabel#CAMIllustrationTitle {
    color: #25445d;
    font-size: 8.5pt;
    font-weight: 700;
}
QLabel#CAMIllustrationCaption { color: #465d6f; }
QWidget#CAMIllustrationCanvas {
    background: #f8fbfd;
    border: 1px solid #c9d6df;
    border-radius: 4px;
}
QListWidget#CAMToolSelectorList {
    background: #ffffff;
    border: 1px solid #aebbc7;
    selection-background-color: #176aa6;
    selection-color: #ffffff;
}
QFrame#PanelHeader {
    background: #edf2f6;
    border-bottom: 1px solid #c4ced8;
}
QLabel#PanelTitle {
    color: #203243;
    font-size: 10pt;
    font-weight: 600;
}
QLabel#PanelSummary { color: #516273; }
QFrame#OperationManagerSummary {
    background: #f7f9fb;
    border-bottom: 1px solid #d5dde4;
}
QLabel#OperationManagerProject { color: #203243; font-weight: 600; }
QLabel#OperationManagerCounts { color: #315f7d; font-size: 8.5pt; }
QComboBox#OperationStatusFilter {
    background: #ffffff;
    border: 1px solid #aebbc7;
    border-radius: 3px;
    min-height: 26px;
    padding: 1px 4px;
}
QTreeView#OperationManagerTree {
    background: #ffffff;
    border: 0;
    border-top: 1px solid #c7d1da;
    selection-background-color: #176aa6;
    selection-color: #ffffff;
}
QTreeView#OperationManagerTree::item { border-bottom: 1px solid #edf1f4; }
QTreeView#OperationManagerTree::item:hover { background: #eef5fa; }
QHeaderView::section {
    background: #edf2f6;
    color: #516273;
    border: 0;
    border-bottom: 1px solid #c4ced8;
    padding: 3px 5px;
    font-size: 8pt;
    font-weight: 600;
}
QFrame#OperationManagerEmptyState {
    background: #f4f7f9;
    border-top: 1px solid #c7d1da;
}
QLabel#OperationManagerStateTitle { color: #203243; font-weight: 600; }
QLabel#SemanticInfo { color: #245b87; font-weight: 600; }
QLineEdit#OperationSearch {
    background: #ffffff;
    border: 1px solid #aebbc7;
    border-radius: 3px;
    min-height: 26px;
    padding: 1px 7px;
}
QLineEdit#OperationSearch:focus { border: 2px solid #2472ad; }
QToolBar#OperationManagerTools {
    background: #f7f9fb;
    border: 0;
    border-bottom: 1px solid #d5dde4;
    spacing: 2px;
    padding: 2px 4px;
}
QPushButton#PrimaryPanelAction {
    background: #176aa6;
    color: #ffffff;
    border: 1px solid #125785;
    border-radius: 3px;
    min-height: 28px;
    padding: 2px 12px;
    font-weight: 600;
}
QPushButton#PrimaryPanelAction:hover { background: #125b91; }
QPushButton#PrimaryPanelAction:focus { border: 2px solid #0b3655; }
QPushButton#PrimaryPanelAction:disabled {
    background: #b8c4ce;
    color: #eef2f5;
    border-color: #a4b0ba;
}
QFrame#FunctionEditorFooter {
    background: #edf2f6;
    border-top: 1px solid #c4ced8;
}
QFrame#FunctionEditorFooter QPushButton:disabled {
    background: #dde4e9;
    color: #7a8894;
    border: 1px solid #c2cbd2;
}
QFrame#FunctionEditorSummary {
    background: #f7f9fb;
    border-bottom: 1px solid #d5dde4;
}
QLabel#FunctionEditorSummaryTitle {
    color: #203243;
    font-size: 11pt;
    font-weight: 700;
}
QLabel#FunctionEditorSummaryContext,
QLabel#FunctionEditorDraftStatus { color: #516273; }
QLabel#FunctionEditorReferenceBadge {
    color: #315f7d;
    background: #e4f0f8;
    border: 1px solid #9fc1d9;
    border-radius: 3px;
    padding: 2px 5px;
    font-size: 8pt;
    font-weight: 700;
}
QLabel#FunctionEditorLegacyBadge,
QLabel#FunctionEditorHostMode {
    color: #675421;
    background: #fff4cf;
    border: 1px solid #d6bd70;
    border-radius: 3px;
    padding: 1px 5px;
    font-size: 8pt;
    font-weight: 700;
}
QFrame#FunctionEditorDisclosureBar {
    background: #edf2f6;
    border-bottom: 1px solid #c4ced8;
}
QFrame[objectName^="FunctionEditorSection_"] {
    background: #ffffff;
    border: 1px solid #ccd5dd;
    border-radius: 4px;
}
QFrame#FunctionEditorSectionHeader {
    background: #eef3f7;
    border: 0;
    border-bottom: 1px solid #d5dde4;
}
QLabel#FunctionEditorSectionTitle {
    color: #25445d;
    font-weight: 700;
    font-size: 8.5pt;
}
QLabel#FunctionEditorSectionSummary,
QLabel#FunctionEditorFieldSource,
QLabel#FunctionEditorDefaultIndicator,
QLabel#FunctionEditorUnit { color: #5b6c7b; font-size: 8pt; }
QLabel#FunctionEditorSectionBadge { color: #875d12; font-weight: 600; }
QWidget[objectName^="FunctionEditorField_"] {
    background: #ffffff;
    border-bottom: 1px solid #edf1f4;
}
QLabel#FunctionEditorFieldLabel { color: #263746; }
QLabel#FunctionEditorInlineDiagnostic[severity="error"] {
    color: #8f201b;
    background: #fff0ef;
    padding: 3px;
}
QLabel#FunctionEditorInlineDiagnostic[severity="warning"] {
    color: #77500e;
    background: #fff8df;
    padding: 3px;
}
QLabel#FunctionEditorInlineDiagnostic[severity="info"] {
    color: #245b87;
    background: #edf6fc;
    padding: 3px;
}
QLineEdit[validationState="error"],
QComboBox[validationState="error"] {
    border: 2px solid #b43a32;
    background: #fff7f6;
}
QWidget#ParallelCalculationProgress {
    background: #f7f9fb;
    border-top: 1px solid #c4ced8;
    border-bottom: 1px solid #d5dde4;
}
QFrame#FunctionEditorDiagnosticView,
QFrame#FunctionEditorHelpPanel {
    background: #ffffff;
    border: 1px solid #ccd5dd;
    border-radius: 4px;
}
QListWidget#FunctionEditorDiagnosticList { border: 0; background: #ffffff; }
QComboBox#FunctionEditorDisclosureSelector,
QWidget[objectName^="FunctionEditorInput_"] {
    background: #ffffff;
    border: 1px solid #aebbc7;
    border-radius: 3px;
    min-height: 25px;
    padding: 1px 4px;
}
QComboBox#FunctionEditorDisclosureSelector:focus,
QWidget[objectName^="FunctionEditorInput_"]:focus {
    border: 2px solid #2472ad;
}
QLabel#DiagnosticSeverityInfo { color: #245b87; font-weight: 600; }
QLabel#DiagnosticSeverityWarning { color: #875d12; font-weight: 600; }
QLabel#DiagnosticSeverityError { color: #9b241b; font-weight: 600; }
QAbstractButton:focus, QComboBox:focus, QTreeWidget:focus, QTreeView:focus,
QTableWidget:focus, QPlainTextEdit:focus {
    border: 2px solid #2472ad;
}
"""
