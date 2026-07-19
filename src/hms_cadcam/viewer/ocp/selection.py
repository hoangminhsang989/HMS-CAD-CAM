"""Translate OCCT selection into stable product metadata."""

from __future__ import annotations

from OCP.AIS import AIS_InteractiveContext, AIS_Shape
from OCP.TopAbs import TopAbs_ShapeEnum
from OCP.TopExp import TopExp
from OCP.TopTools import TopTools_IndexedMapOfShape
from OCP.TopoDS import TopoDS_Shape
from OCP.V3d import V3d_View

from hms_cadcam.cad.models import CadDocumentId, CadObjectId
from hms_cadcam.cad.ocp.topology import get_bounding_box
from hms_cadcam.viewer.models import SelectionMetadata, SelectionMode

_SELECTION_TOPOLOGY = {
    SelectionMode.SOLID: TopAbs_ShapeEnum.TopAbs_SOLID,
    SelectionMode.FACE: TopAbs_ShapeEnum.TopAbs_FACE,
    SelectionMode.WIRE: TopAbs_ShapeEnum.TopAbs_WIRE,
    SelectionMode.EDGE: TopAbs_ShapeEnum.TopAbs_EDGE,
    SelectionMode.VERTEX: TopAbs_ShapeEnum.TopAbs_VERTEX,
}


class OcpSelectionController:
    """Own OCCT selection state for exactly one displayed document."""

    def __init__(self, context: AIS_InteractiveContext) -> None:
        self._context = context
        self._mode = SelectionMode.SOLID
        self._document_id: CadDocumentId | None = None
        self._document_shape: TopoDS_Shape | None = None
        self._presentation: AIS_Shape | None = None
        self._object_shapes: dict[CadObjectId, TopoDS_Shape] = {}
        self._presentations: dict[CadObjectId, AIS_Shape] = {}

    def bind_document(
        self,
        document_id: CadDocumentId,
        shape: TopoDS_Shape,
        presentation: AIS_Shape,
        object_shapes: dict[CadObjectId, TopoDS_Shape] | None = None,
        presentations: dict[CadObjectId, AIS_Shape] | None = None,
    ) -> None:
        self.clear_document()
        self._document_id = document_id
        self._document_shape = shape
        self._presentation = presentation
        fallback_id = CadObjectId(f"{document_id}:object:1")
        self._object_shapes = dict(object_shapes or {fallback_id: shape})
        self._presentations = dict(presentations or {fallback_id: presentation})
        self._activate_mode()

    def set_mode(self, mode: SelectionMode) -> None:
        self._mode = mode
        self._context.ClearSelected(False)
        self._activate_mode()

    def hover(self, view: V3d_View, x: int, y: int) -> None:
        self._context.MoveTo(x, y, view, True)

    def pick(
        self,
        view: V3d_View,
        x: int,
        y: int,
        extend: bool = False,
    ) -> tuple[SelectionMetadata, ...]:
        self._context.MoveTo(x, y, view, True)
        if extend and self._mode is SelectionMode.VERTEX:
            if len(self.current_metadata()) >= 2:
                self._context.ClearSelected(False)
                self._context.SelectDetected()
            else:
                self._context.ShiftSelect(True)
        else:
            self._context.SelectDetected()
        view.Redraw()
        return self.current_metadata()

    def current_metadata(self) -> tuple[SelectionMetadata, ...]:
        if self._document_id is None or self._document_shape is None:
            return ()
        items: list[SelectionMetadata] = []
        self._context.InitSelected()
        while self._context.MoreSelected():
            selected_shape = self._context.SelectedShape()
            object_id = self._object_id_for_selected_shape(selected_shape)
            if selected_shape.IsNull() and object_id is not None:
                selected_shape = self._first_topology_shape(
                    self._object_shapes[object_id]
                )
            if not selected_shape.IsNull():
                index = self._shape_index(selected_shape, object_id)
                if index <= 0 and object_id is not None:
                    selected_shape = self._first_topology_shape(
                        self._object_shapes[object_id]
                    )
                    index = self._shape_index(selected_shape, object_id)
                if index > 0:
                    items.append(
                        SelectionMetadata(
                            document_id=self._document_id,
                            selection_id=(
                                f"{self._document_id}:{self._mode.value}:{index}"
                            ),
                            topology=self._mode,
                            bounding_box=get_bounding_box(selected_shape),
                            object_id=object_id,
                        )
                    )
            self._context.NextSelected()
        return tuple(items)

    def select_objects(self, object_ids: tuple[CadObjectId, ...]) -> None:
        """Programmatically highlight managed AIS objects without topology expansion."""
        if any(object_id not in self._presentations for object_id in object_ids):
            raise KeyError("CAD object is not selectable in the active document")
        self._context.ClearSelected(False)
        for index, object_id in enumerate(object_ids):
            presentation = self._presentations[object_id]
            if index == 0:
                self._context.SetSelected(presentation, False)
            else:
                self._context.AddOrRemoveSelected(presentation, False)
        self._context.UpdateCurrentViewer()

    def clear_selection(self) -> None:
        self._context.ClearSelected(True)

    def clear_document(self) -> None:
        self._context.ClearSelected(False)
        for presentation in self._presentations.values():
            self._context.Deactivate(presentation)
        self._document_id = None
        self._document_shape = None
        self._presentation = None
        self._object_shapes = {}
        self._presentations = {}

    def _activate_mode(self) -> None:
        if not self._presentations:
            return
        selection_index = AIS_Shape.SelectionMode_s(_SELECTION_TOPOLOGY[self._mode])
        for presentation in self._presentations.values():
            self._context.Deactivate(presentation)
            self._context.Activate(presentation, selection_index)

    def _shape_index(
        self,
        selected_shape: TopoDS_Shape,
        object_id: CadObjectId | None,
    ) -> int:
        if self._document_shape is None:
            return 0
        index = self._shape_index_in(self._document_shape, selected_shape)
        if index > 0 or object_id is None:
            return index
        object_shape = self._object_shapes.get(object_id)
        return (
            self._shape_index_in(object_shape, selected_shape)
            if object_shape is not None
            else 0
        )

    def _shape_index_in(
        self,
        container: TopoDS_Shape,
        selected_shape: TopoDS_Shape,
    ) -> int:
        shapes = TopTools_IndexedMapOfShape()
        TopExp.MapShapes_s(
            container,
            _SELECTION_TOPOLOGY[self._mode],
            shapes,
        )
        return shapes.FindIndex(selected_shape)

    def _object_id_for_selected_shape(
        self,
        selected_shape: TopoDS_Shape,
    ) -> CadObjectId | None:
        try:
            selected_presentation = self._context.SelectedInteractive()
        except AttributeError:
            selected_presentation = None
        if selected_presentation is not None:
            for object_id, presentation in self._presentations.items():
                if selected_presentation is presentation or selected_presentation == presentation:
                    return object_id
        for object_id, object_shape in self._object_shapes.items():
            shapes = TopTools_IndexedMapOfShape()
            TopExp.MapShapes_s(
                object_shape,
                _SELECTION_TOPOLOGY[self._mode],
                shapes,
            )
            if shapes.FindIndex(selected_shape) > 0:
                return object_id
        return None

    def _first_topology_shape(self, container: TopoDS_Shape) -> TopoDS_Shape:
        shapes = TopTools_IndexedMapOfShape()
        TopExp.MapShapes_s(
            container,
            _SELECTION_TOPOLOGY[self._mode],
            shapes,
        )
        return shapes.FindKey(1) if shapes.Extent() > 0 else TopoDS_Shape()
