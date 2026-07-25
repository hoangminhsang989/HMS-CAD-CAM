"""Application stylesheet inspired by professional Windows CAD software."""

APP_STYLE = """
QMainWindow, QWidget {
    background: #f4f5f7;
    color: #252b33;
    font-size: 9pt;
}
QMenuBar {
    background: #ffffff;
    border-bottom: 1px solid #cbd1d8;
    padding: 1px 5px;
}
QMenuBar::item { padding: 6px 12px; background: transparent; }
QMenuBar::item:selected { background: #dbe9f8; color: #124c82; }
QMenu { background: #ffffff; border: 1px solid #aeb7c2; }
QMenu::item { padding: 6px 28px; }
QMenu::item:selected { background: #dbe9f8; }
QTabWidget#RibbonTabs::pane { border: 0; border-bottom: 1px solid #b9c1cb; }
QTabWidget#RibbonTabs > QTabBar::tab {
    background: #ffffff;
    padding: 7px 16px;
    border: 0;
    min-width: 58px;
}
QTabWidget#RibbonTabs > QTabBar::tab:selected {
    color: #0d5b9d;
    border-bottom: 3px solid #1f6fb2;
}
QTabWidget#RibbonTabs > QTabBar::tab:hover { background: #eaf2fb; }
QFrame#RibbonPage { background: #ffffff; }
QGroupBox#RibbonGroup {
    background: #ffffff;
    border: 0;
    border-right: 1px solid #d4d9df;
    margin-top: 0;
    padding: 4px 8px 16px 8px;
}
QGroupBox#RibbonGroup::title {
    subcontrol-origin: margin;
    subcontrol-position: bottom center;
    color: #5e6875;
    padding: 0 5px;
}
QToolButton#RibbonButton {
    border: 1px solid transparent;
    background: transparent;
    padding: 4px 7px;
    min-width: 46px;
}
QToolButton#RibbonButton:hover { background: #e7f0fa; border-color: #b7d0e8; }
QToolButton#RibbonButton:disabled { color: #647280; }
QDockWidget { font-weight: 600; color: #27313c; }
QDockWidget::title {
    background: #eef1f4;
    border-bottom: 1px solid #c7ced6;
    padding: 7px 8px;
    text-align: left;
}
QDockWidget > QWidget { font-weight: 400; }
QTreeWidget, QTableWidget, QPlainTextEdit {
    background: #ffffff;
    border: 1px solid #cbd1d8;
    selection-background-color: #cfe3f7;
    alternate-background-color: #f7f8fa;
}
QHeaderView::section {
    background: #edf0f3;
    border: 0;
    border-right: 1px solid #d1d6dc;
    border-bottom: 1px solid #c4cbd3;
    padding: 5px;
}
QStatusBar { background: #155a99; color: #ffffff; min-height: 24px; }
QStatusBar::item { border: 0; }
QLabel#StatusLabel { color: #ffffff; padding: 0 8px; }
QToolBar#QuickAccess {
    background: #ffffff;
    border: 0;
    spacing: 2px;
    padding: 2px 5px;
}
QToolBar#ViewportTools, QToolBar#ContextTools {
    background: rgba(255, 255, 255, 225);
    border: 1px solid #c4cbd3;
    border-radius: 3px;
    spacing: 2px;
    padding: 3px;
}
QToolBar#ViewportTools QToolButton, QToolBar#ContextTools QToolButton {
    background: transparent;
    border: 1px solid transparent;
    padding: 5px;
}
QToolBar#ViewportTools QToolButton:disabled,
QToolBar#ContextTools QToolButton:disabled { color: #a9b7c5; }
QTabWidget#ManagerTabs::pane { border: 1px solid #cbd1d8; background: white; }
QTabWidget#ManagerTabs > QTabBar::tab { padding: 5px 8px; background: #eef1f4; }
QTabWidget#ManagerTabs > QTabBar::tab:selected { background: white; color: #155a99; }
"""
