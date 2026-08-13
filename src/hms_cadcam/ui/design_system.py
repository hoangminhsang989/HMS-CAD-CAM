"""Native HMS CAD/CAM visual design system.

The values are derived from the owner-supplied WorkNC-inspired HTML reference,
but the production surface remains entirely PySide6/Qt.  This module is static:
it owns no timers, widget traversal or application/domain signals.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Final


@dataclass(frozen=True, slots=True)
class CadUiPalette:
    """Soft-dark CAD palette with compact, non-decorative interaction states."""

    window: str = "#101820"
    chrome: str = "#0b1722"
    toolbar: str = "#122535"
    panel: str = "#17232e"
    panel_alt: str = "#1b2a36"
    editor: str = "#202f3b"
    border: str = "#344653"
    border_strong: str = "#496273"
    text: str = "#e6edf2"
    text_muted: str = "#a7b5c0"
    text_disabled: str = "#687985"
    accent: str = "#2f8fd1"
    accent_hover: str = "#3d9cde"
    accent_pressed: str = "#17679f"
    selected: str = "#176aa6"
    gold: str = "#d9ad3d"
    success: str = "#4fbd70"
    warning: str = "#e0aa2e"
    danger: str = "#dc5a55"
    focus: str = "#70b9ea"


PALETTE: Final = CadUiPalette()

# These are machining semantics, not theme accents.  Consumers may reuse the
# registry but a theme change must never rewrite these values.
TOOLPATH_SEMANTIC_COLORS: Final = MappingProxyType(
    {
        "rapid": "#ff3636",
        "cutting": "#ffd22e",
        "link": "#ffffff",
        "retract": "#32d06b",
    }
)

COMPACT_CONTROL_HEIGHT: Final = 26
COMPACT_BUTTON_HEIGHT: Final = 28
COMPACT_RADIUS: Final = 3
COMPACT_SPACING: Final = 4


def native_cad_style(palette: CadUiPalette = PALETTE) -> str:
    """Return the shared native dark stylesheet from immutable design tokens."""

    p = palette
    return f"""
/* R253 native HMS CAD/CAM soft-dark design system. */
QMainWindow#HmsMainWindow, QDialog, QMessageBox {{
    background: {p.window};
    color: {p.text};
}}
QWidget {{ color: {p.text}; }}
QWidget:disabled {{ color: {p.text_disabled}; }}
QToolTip {{
    background: {p.editor}; color: {p.text}; border: 1px solid {p.border_strong};
    padding: 3px 5px;
}}

QMenuBar#MainMenuBar {{
    background: {p.chrome}; color: {p.text};
    border-bottom: 1px solid {p.border}; padding-top: 0; padding-bottom: 0;
}}
QMenuBar#MainMenuBar::item {{
    background: transparent; padding: 4px 10px; border-bottom: 2px solid transparent;
}}
QMenuBar#MainMenuBar::item:selected {{
    background: {p.panel_alt}; color: #ffffff; border-bottom-color: {p.accent};
}}
QMenu {{ background: {p.panel}; color: {p.text}; border: 1px solid {p.border_strong}; }}
QMenu::item {{ padding: 5px 26px 5px 9px; }}
QMenu::item:selected {{ background: {p.selected}; color: #ffffff; }}
QMenu::separator {{ height: 1px; background: {p.border}; margin: 3px 6px; }}

QToolBar#QuickAccess, QToolBar#CadViewerTools {{
    background: {p.chrome}; border: 0; border-bottom: 1px solid {p.border};
    spacing: 2px; padding: 1px 4px;
}}
QToolBar#QuickAccess QToolButton, QToolBar#CadViewerTools QToolButton {{
    background: transparent; border: 1px solid transparent; border-radius: {COMPACT_RADIUS}px;
    min-width: 25px; min-height: 24px; padding: 1px 3px;
}}
QToolBar#QuickAccess QToolButton:hover, QToolBar#CadViewerTools QToolButton:hover {{
    background: {p.panel_alt}; border-color: {p.border_strong};
}}
QLabel#HmsBrandLabel {{
    color: #ffffff; background: {p.chrome}; border-left: 4px solid {p.accent};
    border-right: 1px solid {p.border}; padding: 1px 10px 1px 7px;
    font-size: 10pt; font-weight: 700;
}}

QToolBar#WorkspaceBar {{
    background: {p.toolbar}; border: 0; border-bottom: 1px solid {p.border};
    spacing: 2px; padding: 2px 7px;
}}
QToolBar#WorkspaceBar QToolButton {{
    background: transparent; color: {p.text_muted}; border: 1px solid transparent;
    border-radius: {COMPACT_RADIUS}px; min-height: 23px; padding: 1px 9px; font-weight: 600;
}}
QToolBar#WorkspaceBar QToolButton:hover {{
    background: {p.panel_alt}; color: {p.text}; border-color: {p.border_strong};
}}
QToolBar#WorkspaceBar QToolButton:checked {{
    background: {p.selected}; color: #ffffff; border-color: {p.accent_hover};
}}
QToolBar#WorkspaceBar QToolButton:disabled {{ color: {p.text_disabled}; }}

QToolBar#RibbonContainer {{ background: {p.toolbar}; border: 0; padding: 0; }}
QTabWidget#RibbonTabs::pane {{
    background: {p.toolbar}; border: 0; border-bottom: 1px solid {p.border};
}}
QTabWidget#RibbonTabs > QTabBar::tab {{
    background: {p.chrome}; color: {p.text_muted}; border: 0;
    border-bottom: 2px solid transparent; min-width: 52px; padding: 4px 10px;
}}
QTabWidget#RibbonTabs > QTabBar::tab:selected {{
    background: {p.toolbar}; color: #ffffff; border-bottom-color: {p.accent};
}}
QTabWidget#RibbonTabs > QTabBar::tab:hover {{ background: {p.panel_alt}; color: {p.text}; }}
QFrame#RibbonPage, QGroupBox#RibbonGroup {{ background: {p.toolbar}; color: {p.text}; }}
QGroupBox#RibbonGroup {{
    border: 0; border-right: 1px solid {p.border}; margin-top: 0;
    padding: 2px 5px 13px 5px;
}}
QGroupBox#RibbonGroup::title {{
    subcontrol-origin: margin; subcontrol-position: bottom center;
    color: {p.text_disabled}; padding: 0 4px;
}}
QToolButton#RibbonButton {{
    background: transparent; color: {p.text}; border: 1px solid transparent;
    border-radius: {COMPACT_RADIUS}px; padding: 2px 5px; min-width: 42px;
}}
QToolButton#RibbonButton:hover {{ background: {p.panel_alt}; border-color: {p.border_strong}; }}
QToolButton#RibbonButton:pressed, QToolButton#RibbonButton:checked {{
    background: {p.accent_pressed}; border-color: {p.accent_hover}; color: #ffffff;
}}
QToolButton#RibbonButton:disabled {{ color: {p.text_disabled}; }}

QDockWidget {{ color: {p.text}; font-weight: 600; }}
QDockWidget::title {{
    background: {p.panel_alt}; color: {p.text}; border-bottom: 1px solid {p.border};
    padding: 5px 7px; text-align: left;
}}
QDockWidget > QWidget {{ font-weight: 400; }}
QDockWidget > QWidget, QDockWidget QWidget#OperationManagerHost,
QDockWidget QWidget#FunctionEditorHost, QDockWidget QWidget#DiagnosticsHost,
QDockWidget QWidget#SecondaryPanelHost {{ background: {p.panel}; }}
QSplitter::handle {{ background: {p.border}; }}
QSplitter::handle:hover {{ background: {p.accent_pressed}; }}

QWidget#OperationManagerHost, QWidget#FunctionEditorHost,
QWidget#DiagnosticsHost, QWidget#SecondaryPanelHost,
QDialog#CAMFunctionPopupHost, QWidget#CamWorkspace,
QWidget#CamSimulationPanel, QWidget#MachiningSimulationRoot {{
    background: {p.panel}; color: {p.text};
}}
QFrame#PanelHeader, QFrame#OperationManagerSummary, QFrame#FunctionEditorSummary,
QFrame#FunctionEditorDisclosureBar, QFrame#FunctionEditorFooter {{
    background: {p.panel_alt}; border-color: {p.border};
}}
QLabel#PanelTitle, QLabel#OperationManagerProject,
QLabel#FunctionEditorSummaryTitle, QLabel#FunctionEditorSectionTitle {{ color: {p.text}; }}
QLabel#PanelSummary, QLabel#OperationManagerCounts,
QLabel#FunctionEditorSummaryContext, QLabel#FunctionEditorDraftStatus,
QLabel#FunctionEditorSectionSummary, QLabel#FunctionEditorFieldSource,
QLabel#FunctionEditorDefaultIndicator, QLabel#FunctionEditorUnit {{ color: {p.text_muted}; }}
QWidget#CAMIllustrationPanel, QWidget#CAMIllustrationCanvas {{
    background: {p.editor}; border-color: {p.border};
}}
QLabel#CAMIllustrationTitle {{ color: {p.text}; }}
QLabel#CAMIllustrationCaption {{ color: {p.text_muted}; }}

QTreeView, QTreeWidget, QTableView, QTableWidget, QListView, QListWidget,
QPlainTextEdit, QTextEdit {{
    background: {p.panel}; color: {p.text}; border: 1px solid {p.border};
    selection-background-color: {p.selected}; selection-color: #ffffff;
    alternate-background-color: {p.panel_alt}; gridline-color: {p.border};
}}
QTreeView#OperationManagerTree, QTreeWidget#CamOperationTree,
QPlainTextEdit#OutputLog, QTableWidget#PropertiesTable,
QWidget#FunctionEditorHost QScrollArea, QWidget#FunctionEditorHost QScrollArea > QWidget {{
    background: {p.panel}; color: {p.text}; border-color: {p.border};
}}
QLineEdit#OperationSearch, QComboBox#OperationStatusFilter {{
    background: {p.editor}; color: {p.text}; border-color: {p.border_strong};
}}
QTreeView::item, QTreeWidget::item, QListView::item, QListWidget::item {{
    min-height: 24px; border-bottom: 1px solid #22323e;
}}
QTreeView::item:hover, QTreeWidget::item:hover, QListView::item:hover, QListWidget::item:hover {{ background: {p.editor}; }}
QTreeView::item:selected, QTreeWidget::item:selected, QListView::item:selected, QListWidget::item:selected {{ background: {p.selected}; color: #ffffff; }}
QHeaderView::section {{
    background: {p.panel_alt}; color: {p.text_muted}; border: 0;
    border-right: 1px solid {p.border}; border-bottom: 1px solid {p.border};
    padding: 3px 5px; font-weight: 600;
}}

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit, QTimeEdit {{
    background: {p.editor}; color: {p.text}; border: 1px solid {p.border_strong};
    border-radius: {COMPACT_RADIUS}px; min-height: 24px; padding: 0 5px;
    selection-background-color: {p.selected};
}}
QComboBox::drop-down {{ border: 0; width: 18px; }}
QComboBox QAbstractItemView {{ background: {p.editor}; color: {p.text}; selection-background-color: {p.selected}; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QTreeView:focus, QTreeWidget:focus, QTableView:focus, QTableWidget:focus,
QPlainTextEdit:focus, QTextEdit:focus {{ border: 1px solid {p.focus}; }}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{
    background: #18242d; color: {p.text_disabled}; border-color: {p.border};
}}

QPushButton, QToolButton {{
    background: {p.editor}; color: {p.text}; border: 1px solid {p.border_strong};
    border-radius: {COMPACT_RADIUS}px; min-height: 25px; padding: 1px 7px;
}}
QPushButton:hover, QToolButton:hover {{ background: #29404f; border-color: {p.accent_hover}; }}
QPushButton:pressed, QToolButton:pressed {{ background: {p.accent_pressed}; }}
QPushButton:checked, QToolButton:checked {{ background: {p.selected}; color: #ffffff; border-color: {p.accent_hover}; }}
QPushButton:disabled, QToolButton:disabled {{ background: #18242d; color: {p.text_disabled}; border-color: {p.border}; }}
QPushButton#PrimaryPanelAction, QPushButton#ApplySettingsButton,
QPushButton#OkSettingsButton, QPushButton#ClassicCamApplyButton {{
    background: {p.selected}; color: #ffffff; border-color: {p.accent_hover}; font-weight: 600;
}}
QPushButton#PrimaryPanelAction:hover, QPushButton#ApplySettingsButton:hover,
QPushButton#OkSettingsButton:hover, QPushButton#ClassicCamApplyButton:hover {{ background: {p.accent_hover}; }}

QTabWidget::pane {{ background: {p.panel}; border: 1px solid {p.border}; }}
QTabWidget QTabBar::tab {{
    background: {p.toolbar}; color: {p.text_muted}; border: 1px solid {p.border};
    border-bottom: 0; padding: 4px 9px;
}}
QTabWidget QTabBar::tab:selected {{ background: {p.panel}; color: #ffffff; border-top-color: {p.accent}; }}
QTabWidget QTabBar::tab:hover {{ background: {p.panel_alt}; color: {p.text}; }}
QGroupBox {{
    background: {p.panel}; color: {p.text}; border: 1px solid {p.border};
    border-radius: {COMPACT_RADIUS}px; margin-top: 8px; padding-top: 5px;
}}
QGroupBox::title {{ subcontrol-origin: margin; left: 7px; padding: 0 3px; color: {p.text_muted}; }}

QDialog#GeneralSettingsDialog, QDialog#ToolLibraryDialog, QDialog#ToolDefinitionDialog {{ background: {p.window}; }}
QListWidget#SettingsCategoryList {{ background: {p.toolbar}; border-color: {p.border}; }}
QListWidget#SettingsCategoryList::item {{ padding: 4px 7px; min-height: 25px; }}
QListWidget#SettingsCategoryList::item:selected {{ background: {p.selected}; color: #ffffff; }}
QFrame#UiScalePreviewFrame, QFrame#ViewportBackgroundPreview {{ border: 1px solid {p.border_strong}; border-radius: {COMPACT_RADIUS}px; }}
QLabel#GeneralSettingsTitle, QLabel#SettingsInterfaceHeading,
QLabel#SettingsCadViewerHeading, QLabel#ToolLibraryDetailTitle {{ color: {p.text}; }}
QLabel#GeneralSettingsBreadcrumb, QLabel#UiScalePreviewStatus {{ color: {p.text_muted}; }}

QProgressBar {{
    background: {p.chrome}; color: {p.text}; border: 1px solid {p.border};
    border-radius: 2px; min-height: 12px; text-align: center;
}}
QProgressBar::chunk {{ background: {p.accent}; }}
QSlider::groove:horizontal {{ height: 4px; background: {p.border}; border-radius: 2px; }}
QSlider::handle:horizontal {{ width: 12px; margin: -4px 0; background: {p.accent}; border: 1px solid {p.focus}; border-radius: 6px; }}

QStatusBar {{ background: #0d558a; color: #ffffff; min-height: 23px; border-top: 1px solid #2877aa; }}
QStatusBar::item {{ border: 0; border-left: 1px solid rgba(255,255,255,35); }}
QStatusBar QLabel#StatusLabel {{ color: #ffffff; padding: 0 7px; }}

QScrollBar:vertical {{ background: {p.chrome}; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {p.border_strong}; min-height: 24px; border-radius: 4px; margin: 1px; }}
QScrollBar::handle:vertical:hover {{ background: #5d788a; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ height: 0; background: transparent; }}
QScrollBar:horizontal {{ background: {p.chrome}; height: 10px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {p.border_strong}; min-width: 24px; border-radius: 4px; margin: 1px; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal,
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ width: 0; background: transparent; }}

QLabel#DiagnosticSeverityInfo {{ color: {p.accent_hover}; }}
QLabel#DiagnosticSeverityWarning {{ color: {p.warning}; }}
QLabel#DiagnosticSeverityError {{ color: {p.danger}; }}
QLabel#FunctionEditorInlineDiagnostic[severity="error"] {{ color: #ffd5d2; background: #4a2425; }}
QLabel#FunctionEditorInlineDiagnostic[severity="warning"] {{ color: #ffe6a6; background: #45381f; }}
QLabel#FunctionEditorInlineDiagnostic[severity="info"] {{ color: #cdeaff; background: #17364a; }}
QLineEdit[validationState="error"], QComboBox[validationState="error"] {{ border: 1px solid {p.danger}; background: #3a2426; }}
"""


NATIVE_CAD_STYLE: Final = native_cad_style()


__all__ = [
    "COMPACT_BUTTON_HEIGHT",
    "COMPACT_CONTROL_HEIGHT",
    "COMPACT_RADIUS",
    "COMPACT_SPACING",
    "CadUiPalette",
    "NATIVE_CAD_STYLE",
    "PALETTE",
    "TOOLPATH_SEMANTIC_COLORS",
    "native_cad_style",
]
