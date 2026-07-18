"""Own and tear down OCCT display resources in HWND-safe order."""

from __future__ import annotations

import ctypes

from OCP.AIS import (
    AIS_InteractiveContext,
    AIS_InteractiveObject,
    AIS_Shape,
    AIS_Triangulation,
)
from OCP.Aspect import Aspect_DisplayConnection
from OCP.OpenGl import OpenGl_GraphicDriver
from OCP.Quantity import Quantity_Color, Quantity_TOC_RGB
from OCP.Poly import Poly_Triangulation
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
        self._presentation: AIS_InteractiveObject | None = None
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
    def presentation(self) -> AIS_InteractiveObject | None:
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
        new_presentation = self.prepare_shape(shape, mode)
        self.commit_presentation(new_presentation)
        return new_presentation

    def prepare_shape(self, shape: TopoDS_Shape, mode: DisplayMode) -> AIS_Shape:
        """Display a candidate BREP while retaining the active presentation."""
        if shape.IsNull():
            raise ValueError("Cannot display a null CAD shape")
        new_presentation = AIS_Shape(shape)
        self._prepare_presentation(new_presentation, mode)
        return new_presentation

    def replace_triangulation(
        self,
        triangulation: Poly_Triangulation,
        mode: DisplayMode,
    ) -> AIS_Triangulation:
        """Transactionally display a mesh without creating BREP faces."""
        new_presentation = self.prepare_triangulation(triangulation, mode)
        self.commit_presentation(new_presentation)
        return new_presentation

    def prepare_triangulation(
        self,
        triangulation: Poly_Triangulation,
        mode: DisplayMode,
    ) -> AIS_Triangulation:
        """Display a candidate mesh while retaining the active presentation."""
        if triangulation.NbNodes() <= 0 or triangulation.NbTriangles() <= 0:
            raise ValueError("Cannot display an empty triangle mesh")
        new_presentation = AIS_Triangulation(triangulation)
        self._prepare_presentation(new_presentation, mode)
        return new_presentation

    def _prepare_presentation(
        self,
        new_presentation: AIS_InteractiveObject,
        mode: DisplayMode,
    ) -> None:
        context = self.context
        try:
            context.Display(new_presentation, False)
            self.apply_display_mode(new_presentation, mode, False)
        except Exception:
            context.Remove(new_presentation, False)
            raise

    def commit_presentation(
        self,
        new_presentation: AIS_InteractiveObject,
    ) -> None:
        """Promote a prepared candidate and remove the previous presentation."""
        old_presentation = self._presentation
        if old_presentation is not None and old_presentation is not new_presentation:
            self.context.Remove(old_presentation, False)
        self._presentation = new_presentation
        self.view.Redraw()

    def discard_presentation(
        self,
        candidate: AIS_InteractiveObject,
    ) -> None:
        """Remove a failed candidate without touching the active presentation."""
        if candidate is self._presentation:
            return
        self.context.Remove(candidate, False)
        self.view.Redraw()

    def apply_display_mode(
        self,
        presentation: AIS_InteractiveObject,
        mode: DisplayMode,
        update_viewer: bool = True,
    ) -> None:
        """Apply wireframe, shaded or shaded-with-edges to one AIS object."""
        if isinstance(presentation, AIS_Shape):
            drawer = presentation.Attributes()
            drawer.SetFaceBoundaryDraw(mode is DisplayMode.SHADED_WITH_EDGES)
            presentation.SynchronizeAspects()
            display_index = 0 if mode is DisplayMode.WIREFRAME else 1
        else:
            display_index = 0
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
