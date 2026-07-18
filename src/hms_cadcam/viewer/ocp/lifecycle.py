"""Own and tear down OCCT display resources in HWND-safe order."""

from __future__ import annotations

import ctypes

from OCP.AIS import AIS_InteractiveContext, AIS_Shape
from OCP.Aspect import Aspect_DisplayConnection
from OCP.OpenGl import OpenGl_GraphicDriver
from OCP.Quantity import Quantity_Color, Quantity_TOC_RGB
from OCP.TopoDS import TopoDS_Shape
from OCP.V3d import V3d_View, V3d_Viewer
from OCP.WNT import WNT_Window

from hms_cadcam.viewer.models import DisplayMode


def _void_pointer_capsule(address: int) -> object:
    capsule_new = ctypes.pythonapi.PyCapsule_New
    capsule_new.restype = ctypes.py_object
    capsule_new.argtypes = (ctypes.c_void_p, ctypes.c_char_p, ctypes.c_void_p)
    return capsule_new(ctypes.c_void_p(address), None, None)


class OcpViewportLifecycle:
    """Manage driver, viewer, context, view, HWND and one AIS presentation."""

    def __init__(self) -> None:
        self._display_connection: Aspect_DisplayConnection | None = None
        self._driver: OpenGl_GraphicDriver | None = None
        self._viewer: V3d_Viewer | None = None
        self._context: AIS_InteractiveContext | None = None
        self._view: V3d_View | None = None
        self._window: WNT_Window | None = None
        self._presentation: AIS_Shape | None = None
        self._closed = False

    @property
    def initialized(self) -> bool:
        return self._view is not None and not self._closed

    @property
    def context(self) -> AIS_InteractiveContext:
        if self._context is None:
            raise RuntimeError("OCCT viewport is not initialized")
        return self._context

    @property
    def view(self) -> V3d_View:
        if self._view is None:
            raise RuntimeError("OCCT viewport is not initialized")
        return self._view

    @property
    def presentation(self) -> AIS_Shape | None:
        return self._presentation

    def initialize(self, native_window_id: int) -> None:
        """Create display resources once and attach the OCCT view to an HWND."""
        if self.initialized:
            return
        if self._closed:
            raise RuntimeError("OCCT viewport lifecycle is already closed")
        if native_window_id <= 0:
            raise ValueError("A valid native HWND is required")
        display_connection = Aspect_DisplayConnection()
        driver = OpenGl_GraphicDriver(display_connection)
        viewer = V3d_Viewer(driver)
        viewer.SetDefaultLights()
        viewer.SetLightOn()
        context = AIS_InteractiveContext(viewer)
        view = viewer.CreateView()
        window = WNT_Window(_void_pointer_capsule(native_window_id))
        view.SetWindow(window)
        if not window.IsMapped():
            window.Map()
        view.SetBackgroundColor(Quantity_Color(0.12, 0.16, 0.22, Quantity_TOC_RGB))
        self._display_connection = display_connection
        self._driver = driver
        self._viewer = viewer
        self._context = context
        self._view = view
        self._window = window

    def replace_shape(self, shape: TopoDS_Shape, mode: DisplayMode) -> AIS_Shape:
        """Transactionally replace one AIS presentation and preserve old on error."""
        if shape.IsNull():
            raise ValueError("Cannot display a null CAD shape")
        context = self.context
        old_presentation = self._presentation
        new_presentation = AIS_Shape(shape)
        try:
            context.Display(new_presentation, False)
            self.apply_display_mode(new_presentation, mode, False)
            if old_presentation is not None:
                context.Remove(old_presentation, False)
        except Exception:
            context.Remove(new_presentation, False)
            raise
        self._presentation = new_presentation
        self.view.Redraw()
        return new_presentation

    def apply_display_mode(
        self,
        presentation: AIS_Shape,
        mode: DisplayMode,
        update_viewer: bool = True,
    ) -> None:
        """Apply wireframe, shaded or shaded-with-edges to one AIS object."""
        drawer = presentation.Attributes()
        drawer.SetFaceBoundaryDraw(mode is DisplayMode.SHADED_WITH_EDGES)
        presentation.SynchronizeAspects()
        display_index = 0 if mode is DisplayMode.WIREFRAME else 1
        self.context.SetDisplayMode(presentation, display_index, False)
        self.context.Redisplay(presentation, update_viewer, True)

    def clear(self) -> None:
        """Remove the active presentation without affecting kernel documents."""
        if self._context is not None:
            self._context.ClearSelected(False)
            if self._presentation is not None:
                self._context.Remove(self._presentation, True)
        self._presentation = None

    def fit_all(self) -> None:
        if self._view is not None:
            self._view.FitAll()
            self._view.Redraw()

    def resize(self, width: int, height: int) -> None:
        if width < 0 or height < 0:
            raise ValueError("Viewport size must not be negative")
        if self._view is not None:
            self._view.MustBeResized()
            self._view.Redraw()

    def close(self) -> None:
        """Release OCCT objects before the native HWND is destroyed."""
        if self._closed:
            return
        self._closed = True
        if self._context is not None:
            self._context.RemoveAll(False)
        if self._view is not None:
            self._view.Remove()
        self._presentation = None
        self._view = None
        self._window = None
        self._context = None
        self._viewer = None
        self._driver = None
        self._display_connection = None
