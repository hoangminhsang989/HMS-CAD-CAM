"""Deterministic Qt-free Lathe Toolpath Preview V1/V2 generators."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import math
from types import MappingProxyType
from typing import Mapping, Protocol

from hms_cadcam.cam.lathe.toolpath.model import (
    LATHE_TOOLPATH_ALGORITHM_VERSION,
    LATHE_THREAD_TOOLPATH_PREVIEW_CAPABILITY,
    LATHE_TOOLPATH_NUMERIC_TOLERANCE_MM,
    LatheDwellEvent,
    LatheMetadata,
    LatheMotionClass,
    LathePathSegment,
    LatheToolpathBounds,
    LatheToolpathDiagnostic,
    LatheToolpathDiagnosticCode,
    LatheToolpathEvent,
    LatheToolpathResult,
    LatheToolpathResultIdentity,
    LatheToolpathResultSource,
    LatheToolpathResultState,
    LatheThreadPassMetadata,
    LatheXZPoint,
)
from hms_cadcam.cam.lathe.toolpath.request import (
    EXECUTABLE_LATHE_TOOLPATH_STRATEGIES,
    UNSUPPORTED_LATHE_TOOLPATH_STRATEGIES,
    LatheToolpathRequestV1,
)
from hms_cadcam.cam.lathe.types import LatheStrategyId, LatheThreadHand

CancellationProbe = Callable[[], bool]
_MAX_GENERATED_PASSES = 100_000


class LatheToolpathCancelledError(RuntimeError):
    """Raised only at cooperative cancellation checkpoints."""


@dataclass(frozen=True, slots=True)
class _GenerationValidationError(ValueError):
    field_id: str | None
    rule: str
    code: LatheToolpathDiagnosticCode = LatheToolpathDiagnosticCode.INVALID_PARAMETER

    def __post_init__(self) -> None:
        ValueError.__init__(self, f"{self.field_id or 'request'}:{self.rule}")


def _checkpoint(cancellation: CancellationProbe) -> None:
    if cancellation():
        raise LatheToolpathCancelledError("Lathe toolpath generation cancelled")


class LatheStrategyToolpathGenerator(Protocol):
    strategy_id: LatheStrategyId

    def generate(
        self,
        request: LatheToolpathRequestV1,
        cancellation: CancellationProbe,
    ) -> LatheToolpathResult: ...


class _MotionBuilder:
    def __init__(self, cancellation: CancellationProbe) -> None:
        if not callable(cancellation):
            raise TypeError("Lathe cancellation probe must be callable")
        self._cancellation = cancellation
        self._events: list[LatheToolpathEvent] = []

    @property
    def events(self) -> tuple[LatheToolpathEvent, ...]:
        return tuple(self._events)

    def segment(
        self,
        motion_class: LatheMotionClass,
        start: LatheXZPoint,
        end: LatheXZPoint,
        semantic_source: str,
        *,
        feed_mm_per_rev: float | None = None,
        pass_index: int,
        metadata: LatheMetadata = (),
    ) -> None:
        _checkpoint(self._cancellation)
        if (
            start.distance_to(end)
            <= LATHE_TOOLPATH_NUMERIC_TOLERANCE_MM
        ):
            return
        previous = self._events[-1] if self._events else None
        if (
            isinstance(previous, LathePathSegment)
            and previous.motion_class is motion_class
            and previous.start == start
            and previous.end == end
            and previous.feed_mm_per_rev == feed_mm_per_rev
        ):
            return
        if metadata:
            try:
                metadata_pass_index = dict(metadata)["pass_index"]
            except (TypeError, ValueError, KeyError) as error:
                raise ValueError("Lathe motion metadata requires pass_index") from error
            if metadata_pass_index != pass_index:
                raise ValueError("Lathe motion metadata pass_index differs")
            event_metadata = metadata
        else:
            event_metadata = (("pass_index", pass_index),)
        self._events.append(
            LathePathSegment(
                len(self._events),
                motion_class,
                start,
                end,
                semantic_source,
                feed_mm_per_rev,
                event_metadata,
            )
        )

    def dwell(
        self,
        position: LatheXZPoint,
        duration_seconds: float,
        semantic_source: str,
        *,
        pass_index: int,
    ) -> None:
        _checkpoint(self._cancellation)
        self._events.append(
            LatheDwellEvent(
                len(self._events),
                position,
                duration_seconds,
                semantic_source,
                (("pass_index", pass_index),),
            )
        )


def _identity(request: LatheToolpathRequestV1) -> LatheToolpathResultIdentity:
    return LatheToolpathResultIdentity(
        request.job_id,
        request.request_sequence,
        request.ownership,
        request.operation.revision,
        request.fingerprint,
    )


def _success(
    request: LatheToolpathRequestV1,
    events: tuple[LatheToolpathEvent, ...],
    *,
    pass_count: int,
    diagnostics: tuple[LatheToolpathDiagnostic, ...] = (),
    generation_metadata: LatheMetadata = (),
    thread_pass_metadata: tuple[LatheThreadPassMetadata, ...] = (),
) -> LatheToolpathResult:
    segments = tuple(item for item in events if isinstance(item, LathePathSegment))
    cutting_length = sum(
        item.length_mm
        for item in segments
        if item.motion_class is not LatheMotionClass.RAPID
    )
    rapid_length = sum(
        item.length_mm
        for item in segments
        if item.motion_class is LatheMotionClass.RAPID
    )
    return LatheToolpathResult(
        _identity(request),
        request.strategy_id,
        request.algorithm_version,
        request.cache_key,
        LatheToolpathResultState.SUCCESS,
        LatheToolpathResultSource.WORKER,
        events,
        LatheToolpathBounds.from_events(events),
        pass_count,
        cutting_length,
        rapid_length,
        diagnostics,
        (
            ("global_algorithm_version", LATHE_TOOLPATH_ALGORITHM_VERSION),
            ("preview_scope", "offline_nominal_xz"),
            ("strategy_algorithm_version", request.algorithm_version),
            *generation_metadata,
        ),
        thread_pass_metadata,
    )


def _terminal(
    request: LatheToolpathRequestV1,
    state: LatheToolpathResultState,
    diagnostic: LatheToolpathDiagnostic,
) -> LatheToolpathResult:
    return LatheToolpathResult(
        _identity(request),
        request.strategy_id,
        request.algorithm_version,
        request.cache_key,
        state,
        LatheToolpathResultSource.WORKER,
        diagnostics=(diagnostic,),
        generation_metadata=(
            ("global_algorithm_version", LATHE_TOOLPATH_ALGORITHM_VERSION),
            ("strategy_algorithm_version", request.algorithm_version),
        ),
    )


def _require_request_strategy(
    request: LatheToolpathRequestV1, strategy_id: LatheStrategyId
) -> None:
    if not isinstance(request, LatheToolpathRequestV1):
        raise TypeError("Lathe generator request is invalid")
    if request.strategy_id is not strategy_id:
        raise _GenerationValidationError("strategy_id", "generator_mismatch")


def _float_parameter(request: LatheToolpathRequestV1, name: str) -> float:
    value = request.operation.parameters[name]
    if type(value) is not float or not math.isfinite(value):
        raise _GenerationValidationError(name, "finite_float")
    return value


def _int_parameter(request: LatheToolpathRequestV1, name: str) -> int:
    value = request.operation.parameters[name]
    if type(value) is not int:
        raise _GenerationValidationError(name, "integer")
    return value


def _validate_od_range(
    request: LatheToolpathRequestV1,
    *,
    target_diameter_mm: float,
    start_z_mm: float,
    end_z_mm: float,
) -> float:
    stock = request.stock
    if target_diameter_mm <= stock.inner_diameter_mm or (
        target_diameter_mm > stock.outer_diameter_mm
    ):
        raise _GenerationValidationError(
            "target_diameter_mm", "target_inside_stock_envelope"
        )
    if start_z_mm == end_z_mm:
        raise _GenerationValidationError("end_z_mm", "start_not_equal_end")
    minimum_z = min(stock.front_z_mm, stock.back_z_mm)
    maximum_z = max(stock.front_z_mm, stock.back_z_mm)
    if not (
        minimum_z <= start_z_mm <= maximum_z
        and minimum_z <= end_z_mm <= maximum_z
    ):
        raise _GenerationValidationError("start_z_mm", "inside_stock_axial_range")
    return 1.0 if end_z_mm > start_z_mm else -1.0


def _validate_common_safety(
    *,
    clearance_mm: float,
    retract_mm: float,
    feed_mm_per_rev: float,
) -> None:
    if clearance_mm <= 0.0:
        raise _GenerationValidationError("clearance_mm", "positive")
    if retract_mm < 0.0:
        raise _GenerationValidationError("retract_mm", "non_negative")
    if feed_mm_per_rev <= 0.0:
        raise _GenerationValidationError("feed_mm_per_rev", "positive")


def _validate_axial_range(
    request: LatheToolpathRequestV1,
    *positions_mm: float,
    field_id: str,
) -> None:
    minimum_z = min(request.stock.front_z_mm, request.stock.back_z_mm)
    maximum_z = max(request.stock.front_z_mm, request.stock.back_z_mm)
    if any(position < minimum_z or position > maximum_z for position in positions_mm):
        raise _GenerationValidationError(field_id, "inside_stock_axial_range")


def _require_internal_bore(request: LatheToolpathRequestV1) -> float:
    bore = request.stock.inner_diameter_mm
    if bore <= 0.0:
        raise _GenerationValidationError(
            None,
            "explicit_bore_required",
            LatheToolpathDiagnosticCode.MISSING_INTERNAL_BORE,
        )
    return bore


def _incremental_targets(
    start: float,
    target: float,
    maximum_step: float,
    cancellation: CancellationProbe,
    *,
    field_id: str = "maximum_step",
) -> tuple[float, ...]:
    if maximum_step <= 0.0:
        raise _GenerationValidationError(field_id, "positive")
    delta = target - start
    if abs(delta) <= LATHE_TOOLPATH_NUMERIC_TOLERANCE_MM:
        return (target,)
    direction = 1.0 if delta > 0.0 else -1.0
    current = start
    targets: list[float] = []
    while direction * (target - (current + direction * maximum_step)) > (
        LATHE_TOOLPATH_NUMERIC_TOLERANCE_MM
    ):
        _checkpoint(cancellation)
        current += direction * maximum_step
        targets.append(current)
        if len(targets) >= _MAX_GENERATED_PASSES:
            raise _GenerationValidationError(
                field_id, "pass_count_exceeds_limit"
            )
    targets.append(target)
    return tuple(targets)


def _face_planes(
    front_z_mm: float,
    effective_target_z_mm: float,
    maximum_step_mm: float,
    direction: float,
    cancellation: CancellationProbe,
) -> tuple[float, ...]:
    distance = direction * (effective_target_z_mm - front_z_mm)
    if distance < -LATHE_TOOLPATH_NUMERIC_TOLERANCE_MM:
        raise _GenerationValidationError(
            "face_z_mm", "effective_face_beyond_stock_front"
        )
    if distance <= LATHE_TOOLPATH_NUMERIC_TOLERANCE_MM:
        return (effective_target_z_mm,)
    travelled = 0.0
    planes: list[float] = []
    while travelled + maximum_step_mm < (
        distance - LATHE_TOOLPATH_NUMERIC_TOLERANCE_MM
    ):
        _checkpoint(cancellation)
        travelled += maximum_step_mm
        planes.append(front_z_mm + direction * travelled)
        if len(planes) >= _MAX_GENERATED_PASSES:
            raise _GenerationValidationError(
                "max_depth_of_cut_mm", "pass_count_exceeds_limit"
            )
    planes.append(effective_target_z_mm)
    return tuple(planes)


def _groove_positions(
    left_z_mm: float,
    right_z_mm: float,
    maximum_step_mm: float,
    stock_direction: float,
) -> tuple[float, ...]:
    span = right_z_mm - left_z_mm
    if span < 0.0:
        raise _GenerationValidationError("groove_width_mm", "non_negative_span")
    if span <= LATHE_TOOLPATH_NUMERIC_TOLERANCE_MM:
        return ((left_z_mm + right_z_mm) / 2.0,)
    interval_count = max(1, math.ceil(span / maximum_step_mm))
    if interval_count + 1 > _MAX_GENERATED_PASSES:
        raise _GenerationValidationError("max_step_mm", "pass_count_exceeds_limit")
    positions = tuple(
        left_z_mm + span * index / interval_count
        for index in range(interval_count + 1)
    )
    positions = (left_z_mm, *positions[1:-1], right_z_mm)
    return positions if stock_direction > 0.0 else tuple(reversed(positions))


def _external_safe_x(stock_outer_mm: float, clearance_mm: float) -> float:
    return stock_outer_mm + 2.0 * clearance_mm


def _internal_safe_x(stock_inner_mm: float, clearance_mm: float) -> float:
    return max(0.0, stock_inner_mm - 2.0 * clearance_mm)


def _internal_retract_x(
    safe_x_mm: float,
    cutting_x_mm: float,
    retract_mm: float,
) -> float:
    return max(0.0, min(safe_x_mm, cutting_x_mm - 2.0 * retract_mm))


@dataclass(frozen=True, slots=True)
class FaceToolpathGenerator:
    strategy_id: LatheStrategyId = LatheStrategyId.FACE

    def generate(
        self,
        request: LatheToolpathRequestV1,
        cancellation: CancellationProbe,
    ) -> LatheToolpathResult:
        _require_request_strategy(request, self.strategy_id)
        _checkpoint(cancellation)
        face_z = _float_parameter(request, "face_z_mm")
        outer = _float_parameter(request, "outer_diameter_mm")
        inner = _float_parameter(request, "inner_diameter_mm")
        maximum_step = _float_parameter(request, "max_depth_of_cut_mm")
        finish_allowance = _float_parameter(request, "finish_allowance_mm")
        clearance = _float_parameter(request, "clearance_mm")
        retract = _float_parameter(request, "retract_mm")
        feed = _float_parameter(request, "feed_mm_per_rev")
        stock = request.stock
        _validate_common_safety(
            clearance_mm=clearance,
            retract_mm=retract,
            feed_mm_per_rev=feed,
        )
        if (
            outer <= 0.0
            or inner < 0.0
            or inner >= outer
            or outer > stock.outer_diameter_mm
        ):
            raise _GenerationValidationError(
                "outer_diameter_mm", "valid_facing_diameter_range"
            )
        if stock.inner_diameter_mm > 0.0 and inner < stock.inner_diameter_mm:
            raise _GenerationValidationError(
                "inner_diameter_mm", "at_or_above_stock_bore"
            )
        if maximum_step <= 0.0:
            raise _GenerationValidationError("max_depth_of_cut_mm", "positive")
        if finish_allowance < 0.0:
            raise _GenerationValidationError("finish_allowance_mm", "non_negative")
        direction = stock.axial_direction
        effective_target = face_z - direction * finish_allowance
        _validate_axial_range(
            request,
            face_z,
            effective_target,
            field_id="face_z_mm",
        )
        planes = _face_planes(
            stock.front_z_mm,
            effective_target,
            maximum_step,
            direction,
            cancellation,
        )
        safe_x = _external_safe_x(stock.outer_diameter_mm, clearance)
        safe_z = stock.front_z_mm - direction * max(clearance, retract)
        builder = _MotionBuilder(cancellation)
        for pass_index, plane_z in enumerate(planes):
            _checkpoint(cancellation)
            safe = LatheXZPoint(safe_x, safe_z)
            approach = LatheXZPoint(safe_x, plane_z)
            cut_start = LatheXZPoint(outer, plane_z)
            cut_end = LatheXZPoint(inner, plane_z)
            lead_out = LatheXZPoint(
                max(safe_x, inner + 2.0 * retract),
                plane_z,
            )
            builder.segment(
                LatheMotionClass.RAPID,
                safe,
                approach,
                f"face.slice.{pass_index}.rapid_approach",
                pass_index=pass_index,
            )
            builder.segment(
                LatheMotionClass.LEAD_IN,
                approach,
                cut_start,
                f"face.slice.{pass_index}.lead_in",
                feed_mm_per_rev=feed,
                pass_index=pass_index,
            )
            builder.segment(
                LatheMotionClass.CUTTING,
                cut_start,
                cut_end,
                f"face.slice.{pass_index}.radial_cut",
                feed_mm_per_rev=feed,
                pass_index=pass_index,
            )
            builder.segment(
                LatheMotionClass.LEAD_OUT,
                cut_end,
                lead_out,
                f"face.slice.{pass_index}.lead_out",
                feed_mm_per_rev=feed,
                pass_index=pass_index,
            )
            builder.segment(
                LatheMotionClass.RAPID,
                lead_out,
                safe,
                f"face.slice.{pass_index}.rapid_return",
                pass_index=pass_index,
            )
        return _success(
            request,
            builder.events,
            pass_count=len(planes),
            diagnostics=(
                LatheToolpathDiagnostic(
                    LatheToolpathDiagnosticCode.NOMINAL_FACING_CENTERLINE_PREVIEW
                ),
            ),
        )


@dataclass(frozen=True, slots=True)
class OdRoughToolpathGenerator:
    strategy_id: LatheStrategyId = LatheStrategyId.OD_ROUGH

    def generate(
        self,
        request: LatheToolpathRequestV1,
        cancellation: CancellationProbe,
    ) -> LatheToolpathResult:
        _require_request_strategy(request, self.strategy_id)
        _checkpoint(cancellation)
        start = _float_parameter(request, "start_z_mm")
        end = _float_parameter(request, "end_z_mm")
        target = _float_parameter(request, "target_diameter_mm")
        doc = _float_parameter(request, "max_depth_of_cut_mm")
        radial_leave = _float_parameter(request, "radial_stock_to_leave_mm")
        axial_leave = _float_parameter(request, "axial_stock_to_leave_mm")
        clearance = _float_parameter(request, "clearance_mm")
        retract = _float_parameter(request, "retract_mm")
        feed = _float_parameter(request, "feed_mm_per_rev")
        direction = _validate_od_range(
            request,
            target_diameter_mm=target,
            start_z_mm=start,
            end_z_mm=end,
        )
        if doc <= 0.0:
            raise _GenerationValidationError("max_depth_of_cut_mm", "positive")
        if radial_leave < 0.0 or axial_leave < 0.0:
            raise _GenerationValidationError(
                "radial_stock_to_leave_mm", "non_negative"
            )
        if clearance <= 0.0 or retract < 0.0 or feed <= 0.0:
            raise _GenerationValidationError("clearance_mm", "safe_positive_values")
        rough_target = target + 2.0 * radial_leave
        if rough_target > request.stock.outer_diameter_mm:
            raise _GenerationValidationError(
                "radial_stock_to_leave_mm", "rough_target_below_stock_od"
            )
        effective_end = end - direction * axial_leave
        if direction * (effective_end - start) <= 0.0:
            raise _GenerationValidationError(
                "axial_stock_to_leave_mm", "effective_end_beyond_start"
            )
        decrement = 2.0 * doc
        pass_diameters: list[float] = []
        current = request.stock.outer_diameter_mm
        while current - decrement > (
            rough_target + LATHE_TOOLPATH_NUMERIC_TOLERANCE_MM
        ):
            _checkpoint(cancellation)
            current -= decrement
            pass_diameters.append(current)
            if len(pass_diameters) >= _MAX_GENERATED_PASSES:
                raise _GenerationValidationError(
                    "max_depth_of_cut_mm", "pass_count_exceeds_limit"
                )
        pass_diameters.append(rough_target)

        clearance_x = max(
            request.stock.outer_diameter_mm + 2.0 * clearance,
            request.stock.outer_diameter_mm + 2.0 * retract,
        )
        safe_z = start - direction * max(clearance, retract)
        builder = _MotionBuilder(cancellation)
        for pass_index, diameter in enumerate(pass_diameters):
            _checkpoint(cancellation)
            safe = LatheXZPoint(clearance_x, safe_z)
            approach = LatheXZPoint(clearance_x, start)
            pass_start = LatheXZPoint(diameter, start)
            pass_end = LatheXZPoint(diameter, effective_end)
            retract_x = max(clearance_x, diameter + 2.0 * retract)
            lead_out = LatheXZPoint(retract_x, effective_end)
            builder.segment(
                LatheMotionClass.RAPID,
                safe,
                approach,
                f"od_rough.pass.{pass_index}.rapid_approach",
                pass_index=pass_index,
            )
            builder.segment(
                LatheMotionClass.LEAD_IN,
                approach,
                pass_start,
                f"od_rough.pass.{pass_index}.lead_in",
                feed_mm_per_rev=feed,
                pass_index=pass_index,
            )
            builder.segment(
                LatheMotionClass.CUTTING,
                pass_start,
                pass_end,
                f"od_rough.pass.{pass_index}.axial_cut",
                feed_mm_per_rev=feed,
                pass_index=pass_index,
            )
            builder.segment(
                LatheMotionClass.LEAD_OUT,
                pass_end,
                lead_out,
                f"od_rough.pass.{pass_index}.lead_out",
                feed_mm_per_rev=feed,
                pass_index=pass_index,
            )
            builder.segment(
                LatheMotionClass.RAPID,
                lead_out,
                safe,
                f"od_rough.pass.{pass_index}.rapid_return",
                pass_index=pass_index,
            )
        return _success(request, builder.events, pass_count=len(pass_diameters))


@dataclass(frozen=True, slots=True)
class OdFinishToolpathGenerator:
    strategy_id: LatheStrategyId = LatheStrategyId.OD_FINISH

    def generate(
        self,
        request: LatheToolpathRequestV1,
        cancellation: CancellationProbe,
    ) -> LatheToolpathResult:
        _require_request_strategy(request, self.strategy_id)
        _checkpoint(cancellation)
        start = _float_parameter(request, "start_z_mm")
        end = _float_parameter(request, "end_z_mm")
        target = _float_parameter(request, "target_diameter_mm")
        finish_passes = _int_parameter(request, "finish_passes")
        spring_passes = _int_parameter(request, "spring_passes")
        clearance = _float_parameter(request, "clearance_mm")
        retract = _float_parameter(request, "retract_mm")
        feed = _float_parameter(request, "feed_mm_per_rev")
        direction = _validate_od_range(
            request,
            target_diameter_mm=target,
            start_z_mm=start,
            end_z_mm=end,
        )
        if finish_passes < 1 or spring_passes < 0:
            raise _GenerationValidationError("finish_passes", "valid_pass_counts")
        total_passes = finish_passes + spring_passes
        if total_passes > _MAX_GENERATED_PASSES:
            raise _GenerationValidationError("finish_passes", "pass_count_exceeds_limit")
        if clearance <= 0.0 or retract < 0.0 or feed <= 0.0:
            raise _GenerationValidationError("clearance_mm", "safe_positive_values")
        clearance_x = max(
            request.stock.outer_diameter_mm + 2.0 * clearance,
            target + 2.0 * retract,
        )
        safe_z = start - direction * max(clearance, retract)
        builder = _MotionBuilder(cancellation)
        for pass_index in range(total_passes):
            _checkpoint(cancellation)
            safe = LatheXZPoint(clearance_x, safe_z)
            approach = LatheXZPoint(clearance_x, start)
            pass_start = LatheXZPoint(target, start)
            pass_end = LatheXZPoint(target, end)
            retract_x = max(clearance_x, target + 2.0 * retract)
            lead_out = LatheXZPoint(retract_x, end)
            builder.segment(
                LatheMotionClass.RAPID,
                safe,
                approach,
                f"od_finish.pass.{pass_index}.rapid_approach",
                pass_index=pass_index,
            )
            builder.segment(
                LatheMotionClass.LEAD_IN,
                approach,
                pass_start,
                f"od_finish.pass.{pass_index}.lead_in",
                feed_mm_per_rev=feed,
                pass_index=pass_index,
            )
            builder.segment(
                LatheMotionClass.CUTTING,
                pass_start,
                pass_end,
                f"od_finish.pass.{pass_index}.axial_cut",
                feed_mm_per_rev=feed,
                pass_index=pass_index,
            )
            builder.segment(
                LatheMotionClass.LEAD_OUT,
                pass_end,
                lead_out,
                f"od_finish.pass.{pass_index}.lead_out",
                feed_mm_per_rev=feed,
                pass_index=pass_index,
            )
            builder.segment(
                LatheMotionClass.RAPID,
                lead_out,
                safe,
                f"od_finish.pass.{pass_index}.rapid_return",
                pass_index=pass_index,
            )
        return _success(
            request,
            builder.events,
            pass_count=total_passes,
            diagnostics=(
                LatheToolpathDiagnostic(
                    LatheToolpathDiagnosticCode.NOMINAL_CENTERLINE_PREVIEW
                ),
            ),
        )


def _validate_id_range(
    request: LatheToolpathRequestV1,
    *,
    target_diameter_mm: float,
    start_z_mm: float,
    end_z_mm: float,
) -> tuple[float, float]:
    bore = _require_internal_bore(request)
    if (
        target_diameter_mm <= bore
        or target_diameter_mm >= request.stock.outer_diameter_mm
    ):
        raise _GenerationValidationError(
            "target_diameter_mm", "target_inside_stock_envelope"
        )
    if start_z_mm == end_z_mm:
        raise _GenerationValidationError("end_z_mm", "start_not_equal_end")
    _validate_axial_range(
        request,
        start_z_mm,
        end_z_mm,
        field_id="start_z_mm",
    )
    return (1.0 if end_z_mm > start_z_mm else -1.0, bore)


@dataclass(frozen=True, slots=True)
class IdRoughToolpathGenerator:
    strategy_id: LatheStrategyId = LatheStrategyId.ID_ROUGH

    def generate(
        self,
        request: LatheToolpathRequestV1,
        cancellation: CancellationProbe,
    ) -> LatheToolpathResult:
        _require_request_strategy(request, self.strategy_id)
        _checkpoint(cancellation)
        start = _float_parameter(request, "start_z_mm")
        end = _float_parameter(request, "end_z_mm")
        target = _float_parameter(request, "target_diameter_mm")
        doc = _float_parameter(request, "max_depth_of_cut_mm")
        radial_leave = _float_parameter(request, "radial_stock_to_leave_mm")
        axial_leave = _float_parameter(request, "axial_stock_to_leave_mm")
        clearance = _float_parameter(request, "clearance_mm")
        retract = _float_parameter(request, "retract_mm")
        feed = _float_parameter(request, "feed_mm_per_rev")
        direction, bore = _validate_id_range(
            request,
            target_diameter_mm=target,
            start_z_mm=start,
            end_z_mm=end,
        )
        _validate_common_safety(
            clearance_mm=clearance,
            retract_mm=retract,
            feed_mm_per_rev=feed,
        )
        if doc <= 0.0:
            raise _GenerationValidationError("max_depth_of_cut_mm", "positive")
        if radial_leave < 0.0 or axial_leave < 0.0:
            raise _GenerationValidationError(
                "radial_stock_to_leave_mm", "non_negative"
            )
        rough_target = target - 2.0 * radial_leave
        if rough_target < bore:
            raise _GenerationValidationError(
                "radial_stock_to_leave_mm",
                "rough_target_at_or_above_stock_bore",
            )
        effective_end = end - direction * axial_leave
        if direction * (effective_end - start) <= 0.0:
            raise _GenerationValidationError(
                "axial_stock_to_leave_mm", "effective_end_beyond_start"
            )
        pass_diameters = _incremental_targets(
            bore,
            rough_target,
            2.0 * doc,
            cancellation,
            field_id="max_depth_of_cut_mm",
        )
        safe_x = _internal_safe_x(bore, clearance)
        safe_z = start - direction * max(clearance, retract)
        builder = _MotionBuilder(cancellation)
        for pass_index, diameter in enumerate(pass_diameters):
            _checkpoint(cancellation)
            safe = LatheXZPoint(safe_x, safe_z)
            approach = LatheXZPoint(safe_x, start)
            pass_start = LatheXZPoint(diameter, start)
            pass_end = LatheXZPoint(diameter, effective_end)
            lead_out = LatheXZPoint(
                _internal_retract_x(safe_x, diameter, retract),
                effective_end,
            )
            builder.segment(
                LatheMotionClass.RAPID,
                safe,
                approach,
                f"id_rough.pass.{pass_index}.rapid_approach",
                pass_index=pass_index,
            )
            builder.segment(
                LatheMotionClass.LEAD_IN,
                approach,
                pass_start,
                f"id_rough.pass.{pass_index}.lead_in",
                feed_mm_per_rev=feed,
                pass_index=pass_index,
            )
            builder.segment(
                LatheMotionClass.CUTTING,
                pass_start,
                pass_end,
                f"id_rough.pass.{pass_index}.axial_cut",
                feed_mm_per_rev=feed,
                pass_index=pass_index,
            )
            builder.segment(
                LatheMotionClass.LEAD_OUT,
                pass_end,
                lead_out,
                f"id_rough.pass.{pass_index}.lead_out",
                feed_mm_per_rev=feed,
                pass_index=pass_index,
            )
            builder.segment(
                LatheMotionClass.RAPID,
                lead_out,
                safe,
                f"id_rough.pass.{pass_index}.rapid_return",
                pass_index=pass_index,
            )
        return _success(
            request,
            builder.events,
            pass_count=len(pass_diameters),
            diagnostics=(
                LatheToolpathDiagnostic(
                    LatheToolpathDiagnosticCode.NOMINAL_INTERNAL_CENTERLINE_PREVIEW
                ),
            ),
        )


@dataclass(frozen=True, slots=True)
class IdFinishToolpathGenerator:
    strategy_id: LatheStrategyId = LatheStrategyId.ID_FINISH

    def generate(
        self,
        request: LatheToolpathRequestV1,
        cancellation: CancellationProbe,
    ) -> LatheToolpathResult:
        _require_request_strategy(request, self.strategy_id)
        _checkpoint(cancellation)
        start = _float_parameter(request, "start_z_mm")
        end = _float_parameter(request, "end_z_mm")
        target = _float_parameter(request, "target_diameter_mm")
        finish_passes = _int_parameter(request, "finish_passes")
        spring_passes = _int_parameter(request, "spring_passes")
        clearance = _float_parameter(request, "clearance_mm")
        retract = _float_parameter(request, "retract_mm")
        feed = _float_parameter(request, "feed_mm_per_rev")
        direction, bore = _validate_id_range(
            request,
            target_diameter_mm=target,
            start_z_mm=start,
            end_z_mm=end,
        )
        _validate_common_safety(
            clearance_mm=clearance,
            retract_mm=retract,
            feed_mm_per_rev=feed,
        )
        if finish_passes < 1 or spring_passes < 0:
            raise _GenerationValidationError(
                "finish_passes", "valid_pass_counts"
            )
        total_passes = finish_passes + spring_passes
        if total_passes > _MAX_GENERATED_PASSES:
            raise _GenerationValidationError(
                "finish_passes", "pass_count_exceeds_limit"
            )
        safe_x = _internal_safe_x(bore, clearance)
        safe_z = start - direction * max(clearance, retract)
        builder = _MotionBuilder(cancellation)
        for pass_index in range(total_passes):
            _checkpoint(cancellation)
            safe = LatheXZPoint(safe_x, safe_z)
            approach = LatheXZPoint(safe_x, start)
            pass_start = LatheXZPoint(target, start)
            pass_end = LatheXZPoint(target, end)
            lead_out = LatheXZPoint(
                _internal_retract_x(safe_x, target, retract),
                end,
            )
            builder.segment(
                LatheMotionClass.RAPID,
                safe,
                approach,
                f"id_finish.pass.{pass_index}.rapid_approach",
                pass_index=pass_index,
            )
            builder.segment(
                LatheMotionClass.LEAD_IN,
                approach,
                pass_start,
                f"id_finish.pass.{pass_index}.lead_in",
                feed_mm_per_rev=feed,
                pass_index=pass_index,
            )
            builder.segment(
                LatheMotionClass.CUTTING,
                pass_start,
                pass_end,
                f"id_finish.pass.{pass_index}.axial_cut",
                feed_mm_per_rev=feed,
                pass_index=pass_index,
            )
            builder.segment(
                LatheMotionClass.LEAD_OUT,
                pass_end,
                lead_out,
                f"id_finish.pass.{pass_index}.lead_out",
                feed_mm_per_rev=feed,
                pass_index=pass_index,
            )
            builder.segment(
                LatheMotionClass.RAPID,
                lead_out,
                safe,
                f"id_finish.pass.{pass_index}.rapid_return",
                pass_index=pass_index,
            )
        return _success(
            request,
            builder.events,
            pass_count=total_passes,
            diagnostics=(
                LatheToolpathDiagnostic(
                    LatheToolpathDiagnosticCode.NOMINAL_INTERNAL_CENTERLINE_PREVIEW
                ),
            ),
        )


def _validated_groove_inputs(
    request: LatheToolpathRequestV1,
    *,
    internal: bool,
) -> tuple[float, float, tuple[float, ...], float, float, float]:
    center = _float_parameter(request, "center_z_mm")
    width = _float_parameter(request, "groove_width_mm")
    target = _float_parameter(request, "target_diameter_mm")
    maximum_step = _float_parameter(request, "max_step_mm")
    side_allowance = _float_parameter(request, "side_allowance_mm")
    clearance = _float_parameter(request, "clearance_mm")
    retract = _float_parameter(request, "retract_mm")
    feed = _float_parameter(request, "feed_mm_per_rev")
    _validate_common_safety(
        clearance_mm=clearance,
        retract_mm=retract,
        feed_mm_per_rev=feed,
    )
    bore = _require_internal_bore(request) if internal else request.stock.inner_diameter_mm
    if width <= 0.0:
        raise _GenerationValidationError("groove_width_mm", "positive")
    if maximum_step <= 0.0:
        raise _GenerationValidationError("max_step_mm", "positive")
    if side_allowance < 0.0:
        raise _GenerationValidationError("side_allowance_mm", "non_negative")
    if target <= bore or target >= request.stock.outer_diameter_mm:
        raise _GenerationValidationError(
            "target_diameter_mm", "target_inside_stock_envelope"
        )
    effective_width = width - 2.0 * side_allowance
    if effective_width <= 0.0:
        raise _GenerationValidationError(
            "side_allowance_mm", "positive_effective_groove_width"
        )
    left = center - effective_width / 2.0
    right = center + effective_width / 2.0
    _validate_axial_range(
        request,
        left,
        right,
        field_id="center_z_mm",
    )
    positions = _groove_positions(
        left,
        right,
        maximum_step,
        request.stock.axial_direction,
    )
    return bore, target, positions, clearance, retract, feed


@dataclass(frozen=True, slots=True)
class OdGrooveToolpathGenerator:
    strategy_id: LatheStrategyId = LatheStrategyId.OD_GROOVE

    def generate(
        self,
        request: LatheToolpathRequestV1,
        cancellation: CancellationProbe,
    ) -> LatheToolpathResult:
        _require_request_strategy(request, self.strategy_id)
        _checkpoint(cancellation)
        _bore, target, positions, clearance, retract, feed = (
            _validated_groove_inputs(request, internal=False)
        )
        stock = request.stock
        safe_x = _external_safe_x(stock.outer_diameter_mm, clearance)
        safe_z = stock.front_z_mm - stock.axial_direction * max(
            clearance, retract
        )
        builder = _MotionBuilder(cancellation)
        for pass_index, position in enumerate(positions):
            _checkpoint(cancellation)
            safe = LatheXZPoint(safe_x, safe_z)
            approach = LatheXZPoint(safe_x, position)
            plunge_start = LatheXZPoint(stock.outer_diameter_mm, position)
            plunge_end = LatheXZPoint(target, position)
            lead_out = LatheXZPoint(
                max(safe_x, target + 2.0 * retract),
                position,
            )
            builder.segment(
                LatheMotionClass.RAPID,
                safe,
                approach,
                f"od_groove.plunge.{pass_index}.rapid_approach",
                pass_index=pass_index,
            )
            builder.segment(
                LatheMotionClass.LEAD_IN,
                approach,
                plunge_start,
                f"od_groove.plunge.{pass_index}.lead_in",
                feed_mm_per_rev=feed,
                pass_index=pass_index,
            )
            builder.segment(
                LatheMotionClass.CUTTING,
                plunge_start,
                plunge_end,
                f"od_groove.plunge.{pass_index}.radial_cut",
                feed_mm_per_rev=feed,
                pass_index=pass_index,
            )
            builder.segment(
                LatheMotionClass.LEAD_OUT,
                plunge_end,
                lead_out,
                f"od_groove.plunge.{pass_index}.lead_out",
                feed_mm_per_rev=feed,
                pass_index=pass_index,
            )
            builder.segment(
                LatheMotionClass.RAPID,
                lead_out,
                safe,
                f"od_groove.plunge.{pass_index}.rapid_return",
                pass_index=pass_index,
            )
        return _success(
            request,
            builder.events,
            pass_count=len(positions),
            diagnostics=(
                LatheToolpathDiagnostic(
                    LatheToolpathDiagnosticCode.NOMINAL_MULTI_PLUNGE_GROOVE_PREVIEW
                ),
            ),
        )


@dataclass(frozen=True, slots=True)
class IdGrooveToolpathGenerator:
    strategy_id: LatheStrategyId = LatheStrategyId.ID_GROOVE

    def generate(
        self,
        request: LatheToolpathRequestV1,
        cancellation: CancellationProbe,
    ) -> LatheToolpathResult:
        _require_request_strategy(request, self.strategy_id)
        _checkpoint(cancellation)
        bore, target, positions, clearance, retract, feed = (
            _validated_groove_inputs(request, internal=True)
        )
        stock = request.stock
        safe_x = _internal_safe_x(bore, clearance)
        safe_z = stock.front_z_mm - stock.axial_direction * max(
            clearance, retract
        )
        builder = _MotionBuilder(cancellation)
        for pass_index, position in enumerate(positions):
            _checkpoint(cancellation)
            safe = LatheXZPoint(safe_x, safe_z)
            approach = LatheXZPoint(safe_x, position)
            plunge_start = LatheXZPoint(bore, position)
            plunge_end = LatheXZPoint(target, position)
            lead_out = LatheXZPoint(
                _internal_retract_x(safe_x, target, retract),
                position,
            )
            builder.segment(
                LatheMotionClass.RAPID,
                safe,
                approach,
                f"id_groove.plunge.{pass_index}.rapid_approach",
                pass_index=pass_index,
            )
            builder.segment(
                LatheMotionClass.LEAD_IN,
                approach,
                plunge_start,
                f"id_groove.plunge.{pass_index}.lead_in",
                feed_mm_per_rev=feed,
                pass_index=pass_index,
            )
            builder.segment(
                LatheMotionClass.CUTTING,
                plunge_start,
                plunge_end,
                f"id_groove.plunge.{pass_index}.radial_cut",
                feed_mm_per_rev=feed,
                pass_index=pass_index,
            )
            builder.segment(
                LatheMotionClass.LEAD_OUT,
                plunge_end,
                lead_out,
                f"id_groove.plunge.{pass_index}.lead_out",
                feed_mm_per_rev=feed,
                pass_index=pass_index,
            )
            builder.segment(
                LatheMotionClass.RAPID,
                lead_out,
                safe,
                f"id_groove.plunge.{pass_index}.rapid_return",
                pass_index=pass_index,
            )
        return _success(
            request,
            builder.events,
            pass_count=len(positions),
            diagnostics=(
                LatheToolpathDiagnostic(
                    LatheToolpathDiagnosticCode.NOMINAL_INTERNAL_MULTI_PLUNGE_GROOVE_PREVIEW
                ),
            ),
        )


@dataclass(frozen=True, slots=True)
class PartOffToolpathGenerator:
    strategy_id: LatheStrategyId = LatheStrategyId.PART_OFF

    def generate(
        self,
        request: LatheToolpathRequestV1,
        cancellation: CancellationProbe,
    ) -> LatheToolpathResult:
        _require_request_strategy(request, self.strategy_id)
        _checkpoint(cancellation)
        cutoff = _float_parameter(request, "cutoff_z_mm")
        target = _float_parameter(request, "target_diameter_mm")
        maximum_step = _float_parameter(request, "max_step_mm")
        side_clearance = _float_parameter(request, "side_clearance_mm")
        clearance = _float_parameter(request, "clearance_mm")
        retract = _float_parameter(request, "retract_mm")
        feed = _float_parameter(request, "feed_mm_per_rev")
        stock = request.stock
        _validate_common_safety(
            clearance_mm=clearance,
            retract_mm=retract,
            feed_mm_per_rev=feed,
        )
        if target < 0.0 or target >= stock.outer_diameter_mm:
            raise _GenerationValidationError(
                "target_diameter_mm", "part_off_target_inside_stock_envelope"
            )
        if stock.inner_diameter_mm > 0.0 and target < stock.inner_diameter_mm:
            raise _GenerationValidationError(
                "target_diameter_mm", "not_below_existing_bore"
            )
        if maximum_step <= 0.0:
            raise _GenerationValidationError("max_step_mm", "positive")
        if side_clearance < 0.0:
            raise _GenerationValidationError("side_clearance_mm", "non_negative")
        approach_z = cutoff - stock.axial_direction * side_clearance
        _validate_axial_range(
            request,
            cutoff,
            approach_z,
            field_id="cutoff_z_mm",
        )
        pass_diameters = _incremental_targets(
            stock.outer_diameter_mm,
            target,
            2.0 * maximum_step,
            cancellation,
            field_id="max_step_mm",
        )
        safe_x = _external_safe_x(stock.outer_diameter_mm, clearance)
        safe_z = stock.front_z_mm - stock.axial_direction * max(
            clearance, retract
        )
        builder = _MotionBuilder(cancellation)
        for pass_index, diameter in enumerate(pass_diameters):
            _checkpoint(cancellation)
            safe = LatheXZPoint(safe_x, safe_z)
            approach = LatheXZPoint(safe_x, approach_z)
            axial_lead_start = LatheXZPoint(
                stock.outer_diameter_mm,
                approach_z,
            )
            plunge_start = LatheXZPoint(stock.outer_diameter_mm, cutoff)
            plunge_end = LatheXZPoint(diameter, cutoff)
            lead_out = LatheXZPoint(
                max(safe_x, diameter + 2.0 * retract),
                cutoff,
            )
            builder.segment(
                LatheMotionClass.RAPID,
                safe,
                approach,
                f"part_off.stage.{pass_index}.rapid_approach",
                pass_index=pass_index,
            )
            builder.segment(
                LatheMotionClass.LEAD_IN,
                approach,
                axial_lead_start,
                f"part_off.stage.{pass_index}.lead_to_stock_od",
                feed_mm_per_rev=feed,
                pass_index=pass_index,
            )
            builder.segment(
                LatheMotionClass.LEAD_IN,
                axial_lead_start,
                plunge_start,
                f"part_off.stage.{pass_index}.axial_lead_to_cutoff",
                feed_mm_per_rev=feed,
                pass_index=pass_index,
            )
            builder.segment(
                LatheMotionClass.CUTTING,
                plunge_start,
                plunge_end,
                f"part_off.stage.{pass_index}.radial_cut",
                feed_mm_per_rev=feed,
                pass_index=pass_index,
            )
            builder.segment(
                LatheMotionClass.LEAD_OUT,
                plunge_end,
                lead_out,
                f"part_off.stage.{pass_index}.lead_out",
                feed_mm_per_rev=feed,
                pass_index=pass_index,
            )
            builder.segment(
                LatheMotionClass.RAPID,
                lead_out,
                safe,
                f"part_off.stage.{pass_index}.rapid_return",
                pass_index=pass_index,
            )
        return _success(
            request,
            builder.events,
            pass_count=len(pass_diameters),
            diagnostics=(
                LatheToolpathDiagnostic(
                    LatheToolpathDiagnosticCode.NOMINAL_PART_OFF_CENTERLINE_PREVIEW
                ),
            ),
        )


@dataclass(frozen=True, slots=True)
class _ThreadInputs:
    start_z_mm: float
    end_z_mm: float
    major_diameter_mm: float
    minor_diameter_mm: float
    pitch_mm: float
    thread_hand: LatheThreadHand
    pass_count: int
    spring_passes: int
    infeed_angle_deg: float
    clearance_mm: float
    retract_mm: float
    direction: float
    safe_x_mm: float


def _thread_hand_parameter(request: LatheToolpathRequestV1) -> LatheThreadHand:
    value = request.operation.parameters["thread_hand"]
    try:
        return LatheThreadHand(value)
    except (TypeError, ValueError) as error:
        raise _GenerationValidationError(
            "thread_hand",
            "valid_thread_hand",
        ) from error


def _validate_thread_inputs(
    request: LatheToolpathRequestV1,
    *,
    internal: bool,
) -> _ThreadInputs:
    start = _float_parameter(request, "start_z_mm")
    end = _float_parameter(request, "end_z_mm")
    major = _float_parameter(request, "major_diameter_mm")
    minor = _float_parameter(request, "minor_diameter_mm")
    pitch = _float_parameter(request, "pitch_mm")
    pass_count = _int_parameter(request, "pass_count")
    spring_passes = _int_parameter(request, "spring_passes")
    infeed_angle = _float_parameter(request, "infeed_angle_deg")
    clearance = _float_parameter(request, "clearance_mm")
    retract = _float_parameter(request, "retract_mm")
    common_feed = _float_parameter(request, "feed_mm_per_rev")
    thread_hand = _thread_hand_parameter(request)

    if start == end:
        raise _GenerationValidationError(
            "end_z_mm",
            "start_not_equal_end",
            LatheToolpathDiagnosticCode.THREAD_RANGE_OUTSIDE_STOCK,
        )
    if pitch <= 0.0:
        raise _GenerationValidationError(
            "pitch_mm",
            "positive",
            LatheToolpathDiagnosticCode.INVALID_PITCH,
        )
    if pass_count < 1 or pass_count > _MAX_GENERATED_PASSES:
        raise _GenerationValidationError(
            "pass_count",
            "valid_thread_pass_count",
            LatheToolpathDiagnosticCode.INVALID_PASS_COUNT,
        )
    if spring_passes < 0 or pass_count + spring_passes > _MAX_GENERATED_PASSES:
        raise _GenerationValidationError(
            "spring_passes",
            "valid_thread_spring_passes",
            LatheToolpathDiagnosticCode.INVALID_SPRING_PASSES,
        )
    if not 0.0 <= infeed_angle < 90.0:
        raise _GenerationValidationError(
            "infeed_angle_deg",
            "zero_inclusive_ninety_exclusive",
            LatheToolpathDiagnosticCode.INVALID_INFEED_ANGLE,
        )
    if major <= minor:
        raise _GenerationValidationError(
            "minor_diameter_mm",
            "major_greater_than_minor",
            LatheToolpathDiagnosticCode.INVALID_THREAD_DIAMETER_ORDER,
        )
    _validate_common_safety(
        clearance_mm=clearance,
        retract_mm=retract,
        feed_mm_per_rev=common_feed,
    )

    stock = request.stock
    if internal and stock.inner_diameter_mm <= 0.0:
        raise _GenerationValidationError(
            None,
            "explicit_bore_required",
            LatheToolpathDiagnosticCode.MISSING_INTERNAL_BORE,
        )
    if (internal and major >= stock.outer_diameter_mm) or (
        not internal and major > stock.outer_diameter_mm
    ):
        raise _GenerationValidationError(
            "major_diameter_mm",
            "thread_major_inside_stock",
            LatheToolpathDiagnosticCode.THREAD_MAJOR_EXCEEDS_STOCK,
        )
    if minor <= 0.0 or minor < stock.inner_diameter_mm:
        raise _GenerationValidationError(
            "minor_diameter_mm",
            "thread_minor_at_or_above_bore",
            LatheToolpathDiagnosticCode.THREAD_MINOR_BELOW_BORE,
        )
    minimum_z = min(stock.front_z_mm, stock.back_z_mm)
    maximum_z = max(stock.front_z_mm, stock.back_z_mm)
    if not (
        minimum_z <= start <= maximum_z
        and minimum_z <= end <= maximum_z
    ):
        raise _GenerationValidationError(
            "start_z_mm",
            "thread_range_inside_stock",
            LatheToolpathDiagnosticCode.THREAD_RANGE_OUTSIDE_STOCK,
        )

    safe_x = (
        _internal_safe_x(stock.inner_diameter_mm, clearance)
        if internal
        else max(stock.outer_diameter_mm, major) + 2.0 * clearance
    )
    if not math.isfinite(safe_x) or safe_x < 0.0:
        raise _GenerationValidationError("clearance_mm", "finite_safe_x")
    return _ThreadInputs(
        start,
        end,
        major,
        minor,
        pitch,
        thread_hand,
        pass_count,
        spring_passes,
        infeed_angle,
        clearance,
        retract,
        1.0 if end > start else -1.0,
        safe_x,
    )


def _thread_pass_schedule(
    request: LatheToolpathRequestV1,
    inputs: _ThreadInputs,
    cancellation: CancellationProbe,
    *,
    internal: bool,
) -> tuple[LatheThreadPassMetadata, ...]:
    total_depth = (
        inputs.major_diameter_mm - inputs.minor_diameter_mm
    ) / 2.0
    schedule: list[LatheThreadPassMetadata] = []
    final_diameter = (
        inputs.major_diameter_mm if internal else inputs.minor_diameter_mm
    )
    for pass_index in range(inputs.pass_count):
        _checkpoint(cancellation)
        cumulative = total_depth * (pass_index + 1) / inputs.pass_count
        diameter = (
            inputs.minor_diameter_mm + 2.0 * cumulative
            if internal
            else inputs.major_diameter_mm - 2.0 * cumulative
        )
        if pass_index == inputs.pass_count - 1:
            cumulative = total_depth
            diameter = final_diameter
        schedule.append(
            LatheThreadPassMetadata(
                pass_index,
                inputs.pass_count,
                None,
                cumulative,
                diameter,
                inputs.pitch_mm,
                inputs.thread_hand,
                inputs.infeed_angle_deg,
                True,
                inputs.pitch_mm,
                request.algorithm_version,
            )
        )
    for spring_pass_index in range(inputs.spring_passes):
        _checkpoint(cancellation)
        schedule.append(
            LatheThreadPassMetadata(
                inputs.pass_count + spring_pass_index,
                inputs.pass_count,
                spring_pass_index,
                total_depth,
                final_diameter,
                inputs.pitch_mm,
                inputs.thread_hand,
                inputs.infeed_angle_deg,
                True,
                inputs.pitch_mm,
                request.algorithm_version,
            )
        )
    return tuple(schedule)


_THREAD_SUCCESS_DIAGNOSTICS = (
    LatheToolpathDiagnostic(
        LatheToolpathDiagnosticCode.PHASE_NEUTRAL_SYNCHRONIZED_CENTERLINE_PREVIEW
    ),
    LatheToolpathDiagnostic(
        LatheToolpathDiagnosticCode.THREAD_FEED_DERIVED_FROM_PITCH
    ),
    LatheToolpathDiagnostic(
        LatheToolpathDiagnosticCode.NOMINAL_INFEED_ANGLE_METADATA_ONLY
    ),
    LatheToolpathDiagnostic(LatheToolpathDiagnosticCode.NOT_MACHINE_READY),
)


def _generate_thread(
    request: LatheToolpathRequestV1,
    cancellation: CancellationProbe,
    *,
    strategy_id: LatheStrategyId,
    internal: bool,
) -> LatheToolpathResult:
    _require_request_strategy(request, strategy_id)
    _checkpoint(cancellation)
    inputs = _validate_thread_inputs(request, internal=internal)
    schedule = _thread_pass_schedule(
        request,
        inputs,
        cancellation,
        internal=internal,
    )
    lead_distance = inputs.pitch_mm
    pre_start_z = inputs.start_z_mm - inputs.direction * lead_distance
    post_end_z = inputs.end_z_mm + inputs.direction * lead_distance
    parking_z = pre_start_z - inputs.direction * max(
        inputs.clearance_mm,
        inputs.retract_mm,
        inputs.pitch_mm,
    )
    prefix = "id_thread" if internal else "od_thread"
    builder = _MotionBuilder(cancellation)
    for pass_metadata in schedule:
        _checkpoint(cancellation)
        pass_index = pass_metadata.pass_index
        pass_kind = (
            f"spring.{pass_metadata.spring_pass_index}"
            if pass_metadata.spring_pass_index is not None
            else f"cutting.{pass_index}"
        )
        metadata = pass_metadata.canonical_metadata()
        parking = LatheXZPoint(inputs.safe_x_mm, parking_z)
        radial_origin = LatheXZPoint(
            pass_metadata.cutting_diameter_mm,
            parking_z,
        )
        safe_pre_start = LatheXZPoint(inputs.safe_x_mm, pre_start_z)
        cutting_start = LatheXZPoint(
            pass_metadata.cutting_diameter_mm,
            inputs.start_z_mm,
        )
        cutting_end = LatheXZPoint(
            pass_metadata.cutting_diameter_mm,
            inputs.end_z_mm,
        )
        safe_post_end = LatheXZPoint(inputs.safe_x_mm, post_end_z)
        builder.segment(
            LatheMotionClass.RAPID,
            radial_origin,
            parking,
            f"{prefix}.{pass_kind}.rapid_to_safe_x",
            pass_index=pass_index,
            metadata=metadata,
        )
        builder.segment(
            LatheMotionClass.RAPID,
            parking,
            safe_pre_start,
            f"{prefix}.{pass_kind}.rapid_to_pre_start",
            pass_index=pass_index,
            metadata=metadata,
        )
        builder.segment(
            LatheMotionClass.LEAD_IN,
            safe_pre_start,
            cutting_start,
            f"{prefix}.{pass_kind}.one_pitch_lead_in",
            feed_mm_per_rev=inputs.pitch_mm,
            pass_index=pass_index,
            metadata=metadata,
        )
        builder.segment(
            LatheMotionClass.CUTTING,
            cutting_start,
            cutting_end,
            f"{prefix}.{pass_kind}.pitch_synchronized_cut",
            feed_mm_per_rev=inputs.pitch_mm,
            pass_index=pass_index,
            metadata=metadata,
        )
        builder.segment(
            LatheMotionClass.LEAD_OUT,
            cutting_end,
            safe_post_end,
            f"{prefix}.{pass_kind}.one_pitch_lead_out",
            feed_mm_per_rev=inputs.pitch_mm,
            pass_index=pass_index,
            metadata=metadata,
        )
        builder.segment(
            LatheMotionClass.RAPID,
            safe_post_end,
            parking,
            f"{prefix}.{pass_kind}.rapid_return",
            pass_index=pass_index,
            metadata=metadata,
        )
    _checkpoint(cancellation)
    return _success(
        request,
        builder.events,
        pass_count=len(schedule),
        diagnostics=_THREAD_SUCCESS_DIAGNOSTICS,
        generation_metadata=(
            ("cutting_feed_source", "pitch_mm"),
            ("infeed_model", "metadata_only"),
            ("phase_neutral", True),
            ("thread_hand", inputs.thread_hand.value),
            (
                "thread_preview_capability",
                LATHE_THREAD_TOOLPATH_PREVIEW_CAPABILITY,
            ),
        ),
        thread_pass_metadata=schedule,
    )


@dataclass(frozen=True, slots=True)
class OdThreadToolpathGenerator:
    strategy_id: LatheStrategyId = LatheStrategyId.OD_THREAD

    def generate(
        self,
        request: LatheToolpathRequestV1,
        cancellation: CancellationProbe,
    ) -> LatheToolpathResult:
        return _generate_thread(
            request,
            cancellation,
            strategy_id=self.strategy_id,
            internal=False,
        )


@dataclass(frozen=True, slots=True)
class IdThreadToolpathGenerator:
    strategy_id: LatheStrategyId = LatheStrategyId.ID_THREAD

    def generate(
        self,
        request: LatheToolpathRequestV1,
        cancellation: CancellationProbe,
    ) -> LatheToolpathResult:
        return _generate_thread(
            request,
            cancellation,
            strategy_id=self.strategy_id,
            internal=True,
        )


@dataclass(frozen=True, slots=True)
class AxialDrillToolpathGenerator:
    strategy_id: LatheStrategyId = LatheStrategyId.AXIAL_DRILL

    def generate(
        self,
        request: LatheToolpathRequestV1,
        cancellation: CancellationProbe,
    ) -> LatheToolpathResult:
        _require_request_strategy(request, self.strategy_id)
        _checkpoint(cancellation)
        depth = _float_parameter(request, "depth_mm")
        retract_plane = _float_parameter(request, "retract_plane_z_mm")
        raw_peck = request.operation.parameters["peck_depth_mm"]
        peck = None if raw_peck is None else float(raw_peck)
        dwell = _float_parameter(request, "dwell_seconds")
        clearance = _float_parameter(request, "clearance_mm")
        feed = _float_parameter(request, "feed_mm_per_rev")
        stock = request.stock
        direction = stock.axial_direction
        if depth <= 0.0 or depth > stock.axial_length_mm:
            raise _GenerationValidationError("depth_mm", "depth_inside_stock")
        if direction * (retract_plane - stock.front_z_mm) > 0.0:
            raise _GenerationValidationError(
                "retract_plane_z_mm", "at_or_outside_stock_front"
            )
        if peck is not None and (not math.isfinite(peck) or peck <= 0.0):
            raise _GenerationValidationError("peck_depth_mm", "positive_optional")
        if dwell < 0.0 or clearance <= 0.0 or feed <= 0.0:
            raise _GenerationValidationError("dwell_seconds", "safe_values")
        peck_count = 1 if peck is None else math.ceil(depth / peck)
        if peck_count > _MAX_GENERATED_PASSES:
            raise _GenerationValidationError("peck_depth_mm", "pass_count_exceeds_limit")
        target_depths = tuple(
            depth
            if index == peck_count
            else min(depth, (peck or depth) * index)
            for index in range(1, peck_count + 1)
        )
        if any(
            target_depths[index] - target_depths[index - 1]
            <= LATHE_TOOLPATH_NUMERIC_TOLERANCE_MM
            for index in range(1, len(target_depths))
        ):
            raise _GenerationValidationError("peck_depth_mm", "non_zero_final_peck")

        safe_z = retract_plane - direction * clearance
        safe = LatheXZPoint(0.0, safe_z)
        retract = LatheXZPoint(0.0, retract_plane)
        builder = _MotionBuilder(cancellation)
        builder.segment(
            LatheMotionClass.RAPID,
            safe,
            retract,
            "axial_drill.safe_to_retract",
            pass_index=0,
        )
        for pass_index, current_depth in enumerate(target_depths):
            _checkpoint(cancellation)
            target = LatheXZPoint(
                0.0,
                stock.front_z_mm + direction * current_depth,
            )
            builder.segment(
                LatheMotionClass.CUTTING,
                retract,
                target,
                f"axial_drill.peck.{pass_index}.feed",
                feed_mm_per_rev=feed,
                pass_index=pass_index,
            )
            if pass_index == len(target_depths) - 1 and dwell > 0.0:
                builder.dwell(
                    target,
                    dwell,
                    "axial_drill.final_dwell",
                    pass_index=pass_index,
                )
            builder.segment(
                LatheMotionClass.RAPID,
                target,
                retract,
                f"axial_drill.peck.{pass_index}.retract",
                pass_index=pass_index,
            )
        builder.segment(
            LatheMotionClass.RAPID,
            retract,
            safe,
            "axial_drill.retract_to_safe",
            pass_index=peck_count - 1,
        )
        return _success(request, builder.events, pass_count=peck_count)


class LatheToolpathGeneratorRegistry:
    """Injected immutable exact registry with eleven executable strategies."""

    def __init__(
        self,
        generators: tuple[LatheStrategyToolpathGenerator, ...] | None = None,
    ) -> None:
        defaults: tuple[LatheStrategyToolpathGenerator, ...] = (
            FaceToolpathGenerator(),
            OdRoughToolpathGenerator(),
            OdFinishToolpathGenerator(),
            IdRoughToolpathGenerator(),
            IdFinishToolpathGenerator(),
            OdGrooveToolpathGenerator(),
            IdGrooveToolpathGenerator(),
            PartOffToolpathGenerator(),
            OdThreadToolpathGenerator(),
            IdThreadToolpathGenerator(),
            AxialDrillToolpathGenerator(),
        )
        overrides = () if generators is None else generators
        if not isinstance(overrides, tuple) or any(
            not callable(getattr(item, "generate", None))
            or not isinstance(getattr(item, "strategy_id", None), LatheStrategyId)
            for item in overrides
        ):
            raise TypeError("Lathe toolpath generators are invalid")
        override_ids = tuple(item.strategy_id for item in overrides)
        if len(set(override_ids)) != len(override_ids):
            raise ValueError("Lathe toolpath generator IDs are duplicated")
        legacy_override_ids = {
            LatheStrategyId.OD_ROUGH,
            LatheStrategyId.OD_FINISH,
            LatheStrategyId.AXIAL_DRILL,
        }
        stage12_2_override_ids = set(EXECUTABLE_LATHE_TOOLPATH_STRATEGIES) - {
            LatheStrategyId.OD_THREAD,
            LatheStrategyId.ID_THREAD,
        }
        supplied_ids = frozenset(override_ids)
        if overrides and supplied_ids not in {
            frozenset(legacy_override_ids),
            frozenset(stage12_2_override_ids),
            frozenset(EXECUTABLE_LATHE_TOOLPATH_STRATEGIES),
        }:
            raise ValueError(
                "Lathe V3 registry requires the exact Stage 12.1, Stage 12.2 "
                "or eleven-generator override set"
            )
        by_id = {item.strategy_id: item for item in defaults}
        by_id.update((item.strategy_id, item) for item in overrides)
        selected = tuple(
            by_id[strategy_id]
            for strategy_id in EXECUTABLE_LATHE_TOOLPATH_STRATEGIES
        )
        if tuple(item.strategy_id for item in selected) != (
            EXECUTABLE_LATHE_TOOLPATH_STRATEGIES
        ):
            raise ValueError(
                "Lathe V3 registry must contain exactly eleven generators"
            )
        self._generators: Mapping[
            LatheStrategyId, LatheStrategyToolpathGenerator
        ] = MappingProxyType({item.strategy_id: item for item in selected})

    @property
    def executable_strategy_ids(self) -> tuple[LatheStrategyId, ...]:
        return EXECUTABLE_LATHE_TOOLPATH_STRATEGIES

    @property
    def unsupported_strategy_ids(self) -> tuple[LatheStrategyId, ...]:
        return UNSUPPORTED_LATHE_TOOLPATH_STRATEGIES

    def generate(
        self,
        request: LatheToolpathRequestV1,
        cancellation: CancellationProbe,
    ) -> LatheToolpathResult:
        if not isinstance(request, LatheToolpathRequestV1):
            raise TypeError("Lathe registry request is invalid")
        if not callable(cancellation):
            raise TypeError("Lathe registry cancellation probe is invalid")
        generator = self._generators.get(request.strategy_id)
        if generator is None:
            return _terminal(
                request,
                LatheToolpathResultState.UNSUPPORTED_STRATEGY,
                LatheToolpathDiagnostic(
                    LatheToolpathDiagnosticCode.THREAD_TOOLPATH_NOT_IMPLEMENTED_V2,
                    details=(("strategy_id", request.strategy_id.value),),
                ),
            )
        try:
            return generator.generate(request, cancellation)
        except LatheToolpathCancelledError:
            return _terminal(
                request,
                LatheToolpathResultState.CANCELLED,
                LatheToolpathDiagnostic(LatheToolpathDiagnosticCode.CANCELLED),
            )
        except _GenerationValidationError as error:
            return _terminal(
                request,
                LatheToolpathResultState.INVALID_REQUEST,
                LatheToolpathDiagnostic(
                    error.code,
                    error.field_id,
                    (("rule", error.rule),),
                ),
            )
        except (TypeError, ValueError):
            return _terminal(
                request,
                LatheToolpathResultState.INVALID_REQUEST,
                LatheToolpathDiagnostic(
                    LatheToolpathDiagnosticCode.INVALID_REQUEST
                ),
            )
        except Exception:
            return _terminal(
                request,
                LatheToolpathResultState.GENERATION_FAILED,
                LatheToolpathDiagnostic(
                    LatheToolpathDiagnosticCode.GENERATION_FAILED
                ),
            )


__all__ = [
    "AxialDrillToolpathGenerator",
    "CancellationProbe",
    "FaceToolpathGenerator",
    "IdFinishToolpathGenerator",
    "IdGrooveToolpathGenerator",
    "IdRoughToolpathGenerator",
    "IdThreadToolpathGenerator",
    "LatheStrategyToolpathGenerator",
    "LatheToolpathCancelledError",
    "LatheToolpathGeneratorRegistry",
    "OdFinishToolpathGenerator",
    "OdGrooveToolpathGenerator",
    "OdRoughToolpathGenerator",
    "OdThreadToolpathGenerator",
    "PartOffToolpathGenerator",
]
