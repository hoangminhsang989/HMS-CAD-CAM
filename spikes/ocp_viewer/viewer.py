"""Standalone PySide6 widget embedding an OCCT view on Windows."""

from __future__ import annotations

import ctypes

from OCP.AIS import AIS_InteractiveContext, AIS_Shape
from OCP.Aspect import Aspect_DisplayConnection
from OCP.OpenGl import OpenGl_GraphicDriver
from OCP.Quantity import Quantity_Color, Quantity_TOC_RGB
from OCP.TopAbs import TopAbs_ShapeEnum
from OCP.V3d import V3d_TypeOfOrientation, V3d_View, V3d_Viewer
from OCP.WNT import WNT_Window
from PySide6.QtCore import QPoint, QTimer, Qt, Signal
from PySide6.QtGui import (
    QCloseEvent,
    QMouseEvent,
    QPaintEngine,
    QPaintEvent,
    QResizeEvent,
    QShowEvent,
    QWheelEvent,
)
from PySide6.QtWidgets import QWidget

from geometry import create_demo_box, selection_metadata
from model import (
    DisplayMode,
    InteractionMode,
    SelectionKind,
    SelectionModeState,
    SelectionSummary,
    ViewOrientation,
)


_ORIENTATIONS = {
    ViewOrientation.TOP: V3d_TypeOfOrientation.V3d_TypeOfOrientation_Zup_Top,
    ViewOrientation.BOTTOM: V3d_TypeOfOrientation.V3d_TypeOfOrientation_Zup_Bottom,
    ViewOrientation.FRONT: V3d_TypeOfOrientation.V3d_TypeOfOrientation_Zup_Front,
    ViewOrientation.BACK: V3d_TypeOfOrientation.V3d_TypeOfOrientation_Zup_Back,
    ViewOrientation.LEFT: V3d_TypeOfOrientation.V3d_TypeOfOrientation_Zup_Left,
    ViewOrientation.RIGHT: V3d_TypeOfOrientation.V3d_TypeOfOrientation_Zup_Right,
    ViewOrientation.ISOMETRIC: V3d_TypeOfOrientation.V3d_TypeOfOrientation_Zup_AxoRight,
}

_SELECTION_TOPOLOGY = {
    SelectionKind.SOLID: TopAbs_ShapeEnum.TopAbs_SOLID,
    SelectionKind.FACE: TopAbs_ShapeEnum.TopAbs_FACE,
    SelectionKind.EDGE: TopAbs_ShapeEnum.TopAbs_EDGE,
}


def _void_pointer_capsule(address: int) -> object:
    """Wrap a native HWND in the capsule expected by the OCP binding."""
    capsule_new = ctypes.pythonapi.PyCapsule_New
    capsule_new.restype = ctypes.py_object
    capsule_new.argtypes = (ctypes.c_void_p, ctypes.c_char_p, ctypes.c_void_p)
    return capsule_new(ctypes.c_void_p(address), None, None)


class OcpViewerWidget(QWidget):
    """Own the OCCT display objects needed for this isolated spike."""

    selection_changed = Signal(object)

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
        self._presentation: AIS_Shape | None = None
        self._interaction_mode = InteractionMode.SELECT
        self._selection_mode = SelectionModeState()
        self._drag_origin: QPoint | None = None
        self._dragged = False

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
        self._presentation = AIS_Shape(create_demo_box())
        self._context.Display(self._presentation, False)
        self.set_display_mode(DisplayMode.SHADED_WITH_EDGES)
        self.set_selection_kind(self._selection_mode.kind)
        self.reset_isometric()

    @property
    def selection_kind(self) -> SelectionKind:
        """Return the active selection level without exposing OCCT state."""
        return self._selection_mode.kind

    def set_interaction_mode(self, mode: InteractionMode) -> None:
        """Select mouse behavior for left-button drags."""
        self._interaction_mode = mode

    def set_selection_kind(self, kind: SelectionKind) -> None:
        """Activate solid, face or edge selection for the displayed shape."""
        self._selection_mode.set_kind(kind)
        if self._context is None or self._presentation is None:
            return
        self._context.ClearSelected(False)
        self._context.Deactivate(self._presentation)
        mode = AIS_Shape.SelectionMode_s(_SELECTION_TOPOLOGY[kind])
        self._context.Activate(self._presentation, mode)
        self._emit_selection_summary()

    def set_display_mode(self, mode: DisplayMode) -> None:
        """Switch between wireframe, shaded and shaded-with-edges."""
        if self._context is None or self._presentation is None:
            return
        drawer = self._presentation.Attributes()
        drawer.SetFaceBoundaryDraw(mode is DisplayMode.SHADED_WITH_EDGES)
        self._presentation.SynchronizeAspects()
        display_index = 0 if mode is DisplayMode.WIREFRAME else 1
        self._context.SetDisplayMode(self._presentation, display_index, False)
        self._context.Redisplay(self._presentation, True, True)

    def set_view_orientation(self, orientation: ViewOrientation) -> None:
        """Apply a named Z-up camera orientation and fit the model."""
        if self._view is None:
            return
        self._view.SetProj(_ORIENTATIONS[orientation])
        self.fit_all()

    def reset_isometric(self) -> None:
        """Restore the standard isometric camera and fit all objects."""
        self.set_view_orientation(ViewOrientation.ISOMETRIC)

    def fit_all(self) -> None:
        """Fit all displayed objects into the view."""
        if self._view is not None:
            self._view.FitAll()
            self._view.Redraw()

    def zoom(self, factor: float) -> None:
        """Zoom relative to the current camera scale."""
        if self._view is not None and factor > 0.0:
            self._view.SetScale(max(1.0e-6, self._view.Scale() * factor))
            self._view.Redraw()

    def select_at(self, x: int, y: int) -> SelectionSummary:
        """Pick at widget coordinates and return only intermediate metadata."""
        if self._context is None or self._view is None:
            return SelectionSummary(count=0, items=())
        self._context.MoveTo(x, y, self._view, True)
        self._context.SelectDetected()
        self._view.Redraw()
        return self._emit_selection_summary()

    def _emit_selection_summary(self) -> SelectionSummary:
        items = []
        if self._context is not None:
            self._context.InitSelected()
            while self._context.MoreSelected():
                items.append(selection_metadata(self._context.SelectedShape()))
                self._context.NextSelected()
        summary = SelectionSummary(count=len(items), items=tuple(items))
        self.selection_changed.emit(summary)
        return summary

    def mousePressEvent(  # noqa: N802 - Qt API name
        self,
        event: QMouseEvent,
    ) -> None:
        """Begin selection, rotation or panning with the left button."""
        if event.button() != Qt.MouseButton.LeftButton or self._view is None:
            super().mousePressEvent(event)
            return
        point = event.position().toPoint()
        self._drag_origin = point
        self._dragged = False
        if self._interaction_mode is InteractionMode.ROTATE:
            self._view.StartRotation(point.x(), point.y())
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API name
        """Continue camera drag or update selection pre-highlight."""
        point = event.position().toPoint()
        if event.buttons() & Qt.MouseButton.LeftButton and self._drag_origin is not None:
            delta = point - self._drag_origin
            self._dragged = self._dragged or delta.manhattanLength() > 2
            if (
                self._view is not None
                and self._interaction_mode is InteractionMode.ROTATE
            ):
                self._view.Rotation(point.x(), point.y())
            elif (
                self._view is not None
                and self._interaction_mode is InteractionMode.PAN
            ):
                self._view.Pan(delta.x(), -delta.y(), 1.0, True)
                self._drag_origin = point
                self._view.Redraw()
        elif self._context is not None and self._view is not None:
            self._context.MoveTo(point.x(), point.y(), self._view, True)
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API name
        """Complete a click selection when no camera drag occurred."""
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self._interaction_mode is InteractionMode.SELECT
            and not self._dragged
        ):
            point = event.position().toPoint()
            self.select_at(point.x(), point.y())
        self._drag_origin = None
        event.accept()

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802 - Qt API name
        """Zoom the camera with the mouse wheel."""
        self.zoom(1.15 if event.angleDelta().y() > 0 else 1.0 / 1.15)
        event.accept()

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802 - Qt API name
        super().showEvent(event)
        self.initialize_display()
        QTimer.singleShot(0, self._synchronize_view_size)

    def _synchronize_view_size(self) -> None:
        """Apply the final Qt layout size after the native view is initialized."""
        if self._view is not None:
            self._view.MustBeResized()
            self._view.Redraw()

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
        self._presentation = None
        self._context = None
        self._viewer = None
        self._driver = None
        self._display_connection = None
        event.accept()
