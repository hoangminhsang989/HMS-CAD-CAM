"""Open CASCADE implementation of the product viewport backend."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, replace
from threading import get_ident
from uuid import UUID

from OCP.AIS import AIS_Shape, AIS_Triangulation
from OCP.BRep import BRep_Builder
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeVertex
from OCP.GC import GC_MakeArcOfCircle
from OCP.Poly import Poly_Triangle, Poly_Triangulation
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
from hms_cadcam.cam.domain import OperationId, SimulationResultId, WcsFrame
from hms_cadcam.cam.simulation.model import SimulationResult, SimulationStatus
from hms_cadcam.cam.toolpath import ArcMove, LinearMove, RapidMove, ToolpathArtifact
from hms_cadcam.cam.lathe.toolpath.model import LatheMotionClass
from hms_cadcam.viewer.backend import SelectionCallback
from hms_cadcam.viewer.cam3d import (
    Cam3DPreviewActorIdentity,
    Cam3DPreviewOwnership,
    Cam3DPreviewPublication,
    Cam3DPreviewPublicationCode,
    Cam3DPreviewPublicationResult,
)
from hms_cadcam.viewer.lathe import (
    LathePreviewActorIdentity,
    LathePreviewOwnership,
    LathePreviewPublication,
    LathePreviewPublicationCode,
    LathePreviewPublicationResult,
)
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
from hms_cadcam.viewer.simulation import (
    SimulationDisplayContext,
    SimulationDisplayPolicy,
    SimulationDisplayRequest,
    SimulationIssueMarker,
    SimulationMarkerKind,
    SimulationPathSemantic,
    SimulationPresentation,
    SimulationPresentationRegistry,
)
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

_SIMULATION_STATUS_COLORS = {
    SimulationStatus.PASS: (0.1, 0.85, 0.25),
    SimulationStatus.WARN: (1.0, 0.72, 0.05),
    SimulationStatus.FAIL: (1.0, 0.12, 0.08),
}
_CAM3D_PREVIEW_COLOR = (0.1, 0.72, 0.95)
_CAM3D_PREVIEW_TRANSPARENCY = 0.12

_SIMULATION_PATH_COLORS = {
    SimulationPathSemantic.RAPID: (0.25, 0.58, 1.0),
    SimulationPathSemantic.CUTTING: (0.15, 1.0, 0.3),
    SimulationPathSemantic.LINK: (1.0, 0.82, 0.18),
    SimulationPathSemantic.RETRACT: (0.92, 0.25, 0.95),
    SimulationPathSemantic.APPROACH: (1.0, 0.62, 0.08),
}

_LATHE_PREVIEW_COLORS = {
    LatheMotionClass.RAPID: (1.0, 0.0, 0.0),
    LatheMotionClass.CUTTING: (1.0, 1.0, 0.0),
    LatheMotionClass.LEAD_IN: (1.0, 1.0, 1.0),
    LatheMotionClass.LEAD_OUT: (0.0, 1.0, 0.0),
}

def _simulation_marker_color(
    marker: SimulationIssueMarker,
) -> tuple[float, float, float]:
    if marker.kind is SimulationMarkerKind.GOUGE:
        return (1.0, 0.0, 0.72)
    if marker.kind in {
        SimulationMarkerKind.RAPID_BELOW_SAFE,
        SimulationMarkerKind.CLEARANCE_WARNING,
    }:
        return (1.0, 0.72, 0.05)
    if marker.kind in {
        SimulationMarkerKind.INVALID,
        SimulationMarkerKind.UNSUPPORTED,
    }:
        return (0.75, 0.75, 0.75)
    return (1.0, 0.08, 0.05)


def _simulation_marker_shape(marker: SimulationIssueMarker) -> AIS_Shape | None:
    anchor = marker.anchor_point
    if anchor is None and marker.bounds is None:
        return None
    if marker.bounds is None:
        assert anchor is not None
        return AIS_Shape(
            BRepBuilderAPI_MakeVertex(gp_Pnt(anchor.x, anchor.y, anchor.z)).Vertex()
        )
    minimum, maximum = marker.bounds.minimum, marker.bounds.maximum
    corners = (
        (minimum.x, minimum.y, minimum.z),
        (maximum.x, minimum.y, minimum.z),
        (maximum.x, maximum.y, minimum.z),
        (minimum.x, maximum.y, minimum.z),
        (minimum.x, minimum.y, maximum.z),
        (maximum.x, minimum.y, maximum.z),
        (maximum.x, maximum.y, maximum.z),
        (minimum.x, maximum.y, maximum.z),
    )
    edges = (
        (0, 1), (1, 2), (2, 3), (3, 0),
        (4, 5), (5, 6), (6, 7), (7, 4),
        (0, 4), (1, 5), (2, 6), (3, 7),
    )
    compound, builder = TopoDS_Compound(), BRep_Builder()
    builder.MakeCompound(compound)
    for first, second in edges:
        start, end = corners[first], corners[second]
        if start == end:
            continue
        builder.Add(
            compound,
            BRepBuilderAPI_MakeEdge(gp_Pnt(*start), gp_Pnt(*end)).Edge(),
        )
    if anchor is not None:
        builder.Add(
            compound,
            BRepBuilderAPI_MakeVertex(
                gp_Pnt(anchor.x, anchor.y, anchor.z)
            ).Vertex(),
        )
    return AIS_Shape(compound)


class _OcpCam3DPreviewRollbackError(RuntimeError):
    pass


@dataclass(slots=True)
class _OcpCam3DPreviewActor:
    identity: Cam3DPreviewActorIdentity
    triangulation: Poly_Triangulation
    native: AIS_Triangulation


class _OcpLathePreviewRollbackError(RuntimeError):
    pass


@dataclass(slots=True)
class _OcpLathePreviewActor:
    identity: LathePreviewActorIdentity
    natives: tuple[AIS_Shape, ...]


class OcpCadViewportBackend:
    """Render OCP kernel documents without exposing native objects to Qt UI."""

    def __init__(
        self,
        kernel: CadKernel,
        *,
        lifecycle: OcpViewportLifecycle | None = None,
    ) -> None:
        if not isinstance(kernel, OcpCadKernel):
            raise TypeError("OCP viewport requires OcpCadKernel")
        if lifecycle is not None and not isinstance(
            lifecycle, OcpViewportLifecycle
        ):
            raise TypeError("OCP viewport lifecycle is invalid")
        self._kernel = kernel
        self._lifecycle = lifecycle or OcpViewportLifecycle()
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
        self._simulations: dict[OperationId, tuple[AIS_Shape, ...]] = {}
        self._simulation_marker_objects: dict[OperationId, dict[int, str]] = {}
        self._focused_simulation_marker: tuple[
            OperationId, str, AIS_Shape
        ] | None = None
        self._simulation_registry = SimulationPresentationRegistry()
        self._cam3d_preview_actor: _OcpCam3DPreviewActor | None = None
        self._lathe_preview_actor: _OcpLathePreviewActor | None = None
        self._owner_thread_id: int | None = None

    def get_status(self) -> ViewportStatus:
        return ViewportStatus(
            available=True,
            initialized=self._lifecycle.initialized,
            backend="OCP",
        )

    def set_selection_callback(self, callback: SelectionCallback) -> None:
        self._selection_callback = callback

    def initialize(self, native_window_id: int) -> None:
        current_thread = get_ident()
        if self._owner_thread_id is None:
            self._owner_thread_id = current_thread
        elif self._owner_thread_id != current_thread:
            raise RuntimeError("OCP viewport must initialize on its owner thread")
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
        self._clear_cam3d_preview_unconditionally()
        self._clear_lathe_preview_unconditionally()
        self.clear_simulations()
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
        self._clear_cam3d_preview_unconditionally()
        self._clear_lathe_preview_unconditionally()
        self.clear_simulations()
        simulation_registry = getattr(self, "_simulation_registry", None)
        if simulation_registry is not None:
            simulation_registry.bind_project(None, None)
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
        simulation_registry = getattr(self, "_simulation_registry", None)
        current_simulation = (
            simulation_registry.current(operation_id)
            if simulation_registry is not None
            else None
        )
        if (
            current_simulation is not None
            and current_simulation.artifact_fingerprint != artifact.artifact_fingerprint
        ):
            try:
                self.remove_simulation(operation_id)
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
            if getattr(self, "_simulation_registry", None) is not None:
                self.remove_simulation(operation_id)
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
        metadata_registry = getattr(self, "_toolpath_metadata", {})
        previous_metadata = metadata_registry.pop(operation_id, None)
        try:
            if getattr(self, "_simulation_registry", None) is not None:
                self.remove_simulation(operation_id)
        except Exception:
            self._toolpaths[operation_id] = presentations
            if previous_metadata is not None:
                metadata_registry[operation_id] = previous_metadata
            for presentation in presentations:
                context.Display(presentation, False)
                if previous_metadata is not None and not previous_metadata.visible:
                    context.Erase(presentation, False)
            context.UpdateCurrentViewer()
            raise

    def bind_simulation_project(
        self,
        project_id: UUID | None,
        generation: int | None,
    ) -> None:
        """Bind overlay metadata to one project generation, clearing old native state."""
        if (
            project_id == self._simulation_registry.project_id
            and generation == self._simulation_registry.generation
        ):
            return
        self.clear_simulations()
        self._simulation_registry.bind_project(project_id, generation)

    def request_simulation_display(
        self,
        operation_id: OperationId,
        *,
        generation: int,
    ) -> SimulationDisplayRequest | None:
        return self._simulation_registry.request_display(
            operation_id,
            generation=generation,
        )

    def display_simulation(
        self,
        result: SimulationResult,
        artifact: ToolpathArtifact,
        wcs: WcsFrame,
        context: SimulationDisplayContext,
        request: SimulationDisplayRequest | None = None,
        policy: SimulationDisplayPolicy | None = None,
    ) -> bool:
        """Atomically validate, render and replace one simulation overlay."""
        self._require_initialized()
        candidate = self._simulation_registry.prepare(
            result=result,
            artifact=artifact,
            wcs=wcs,
            context=context,
            request=request,
            policy=policy,
        )
        if candidate is None:
            return False
        operation_id = candidate.key.operation_id
        focused_marker = getattr(self, "_focused_simulation_marker", None)
        if focused_marker is not None and focused_marker[0] == operation_id:
            self.clear_simulation_issue_focus()
        previous_metadata = self._simulation_registry.current(operation_id)
        previous_objects = self._simulations.get(operation_id, ())
        previous_marker_objects = self._simulation_marker_objects.get(
            operation_id,
            {},
        )
        objects, marker_objects = self._build_simulation_candidate(candidate)
        try:
            committed = self._simulation_registry.commit(candidate, request=request)
        except Exception:
            self._discard_simulation_candidate(objects)
            raise
        if not committed:
            self._discard_simulation_candidate(objects)
            return False
        self._simulations[operation_id] = objects
        self._simulation_marker_objects[operation_id] = marker_objects
        context_native = self._lifecycle.context
        try:
            for native in previous_objects:
                context_native.Remove(native, False)
            context_native.UpdateCurrentViewer()
        except Exception as error:
            self._simulation_registry.restore(operation_id, previous_metadata)
            if previous_metadata is None:
                self._simulations.pop(operation_id, None)
                self._simulation_marker_objects.pop(operation_id, None)
            else:
                self._simulations[operation_id] = previous_objects
                self._simulation_marker_objects[operation_id] = previous_marker_objects
            cleanup_error = self._rollback_simulation_swap(
                candidate_objects=objects,
                previous_objects=previous_objects,
                previous_visible=(
                    previous_metadata.visible
                    if previous_metadata is not None
                    else True
                ),
            )
            if cleanup_error is not None:
                raise RuntimeError(
                    "Simulation replacement rollback failed"
                ) from error
            raise
        return True

    def get_simulation_presentations(self) -> tuple[SimulationPresentation, ...]:
        """Return only native-free current-result metadata."""
        return self._simulation_registry.presentations

    def set_simulation_visibility(
        self,
        operation_id: OperationId,
        visible: bool,
    ) -> None:
        metadata = self._simulation_registry.current(operation_id)
        if metadata is None:
            return
        context = self._lifecycle.context
        objects = self._simulations.get(operation_id, ())
        try:
            for native in objects:
                (context.Display if visible else context.Erase)(native, False)
            context.UpdateCurrentViewer()
        except Exception:
            for native in objects:
                (context.Display if metadata.visible else context.Erase)(native, False)
            context.UpdateCurrentViewer()
            raise
        self._simulation_registry.set_visible(operation_id, visible)

    def lookup_simulation_issue(
        self,
        *,
        project_id: UUID,
        operation_id: OperationId,
        result_id: SimulationResultId,
        marker_id: str,
    ) -> SimulationIssueMarker | None:
        """Resolve marker metadata without producing a CAD GeometryReference."""
        return self._simulation_registry.lookup_issue(
            project_id=project_id,
            operation_id=operation_id,
            result_id=result_id,
            marker_id=marker_id,
        )

    def lookup_native_simulation_marker(
        self,
        native: object,
    ) -> SimulationIssueMarker | None:
        """Selection foundation for a future issue controller/panel."""
        native_id = id(native)
        for operation_id, native_markers in self._simulation_marker_objects.items():
            marker_id = native_markers.get(native_id)
            if marker_id is None:
                continue
            metadata = self._simulation_registry.current(operation_id)
            if metadata is None:
                return None
            return self.lookup_simulation_issue(
                project_id=metadata.key.project_id,
                operation_id=operation_id,
                result_id=metadata.key.result_id,
                marker_id=marker_id,
            )
        return None

    def focus_simulation_issue(
        self,
        *,
        project_id: UUID,
        operation_id: OperationId,
        result_id: SimulationResultId,
        marker_id: str,
    ) -> bool:
        """Highlight one current marker without activating CAD selection."""
        marker = self.lookup_simulation_issue(
            project_id=project_id,
            operation_id=operation_id,
            result_id=result_id,
            marker_id=marker_id,
        )
        if marker is None:
            return False
        native_ids = self._simulation_marker_objects.get(operation_id, {})
        native = next(
            (
                item
                for item in self._simulations.get(operation_id, ())
                if native_ids.get(id(item)) == marker_id
            ),
            None,
        )
        if native is None:
            return False
        self.clear_simulation_issue_focus()
        context = self._lifecycle.context
        context.SetColor(
            native,
            Quantity_Color(1.0, 1.0, 0.0, Quantity_TOC_RGB),
            False,
        )
        context.UpdateCurrentViewer()
        self._focused_simulation_marker = (operation_id, marker_id, native)
        return True

    def clear_simulation_issue_focus(self) -> None:
        focused = getattr(self, "_focused_simulation_marker", None)
        if focused is None:
            return
        operation_id, marker_id, native = focused
        metadata = self._simulation_registry.current(operation_id)
        marker = (
            next(
                (item for item in metadata.markers if item.marker_id == marker_id),
                None,
            )
            if metadata is not None
            else None
        )
        if marker is not None and self._lifecycle.initialized:
            self._lifecycle.context.SetColor(
                native,
                Quantity_Color(
                    *_simulation_marker_color(marker),
                    Quantity_TOC_RGB,
                ),
                False,
            )
            self._lifecycle.context.UpdateCurrentViewer()
        self._focused_simulation_marker = None

    def remove_simulation(self, operation_id: OperationId) -> None:
        """Remove one overlay atomically and invalidate its marker identities."""
        focused_marker = getattr(self, "_focused_simulation_marker", None)
        if focused_marker is not None and focused_marker[0] == operation_id:
            self.clear_simulation_issue_focus()
        objects = self._simulations.get(operation_id, ())
        metadata = self._simulation_registry.current(operation_id)
        if not objects and metadata is None:
            self._simulation_registry.remove(operation_id)
            return
        if not self._lifecycle.initialized:
            self._simulations.pop(operation_id, None)
            self._simulation_marker_objects.pop(operation_id, None)
            self._simulation_registry.remove(operation_id)
            return
        context = self._lifecycle.context
        try:
            for native in objects:
                context.Remove(native, False)
            context.UpdateCurrentViewer()
        except Exception:
            for native in objects:
                context.Display(native, False)
                if metadata is not None and not metadata.visible:
                    context.Erase(native, False)
            context.UpdateCurrentViewer()
            raise
        self._simulations.pop(operation_id, None)
        self._simulation_marker_objects.pop(operation_id, None)
        self._simulation_registry.remove(operation_id)

    def clear_simulations(self) -> None:
        """Clear every project overlay while leaving source toolpaths untouched."""
        self.clear_simulation_issue_focus()
        simulations = getattr(self, "_simulations", None)
        simulation_registry = getattr(self, "_simulation_registry", None)
        if simulations is None or simulation_registry is None:
            return
        if not simulations:
            self._simulation_marker_objects.clear()
            simulation_registry.clear()
            return
        if not self._lifecycle.initialized:
            self._simulations.clear()
            self._simulation_marker_objects.clear()
            self._simulation_registry.clear()
            return
        context = self._lifecycle.context
        snapshot = dict(self._simulations)
        metadata = {
            item.key.operation_id: item
            for item in self._simulation_registry.presentations
        }
        try:
            for objects in snapshot.values():
                for native in objects:
                    context.Remove(native, False)
            context.UpdateCurrentViewer()
        except Exception:
            for operation_id, objects in snapshot.items():
                for native in objects:
                    context.Display(native, False)
                    if not metadata[operation_id].visible:
                        context.Erase(native, False)
            context.UpdateCurrentViewer()
            raise
        self._simulations.clear()
        self._simulation_marker_objects.clear()
        self._simulation_registry.clear()

    def publish_cam3d_preview(
        self,
        publication: Cam3DPreviewPublication,
    ) -> Cam3DPreviewPublicationResult:
        """Build and atomically replace one exact-owner CAM 3D preview actor."""
        if not isinstance(publication, Cam3DPreviewPublication):
            return Cam3DPreviewPublicationResult(
                Cam3DPreviewPublicationCode.INVALID_PAYLOAD
            )
        if self._closed:
            return Cam3DPreviewPublicationResult(
                Cam3DPreviewPublicationCode.CLOSED,
                publication.identity,
            )
        if not self._lifecycle.initialized:
            return Cam3DPreviewPublicationResult(
                Cam3DPreviewPublicationCode.NOT_INITIALIZED,
                publication.identity,
            )
        if not self._on_owner_thread():
            return Cam3DPreviewPublicationResult(
                Cam3DPreviewPublicationCode.WRONG_THREAD,
                publication.identity,
            )
        previous = getattr(self, "_cam3d_preview_actor", None)
        if previous is not None and (
            previous.identity.ownership != publication.identity.ownership
            or previous.identity.project_generation
            != publication.identity.project_generation
        ):
            return Cam3DPreviewPublicationResult(
                Cam3DPreviewPublicationCode.OWNERSHIP_MISMATCH,
                previous.identity,
            )
        try:
            candidate = self._build_cam3d_preview_actor(publication)
        except _OcpCam3DPreviewRollbackError:
            logging.getLogger(__name__).exception(
                "CAM 3D preview candidate cleanup failed"
            )
            return Cam3DPreviewPublicationResult(
                Cam3DPreviewPublicationCode.ROLLBACK_FAILURE,
                previous.identity if previous is not None else None,
            )
        except Exception:
            logging.getLogger(__name__).exception(
                "CAM 3D preview candidate build/display failed"
            )
            return Cam3DPreviewPublicationResult(
                Cam3DPreviewPublicationCode.BACKEND_FAILURE,
                previous.identity if previous is not None else None,
            )

        self._cam3d_preview_actor = candidate
        if previous is None:
            return Cam3DPreviewPublicationResult(
                Cam3DPreviewPublicationCode.PUBLISHED,
                candidate.identity,
            )
        context = self._lifecycle.context
        try:
            context.Remove(previous.native, False)
            context.UpdateCurrentViewer()
        except Exception:
            self._cam3d_preview_actor = previous
            rollback_error = self._rollback_cam3d_preview_swap(
                candidate,
                previous,
            )
            logging.getLogger(__name__).exception(
                "CAM 3D preview replacement failed"
            )
            return Cam3DPreviewPublicationResult(
                (
                    Cam3DPreviewPublicationCode.ROLLBACK_FAILURE
                    if rollback_error is not None
                    else Cam3DPreviewPublicationCode.BACKEND_FAILURE
                ),
                previous.identity,
            )
        return Cam3DPreviewPublicationResult(
            Cam3DPreviewPublicationCode.REPLACED,
            candidate.identity,
        )

    def clear_cam3d_preview(
        self,
        ownership: Cam3DPreviewOwnership,
    ) -> Cam3DPreviewPublicationResult:
        """Clear only an actor owned by the exact semantic context."""
        if not isinstance(ownership, Cam3DPreviewOwnership):
            return Cam3DPreviewPublicationResult(
                Cam3DPreviewPublicationCode.INVALID_PAYLOAD
            )
        if self._closed:
            return Cam3DPreviewPublicationResult(
                Cam3DPreviewPublicationCode.CLOSED
            )
        if not self._on_owner_thread():
            return Cam3DPreviewPublicationResult(
                Cam3DPreviewPublicationCode.WRONG_THREAD
            )
        actor = self._cam3d_preview_actor
        if actor is None:
            return Cam3DPreviewPublicationResult(
                Cam3DPreviewPublicationCode.ALREADY_CLEAR
            )
        if actor.identity.ownership != ownership:
            return Cam3DPreviewPublicationResult(
                Cam3DPreviewPublicationCode.OWNERSHIP_MISMATCH,
                actor.identity,
            )
        try:
            self._remove_cam3d_preview_actor(actor)
        except Exception:
            rollback_error = self._restore_cam3d_preview_actor(actor)
            logging.getLogger(__name__).exception(
                "CAM 3D preview clear failed"
            )
            return Cam3DPreviewPublicationResult(
                (
                    Cam3DPreviewPublicationCode.ROLLBACK_FAILURE
                    if rollback_error is not None
                    else Cam3DPreviewPublicationCode.BACKEND_FAILURE
                ),
                actor.identity,
            )
        self._cam3d_preview_actor = None
        return Cam3DPreviewPublicationResult(
            Cam3DPreviewPublicationCode.CLEARED,
            actor.identity,
        )

    def get_cam3d_preview_identity(self) -> Cam3DPreviewActorIdentity | None:
        actor = getattr(self, "_cam3d_preview_actor", None)
        return actor.identity if actor is not None else None

    def _build_cam3d_preview_actor(
        self,
        publication: Cam3DPreviewPublication,
    ) -> _OcpCam3DPreviewActor:
        mesh = publication.mesh
        triangulation = Poly_Triangulation(
            mesh.vertex_count,
            mesh.triangle_count,
            False,
            False,
        )
        for index, point in enumerate(mesh.vertices, start=1):
            triangulation.SetNode(index, gp_Pnt(*point))
        for index, triangle in enumerate(mesh.triangles, start=1):
            triangulation.SetTriangle(
                index,
                Poly_Triangle(*(item + 1 for item in triangle)),
            )
        triangulation.ComputeNormals()
        native = AIS_Triangulation(triangulation)
        actor = _OcpCam3DPreviewActor(
            publication.identity,
            triangulation,
            native,
        )
        context = self._lifecycle.context
        try:
            context.Display(native, False)
            context.SetColor(
                native,
                Quantity_Color(*_CAM3D_PREVIEW_COLOR, Quantity_TOC_RGB),
                False,
            )
            context.SetTransparency(
                native,
                _CAM3D_PREVIEW_TRANSPARENCY,
                False,
            )
            deactivate = getattr(context, "Deactivate", None)
            if callable(deactivate):
                deactivate(native)
            context.UpdateCurrentViewer()
        except Exception as error:
            cleanup_error = self._discard_cam3d_preview_actor(actor)
            if cleanup_error is not None:
                raise _OcpCam3DPreviewRollbackError(
                    "CAM 3D preview candidate cleanup failed"
                ) from error
            raise
        return actor

    def _remove_cam3d_preview_actor(
        self,
        actor: _OcpCam3DPreviewActor,
    ) -> None:
        context = self._lifecycle.context
        context.Remove(actor.native, False)
        context.UpdateCurrentViewer()

    def _discard_cam3d_preview_actor(
        self,
        actor: _OcpCam3DPreviewActor,
    ) -> Exception | None:
        context = self._lifecycle.context
        first_error: Exception | None = None
        try:
            context.Remove(actor.native, False)
        except Exception as error:
            first_error = error
        try:
            context.UpdateCurrentViewer()
        except Exception as error:
            first_error = first_error or error
        return first_error

    def _restore_cam3d_preview_actor(
        self,
        actor: _OcpCam3DPreviewActor,
    ) -> Exception | None:
        context = self._lifecycle.context
        try:
            context.Display(actor.native, False)
            deactivate = getattr(context, "Deactivate", None)
            if callable(deactivate):
                deactivate(actor.native)
            context.UpdateCurrentViewer()
        except Exception as error:
            return error
        return None

    def _rollback_cam3d_preview_swap(
        self,
        candidate: _OcpCam3DPreviewActor,
        previous: _OcpCam3DPreviewActor,
    ) -> Exception | None:
        first_error = self._discard_cam3d_preview_actor(candidate)
        restore_error = self._restore_cam3d_preview_actor(previous)
        return first_error or restore_error

    def _clear_cam3d_preview_unconditionally(self) -> None:
        actor = getattr(self, "_cam3d_preview_actor", None)
        if actor is None:
            return
        self._remove_cam3d_preview_actor(actor)
        self._cam3d_preview_actor = None

    def publish_lathe_preview(
        self,
        publication: LathePreviewPublication,
    ) -> LathePreviewPublicationResult:
        """Build and atomically replace the current Lathe path actor group."""

        if not isinstance(publication, LathePreviewPublication):
            return LathePreviewPublicationResult(
                LathePreviewPublicationCode.INVALID_PAYLOAD
            )
        if getattr(self, "_closed", False):
            return LathePreviewPublicationResult(
                LathePreviewPublicationCode.CLOSED,
                publication.identity,
            )
        lifecycle = getattr(self, "_lifecycle", None)
        if lifecycle is None or not getattr(lifecycle, "initialized", False):
            return LathePreviewPublicationResult(
                LathePreviewPublicationCode.NOT_INITIALIZED,
                publication.identity,
            )
        try:
            context = lifecycle.context
        except (AttributeError, RuntimeError):
            context = None
        if context is None:
            return LathePreviewPublicationResult(
                LathePreviewPublicationCode.NOT_INITIALIZED,
                publication.identity,
            )
        if not self._on_owner_thread():
            return LathePreviewPublicationResult(
                LathePreviewPublicationCode.WRONG_THREAD,
                publication.identity,
            )
        previous = self._lathe_preview_actor_or_none()
        try:
            candidate = self._build_lathe_preview_actor(publication)
        except _OcpLathePreviewRollbackError:
            logging.getLogger(__name__).exception(
                "Lathe preview candidate cleanup failed"
            )
            return LathePreviewPublicationResult(
                LathePreviewPublicationCode.ROLLBACK_FAILURE,
                previous.identity if previous is not None else None,
            )
        except Exception:
            logging.getLogger(__name__).exception(
                "Lathe preview candidate build/display failed"
            )
            return LathePreviewPublicationResult(
                LathePreviewPublicationCode.BACKEND_FAILURE,
                previous.identity if previous is not None else None,
            )
        self._lathe_preview_actor = candidate
        if previous is None:
            return LathePreviewPublicationResult(
                LathePreviewPublicationCode.PUBLISHED,
                candidate.identity,
            )
        try:
            self._remove_lathe_preview_actor(previous)
        except Exception:
            self._lathe_preview_actor = previous
            rollback_error = self._rollback_lathe_preview_swap(
                candidate, previous
            )
            logging.getLogger(__name__).exception(
                "Lathe preview replacement failed"
            )
            return LathePreviewPublicationResult(
                (
                    LathePreviewPublicationCode.ROLLBACK_FAILURE
                    if rollback_error is not None
                    else LathePreviewPublicationCode.BACKEND_FAILURE
                ),
                previous.identity,
            )
        return LathePreviewPublicationResult(
            LathePreviewPublicationCode.REPLACED,
            candidate.identity,
        )

    def clear_lathe_preview(
        self,
        ownership: LathePreviewOwnership,
    ) -> LathePreviewPublicationResult:
        """Clear only the exact current Lathe operation owner."""

        if not isinstance(ownership, LathePreviewOwnership):
            return LathePreviewPublicationResult(
                LathePreviewPublicationCode.INVALID_PAYLOAD
            )
        if getattr(self, "_closed", False):
            return LathePreviewPublicationResult(
                LathePreviewPublicationCode.CLOSED
            )
        if not self._on_owner_thread():
            return LathePreviewPublicationResult(
                LathePreviewPublicationCode.WRONG_THREAD
            )
        actor = self._lathe_preview_actor_or_none()
        if actor is None:
            return LathePreviewPublicationResult(
                LathePreviewPublicationCode.ALREADY_CLEAR
            )
        if actor.identity.ownership != ownership:
            return LathePreviewPublicationResult(
                LathePreviewPublicationCode.OWNERSHIP_MISMATCH,
                actor.identity,
            )
        try:
            self._remove_lathe_preview_actor(actor)
        except Exception:
            rollback_error = self._restore_lathe_preview_actor(actor)
            logging.getLogger(__name__).exception("Lathe preview clear failed")
            return LathePreviewPublicationResult(
                (
                    LathePreviewPublicationCode.ROLLBACK_FAILURE
                    if rollback_error is not None
                    else LathePreviewPublicationCode.BACKEND_FAILURE
                ),
                actor.identity,
            )
        self._lathe_preview_actor = None
        return LathePreviewPublicationResult(
            LathePreviewPublicationCode.CLEARED,
            actor.identity,
        )

    def get_lathe_preview_identity(self) -> LathePreviewActorIdentity | None:
        actor = self._lathe_preview_actor_or_none()
        return actor.identity if actor is not None else None

    def _lathe_preview_actor_or_none(self) -> _OcpLathePreviewActor | None:
        """Treat an absent optional Lathe slot as an empty preview state."""

        return getattr(self, "_lathe_preview_actor", None)

    def _build_lathe_preview_actor(
        self,
        publication: LathePreviewPublication,
    ) -> _OcpLathePreviewActor:
        context = self._lifecycle.context
        natives: list[AIS_Shape] = []
        try:
            for motion_class in LatheMotionClass:
                segments = tuple(
                    item
                    for item in publication.segments
                    if item.motion_class is motion_class
                )
                if not segments:
                    continue
                compound, builder = TopoDS_Compound(), BRep_Builder()
                builder.MakeCompound(compound)
                for segment in segments:
                    builder.Add(
                        compound,
                        BRepBuilderAPI_MakeEdge(
                            gp_Pnt(*segment.start),
                            gp_Pnt(*segment.end),
                        ).Edge(),
                    )
                native = AIS_Shape(compound)
                natives.append(native)
                context.Display(native, False)
                context.SetColor(
                    native,
                    Quantity_Color(
                        *_LATHE_PREVIEW_COLORS[motion_class],
                        Quantity_TOC_RGB,
                    ),
                    False,
                )
                deactivate = getattr(context, "Deactivate", None)
                if callable(deactivate):
                    deactivate(native)
            context.UpdateCurrentViewer()
        except Exception as error:
            actor = _OcpLathePreviewActor(publication.identity, tuple(natives))
            cleanup_error = self._discard_lathe_preview_actor(actor)
            if cleanup_error is not None:
                raise _OcpLathePreviewRollbackError(
                    "Lathe preview candidate cleanup failed"
                ) from error
            raise
        return _OcpLathePreviewActor(publication.identity, tuple(natives))

    def _remove_lathe_preview_actor(self, actor: _OcpLathePreviewActor) -> None:
        context = self._lifecycle.context
        for native in actor.natives:
            context.Remove(native, False)
        context.UpdateCurrentViewer()

    def _discard_lathe_preview_actor(
        self, actor: _OcpLathePreviewActor
    ) -> Exception | None:
        first_error: Exception | None = None
        context = self._lifecycle.context
        for native in actor.natives:
            try:
                context.Remove(native, False)
            except Exception as error:
                first_error = first_error or error
        try:
            context.UpdateCurrentViewer()
        except Exception as error:
            first_error = first_error or error
        return first_error

    def _restore_lathe_preview_actor(
        self, actor: _OcpLathePreviewActor
    ) -> Exception | None:
        context = self._lifecycle.context
        try:
            for native in actor.natives:
                context.Display(native, False)
                deactivate = getattr(context, "Deactivate", None)
                if callable(deactivate):
                    deactivate(native)
            context.UpdateCurrentViewer()
        except Exception as error:
            return error
        return None

    def _rollback_lathe_preview_swap(
        self,
        candidate: _OcpLathePreviewActor,
        previous: _OcpLathePreviewActor,
    ) -> Exception | None:
        first_error = self._discard_lathe_preview_actor(candidate)
        restore_error = self._restore_lathe_preview_actor(previous)
        return first_error or restore_error

    def _clear_lathe_preview_unconditionally(self) -> None:
        actor = self._lathe_preview_actor_or_none()
        if actor is None:
            return
        self._remove_lathe_preview_actor(actor)
        self._lathe_preview_actor = None

    def _on_owner_thread(self) -> bool:
        current = get_ident()
        owner_thread_id = getattr(self, "_owner_thread_id", None)
        if owner_thread_id is None:
            self._owner_thread_id = current
            owner_thread_id = current
        return owner_thread_id == current

    def _build_simulation_candidate(
        self,
        metadata: SimulationPresentation,
    ) -> tuple[tuple[AIS_Shape, ...], dict[int, str]]:
        context = self._lifecycle.context
        objects: list[AIS_Shape] = []
        marker_objects: dict[int, str] = {}
        try:
            status_point = metadata.statistics.bounds.minimum
            status_native = AIS_Shape(
                BRepBuilderAPI_MakeVertex(
                    gp_Pnt(status_point.x, status_point.y, status_point.z)
                ).Vertex()
            )
            objects.append(status_native)
            self._display_simulation_native(
                status_native,
                _SIMULATION_STATUS_COLORS[metadata.status],
                metadata.visible,
            )
            for semantic in SimulationPathSemantic:
                segments = tuple(
                    item for item in metadata.path_segments
                    if item.semantic is semantic
                )
                if not segments:
                    continue
                compound, builder = TopoDS_Compound(), BRep_Builder()
                builder.MakeCompound(compound)
                edge_count = 0
                for segment in segments:
                    for start, end in zip(segment.points, segment.points[1:]):
                        if start == end:
                            continue
                        builder.Add(
                            compound,
                            BRepBuilderAPI_MakeEdge(
                                gp_Pnt(start.x, start.y, start.z),
                                gp_Pnt(end.x, end.y, end.z),
                            ).Edge(),
                        )
                        edge_count += 1
                if edge_count == 0:
                    continue
                native = AIS_Shape(compound)
                objects.append(native)
                self._display_simulation_native(
                    native,
                    _SIMULATION_PATH_COLORS[semantic],
                    metadata.visible,
                )
            for marker in metadata.markers:
                native = _simulation_marker_shape(marker)
                if native is None:
                    continue
                objects.append(native)
                marker_objects[id(native)] = marker.marker_id
                self._display_simulation_native(
                    native,
                    _simulation_marker_color(marker),
                    metadata.visible,
                )
            context.UpdateCurrentViewer()
        except Exception:
            cleanup_error = None
            for native in objects:
                try:
                    context.Remove(native, False)
                except Exception as error:
                    cleanup_error = error
            context.UpdateCurrentViewer()
            if cleanup_error is not None:
                raise RuntimeError(
                    "Simulation candidate display cleanup failed"
                ) from cleanup_error
            raise
        return tuple(objects), marker_objects

    def _display_simulation_native(
        self,
        native: AIS_Shape,
        color: tuple[float, float, float],
        visible: bool,
    ) -> None:
        context = self._lifecycle.context
        context.SetColor(
            native,
            Quantity_Color(*color, Quantity_TOC_RGB),
            False,
        )
        context.Display(native, False)
        # Simulation overlays must never enter the CAD topology picking path.
        deactivate = getattr(context, "Deactivate", None)
        if callable(deactivate):
            deactivate(native)
        if not visible:
            context.Erase(native, False)

    def _discard_simulation_candidate(
        self,
        objects: tuple[AIS_Shape, ...],
    ) -> None:
        context = self._lifecycle.context
        cleanup_error = None
        for native in objects:
            try:
                context.Remove(native, False)
            except Exception as error:
                cleanup_error = error
        context.UpdateCurrentViewer()
        if cleanup_error is not None:
            raise RuntimeError("Simulation candidate discard failed") from cleanup_error

    def _rollback_simulation_swap(
        self,
        *,
        candidate_objects: tuple[AIS_Shape, ...],
        previous_objects: tuple[AIS_Shape, ...],
        previous_visible: bool,
    ) -> Exception | None:
        context = self._lifecycle.context
        cleanup_error: Exception | None = None
        for native in candidate_objects:
            try:
                context.Remove(native, False)
            except Exception as error:
                cleanup_error = cleanup_error or error
        for native in previous_objects:
            try:
                context.Display(native, False)
                if not previous_visible:
                    context.Erase(native, False)
            except Exception as error:
                cleanup_error = cleanup_error or error
        try:
            context.UpdateCurrentViewer()
        except Exception as error:
            cleanup_error = cleanup_error or error
        return cleanup_error

    def fit_all(self) -> None:
        self._lifecycle.fit_all()

    def set_background_color(self, color: ObjectColor) -> None:
        """Change only the viewport clear color and redraw once."""
        self._lifecycle.set_background_color(color)

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
        self._clear_cam3d_preview_unconditionally()
        self._clear_lathe_preview_unconditionally()
        self.clear_simulations()
        simulation_registry = getattr(self, "_simulation_registry", None)
        if simulation_registry is not None:
            simulation_registry.bind_project(None, None)
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
        self._owner_thread_id = None

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
