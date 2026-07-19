"""Own managed AIS presentations and their session-only appearance state."""

from __future__ import annotations

import logging
from collections.abc import Callable
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
        presentation_ids = self.presentation_ids(object_id)
        previous = {
            item_id: self.appearances[item_id].visible
            for item_id in presentation_ids
        }
        desired = {item_id: visible for item_id in presentation_ids}
        self._apply_native_transaction(
            presentation_ids,
            lambda presentation, item_id: self._set_native_visibility(
                presentation,
                desired[item_id],
            ),
            lambda presentation, item_id: self._set_native_visibility(
                presentation,
                previous[item_id],
            ),
        )
        for item in node.walk():
            current = self.appearances[item.object_id]
            self.appearances[item.object_id] = replace(current, visible=visible)
        self._refresh_container_visibility()

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
        presentation_ids = self.presentation_ids(object_id)
        previous = {
            item_id: self.appearances[item_id].color
            for item_id in presentation_ids
        }
        self._apply_native_transaction(
            presentation_ids,
            lambda presentation, _item_id: self._context.SetColor(
                presentation,
                native_color,
                False,
            ),
            lambda presentation, item_id: self._context.SetColor(
                presentation,
                _native_color(previous[item_id]),
                False,
            ),
        )
        for item in node.walk():
            current = self.appearances[item.object_id]
            self.appearances[item.object_id] = replace(current, color=color)

    def set_transparency(self, object_id: CadObjectId, value: float) -> None:
        validated = ObjectAppearance(transparency=value).transparency
        node = self._require_node(object_id)
        presentation_ids = self.presentation_ids(object_id)
        previous = {
            item_id: self.appearances[item_id].transparency
            for item_id in presentation_ids
        }
        self._apply_native_transaction(
            presentation_ids,
            lambda presentation, _item_id: self._context.SetTransparency(
                presentation,
                validated,
                False,
            ),
            lambda presentation, item_id: self._context.SetTransparency(
                presentation,
                previous[item_id],
                False,
            ),
        )
        for item in node.walk():
            current = self.appearances[item.object_id]
            self.appearances[item.object_id] = replace(
                current,
                transparency=validated,
            )

    def isolate(self, object_id: CadObjectId) -> None:
        target_ids = set(self.presentation_ids(object_id))
        snapshot = self._isolate_snapshot
        if snapshot is None:
            snapshot = {
                item_id: appearance.visible
                for item_id, appearance in self.appearances.items()
            }
        presentation_ids = tuple(self.presentations)
        previous = {
            item_id: self.appearances[item_id].visible
            for item_id in presentation_ids
        }
        desired = {
            item_id: item_id in target_ids for item_id in presentation_ids
        }
        self._apply_native_transaction(
            presentation_ids,
            lambda presentation, item_id: self._set_native_visibility(
                presentation,
                desired[item_id],
            ),
            lambda presentation, item_id: self._set_native_visibility(
                presentation,
                previous[item_id],
            ),
        )
        for presentation_id, visible in desired.items():
            current = self.appearances[presentation_id]
            self.appearances[presentation_id] = replace(current, visible=visible)
        self._refresh_container_visibility()
        self._isolate_snapshot = snapshot

    def reset_isolate(self) -> None:
        snapshot = self._isolate_snapshot
        if snapshot is None:
            return
        presentation_ids = tuple(self.presentations)
        previous = {
            item_id: self.appearances[item_id].visible
            for item_id in presentation_ids
        }
        self._apply_native_transaction(
            presentation_ids,
            lambda presentation, item_id: self._set_native_visibility(
                presentation,
                snapshot[item_id],
            ),
            lambda presentation, item_id: self._set_native_visibility(
                presentation,
                previous[item_id],
            ),
        )
        for item_id, visible in snapshot.items():
            current = self.appearances[item_id]
            self.appearances[item_id] = replace(current, visible=visible)
        self._refresh_container_visibility()
        self._isolate_snapshot = None

    def clear_isolate(self) -> None:
        self._isolate_snapshot = None

    def _set_native_visibility(
        self,
        presentation: AIS_InteractiveObject,
        visible: bool,
    ) -> None:
        if visible:
            self._context.Display(presentation, False)
        else:
            self._context.Erase(presentation, False)

    def _apply_native_transaction(
        self,
        object_ids: tuple[CadObjectId, ...],
        apply: Callable[[AIS_InteractiveObject, CadObjectId], None],
        rollback: Callable[[AIS_InteractiveObject, CadObjectId], None],
    ) -> None:
        try:
            for object_id in object_ids:
                apply(self.presentations[object_id], object_id)
            self._context.UpdateCurrentViewer()
        except Exception as error:
            rollback_error = self._rollback_native(object_ids, rollback)
            if rollback_error is not None:
                raise RuntimeError(
                    "CAD appearance apply and rollback both failed"
                ) from error
            raise

    def _rollback_native(
        self,
        object_ids: tuple[CadObjectId, ...],
        rollback: Callable[[AIS_InteractiveObject, CadObjectId], None],
    ) -> Exception | None:
        first_error: Exception | None = None
        logger = logging.getLogger(__name__)
        for object_id in object_ids:
            try:
                rollback(self.presentations[object_id], object_id)
            except Exception as error:
                if first_error is None:
                    first_error = error
                logger.exception("Cannot rollback CAD appearance for %s", object_id)
        try:
            self._context.UpdateCurrentViewer()
        except Exception as error:
            if first_error is None:
                first_error = error
            logger.exception("Cannot redraw CAD viewer after appearance rollback")
        return first_error

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


def _native_color(color: ObjectColor) -> Quantity_Color:
    return Quantity_Color(
        color.red,
        color.green,
        color.blue,
        Quantity_TOC_RGB,
    )
