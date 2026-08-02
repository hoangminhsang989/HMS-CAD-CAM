"""Pure deterministic Lathe 2D stock-removal and safety engine."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Callable

from hms_cadcam.cam.lathe.simulation.models import (
    AxisymmetricStock,
    PlannedMotion,
    SafetyCode,
    SafetyEvent,
    SafetySeverity,
    SimulationFrame,
    SimulationMotionKind,
    SimulationPlan,
    SimulationPoint,
    SimulationResult,
    SimulationSettings,
    SimulationState,
    ToolEnvelope,
)
from hms_cadcam.cam.lathe.simulation.stock import remove_at, stock_metrics
from hms_cadcam.cam.lathe.types import LatheStrategyId

CancelProbe = Callable[[], bool]
ProgressCallback = Callable[[float], None]

_INTERNAL = {
    LatheStrategyId.ID_ROUGH,
    LatheStrategyId.ID_FINISH,
    LatheStrategyId.ID_GROOVE,
    LatheStrategyId.ID_THREAD,
}
_THREAD = {LatheStrategyId.OD_THREAD, LatheStrategyId.ID_THREAD}
_CUTTING = {
    SimulationMotionKind.CUTTING,
    SimulationMotionKind.LEAD_IN,
    SimulationMotionKind.LEAD_OUT,
    SimulationMotionKind.THREAD_CUTTING,
}


def _event(
    code: SafetyCode,
    severity: SafetySeverity,
    sequence: int,
    motion: PlannedMotion,
    position: SimulationPoint,
) -> SafetyEvent:
    return SafetyEvent(code, severity, sequence, motion.operation_id, motion.segment_id, position)


def _material_at(stock: AxisymmetricStock, point: SimulationPoint, tolerance: float) -> bool:
    nearest = min(stock.stations, key=lambda item: abs(item.z_mm - point.z_mm))
    return (
        stock.z_min_mm - tolerance <= point.z_mm <= stock.z_max_mm + tolerance
        and nearest.inner_radius_mm - tolerance <= point.radius_mm <= nearest.outer_radius_mm + tolerance
    )


def _holder_contact(stock: AxisymmetricStock, point: SimulationPoint, tool: ToolEnvelope, tolerance: float) -> bool:
    if not tool.holder_known:
        return False
    holder_point = SimulationPoint(
        max(0.0, point.radius_mm + tool.holder_radial_offset_mm),
        point.z_mm + tool.holder_axial_offset_mm,
    )
    radius = tool.holder_radius_mm or 0.0
    if not stock.z_min_mm - radius <= holder_point.z_mm <= stock.z_max_mm + radius:
        return False
    nearest = min(stock.stations, key=lambda item: abs(item.z_mm - holder_point.z_mm))
    return holder_point.radius_mm - radius <= nearest.outer_radius_mm + tolerance and holder_point.radius_mm + radius >= nearest.inner_radius_mm - tolerance


def _sample_motion(motion: PlannedMotion, resolution: float) -> tuple[SimulationPoint, ...]:
    distance = math.hypot(
        motion.end.radius_mm - motion.start.radius_mm,
        motion.end.z_mm - motion.start.z_mm,
    )
    count = 1 if motion.kind is SimulationMotionKind.DWELL else max(1, math.ceil(distance / resolution))
    return tuple(
        SimulationPoint(
            motion.start.radius_mm + (motion.end.radius_mm - motion.start.radius_mm) * index / count,
            motion.start.z_mm + (motion.end.z_mm - motion.start.z_mm) * index / count,
        )
        for index in range(1, count + 1)
    )


def _fingerprint(
    state: SimulationState,
    stock: AxisymmetricStock,
    frames: tuple[SimulationFrame, ...],
    events: tuple[SafetyEvent, ...],
) -> str:
    payload = {
        "format": "HMS_LATHE_SIMULATION_RESULT_V1",
        "state": state.value,
        "stock": [[item.z_mm, item.inner_radius_mm, item.outer_radius_mm] for item in stock.stations],
        "frames": [[item.sequence, item.operation_id, item.strategy_id.value, item.segment_id, item.progress, item.tool_position.radius_mm, item.tool_position.z_mm, item.motion_kind.value, item.stock_revision] for item in frames],
        "events": [[item.code.value, item.severity.value, item.sequence, item.operation_id, item.segment_id] for item in events],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def run_engine(
    plan: SimulationPlan,
    initial_stock: AxisymmetricStock,
    tool: ToolEnvelope,
    settings: SimulationSettings,
    *,
    cancelled: CancelProbe = lambda: False,
    progress: ProgressCallback | None = None,
) -> SimulationResult:
    """Run a bounded simulation; partial/cancelled results are never complete."""

    if not isinstance(plan, SimulationPlan) or not plan.motions:
        raise ValueError("Simulation plan is empty")
    if not isinstance(initial_stock, AxisymmetricStock) or not isinstance(tool, ToolEnvelope) or not isinstance(settings, SimulationSettings):
        raise TypeError("Simulation engine inputs are invalid")
    current = initial_stock
    frames: list[SimulationFrame] = []
    events: list[SafetyEvent] = []
    state = SimulationState.COMPLETE
    complete = True
    total_samples = sum(len(_sample_motion(item, settings.sampling_resolution_mm)) for item in plan.motions)
    completed_samples = 0

    def add_event(code: SafetyCode, severity: SafetySeverity, motion: PlannedMotion, point: SimulationPoint) -> None:
        nonlocal state, complete
        if len(events) >= settings.maximum_event_count:
            if not events or events[-1].code is not SafetyCode.INPUT_LIMIT_REACHED:
                events.append(_event(SafetyCode.INPUT_LIMIT_REACHED, SafetySeverity.BLOCKING_ERROR, len(events), motion, point))
            state = SimulationState.INCOMPLETE
            complete = False
            return
        events.append(_event(code, severity, len(events), motion, point))

    first = plan.motions[0]
    required_stations = (
        math.ceil(
            (initial_stock.z_max_mm - initial_stock.z_min_mm)
            / settings.sampling_resolution_mm
        )
        + 1
    )
    if required_stations > settings.maximum_stock_stations:
        add_event(
            SafetyCode.STATION_LIMIT_REACHED,
            SafetySeverity.BLOCKING_ERROR,
            first,
            first.start,
        )
        state = SimulationState.INCOMPLETE
        complete = False
    if not tool.tool_known:
        add_event(SafetyCode.TOOL_GEOMETRY_UNKNOWN, SafetySeverity.BLOCKING_ERROR, first, first.start)
        state = (
            SimulationState.REJECTED
            if state is SimulationState.COMPLETE
            else state
        )
        complete = False
    if not tool.holder_known:
        add_event(SafetyCode.HOLDER_GEOMETRY_UNKNOWN, SafetySeverity.WARNING, first, first.start)
    if not complete:
        final_frames: tuple[SimulationFrame, ...] = ()
        final_events = tuple(events)
        return SimulationResult(state, initial_stock, current, final_frames, final_events, stock_metrics(initial_stock, current), _fingerprint(state, current, final_frames, final_events), False)

    stop = False
    for motion in plan.motions:
        if cancelled():
            state, complete = SimulationState.CANCELLED, False
            break
        samples = _sample_motion(motion, settings.sampling_resolution_mm)
        if motion.strategy_id in _THREAD:
            add_event(SafetyCode.THREAD_PROFILE_APPROXIMATION, SafetySeverity.INFORMATION, motion, motion.start)
        for point in samples:
            if cancelled():
                state, complete, stop = SimulationState.CANCELLED, False, True
                break
            if len(frames) >= settings.maximum_frame_count:
                add_event(SafetyCode.FRAME_LIMIT_REACHED, SafetySeverity.BLOCKING_ERROR, motion, point)
                state, complete, stop = SimulationState.INCOMPLETE, False, True
                break
            frame_events_before = len(events)
            outside = not (current.z_min_mm - settings.tolerance_mm <= point.z_mm <= current.z_max_mm + settings.tolerance_mm)
            if outside:
                add_event(SafetyCode.MOTION_OUTSIDE_STOCK_DOMAIN, SafetySeverity.WARNING, motion, point)
            if motion.kind is SimulationMotionKind.RAPID and _material_at(current, point, settings.tolerance_mm):
                add_event(SafetyCode.RAPID_TOOL_STOCK_CONTACT, SafetySeverity.COLLISION, motion, point)
                if settings.stop_on_collision:
                    state, complete, stop = SimulationState.INCOMPLETE, False, True
            if _holder_contact(current, point, tool, settings.tolerance_mm):
                add_event(SafetyCode.HOLDER_STOCK_CONTACT, SafetySeverity.COLLISION, motion, point)
                if settings.stop_on_collision:
                    state, complete, stop = SimulationState.INCOMPLETE, False, True
            if not stop and not outside and motion.kind in _CUTTING:
                current = remove_at(
                    current,
                    z_mm=point.z_mm,
                    tool_radius_mm=point.radius_mm,
                    envelope_mm=tool.nose_radius_mm or settings.sampling_resolution_mm,
                    internal=motion.strategy_id in _INTERNAL,
                    axial_drill=motion.strategy_id is LatheStrategyId.AXIAL_DRILL,
                )
            completed_samples += 1
            progress_value = min(1.0, completed_samples / max(1, total_samples))
            frame = SimulationFrame(
                len(frames), motion.operation_id, motion.strategy_id, motion.segment_id,
                progress_value, point, motion.kind, current.revision,
                stock_metrics(initial_stock, current), tuple(events[frame_events_before:]),
            )
            frames.append(frame)
            if progress is not None and (len(frames) == 1 or len(frames) % settings.progress_interval_frames == 0 or progress_value == 1.0):
                progress(progress_value)
            if stop:
                break
        if stop:
            break
    final_frames = tuple(frames)
    final_events = tuple(events)
    return SimulationResult(state, initial_stock, current, final_frames, final_events, stock_metrics(initial_stock, current), _fingerprint(state, current, final_frames, final_events), complete)


__all__ = ["CancelProbe", "ProgressCallback", "run_engine"]
