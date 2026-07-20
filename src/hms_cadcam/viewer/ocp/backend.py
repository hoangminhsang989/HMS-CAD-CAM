"""Open CASCADE implementation of the product viewport backend."""

from __future__ import annotations

import logging
import math
from dataclasses import replace

from OCP.AIS import AIS_Shape
from OCP.BRep import BRep_Builder
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeVertex
from OCP.GC import GC_MakeArcOfCircle
from OCP.Quantity import Quantity_Color, Quantity_TOC_RGB
from OCP.TopoDS import TopoDS_Compound
from OCP.V3d import V3d_TypeOfOrientation
from OCP.gp import gp_Pnt

from hms_cadcam.cad.kernel import CadKernel
from hms_cadcam.cad.models import (
    CadDocumentId,
    CadDocumentKind,
    CadDocumentTree,
    CadGeometryKind,
    CadObjectId,
)
from hms_cadcam.cad.ocp import OcpCadKernel
from hms_cadcam.cam.domain import OperationId
from hms_cadcam.cam.toolpath import ArcMove, LinearMove, RapidMove, ToolpathArtifact
from hms_cadcam.viewer.backend import SelectionCallback
from hms_cadcam.viewer.models import (
    DisplayMode,
    KeyboardModifier,
    MouseButton,
    ObjectColor,
    SelectionMetadata,
    SelectionMode,
    ViewDirection,
    ViewportStatus,
)
from hms_cadcam.viewer.ocp.input_controller import OcpInputController
from hms_cadcam.viewer.ocp.lifecycle import OcpViewportLifecycle
from hms_cadcam.viewer.ocp.registry import OcpPresentationRegistry
from hms_cadcam.viewer.ocp.selection import OcpSelectionController
from hms_cadcam.viewer.toolpath import ToolpathPresentation

_VIEW_DIRECTIONS = {
    ViewDirection.TOP: V3d_TypeOfOrientation.V3d_TypeOfOrientation_Zup_Top,
    ViewDirection.BOTTOM: V3d_TypeOfOrientation.V3d_TypeOfOrientation_Zup_Bottom,
    ViewDirection.FRONT: V3d_TypeOfOrientation.V3d_TypeOfOrientation_Zup_Front,
    ViewDirection.BACK: V3d_TypeOfOrientation.V3d_TypeOfOrientation_Zup_Back,
    ViewDirection.LEFT: V3d_TypeOfOrientation.V3d_TypeOfOrientation_Zup_Left,
    ViewDirection.RIGHT: V3d_TypeOfOrientation.V3d_TypeOfOrientation_Zup_Right,
    ViewDirection.ISOMETRIC: (
        V3d_TypeOfOrientation.V3d_TypeOfOrientation_Zup_AxoRight
    ),
}


class OcpCadViewportBackend:
    """Render OCP kernel documents without exposing native objects to Qt UI."""

    def __init__(self, kernel: CadKernel) -> None:
        if not isinstance(kernel, OcpCadKernel):
            raise TypeError("OCP viewport requires OcpCadKernel")
        self._kernel = kernel
        self._lifecycle = OcpViewportLifecycle()
        self._selection: OcpSelectionController | None = None
        self._input: OcpInputController | None = None
        self._selection_callback: SelectionCallback = lambda _items: None
        self._document_id: CadDocumentId | None = None
        self._tree: CadDocumentTree | None = None
        self._selected_object_ids: tuple[CadObjectId, ...] = ()
        self._display_mode = DisplayMode.SHADED_WITH_EDGES
        self._selection_mode = SelectionMode.SOLID
        self._view_direction = ViewDirection.ISOMETRIC
        self._pending_size = (0, 0)
        self._closed = False
        self._toolpaths: dict[OperationId, tuple[AIS_Shape, ...]] = {}
        self._toolpath_metadata: dict[OperationId, ToolpathPresentation] = {}

    def get_status(self) -> ViewportStatus:
        return ViewportStatus(
            available=True,
            initialized=self._lifecycle.initialized,
            backend="OCP",
        )

    def set_selection_callback(self, callback: SelectionCallback) -> None:
        self._selection_callback = callback

    def initialize(self, native_window_id: int) -> None:
        if self._lifecycle.initialized:
            return
        if self._closed:
            raise RuntimeError("OCP viewport backend is already closed")
        self._lifecycle.initialize(native_window_id)
        self._selection = OcpSelectionController(self._lifecycle.context)
        self._selection.set_mode(self._selection_mode)
        self._input = OcpInputController(self._lifecycle.view, self._selection)
        self.set_view_direction(self._view_direction)
        self.resize(*self._pending_size)

    def display_document(self, document_id: CadDocumentId) -> None:
        self._require_initialized()
        self.clear_toolpaths()
        old_document_id = self._document_id
        old_tree = self._tree
        old_presentation = self._lifecycle.presentation
        old_presentations = dict(getattr(self._lifecycle, "presentations", {}))
        if old_presentation is None and old_presentations:
            old_presentation = next(iter(old_presentations.values()))
        metadata = self._kernel.get_document_metadata(document_id)
        tree = self._kernel.get_document_tree(document_id)
        shape = None
        registry: OcpPresentationRegistry | None = None
        presentation = None
        presentation_shapes = {}
        if hasattr(self._lifecycle, "prepare_registry"):
            if metadata.geometry_kind is CadGeometryKind.BREP:
                shape = self._kernel._resolve_shape(document_id)
                presentation_shapes = self._kernel._resolve_presentation_shapes(
                    document_id
                )
                if metadata.document_kind in {
                    CadDocumentKind.XCAF_PART,
                    CadDocumentKind.XCAF_ASSEMBLY,
                }:
                    registry = self._lifecycle.prepare_xcaf_registry(
                        tree,
                        self._kernel._resolve_xcaf_presentation_sources(document_id),
                        self._display_mode,
                    )
                else:
                    registry = self._lifecycle.prepare_registry(
                        tree,
                        presentation_shapes,
                        self._display_mode,
                    )
            else:
                triangulation = self._kernel._resolve_triangulation(document_id)
                registry = self._lifecycle.prepare_registry(
                    tree,
                    {},
                    self._display_mode,
                    triangulation,
                )
            presentation = next(iter(registry.presentations.values()))
        elif metadata.geometry_kind is CadGeometryKind.BREP:
            shape = self._kernel._resolve_shape(document_id)
            presentation = self._lifecycle.prepare_shape(shape, self._display_mode)
        else:
            triangulation = self._kernel._resolve_triangulation(document_id)
            presentation = self._lifecycle.prepare_triangulation(
                triangulation,
                self._display_mode,
            )
        if self._input is not None:
            self._input.reset()
        try:
            selection = self._require_selection()
            if shape is None:
                selection.clear_document()
            else:
                if registry is not None:
                    selection.bind_document(
                        document_id,
                        shape,
                        presentation,
                        presentation_shapes,
                        registry.presentations,
                    )
                else:
                    selection.bind_document(document_id, shape, presentation)
                selection.set_mode(self._selection_mode)
            if registry is not None:
                self._lifecycle.commit_registry(registry)
            else:
                self._lifecycle.commit_presentation(presentation)
        except Exception as error:
            cleanup_error: Exception | None = None
            try:
                if registry is not None:
                    self._lifecycle.discard_registry(registry)
                elif presentation is not None:
                    self._lifecycle.discard_presentation(presentation)
            except Exception as discard_error:
                cleanup_error = discard_error
                logging.getLogger(__name__).exception(
                    "Cannot discard failed CAD presentation candidate"
                )
            try:
                self._restore_selection(
                    old_document_id,
                    old_tree,
                    old_presentation,
                    old_presentations,
                )
            except Exception as restore_error:
                if cleanup_error is None:
                    cleanup_error = restore_error
                logging.getLogger(__name__).exception(
                    "Cannot restore previous CAD selection after replace failure"
                )
            self._emit_selection(())
            if cleanup_error is not None:
                raise RuntimeError(
                    "CAD candidate discard or previous selection restore failed"
                ) from error
            raise
        self._document_id = document_id
        self._tree = tree
        self._selected_object_ids = ()
        self._emit_selection(())
        self.fit_all()

    def clear(self) -> None:
        self.clear_toolpaths()
        if self._selection is not None:
            self._selection.clear_document()
        if self._input is not None:
            self._input.reset()
        self._lifecycle.clear()
        self._document_id = None
        self._tree = None
        self._selected_object_ids = ()
        self._emit_selection(())

    def display_toolpath(self, artifact: ToolpathArtifact) -> None:
        """Render motion compounds separately from selectable CAD presentations."""
        self._require_initialized()
        metadata = ToolpathPresentation.from_artifact(artifact)
        context = self._lifecycle.context
        groups = {key: [] for key in (
            "rapid", "approach", "peck_resume", "plunge", "plunge_link",
            "lead_in", "pocket_cutting", "cutting", "lead_out", "link",
            "retract", "synchronized_descent", "synchronized_retract",
            "final_retract", "reaming_approach", "reaming_descent",
            "boring_approach", "boring_descent", "controlled_retract",
        )}
        movements = tuple(
            event for event in artifact.events
            if isinstance(event, (RapidMove, LinearMove, ArcMove))
        )
        for event, segment in zip(movements, metadata.segments, strict=True):
            groups[segment.semantic].append(event)
        annotation_groups = {key: [] for key in (
            "dwell", "synchronization_begin", "spindle_reversal",
            "hole_complete", "synchronization_end", "process_begin",
            "spindle_begin", "coolant_begin", "process_end",
        )}
        for annotation in metadata.annotations:
            annotation_groups[annotation.semantic].append(annotation.position)
        colors = {
            "rapid": (0.2, 0.55, 1.0),
            "approach": (1.0, 0.72, 0.15),
            "peck_resume": (0.95, 0.8, 0.25),
            "plunge": (1.0, 0.55, 0.05),
            "plunge_link": (1.0, 0.65, 0.1),
            "lead_in": (0.0, 0.85, 0.85),
            "pocket_cutting": (0.25, 0.95, 0.2),
            "cutting": (0.1, 0.9, 0.25),
            "lead_out": (0.0, 0.65, 0.85),
            "link": (1.0, 0.8, 0.2),
            "retract": (0.9, 0.25, 0.9),
            "synchronized_descent": (0.15, 0.95, 0.35),
            "synchronized_retract": (0.95, 0.35, 0.8),
            "final_retract": (0.65, 0.35, 1.0),
            "reaming_approach": (1.0, 0.7, 0.1),
            "reaming_descent": (0.15, 0.95, 0.25),
            "boring_approach": (1.0, 0.62, 0.08),
            "boring_descent": (0.2, 1.0, 0.3),
            "controlled_retract": (0.95, 0.3, 0.75),
            "dwell": (1.0, 0.2, 0.2),
            "synchronization_begin": (1.0, 0.85, 0.15),
            "spindle_reversal": (1.0, 0.35, 0.1),
            "hole_complete": (0.25, 1.0, 0.85),
            "synchronization_end": (0.55, 0.85, 1.0),
            "process_begin": (1.0, 0.9, 0.15),
            "spindle_begin": (0.95, 0.55, 0.1),
            "coolant_begin": (0.1, 0.75, 1.0),
            "process_end": (0.3, 0.85, 1.0),
        }
        operation_id = artifact.source_operation_id
        previous = self._toolpaths.get(operation_id, ())
        metadata_registry = getattr(self, "_toolpath_metadata", None)
        if metadata_registry is None:
            metadata_registry = {}
            self._toolpath_metadata = metadata_registry
        previous_metadata = metadata_registry.get(operation_id)
        if previous_metadata is not None:
            metadata = replace(
                metadata,
                visible=previous_metadata.visible,
                highlighted=previous_metadata.highlighted,
            )
        presentations = []
        try:
            for semantic, events in groups.items():
                if not events:
                    continue
                compound, builder = TopoDS_Compound(), BRep_Builder()
                builder.MakeCompound(compound)
                for event in events:
                    start, end = event.start.position, event.end.position
                    if isinstance(event, ArcMove):
                        angle = math.atan2(start.y - event.center.y, start.x - event.center.x)
                        radius = math.hypot(start.x - event.center.x, start.y - event.center.y)
                        middle_angle = angle + event.sweep_radians / 2.0
                        middle = gp_Pnt(event.center.x + radius * math.cos(middle_angle),
                                        event.center.y + radius * math.sin(middle_angle), start.z)
                        curve = GC_MakeArcOfCircle(gp_Pnt(start.x, start.y, start.z), middle,
                                                   gp_Pnt(end.x, end.y, end.z)).Value()
                        edge = BRepBuilderAPI_MakeEdge(curve).Edge()
                    else:
                        edge = BRepBuilderAPI_MakeEdge(gp_Pnt(start.x, start.y, start.z),
                                                       gp_Pnt(end.x, end.y, end.z)).Edge()
                    builder.Add(compound, edge)
                presentation = AIS_Shape(compound)
                presentations.append(presentation)
                red, green, blue = colors[semantic]
                context.SetColor(presentation, Quantity_Color(red, green, blue, Quantity_TOC_RGB), False)
                context.Display(presentation, False)
                if not metadata.visible:
                    context.Erase(presentation, False)
            for semantic, points in annotation_groups.items():
                if not points:
                    continue
                compound, builder = TopoDS_Compound(), BRep_Builder()
                builder.MakeCompound(compound)
                for point in points:
                    vertex = BRepBuilderAPI_MakeVertex(
                        gp_Pnt(point.x, point.y, point.z)
                    ).Vertex()
                    builder.Add(compound, vertex)
                presentation = AIS_Shape(compound)
                presentations.append(presentation)
                red, green, blue = colors[semantic]
                context.SetColor(
                    presentation,
                    Quantity_Color(red, green, blue, Quantity_TOC_RGB),
                    False,
                )
                context.Display(presentation, False)
                if not metadata.visible:
                    context.Erase(presentation, False)
            context.UpdateCurrentViewer()
        except Exception:
            for presentation in presentations:
                context.Remove(presentation, False)
            context.UpdateCurrentViewer()
            raise
        self._toolpaths[operation_id] = tuple(presentations)
        metadata_registry[operation_id] = metadata
        try:
            for presentation in previous:
                context.Remove(presentation, False)
            context.UpdateCurrentViewer()
        except Exception:
            if previous_metadata is None:
                self._toolpaths.pop(operation_id, None)
                metadata_registry.pop(operation_id, None)
            else:
                self._toolpaths[operation_id] = previous
                metadata_registry[operation_id] = previous_metadata
            for presentation in presentations:
                context.Remove(presentation, False)
            for presentation in previous:
                context.Display(presentation, False)
                if previous_metadata is not None and not previous_metadata.visible:
                    context.Erase(presentation, False)
            context.UpdateCurrentViewer()
            raise

    def get_toolpath_presentations(self) -> tuple[ToolpathPresentation, ...]:
        """Return deterministic native-free metadata for displayed CAM artifacts."""
        metadata = getattr(self, "_toolpath_metadata", {})
        return tuple(metadata[key] for key in sorted(metadata, key=str))

    def clear_toolpaths(self) -> None:
        if not self._toolpaths:
            getattr(self, "_toolpath_metadata", {}).clear()
            return
        if not self._lifecycle.initialized:
            self._toolpaths.clear()
            getattr(self, "_toolpath_metadata", {}).clear()
            return
        context = self._lifecycle.context
        for presentations in self._toolpaths.values():
            for presentation in presentations:
                context.Remove(presentation, False)
        self._toolpaths.clear()
        getattr(self, "_toolpath_metadata", {}).clear()
        context.UpdateCurrentViewer()

    def set_toolpath_visibility(self, operation_id: OperationId, visible: bool) -> None:
        context = self._lifecycle.context
        for presentation in self._toolpaths.get(operation_id, ()):
            (context.Display if visible else context.Erase)(presentation, False)
        context.UpdateCurrentViewer()
        metadata = getattr(self, "_toolpath_metadata", {})
        if operation_id in metadata:
            metadata[operation_id] = replace(metadata[operation_id], visible=visible)

    def remove_toolpath(self, operation_id: OperationId) -> None:
        """Remove one operation presentation and leave every other registry entry intact."""
        if not self._lifecycle.initialized:
            self._toolpaths.pop(operation_id, None)
            getattr(self, "_toolpath_metadata", {}).pop(operation_id, None)
            return
        context = self._lifecycle.context
        presentations = self._toolpaths.get(operation_id, ())
        try:
            for presentation in presentations:
                context.Remove(presentation, False)
            context.UpdateCurrentViewer()
        except Exception:
            for presentation in presentations:
                context.Display(presentation, False)
            context.UpdateCurrentViewer()
            raise
        self._toolpaths.pop(operation_id, None)
        getattr(self, "_toolpath_metadata", {}).pop(operation_id, None)

    def fit_all(self) -> None:
        self._lifecycle.fit_all()
    def set_view_direction(self, direction: ViewDirection) -> None:
        self._view_direction = direction
        if self._lifecycle.initialized:
            self._lifecycle.view.SetProj(_VIEW_DIRECTIONS[direction])
            self.fit_all()

    def set_display_mode(self, mode: DisplayMode) -> None:
        self._display_mode = mode
        presentations = getattr(self._lifecycle, "presentations", {})
        if presentations:
            for presentation in presentations.values():
                self._lifecycle.apply_display_mode(presentation, mode, False)
            self._lifecycle.view.Redraw()
        else:
            presentation = self._lifecycle.presentation
            if presentation is not None:
                self._lifecycle.apply_display_mode(presentation, mode)

    def set_selection_mode(self, mode: SelectionMode) -> None:
        self._selection_mode = mode
        if self._selection is not None:
            self._selection.set_mode(mode)
            self._emit_selection(())

    def select_objects(
        self,
        document_id: CadDocumentId,
        object_ids: tuple[CadObjectId, ...],
    ) -> None:
        registry = self._require_registry(document_id)
        leaf_ids: list[CadObjectId] = []
        for object_id in object_ids:
            for leaf_id in registry.presentation_ids(object_id):
                if leaf_id not in leaf_ids:
                    leaf_ids.append(leaf_id)
        self._require_selection().select_objects(tuple(leaf_ids))
        self._selected_object_ids = tuple(object_ids)

    def set_object_visibility(
        self,
        document_id: CadDocumentId,
        object_id: CadObjectId,
        visible: bool,
    ) -> None:
        registry = self._require_registry(document_id)
        affected = set(registry.presentation_ids(object_id))
        selected_leaves = {
            leaf_id
            for selected_id in self._selected_object_ids
            for leaf_id in registry.presentation_ids(selected_id)
        }
        registry.set_visibility(object_id, visible)
        if not visible and affected.intersection(selected_leaves):
            self._require_selection().clear_selection()
            self._selected_object_ids = ()
            self._emit_selection(())

    def isolate_object(
        self,
        document_id: CadDocumentId,
        object_id: CadObjectId,
    ) -> None:
        registry = self._require_registry(document_id)
        retained = set(registry.presentation_ids(object_id))
        selected_leaves = {
            leaf_id
            for selected_id in self._selected_object_ids
            for leaf_id in registry.presentation_ids(selected_id)
        }
        registry.isolate(object_id)
        if selected_leaves and not selected_leaves.issubset(retained):
            self._require_selection().clear_selection()
            self._selected_object_ids = ()
            self._emit_selection(())

    def reset_isolate(self, document_id: CadDocumentId) -> None:
        self._require_registry(document_id).reset_isolate()

    def set_object_color(
        self,
        document_id: CadDocumentId,
        object_id: CadObjectId,
        color: ObjectColor,
    ) -> None:
        self._require_registry(document_id).set_color(object_id, color)

    def set_object_transparency(
        self,
        document_id: CadDocumentId,
        object_id: CadObjectId,
        transparency: float,
    ) -> None:
        self._require_registry(document_id).set_transparency(
            object_id,
            transparency,
        )

    def reset_object_appearance(
        self,
        document_id: CadDocumentId,
        object_id: CadObjectId,
    ) -> None:
        self._require_registry(document_id).reset_appearance(object_id)

    def resize(self, width: int, height: int) -> None:
        if width < 0 or height < 0:
            raise ValueError("Viewport size must not be negative")
        self._pending_size = (width, height)
        self._lifecycle.resize(width, height)

    def handle_mouse_press(
        self,
        x: int,
        y: int,
        button: MouseButton,
        modifiers: KeyboardModifier,
    ) -> None:
        if self._input is not None:
            self._input.press(x, y, button, modifiers)

    def handle_mouse_move(
        self,
        x: int,
        y: int,
        buttons: frozenset[MouseButton],
        modifiers: KeyboardModifier,
    ) -> None:
        if self._input is not None:
            self._input.move(x, y, buttons, modifiers)

    def handle_mouse_release(
        self,
        x: int,
        y: int,
        button: MouseButton,
        modifiers: KeyboardModifier,
    ) -> None:
        if self._input is None:
            return
        selection = self._input.release(x, y, button, modifiers)
        if selection is not None:
            self._emit_selection(selection)

    def handle_wheel(
        self,
        x: int,
        y: int,
        delta: int,
        modifiers: KeyboardModifier,
    ) -> None:
        del x, y
        if self._input is not None:
            self._input.wheel(delta, modifiers)

    def close(self) -> None:
        if self._closed:
            return
        self.clear_toolpaths()
        self._closed = True
        if self._selection is not None:
            self._selection.clear_document()
        if self._input is not None:
            self._input.reset()
        self._selection_callback = lambda _items: None
        self._input = None
        self._selection = None
        self._document_id = None
        self._tree = None
        self._selected_object_ids = ()
        self._lifecycle.close()

    def _require_initialized(self) -> None:
        if not self._lifecycle.initialized:
            raise RuntimeError("OCP viewport is not initialized")

    def _require_selection(self) -> OcpSelectionController:
        if self._selection is None:
            raise RuntimeError("OCP selection is not initialized")
        return self._selection

    def _restore_selection(
        self,
        document_id,
        tree,
        presentation,
        presentations,
    ) -> None:
        selection = self._require_selection()
        if document_id is None or presentation is None:
            selection.clear_document()
            return
        metadata = self._kernel.get_document_metadata(document_id)
        if metadata.geometry_kind is CadGeometryKind.BREP:
            shape = self._kernel._resolve_shape(document_id)
            if presentations:
                object_shapes = self._kernel._resolve_presentation_shapes(document_id)
                selection.bind_document(
                    document_id,
                    shape,
                    presentation,
                    object_shapes,
                    presentations,
                )
            else:
                selection.bind_document(document_id, shape, presentation)
            selection.set_mode(self._selection_mode)
        else:
            selection.clear_document()

    def _emit_selection(self, items: tuple[SelectionMetadata, ...]) -> None:
        if self._closed:
            return
        self._selected_object_ids = tuple(
            dict.fromkeys(
                item.object_id for item in items if item.object_id is not None
            )
        )
        try:
            self._selection_callback(items)
        except Exception:
            logging.getLogger(__name__).exception(
                "CAD viewport selection callback failed"
            )

    def _require_registry(
        self,
        document_id: CadDocumentId,
    ) -> OcpPresentationRegistry:
        if document_id != self._document_id:
            raise KeyError(f"CAD document is not displayed: {document_id}")
        registry = getattr(self._lifecycle, "registry", None)
        if registry is None:
            raise RuntimeError("Managed presentation registry is unavailable")
        return registry
