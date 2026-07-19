"""Own and tear down OCCT display resources in HWND-safe order."""

from __future__ import annotations

import ctypes
import logging

from OCP.AIS import (
    AIS_ColoredShape,
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
from OCP.TopAbs import TopAbs_ShapeEnum
from OCP.TopExp import TopExp
from OCP.TopTools import TopTools_IndexedMapOfShape
from OCP.V3d import V3d_View, V3d_Viewer
from OCP.WNT import WNT_Window

from hms_cadcam.cad.models import CadDocumentTree, CadObjectId, XcafColor
from hms_cadcam.cad.ocp.xcaf import OcpXcafPresentationSource
from hms_cadcam.viewer.models import (
    DEFAULT_OBJECT_COLOR,
    DisplayMode,
    ObjectAppearance,
    ObjectColor,
)
from hms_cadcam.viewer.ocp.registry import OcpPresentationRegistry


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
        self._registry: OcpPresentationRegistry | None = None
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
        if self._registry is not None and len(self._registry.presentations) == 1:
            return next(iter(self._registry.presentations.values()))
        return self._presentation

    @property
    def registry(self) -> OcpPresentationRegistry | None:
        return self._registry

    @property
    def presentations(self) -> dict[CadObjectId, AIS_InteractiveObject]:
        if self._registry is not None:
            return dict(self._registry.presentations)
        return {}

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
            context.SetColor(
                new_presentation,
                Quantity_Color(
                    DEFAULT_OBJECT_COLOR.red,
                    DEFAULT_OBJECT_COLOR.green,
                    DEFAULT_OBJECT_COLOR.blue,
                    Quantity_TOC_RGB,
                ),
                False,
            )
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
        if self._registry is not None:
            for presentation in self._registry.presentations.values():
                self.context.Remove(presentation, False)
            self._registry.clear_isolate()
            self._registry = None
        if old_presentation is not None and old_presentation is not new_presentation:
            self.context.Remove(old_presentation, False)
        self._presentation = new_presentation
        self.view.Redraw()

    def prepare_registry(
        self,
        tree: CadDocumentTree,
        shapes: dict[CadObjectId, TopoDS_Shape],
        mode: DisplayMode,
        triangulation: Poly_Triangulation | None = None,
    ) -> OcpPresentationRegistry:
        """Prepare all managed presentations without replacing the active registry."""
        candidates: dict[CadObjectId, AIS_InteractiveObject] = {}
        try:
            for node in tree.presentation_nodes:
                if triangulation is not None:
                    if len(tree.presentation_nodes) != 1:
                        raise ValueError("A mesh document must have one presentation")
                    candidate = self.prepare_triangulation(triangulation, mode)
                else:
                    shape = shapes.get(node.object_id)
                    if shape is None:
                        raise KeyError(f"Missing shape for CAD object: {node.object_id}")
                    candidate = self.prepare_shape(shape, mode)
                candidates[node.object_id] = candidate
            return OcpPresentationRegistry(self.context, tree, candidates)
        except Exception as error:
            try:
                self._remove_candidate_presentations(tuple(candidates.values()))
            except Exception:
                raise RuntimeError(
                    "CAD registry prepare and candidate cleanup both failed"
                ) from error
            raise

    def prepare_xcaf_registry(
        self,
        tree: CadDocumentTree,
        sources: dict[CadObjectId, OcpXcafPresentationSource],
        mode: DisplayMode,
    ) -> OcpPresentationRegistry:
        """Prepare one XCAF-aware presentation for every placed part occurrence."""
        candidates: dict[CadObjectId, AIS_InteractiveObject] = {}
        base_appearances: dict[CadObjectId, ObjectAppearance] = {}
        native_base_styles: dict[
            CadObjectId, tuple[tuple[TopoDS_Shape, ObjectColor], ...]
        ] = {}
        try:
            for node in tree.presentation_nodes:
                source = sources.get(node.object_id)
                if source is None:
                    raise KeyError(f"Missing XCAF source for CAD object: {node.object_id}")
                candidate = AIS_ColoredShape(source.shape)
                styles = _source_styles(candidate, source)
                for styled_shape, color in styles:
                    candidate.SetCustomColor(
                        styled_shape, _quantity_color(color)
                    )
                self._prepare_xcaf_presentation(candidate, mode)
                effective_color = _source_color(node.source_appearance)
                base_appearances[node.object_id] = ObjectAppearance(
                    color=effective_color or DEFAULT_OBJECT_COLOR
                )
                native_base_styles[node.object_id] = styles
                candidates[node.object_id] = candidate
            return OcpPresentationRegistry(
                self.context,
                tree,
                candidates,
                base_appearances=base_appearances,
                native_base_styles=native_base_styles,
            )
        except Exception as error:
            try:
                self._remove_candidate_presentations(tuple(candidates.values()))
            except Exception:
                raise RuntimeError(
                    "XCAF registry prepare and candidate cleanup both failed"
                ) from error
            raise

    def _prepare_xcaf_presentation(
        self,
        presentation: AIS_ColoredShape,
        mode: DisplayMode,
    ) -> None:
        """Display XCAF styles without replacing product/subshape source colors."""
        try:
            self.context.Display(presentation, False)
            self.apply_display_mode(presentation, mode, False)
        except Exception:
            self.context.Remove(presentation, False)
            raise

    def commit_registry(self, registry: OcpPresentationRegistry) -> None:
        """Atomically promote a prepared registry and dispose the previous one."""
        if registry is self._registry:
            return
        old_registry = self._registry
        old_presentation = self._presentation
        try:
            if old_registry is not None:
                for presentation in old_registry.presentations.values():
                    self.context.Remove(presentation, False)
            if old_presentation is not None:
                self.context.Remove(old_presentation, False)
            self.view.Redraw()
        except Exception as error:
            rollback_error = self._restore_active_presentations(
                old_registry,
                old_presentation,
            )
            if rollback_error is not None:
                raise RuntimeError(
                    "CAD registry commit and rollback both failed"
                ) from error
            raise
        if old_registry is not None:
            old_registry.clear_isolate()
        self._registry = registry
        self._presentation = None

    def _restore_active_presentations(
        self,
        registry: OcpPresentationRegistry | None,
        presentation: AIS_InteractiveObject | None,
    ) -> Exception | None:
        first_error: Exception | None = None
        logger = logging.getLogger(__name__)
        if registry is not None:
            for object_id, old_presentation in registry.presentations.items():
                try:
                    if registry.appearances[object_id].visible:
                        self.context.Display(old_presentation, False)
                    else:
                        self.context.Erase(old_presentation, False)
                except Exception as error:
                    if first_error is None:
                        first_error = error
                    logger.exception(
                        "Cannot restore CAD presentation %s after commit failure",
                        object_id,
                    )
        if presentation is not None:
            try:
                self.context.Display(presentation, False)
            except Exception as error:
                if first_error is None:
                    first_error = error
                logger.exception("Cannot restore legacy CAD presentation")
        try:
            self.view.Redraw()
        except Exception as error:
            if first_error is None:
                first_error = error
            logger.exception("Cannot redraw restored CAD registry")
        return first_error

    def discard_registry(self, registry: OcpPresentationRegistry) -> None:
        """Remove candidates after a failed prepare/bind without touching active state."""
        if registry is self._registry:
            return
        self._remove_candidate_presentations(
            tuple(registry.presentations.values())
        )
        registry.clear_isolate()
        self.view.Redraw()

    def _remove_candidate_presentations(
        self,
        presentations: tuple[AIS_InteractiveObject, ...],
    ) -> None:
        pending = presentations
        first_error: Exception | None = None
        for _attempt in range(2):
            failed: list[AIS_InteractiveObject] = []
            for presentation in pending:
                try:
                    self.context.Remove(presentation, False)
                except Exception as error:
                    if first_error is None:
                        first_error = error
                    failed.append(presentation)
            if not failed:
                return
            pending = tuple(failed)
        raise RuntimeError("Cannot remove failed CAD presentation candidates") from (
            first_error
        )

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
            if self._registry is not None:
                self._registry.clear_isolate()
                for presentation in self._registry.presentations.values():
                    self._context.Remove(presentation, False)
            if self._presentation is not None:
                self._context.Remove(self._presentation, True)
            elif self._registry is not None and self._view is not None:
                self._view.Redraw()
        self._registry = None
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
        if self._registry is not None:
            self._registry.clear_isolate()
        self._registry = None
        self._view = None
        self._window = None
        self._context = None
        self._viewer = None
        self._driver = None
        self._display_connection = None


def _source_color(appearance) -> ObjectColor | None:
    color: XcafColor | None = appearance.surface_color or appearance.generic_color
    if color is None:
        return None
    return ObjectColor(color.red, color.green, color.blue)


def _quantity_color(color: ObjectColor) -> Quantity_Color:
    return Quantity_Color(color.red, color.green, color.blue, Quantity_TOC_RGB)


def _face_shapes(shape: TopoDS_Shape) -> tuple[TopoDS_Shape, ...]:
    faces = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, TopAbs_ShapeEnum.TopAbs_FACE, faces)
    return tuple(faces.FindKey(index) for index in range(1, faces.Extent() + 1))


def _uniform_styles(
    shape: TopoDS_Shape,
    color: ObjectColor,
) -> tuple[tuple[TopoDS_Shape, ObjectColor], ...]:
    return ((shape, color),) + tuple((face, color) for face in _face_shapes(shape))


def _source_styles(
    presentation: AIS_ColoredShape,
    source: OcpXcafPresentationSource,
) -> tuple[tuple[TopoDS_Shape, ObjectColor], ...]:
    occurrence_color = _source_color(source.occurrence_appearance)
    if occurrence_color is not None:
        return _uniform_styles(presentation.Shape(), occurrence_color)
    styles: list[tuple[TopoDS_Shape, ObjectColor]] = []
    product_color = _source_color(source.product_appearance)
    if product_color is not None:
        styles.extend(_uniform_styles(presentation.Shape(), product_color))
    for subshape in source.subshape_sources:
        color = _source_color(subshape.source_appearance)
        if color is not None:
            styles.append((subshape.shape, color))
    return tuple(styles)
