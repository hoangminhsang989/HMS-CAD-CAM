"""Translate OCCT selection into stable product metadata."""

from __future__ import annotations

from OCP.AIS import AIS_InteractiveContext, AIS_Shape
from OCP.TopAbs import TopAbs_ShapeEnum
from OCP.TopExp import TopExp
from OCP.TopTools import TopTools_IndexedMapOfShape
from OCP.TopoDS import TopoDS_Shape
from OCP.V3d import V3d_View

from hms_cadcam.cad.models import CadDocumentId
from hms_cadcam.cad.ocp.topology import get_bounding_box
from hms_cadcam.viewer.models import SelectionMetadata, SelectionMode

_SELECTION_TOPOLOGY = {
    SelectionMode.SOLID: TopAbs_ShapeEnum.TopAbs_SOLID,
    SelectionMode.FACE: TopAbs_ShapeEnum.TopAbs_FACE,
    SelectionMode.EDGE: TopAbs_ShapeEnum.TopAbs_EDGE,
}


class OcpSelectionController:
    """Own OCCT selection state for exactly one displayed document."""

    def __init__(self, context: AIS_InteractiveContext) -> None:
        self._context = context
        self._mode = SelectionMode.SOLID
        self._document_id: CadDocumentId | None = None
        self._document_shape: TopoDS_Shape | None = None
        self._presentation: AIS_Shape | None = None

    def bind_document(
        self,
        document_id: CadDocumentId,
        shape: TopoDS_Shape,
        presentation: AIS_Shape,
    ) -> None:
        self.clear_document()
        self._document_id = document_id
        self._document_shape = shape
        self._presentation = presentation
        self._activate_mode()

    def set_mode(self, mode: SelectionMode) -> None:
        self._mode = mode
        self._context.ClearSelected(False)
        self._activate_mode()

    def hover(self, view: V3d_View, x: int, y: int) -> None:
        self._context.MoveTo(x, y, view, True)

    def pick(self, view: V3d_View, x: int, y: int) -> tuple[SelectionMetadata, ...]:
        self._context.MoveTo(x, y, view, True)
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
            if not selected_shape.IsNull():
                index = self._shape_index(selected_shape)
                if index > 0:
                    items.append(
                        SelectionMetadata(
                            document_id=self._document_id,
                            selection_id=(
                                f"{self._document_id}:{self._mode.value}:{index}"
                            ),
                            topology=self._mode,
                            bounding_box=get_bounding_box(selected_shape),
                        )
                    )
            self._context.NextSelected()
        return tuple(items)

    def clear_document(self) -> None:
        self._context.ClearSelected(False)
        if self._presentation is not None:
            self._context.Deactivate(self._presentation)
        self._document_id = None
        self._document_shape = None
        self._presentation = None

    def _activate_mode(self) -> None:
        if self._presentation is None:
            return
        self._context.Deactivate(self._presentation)
        selection_index = AIS_Shape.SelectionMode_s(_SELECTION_TOPOLOGY[self._mode])
        self._context.Activate(self._presentation, selection_index)

    def _shape_index(self, selected_shape: TopoDS_Shape) -> int:
        if self._document_shape is None:
            return 0
        shapes = TopTools_IndexedMapOfShape()
        TopExp.MapShapes_s(
            self._document_shape,
            _SELECTION_TOPOLOGY[self._mode],
            shapes,
        )
        return shapes.FindIndex(selected_shape)
