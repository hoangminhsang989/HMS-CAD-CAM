"""Stateful mouse interaction controller for an OCCT view."""

from __future__ import annotations

from OCP.V3d import V3d_View

from hms_cadcam.viewer.models import (
    KeyboardModifier,
    MouseButton,
    SelectionMetadata,
)
from hms_cadcam.viewer.ocp.selection import OcpSelectionController


class OcpInputController:
    """Map product mouse events to selection, rotation, panning and zoom."""

    def __init__(self, view: V3d_View, selection: OcpSelectionController) -> None:
        self._view = view
        self._selection = selection
        self._pressed_button = MouseButton.NONE
        self._origin: tuple[int, int] | None = None
        self._dragged = False

    def press(
        self,
        x: int,
        y: int,
        button: MouseButton,
        modifiers: KeyboardModifier,
    ) -> None:
        del modifiers
        self._pressed_button = button
        self._origin = (x, y)
        self._dragged = False
        if button is MouseButton.MIDDLE:
            self._view.StartRotation(x, y)

    def move(
        self,
        x: int,
        y: int,
        buttons: frozenset[MouseButton],
        modifiers: KeyboardModifier,
    ) -> None:
        del modifiers
        if self._origin is None or self._pressed_button not in buttons:
            if not buttons:
                self._selection.hover(self._view, x, y)
            return
        origin_x, origin_y = self._origin
        delta_x = x - origin_x
        delta_y = y - origin_y
        self._dragged = self._dragged or abs(delta_x) + abs(delta_y) > 2
        if self._pressed_button is MouseButton.MIDDLE:
            self._view.Rotation(x, y)
        elif self._pressed_button is MouseButton.RIGHT:
            self._view.Pan(delta_x, -delta_y, 1.0, True)
            self._origin = (x, y)
            self._view.Redraw()

    def release(
        self,
        x: int,
        y: int,
        button: MouseButton,
        modifiers: KeyboardModifier,
    ) -> tuple[SelectionMetadata, ...] | None:
        del modifiers
        selection = None
        if button is MouseButton.LEFT and not self._dragged:
            selection = self._selection.pick(self._view, x, y)
        self.reset()
        return selection

    def wheel(
        self,
        delta: int,
        modifiers: KeyboardModifier,
    ) -> None:
        del modifiers
        if delta == 0:
            return
        factor = 1.15 if delta > 0 else 1.0 / 1.15
        self._view.SetScale(max(1.0e-6, self._view.Scale() * factor))
        self._view.Redraw()

    def reset(self) -> None:
        self._pressed_button = MouseButton.NONE
        self._origin = None
        self._dragged = False
