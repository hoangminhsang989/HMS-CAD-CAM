"""Deterministic MaterialState cell-mask to validated RestRegion polygons."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from collections import deque
from typing import Iterable

from hms_cadcam.cam.domain import (
    ContentFingerprint, ContourLoop, ContourOrientation, ContourSegment,
    ContourCurveKind, Point3
)
from hms_cadcam.cam.domain.errors import CamValidationError
from hms_cadcam.cam.material_state import MaterialState


@dataclass(frozen=True, slots=True)
class RestRegion:
    """One connected region with an exterior and zero or more holes."""

    exterior: ContourLoop
    holes: tuple[ContourLoop, ...] = ()
    fingerprint: ContentFingerprint | None = None

    def __post_init__(self) -> None:
        if self.exterior.orientation is not ContourOrientation.COUNTERCLOCKWISE:
            raise CamValidationError("Rest region exterior must be counter-clockwise")
        if any(hole.orientation is not ContourOrientation.CLOCKWISE for hole in self.holes):
            raise CamValidationError("Rest region holes must be clockwise")
        if self.fingerprint is None:
            object.__setattr__(self, "fingerprint", ContentFingerprint.from_payload(self.to_dict()))

    def to_dict(self) -> dict:
        return {"exterior": self.exterior.to_dict(), "holes": [hole.to_dict() for hole in self.holes]}


def _area(loop: ContourLoop) -> float:
    points = [segment.start for segment in loop.segments]
    return 0.5 * sum(a.x * b.y - b.x * a.y for a, b in zip(points, (*points[1:], points[0]), strict=True))


def _orientation(a: Point3, b: Point3, c: Point3) -> float:
    return (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x)


def _inside(point: Point3, loop: ContourLoop) -> bool:
    inside = False
    points = [segment.start for segment in loop.segments]
    for first, second in zip(points, (*points[1:], points[0]), strict=True):
        if ((first.y > point.y) != (second.y > point.y)) and point.x < (second.x - first.x) * (point.y - first.y) / (second.y - first.y) + first.x:
            inside = not inside
    return inside


def validate_rest_region(region: RestRegion) -> None:
    """Independently validate topology before Pocket consumption."""
    loops = (region.exterior, *region.holes)
    for loop in loops:
        if not loop.closed or len(loop.segments) < 3:
            raise CamValidationError("Rest region contour is not a valid closed polygon")
        points = [segment.start for segment in loop.segments]
        if any(segment.start == segment.end for segment in loop.segments):
            raise CamValidationError("Rest region has a zero-length edge")
        for index, first in enumerate(points):
            first_end = loop.segments[index].end
            for other_index, second in enumerate(points):
                if other_index <= index or other_index in {index - 1, index + 1, (index + len(points) - 1) % len(points)}:
                    continue
                second_end = loop.segments[other_index].end
                if (_orientation(first, first_end, second) * _orientation(first, first_end, second_end) < 0
                        and _orientation(second, second_end, first) * _orientation(second, second_end, first_end) < 0):
                    raise CamValidationError("Rest region contour self-intersects")
    for hole in region.holes:
        if not _inside(hole.segments[0].start, region.exterior):
            raise CamValidationError("Rest region hole is outside exterior")


def _loop(edges: list[tuple[tuple[int, int], tuple[int, int]]], state: MaterialState) -> ContourLoop:
    if len(edges) < 3:
        raise CamValidationError("Rest region boundary is too small")
    unit = state.unit
    segments = []
    for (x1, y1), (x2, y2) in edges:
        segments.append(ContourSegment(
            ContourCurveKind.LINE,
            Point3(x1 * state.cell_size_x, y1 * state.cell_size_y, 0.0, unit),
            Point3(x2 * state.cell_size_x, y2 * state.cell_size_y, 0.0, unit),
        ))
    signed = 0.5 * sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(
        [edge[0] for edge in edges], [edge[1] for edge in edges], strict=True
    ))
    orientation = ContourOrientation.COUNTERCLOCKWISE if signed > 0 else ContourOrientation.CLOCKWISE
    return ContourLoop(tuple(segments), orientation)


def _trace(edges: set[tuple[tuple[int, int], tuple[int, int]]]) -> list[list[tuple[tuple[int, int], tuple[int, int]]]]:
    outgoing: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for start, end in edges:
        outgoing.setdefault(start, []).append(end)
    for values in outgoing.values():
        values.sort()
    remaining = set(edges)
    loops = []
    while remaining:
        edge = min(remaining)
        start, current = edge
        chain = [edge]
        remaining.remove(edge)
        while current != start:
            choices = sorted(end for candidate, end in remaining if candidate == current)
            if not choices:
                raise CamValidationError("Rest region boundary is not closed")
            next_edge = (current, choices[0])
            remaining.remove(next_edge)
            chain.append(next_edge)
            current = next_edge[1]
        loops.append(chain)
    return loops


def extract_cell_mask_regions(
    state: MaterialState, eligible_cells: Iterable[tuple[int, int]],
) -> tuple[RestRegion, ...]:
    """Extract deterministic 4-connected regions from ``(row, column)`` cells.

    The explicit mask boundary is useful to CAM algorithms that have already
    validated their material predicate.  Rejecting malformed cells is
    intentional: silently clipping them would create a false machining region.
    """
    if not isinstance(state, MaterialState):
        raise TypeError("Material state is invalid")
    eligible: set[tuple[int, int]] = set()
    for cell in eligible_cells:
        if (not isinstance(cell, tuple) or len(cell) != 2
                or type(cell[0]) is not int or type(cell[1]) is not int):
            raise CamValidationError("Rest region cell is invalid")
        row, column = cell
        if not 0 <= row < state.height or not 0 <= column < state.width:
            raise CamValidationError("Rest region cell is outside material state")
        eligible.add((column, row))
    regions: list[RestRegion] = []
    while eligible:
        seed = min(eligible)
        eligible.remove(seed)
        component = {seed}
        queue = deque([seed])
        while queue:
            column, row = queue.popleft()
            for neighbor in ((column - 1, row), (column + 1, row), (column, row - 1), (column, row + 1)):
                if neighbor in eligible:
                    eligible.remove(neighbor); component.add(neighbor); queue.append(neighbor)
        edges: set[tuple[tuple[int, int], tuple[int, int]]] = set()
        for column, row in component:
            candidates = (
                (((column, row), (column + 1, row)), (column, row - 1)),
                (((column + 1, row), (column + 1, row + 1)), (column + 1, row)),
                (((column + 1, row + 1), (column, row + 1)), (column, row + 1)),
                (((column, row + 1), (column, row)), (column - 1, row)),
            )
            for edge, neighbor in candidates:
                if neighbor not in component:
                    edges.add(edge)
        traced = [_loop(chain, state) for chain in _trace(edges)]
        exteriors = sorted((loop for loop in traced if loop.orientation is ContourOrientation.COUNTERCLOCKWISE), key=lambda loop: loop.to_dict().__repr__())
        holes = sorted((loop for loop in traced if loop.orientation is ContourOrientation.CLOCKWISE), key=lambda loop: loop.to_dict().__repr__())
        for exterior in exteriors:
            region = RestRegion(exterior, tuple(holes))
            validate_rest_region(region)
            regions.append(region)
    return tuple(sorted(regions, key=lambda region: region.fingerprint.digest))


def extract_rest_regions(state: MaterialState) -> tuple[RestRegion, ...]:
    """Extract legacy residual regions with its byte/semantic predicate unchanged."""
    threshold = state.precision.residual_threshold
    return extract_cell_mask_regions(
        state,
        ((row, column) for row in range(state.height) for column in range(state.width)
         if state.top_heights[row * state.width + column] > threshold),
    )
