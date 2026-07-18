"""Own managed AIS presentations and their session-only appearance state."""

from __future__ import annotations

from dataclasses import replace

from OCP.AIS import AIS_InteractiveContext, AIS_InteractiveObject
from OCP.Quantity import Quantity_Color, Quantity_TOC_RGB

from hms_cadcam.cad.models import CadDocumentId, CadDocumentTree, CadObjectId
from hms_cadcam.viewer.models import ObjectAppearance, ObjectColor


class OcpPresentationRegistry:
    """Bind managed topology nodes to AIS objects for exactly one document."""

    def __init__(
        self,
        context: AIS_InteractiveContext,
        tree: CadDocumentTree,
        presentations: dict[CadObjectId, AIS_InteractiveObject],
    ) -> None:
        expected = {node.object_id for node in tree.presentation_nodes}
        if set(presentations) != expected:
            raise ValueError("Presentation registry does not match the document tree")
        self._context = context
        self.document_id = tree.document_id
        self.tree = tree
        self.presentations = dict(presentations)
        self.appearances = {
            node.object_id: ObjectAppearance() for node in tree.root.walk()
        }
        self._isolate_snapshot: dict[CadObjectId, bool] | None = None

    @property
    def isolate_active(self) -> bool:
        return self._isolate_snapshot is not None

    def contains(self, object_id: CadObjectId) -> bool:
        return self.tree.find(object_id) is not None

    def presentation_ids(self, object_id: CadObjectId) -> tuple[CadObjectId, ...]:
        node = self._require_node(object_id)
        return tuple(
            item.object_id for item in node.walk() if item.has_presentation
        )

    def set_visibility(self, object_id: CadObjectId, visible: bool) -> None:
        if not isinstance(visible, bool):
            raise TypeError("Object visibility must be bool")
        node = self._require_node(object_id)
        for item in node.walk():
            current = self.appearances[item.object_id]
            self.appearances[item.object_id] = replace(current, visible=visible)
            presentation = self.presentations.get(item.object_id)
            if presentation is None:
                continue
            if visible:
                self._context.Display(presentation, False)
            else:
                self._context.Erase(presentation, False)
        self._refresh_container_visibility()
        self._context.UpdateCurrentViewer()

    def set_color(self, object_id: CadObjectId, color: ObjectColor) -> None:
        if not isinstance(color, ObjectColor):
            raise TypeError("Object color must be ObjectColor")
        node = self._require_node(object_id)
        native_color = Quantity_Color(
            color.red,
            color.green,
            color.blue,
            Quantity_TOC_RGB,
        )
        for item in node.walk():
            current = self.appearances[item.object_id]
            self.appearances[item.object_id] = replace(current, color=color)
            presentation = self.presentations.get(item.object_id)
            if presentation is not None:
                self._context.SetColor(presentation, native_color, False)
        self._context.UpdateCurrentViewer()

    def set_transparency(self, object_id: CadObjectId, value: float) -> None:
        validated = ObjectAppearance(transparency=value).transparency
        node = self._require_node(object_id)
        for item in node.walk():
            current = self.appearances[item.object_id]
            self.appearances[item.object_id] = replace(
                current,
                transparency=validated,
            )
            presentation = self.presentations.get(item.object_id)
            if presentation is not None:
                self._context.SetTransparency(presentation, validated, False)
        self._context.UpdateCurrentViewer()

    def isolate(self, object_id: CadObjectId) -> None:
        target_ids = set(self.presentation_ids(object_id))
        if self._isolate_snapshot is None:
            self._isolate_snapshot = {
                item_id: appearance.visible
                for item_id, appearance in self.appearances.items()
            }
        else:
            self._apply_visibility_snapshot(self._isolate_snapshot)
        for presentation_id in self.presentations:
            self._set_leaf_visibility(
                presentation_id,
                presentation_id in target_ids,
            )
        self._refresh_container_visibility()
        self._context.UpdateCurrentViewer()

    def reset_isolate(self) -> None:
        snapshot = self._isolate_snapshot
        if snapshot is None:
            return
        self._apply_visibility_snapshot(snapshot)
        self._isolate_snapshot = None
        self._context.UpdateCurrentViewer()

    def clear_isolate(self) -> None:
        self._isolate_snapshot = None

    def _set_leaf_visibility(self, object_id: CadObjectId, visible: bool) -> None:
        current = self.appearances[object_id]
        self.appearances[object_id] = replace(current, visible=visible)
        presentation = self.presentations[object_id]
        if visible:
            self._context.Display(presentation, False)
        else:
            self._context.Erase(presentation, False)

    def _apply_visibility_snapshot(
        self,
        snapshot: dict[CadObjectId, bool],
    ) -> None:
        for object_id, visible in snapshot.items():
            appearance = self.appearances[object_id]
            self.appearances[object_id] = replace(appearance, visible=visible)
            presentation = self.presentations.get(object_id)
            if presentation is None:
                continue
            if visible:
                self._context.Display(presentation, False)
            else:
                self._context.Erase(presentation, False)

    def _refresh_container_visibility(self) -> None:
        for node in reversed(self.tree.root.walk()):
            if not node.children:
                continue
            visible = any(
                self.appearances[item.object_id].visible
                for child in node.children
                for item in child.walk()
                if item.has_presentation
            )
            self.appearances[node.object_id] = replace(
                self.appearances[node.object_id],
                visible=visible,
            )

    def _require_node(self, object_id: CadObjectId):
        node = self.tree.find(object_id)
        if node is None:
            raise KeyError(f"CAD object not found: {object_id}")
        return node
