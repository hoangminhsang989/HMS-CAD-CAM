"""Shared visual and sizing tokens for the Stage 9A workspace shell."""

from __future__ import annotations


OPERATION_MANAGER_MIN_WIDTH = 260
OPERATION_MANAGER_DEFAULT_WIDTH = 300
OPERATION_MANAGER_MAX_WIDTH = 340

FUNCTION_EDITOR_MIN_WIDTH = 300
FUNCTION_EDITOR_DEFAULT_WIDTH = 410
FUNCTION_EDITOR_MAX_WIDTH = 520

SECONDARY_PANEL_MIN_WIDTH = 360
SECONDARY_PANEL_DEFAULT_WIDTH = 460
SECONDARY_PANEL_MAX_WIDTH = 620

DIAGNOSTICS_DEFAULT_HEIGHT = 118
DIAGNOSTICS_MAX_HEIGHT = 280

VIEWPORT_MIN_WIDTH = 520
VIEWPORT_MIN_HEIGHT = 360


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
QFrame#FunctionEditorFooter {
    background: #edf2f6;
    border-top: 1px solid #c4ced8;
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
