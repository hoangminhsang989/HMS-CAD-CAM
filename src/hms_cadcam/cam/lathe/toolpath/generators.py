"""Deterministic Qt-free Lathe OD rough/finish and axial-drill generators."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import math
from types import MappingProxyType
from typing import Mapping, Protocol

from hms_cadcam.cam.lathe.toolpath.model import (
    LATHE_TOOLPATH_ALGORITHM_VERSION,
    LATHE_TOOLPATH_NUMERIC_TOLERANCE_MM,
    LatheDwellEvent,
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
    LatheXZPoint,
)
from hms_cadcam.cam.lathe.toolpath.request import (
    EXECUTABLE_LATHE_TOOLPATH_STRATEGIES,
    UNSUPPORTED_LATHE_TOOLPATH_STRATEGIES,
    LatheToolpathRequestV1,
)
from hms_cadcam.cam.lathe.types import LatheStrategyId

CancellationProbe = Callable[[], bool]
_MAX_GENERATED_PASSES = 100_000


class LatheToolpathCancelledError(RuntimeError):
    """Raised only at cooperative cancellation checkpoints."""


@dataclass(frozen=True, slots=True)
class _GenerationValidationError(ValueError):
    field_id: str
    rule: str

    def __post_init__(self) -> None:
        ValueError.__init__(self, f"{self.field_id}:{self.rule}")


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
    ) -> None:
        _checkpoint(self._cancellation)
        self._events.append(
            LathePathSegment(
                len(self._events),
                motion_class,
                start,
                end,
                semantic_source,
                feed_mm_per_rev,
                (("pass_index", pass_index),),
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
        ),
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
    """Injected immutable exact registry with three executable strategies."""

    def __init__(
        self,
        generators: tuple[LatheStrategyToolpathGenerator, ...] | None = None,
    ) -> None:
        selected = generators or (
            OdRoughToolpathGenerator(),
            OdFinishToolpathGenerator(),
            AxialDrillToolpathGenerator(),
        )
        if not isinstance(selected, tuple) or any(
            not callable(getattr(item, "generate", None))
            or not isinstance(getattr(item, "strategy_id", None), LatheStrategyId)
            for item in selected
        ):
            raise TypeError("Lathe toolpath generators are invalid")
        ids = tuple(item.strategy_id for item in selected)
        if len(set(ids)) != len(ids):
            raise ValueError("Lathe toolpath generator IDs are duplicated")
        if set(ids) != set(EXECUTABLE_LATHE_TOOLPATH_STRATEGIES):
            raise ValueError("Lathe V1 registry must contain exactly three generators")
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
                    LatheToolpathDiagnosticCode.TOOLPATH_NOT_IMPLEMENTED_V1,
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
                    LatheToolpathDiagnosticCode.INVALID_PARAMETER,
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
    "LatheStrategyToolpathGenerator",
    "LatheToolpathCancelledError",
    "LatheToolpathGeneratorRegistry",
    "OdFinishToolpathGenerator",
    "OdRoughToolpathGenerator",
]
