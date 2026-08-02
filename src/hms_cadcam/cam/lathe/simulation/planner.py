"""Deterministic adapter from canonical HMS Lathe toolpaths to simulation."""

from __future__ import annotations

from hms_cadcam.cam.lathe.simulation.coordinates import diameter_x_to_radius_mm, finite_mm
from hms_cadcam.cam.lathe.simulation.models import MAX_INPUT_SEGMENTS, MAX_OPERATIONS, PlannedMotion, SimulationMotionKind, SimulationPlan, SimulationPoint, motion_kind
from hms_cadcam.cam.lathe.toolpath.model import LatheDwellEvent, LathePathSegment, LatheToolpathResult, LatheToolpathResultState
from hms_cadcam.cam.lathe.types import LatheStrategyId


def build_simulation_plan(results: tuple[LatheToolpathResult, ...]) -> SimulationPlan:
    """Build one immutable plan in caller order, rejecting unknown semantics."""

    if not isinstance(results, tuple) or not results:
        raise ValueError("Simulation requires a non-empty toolpath tuple")
    if len(results) > MAX_OPERATIONS:
        raise ValueError("Simulation operation limit reached")
    motions: list[PlannedMotion] = []
    operation_ids: list[str] = []
    strategies: list[LatheStrategyId] = []
    sequence = 0
    for result in results:
        if not isinstance(result, LatheToolpathResult) or result.state is not LatheToolpathResultState.SUCCESS:
            raise ValueError("Simulation accepts successful canonical Lathe toolpaths only")
        strategy = result.strategy_id
        if strategy not in tuple(LatheStrategyId):
            raise ValueError("Unsupported future Lathe strategy")
        operation_id = str(result.identity.ownership.operation_id)
        operation_ids.append(operation_id)
        strategies.append(strategy)
        for event in result.motions:
            if len(motions) >= MAX_INPUT_SEGMENTS:
                raise ValueError("Simulation input segment limit reached")
            if isinstance(event, LathePathSegment):
                start = SimulationPoint(diameter_x_to_radius_mm(event.start.x_diameter_mm), finite_mm(event.start.z_mm, "Segment start Z"))
                end = SimulationPoint(diameter_x_to_radius_mm(event.end.x_diameter_mm), finite_mm(event.end.z_mm, "Segment end Z"))
                kind = motion_kind(event.motion_class, strategy)
            elif isinstance(event, LatheDwellEvent):
                start = end = SimulationPoint(diameter_x_to_radius_mm(event.position.x_diameter_mm), finite_mm(event.position.z_mm, "Dwell Z"))
                kind = SimulationMotionKind.DWELL
            else:
                raise TypeError("Unsupported canonical Lathe motion type")
            motions.append(PlannedMotion(operation_id, strategy, f"{operation_id}:{event.sequence_index}", sequence, kind, start, end))
            sequence += 1
    return SimulationPlan(tuple(motions), tuple(strategies), tuple(operation_ids))


__all__ = ["build_simulation_plan"]
