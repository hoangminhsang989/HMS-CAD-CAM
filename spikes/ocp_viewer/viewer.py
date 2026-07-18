"""Standalone PySide6 widget embedding an OCCT view on Windows."""

from __future__ import annotations

import ctypes

from OCP.AIS import AIS_InteractiveContext, AIS_Shape
from OCP.Aspect import Aspect_DisplayConnection
from OCP.OpenGl import OpenGl_GraphicDriver
from OCP.Quantity import Quantity_Color, Quantity_TOC_RGB
from OCP.V3d import V3d_View, V3d_Viewer
from OCP.WNT import WNT_Window
from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent, QPaintEngine, QPaintEvent, QResizeEvent, QShowEvent
from PySide6.QtWidgets import QWidget

from geometry import create_demo_box


def _void_pointer_capsule(address: int) -> object:
    """Wrap a native HWND in the capsule expected by the OCP binding."""
    capsule_new = ctypes.pythonapi.PyCapsule_New
    capsule_new.restype = ctypes.py_object
    capsule_new.argtypes = (ctypes.c_void_p, ctypes.c_char_p, ctypes.c_void_p)
    return capsule_new(ctypes.c_void_p(address), None, None)


class OcpViewerWidget(QWidget):
    """Own the OCCT display objects needed for this isolated spike."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_NativeWindow)
        self.setAttribute(Qt.WidgetAttribute.WA_PaintOnScreen)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setMinimumSize(640, 480)
        self._display_connection: Aspect_DisplayConnection | None = None
        self._driver: OpenGl_GraphicDriver | None = None
        self._viewer: V3d_Viewer | None = None
        self._context: AIS_InteractiveContext | None = None
        self._view: V3d_View | None = None
        self._window: WNT_Window | None = None

    def initialize_display(self) -> None:
        """Create OCCT display objects and bind the view to this QWidget HWND."""
        if self._view is not None:
            return
        self._display_connection = Aspect_DisplayConnection()
        self._driver = OpenGl_GraphicDriver(self._display_connection)
        self._viewer = V3d_Viewer(self._driver)
        self._viewer.SetDefaultLights()
        self._viewer.SetLightOn()
        self._context = AIS_InteractiveContext(self._viewer)
        self._view = self._viewer.CreateView()
        hwnd = _void_pointer_capsule(int(self.winId()))
        self._window = WNT_Window(hwnd)
        self._view.SetWindow(self._window)
        if not self._window.IsMapped():
            self._window.Map()
        self._view.SetBackgroundColor(
            Quantity_Color(0.12, 0.16, 0.22, Quantity_TOC_RGB)
        )
        presentation = AIS_Shape(create_demo_box())
        self._context.Display(presentation, False)
        self._view.FitAll()
        self._view.Redraw()

    def fit_all(self) -> None:
        """Fit all displayed objects into the view."""
        if self._view is not None:
            self._view.FitAll()
            self._view.Redraw()

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802 - Qt API name
        super().showEvent(event)
        self.initialize_display()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt API name
        super().resizeEvent(event)
        if self._view is not None:
            self._view.MustBeResized()
            self._view.Redraw()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - Qt API name
        del event
        if self._view is not None:
            self._view.Redraw()

    def paintEngine(self) -> QPaintEngine | None:  # noqa: N802 - Qt API name
        """Let OCCT render directly into the native widget."""
        return None

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt API name
        """Release the OCCT view before Qt destroys its native HWND."""
        if self._context is not None:
            self._context.RemoveAll(False)
        if self._view is not None:
            self._view.Remove()
        self._view = None
        self._window = None
        self._context = None
        self._viewer = None
        self._driver = None
        self._display_connection = None
        event.accept()
