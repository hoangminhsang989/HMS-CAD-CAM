"""Exact immutable Lathe Foundation V1 parameter schemas and defaults."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import math
from types import MappingProxyType
from typing import Mapping, TypeAlias

from hms_cadcam.cam.domain.operation import DiagnosticSeverity
from hms_cadcam.cam.lathe.types import (
    LatheDiagnostic,
    LatheDiagnosticCode,
    LatheParameterGroup,
    LatheParameterUnitKind,
    LatheParameterValueKind,
    LatheSpindleDirection,
    LatheStrategyId,
    LatheThreadHand,
    ordered_lathe_diagnostics,
)

LatheParameterValue: TypeAlias = float | int | StrEnum | None


class LatheParameterValidationError(ValueError):
    """Typed validation failure carrying stable diagnostics."""

    def __init__(self, diagnostics: tuple[LatheDiagnostic, ...]) -> None:
        self.diagnostics = ordered_lathe_diagnostics(diagnostics)
        super().__init__("Lathe parameter validation failed")


@dataclass(frozen=True, slots=True)
class LatheParameterDescriptor:
    """Presenter-neutral metadata for one exact Lathe parameter."""

    parameter_id: str
    value_kind: LatheParameterValueKind
    unit_kind: LatheParameterUnitKind
    group: LatheParameterGroup
    required: bool
    order: int
    minimum: float | int | None = None
    maximum: float | int | None = None
    exclusive_minimum: bool = False
    exclusive_maximum: bool = False
    enum_values: tuple[str, ...] = ()
    label_key: str = ""
    help_key: str = ""
    enum_type: type[StrEnum] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.parameter_id, str) or not self.parameter_id:
            raise ValueError("Lathe parameter ID must be non-empty")
        if not isinstance(self.value_kind, LatheParameterValueKind):
            raise TypeError("Lathe parameter value kind is invalid")
        if not isinstance(self.unit_kind, LatheParameterUnitKind):
            raise TypeError("Lathe parameter unit kind is invalid")
        if not isinstance(self.group, LatheParameterGroup):
            raise TypeError("Lathe parameter group is invalid")
        if type(self.required) is not bool:
            raise TypeError("Lathe parameter required flag must be bool")
        if type(self.order) is not int or self.order < 0:
            raise ValueError("Lathe parameter order must be non-negative")
        if type(self.exclusive_minimum) is not bool or type(
            self.exclusive_maximum
        ) is not bool:
            raise TypeError("Lathe parameter bound exclusivity must be bool")
        for bound in (self.minimum, self.maximum):
            if bound is not None and (
                isinstance(bound, bool)
                or not isinstance(bound, (int, float))
                or not math.isfinite(float(bound))
            ):
                raise ValueError("Lathe parameter bound must be finite")
        if (
            self.minimum is not None
            and self.maximum is not None
            and float(self.minimum) > float(self.maximum)
        ):
            raise ValueError("Lathe parameter bounds are inverted")
        expected_label = f"lathe.parameter.{self.parameter_id}.label"
        expected_help = f"lathe.parameter.{self.parameter_id}.help"
        if self.label_key != expected_label or self.help_key != expected_help:
            raise ValueError("Lathe parameter semantic keys are invalid")
        if self.value_kind is LatheParameterValueKind.ENUM:
            if (
                self.enum_type is None
                or not issubclass(self.enum_type, StrEnum)
                or not self.enum_values
                or len(set(self.enum_values)) != len(self.enum_values)
                or tuple(item.value for item in self.enum_type) != self.enum_values
            ):
                raise ValueError("Lathe enum parameter metadata is invalid")
        elif self.enum_type is not None or self.enum_values:
            raise ValueError("Only enum parameters may declare enum metadata")

    def normalize(self, value: object) -> LatheParameterValue:
        """Normalize one value without string or bool numeric coercion."""

        if value is None:
            if self.required:
                raise self._error("required")
            return None
        if self.value_kind is LatheParameterValueKind.FLOAT:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise self._error("float_type")
            try:
                normalized: float | int | StrEnum = float(value)
            except OverflowError as error:
                raise self._error("finite") from error
            if not math.isfinite(normalized):
                raise self._error("finite")
            normalized = 0.0 if normalized == 0.0 else normalized
        elif self.value_kind is LatheParameterValueKind.INTEGER:
            if type(value) is not int:
                raise self._error("integer_type")
            normalized = value
        else:
            if self.enum_type is None or type(value) is not self.enum_type:
                raise self._error("enum_type")
            normalized = value
        numeric = (
            float(normalized)
            if self.value_kind is not LatheParameterValueKind.ENUM
            else None
        )
        if numeric is not None and self.minimum is not None:
            below = numeric < float(self.minimum)
            equal_exclusive = self.exclusive_minimum and numeric == float(self.minimum)
            if below or equal_exclusive:
                raise self._error("minimum")
        if numeric is not None and self.maximum is not None:
            above = numeric > float(self.maximum)
            equal_exclusive = self.exclusive_maximum and numeric == float(self.maximum)
            if above or equal_exclusive:
                raise self._error("maximum")
        return normalized

    def _error(self, rule: str) -> LatheParameterValidationError:
        return LatheParameterValidationError(
            (
                LatheDiagnostic(
                    LatheDiagnosticCode.INVALID_PARAMETER,
                    DiagnosticSeverity.ERROR,
                    self.parameter_id,
                    (("rule", rule),),
                ),
            )
        )


def _descriptor(
    parameter_id: str,
    value_kind: LatheParameterValueKind,
    unit_kind: LatheParameterUnitKind,
    group: LatheParameterGroup,
    order: int,
    *,
    required: bool = True,
    minimum: float | int | None = None,
    maximum: float | int | None = None,
    exclusive_minimum: bool = False,
    exclusive_maximum: bool = False,
    enum_type: type[StrEnum] | None = None,
) -> LatheParameterDescriptor:
    enum_values = () if enum_type is None else tuple(item.value for item in enum_type)
    return LatheParameterDescriptor(
        parameter_id=parameter_id,
        value_kind=value_kind,
        unit_kind=unit_kind,
        group=group,
        required=required,
        order=order,
        minimum=minimum,
        maximum=maximum,
        exclusive_minimum=exclusive_minimum,
        exclusive_maximum=exclusive_maximum,
        enum_values=enum_values,
        label_key=f"lathe.parameter.{parameter_id}.label",
        help_key=f"lathe.parameter.{parameter_id}.help",
        enum_type=enum_type,
    )


_FLOAT = LatheParameterValueKind.FLOAT
_INTEGER = LatheParameterValueKind.INTEGER
_ENUM = LatheParameterValueKind.ENUM
_NONE = LatheParameterUnitKind.NONE
_MM = LatheParameterUnitKind.MILLIMETRE
_BASIC = LatheParameterGroup.BASIC
_ADVANCED = LatheParameterGroup.ADVANCED

COMMON_PARAMETER_DESCRIPTORS: tuple[LatheParameterDescriptor, ...] = (
    _descriptor(
        "spindle_speed_rpm",
        _FLOAT,
        LatheParameterUnitKind.RPM,
        _BASIC,
        10,
        minimum=0.0,
        exclusive_minimum=True,
    ),
    _descriptor(
        "feed_mm_per_rev",
        _FLOAT,
        LatheParameterUnitKind.MM_PER_REVOLUTION,
        _BASIC,
        20,
        minimum=0.0,
        exclusive_minimum=True,
    ),
    _descriptor(
        "clearance_mm", _FLOAT, _MM, _BASIC, 30, minimum=0.0, exclusive_minimum=True
    ),
    _descriptor("retract_mm", _FLOAT, _MM, _ADVANCED, 40, minimum=0.0),
    _descriptor(
        "spindle_direction",
        _ENUM,
        _NONE,
        _BASIC,
        50,
        enum_type=LatheSpindleDirection,
    ),
)

COMMON_PARAMETER_IDS: tuple[str, ...] = tuple(
    item.parameter_id for item in COMMON_PARAMETER_DESCRIPTORS
)


def _specific_descriptors() -> dict[LatheStrategyId, tuple[LatheParameterDescriptor, ...]]:
    position = lambda name, order, group=_BASIC: _descriptor(  # noqa: E731
        name, _FLOAT, _MM, group, order
    )
    positive = lambda name, order, group=_BASIC: _descriptor(  # noqa: E731
        name,
        _FLOAT,
        _MM,
        group,
        order,
        minimum=0.0,
        exclusive_minimum=True,
    )
    non_negative = lambda name, order, group=_ADVANCED: _descriptor(  # noqa: E731
        name, _FLOAT, _MM, group, order, minimum=0.0
    )
    rough = (
        position("start_z_mm", 60),
        position("end_z_mm", 70),
        positive("target_diameter_mm", 80),
        positive("max_depth_of_cut_mm", 90),
        non_negative("radial_stock_to_leave_mm", 100),
        non_negative("axial_stock_to_leave_mm", 110),
    )
    finish = (
        position("start_z_mm", 60),
        position("end_z_mm", 70),
        positive("target_diameter_mm", 80),
        _descriptor("finish_passes", _INTEGER, _NONE, _ADVANCED, 90, minimum=1),
        _descriptor("spring_passes", _INTEGER, _NONE, _ADVANCED, 100, minimum=0),
    )
    groove = (
        position("center_z_mm", 60),
        positive("groove_width_mm", 70),
        positive("target_diameter_mm", 80),
        positive("max_step_mm", 90, _ADVANCED),
        non_negative("side_allowance_mm", 100),
    )
    thread = (
        position("start_z_mm", 60),
        position("end_z_mm", 70),
        positive("major_diameter_mm", 80),
        positive("minor_diameter_mm", 90),
        positive("pitch_mm", 100),
        _descriptor(
            "thread_hand", _ENUM, _NONE, _BASIC, 110, enum_type=LatheThreadHand
        ),
        _descriptor("pass_count", _INTEGER, _NONE, _ADVANCED, 120, minimum=1),
        _descriptor("spring_passes", _INTEGER, _NONE, _ADVANCED, 130, minimum=0),
        _descriptor(
            "infeed_angle_deg",
            _FLOAT,
            LatheParameterUnitKind.DEGREE,
            _ADVANCED,
            140,
            minimum=0.0,
            maximum=90.0,
            exclusive_maximum=True,
        ),
    )
    return {
        LatheStrategyId.FACE: (
            position("face_z_mm", 60),
            positive("outer_diameter_mm", 70),
            non_negative("inner_diameter_mm", 80, _BASIC),
            positive("max_depth_of_cut_mm", 90, _ADVANCED),
            non_negative("finish_allowance_mm", 100),
        ),
        LatheStrategyId.OD_ROUGH: rough,
        LatheStrategyId.OD_FINISH: finish,
        LatheStrategyId.ID_ROUGH: rough,
        LatheStrategyId.ID_FINISH: finish,
        LatheStrategyId.OD_GROOVE: groove,
        LatheStrategyId.ID_GROOVE: groove,
        LatheStrategyId.PART_OFF: (
            position("cutoff_z_mm", 60),
            non_negative("target_diameter_mm", 70, _BASIC),
            positive("max_step_mm", 80, _ADVANCED),
            non_negative("side_clearance_mm", 90),
        ),
        LatheStrategyId.OD_THREAD: thread,
        LatheStrategyId.ID_THREAD: thread,
        LatheStrategyId.AXIAL_DRILL: (
            positive("depth_mm", 60),
            position("retract_plane_z_mm", 70),
            _descriptor(
                "peck_depth_mm",
                _FLOAT,
                _MM,
                _ADVANCED,
                80,
                required=False,
                minimum=0.0,
                exclusive_minimum=True,
            ),
            _descriptor(
                "dwell_seconds",
                _FLOAT,
                LatheParameterUnitKind.SECOND,
                _ADVANCED,
                90,
                minimum=0.0,
            ),
        ),
    }


@dataclass(frozen=True, slots=True)
class LatheParameterSchema:
    strategy_id: LatheStrategyId
    descriptors: tuple[LatheParameterDescriptor, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.strategy_id, LatheStrategyId):
            raise TypeError("Lathe parameter schema strategy is invalid")
        if not isinstance(self.descriptors, tuple) or any(
            not isinstance(item, LatheParameterDescriptor) for item in self.descriptors
        ):
            raise TypeError("Lathe parameter descriptors must be a typed tuple")
        ids = tuple(item.parameter_id for item in self.descriptors)
        orders = tuple(item.order for item in self.descriptors)
        if len(set(ids)) != len(ids) or len(set(orders)) != len(orders):
            raise ValueError("Lathe parameter descriptor IDs and orders must be unique")
        if orders != tuple(sorted(orders)):
            raise ValueError("Lathe parameter descriptors must use canonical order")

    def normalize(
        self, values: Mapping[str, object]
    ) -> tuple[tuple[str, LatheParameterValue], ...]:
        if not isinstance(values, Mapping):
            raise TypeError("Lathe parameter input must be a mapping")
        descriptors = {item.parameter_id: item for item in self.descriptors}
        unknown = set(values) - set(descriptors)
        if unknown:
            raise _parameter_error(sorted(unknown)[0], "unknown")
        missing = tuple(
            item.parameter_id
            for item in self.descriptors
            if item.required and item.parameter_id not in values
        )
        if missing:
            raise _parameter_error(missing[0], "required")
        normalized = tuple(
            (
                item.parameter_id,
                item.normalize(values.get(item.parameter_id)),
            )
            for item in self.descriptors
        )
        self._validate_cross_fields(dict(normalized))
        return normalized

    def _validate_cross_fields(self, values: Mapping[str, LatheParameterValue]) -> None:
        if self.strategy_id is LatheStrategyId.FACE and not (
            float(values["inner_diameter_mm"]) < float(values["outer_diameter_mm"])
        ):
            raise _parameter_error("inner_diameter_mm", "inner_less_than_outer")
        if self.strategy_id in {
            LatheStrategyId.OD_ROUGH,
            LatheStrategyId.OD_FINISH,
            LatheStrategyId.ID_ROUGH,
            LatheStrategyId.ID_FINISH,
            LatheStrategyId.OD_THREAD,
            LatheStrategyId.ID_THREAD,
        } and float(values["start_z_mm"]) == float(values["end_z_mm"]):
            raise _parameter_error("end_z_mm", "start_not_equal_end")
        if self.strategy_id in {
            LatheStrategyId.OD_THREAD,
            LatheStrategyId.ID_THREAD,
        } and not (
            float(values["minor_diameter_mm"]) < float(values["major_diameter_mm"])
        ):
            raise _parameter_error("minor_diameter_mm", "minor_less_than_major")


def _parameter_error(parameter_id: str, rule: str) -> LatheParameterValidationError:
    return LatheParameterValidationError(
        (
            LatheDiagnostic(
                LatheDiagnosticCode.INVALID_PARAMETER,
                DiagnosticSeverity.ERROR,
                parameter_id,
                (("rule", rule),),
            ),
        )
    )


_SPECIFIC = _specific_descriptors()
LATHE_PARAMETER_SCHEMAS: tuple[LatheParameterSchema, ...] = tuple(
    LatheParameterSchema(strategy_id, (*COMMON_PARAMETER_DESCRIPTORS, *_SPECIFIC[strategy_id]))
    for strategy_id in LatheStrategyId
)
_SCHEMA_BY_STRATEGY = MappingProxyType(
    {item.strategy_id: item for item in LATHE_PARAMETER_SCHEMAS}
)


def lathe_parameter_schema(strategy_id: LatheStrategyId) -> LatheParameterSchema:
    """Return the exact immutable schema for one typed strategy."""

    if not isinstance(strategy_id, LatheStrategyId):
        raise TypeError("strategy_id must be LatheStrategyId")
    return _SCHEMA_BY_STRATEGY[strategy_id]


@dataclass(frozen=True, slots=True)
class LatheParameterState:
    """Immutable, schema-validated and canonically ordered parameter state."""

    strategy_id: LatheStrategyId
    values: tuple[tuple[str, LatheParameterValue], ...]

    def __post_init__(self) -> None:
        if not isinstance(self.strategy_id, LatheStrategyId):
            raise TypeError("Lathe parameter-state strategy is invalid")
        if not isinstance(self.values, tuple) or any(
            not isinstance(item, tuple) or len(item) != 2 for item in self.values
        ):
            raise TypeError("Lathe parameter-state values must be immutable pairs")
        names = tuple(item[0] for item in self.values)
        if any(not isinstance(name, str) for name in names) or len(set(names)) != len(names):
            raise ValueError("Lathe parameter-state IDs must be unique strings")
        normalized = lathe_parameter_schema(self.strategy_id).normalize(dict(self.values))
        object.__setattr__(self, "values", normalized)

    @classmethod
    def build(
        cls, strategy_id: LatheStrategyId, values: Mapping[str, object]
    ) -> "LatheParameterState":
        """Validate one mapping atomically before exposing immutable state."""

        normalized = lathe_parameter_schema(strategy_id).normalize(values)
        return cls(strategy_id, normalized)

    def value(self, parameter_id: str) -> LatheParameterValue:
        """Return one typed canonical value."""

        for key, value in self.values:
            if key == parameter_id:
                return value
        raise KeyError(parameter_id)

    @property
    def mapping(self) -> Mapping[str, LatheParameterValue]:
        """Return a read-only view of the canonical values."""

        return MappingProxyType(dict(self.values))

    def with_updates(
        self, updates: tuple["LatheParameterUpdate", ...]
    ) -> "LatheParameterState":
        """Apply an immutable update set atomically."""

        if not isinstance(updates, tuple) or not updates or any(
            not isinstance(item, LatheParameterUpdate) for item in updates
        ):
            raise TypeError("Lathe parameter updates must be a non-empty typed tuple")
        if len({item.parameter_id for item in updates}) != len(updates):
            raise ValueError("Lathe parameter update IDs must be unique")
        values = dict(self.values)
        values.update((item.parameter_id, item.value) for item in updates)
        return LatheParameterState.build(self.strategy_id, values)

    def canonical_values(self) -> tuple[tuple[str, object], ...]:
        """Return JSON-compatible values in exact schema order."""

        return tuple(
            (key, value.value if isinstance(value, StrEnum) else value)
            for key, value in self.values
        )


@dataclass(frozen=True, slots=True)
class LatheParameterUpdate:
    parameter_id: str
    value: object

    def __post_init__(self) -> None:
        if not isinstance(self.parameter_id, str) or not self.parameter_id:
            raise ValueError("Lathe parameter update ID must be non-empty")
        if not (
            self.value is None
            or type(self.value) in {bool, int, float, str}
            or isinstance(self.value, StrEnum)
        ):
            raise TypeError("Lathe parameter update value must be an immutable primitive")


_COMMON_DEFAULTS: dict[str, object] = {
    "spindle_speed_rpm": 1000.0,
    "feed_mm_per_rev": 0.2,
    "clearance_mm": 2.0,
    "retract_mm": 1.0,
    "spindle_direction": LatheSpindleDirection.CW,
}

_SPECIFIC_DEFAULTS: dict[LatheStrategyId, dict[str, object]] = {
    LatheStrategyId.FACE: {
        "face_z_mm": 0.0,
        "outer_diameter_mm": 50.0,
        "inner_diameter_mm": 0.0,
        "max_depth_of_cut_mm": 1.0,
        "finish_allowance_mm": 0.2,
    },
    LatheStrategyId.OD_ROUGH: {
        "start_z_mm": 0.0,
        "end_z_mm": -50.0,
        "target_diameter_mm": 40.0,
        "max_depth_of_cut_mm": 2.0,
        "radial_stock_to_leave_mm": 0.5,
        "axial_stock_to_leave_mm": 0.2,
    },
    LatheStrategyId.OD_FINISH: {
        "start_z_mm": 0.0,
        "end_z_mm": -50.0,
        "target_diameter_mm": 40.0,
        "finish_passes": 1,
        "spring_passes": 0,
    },
    LatheStrategyId.ID_ROUGH: {
        "start_z_mm": 0.0,
        "end_z_mm": -30.0,
        "target_diameter_mm": 20.0,
        "max_depth_of_cut_mm": 1.0,
        "radial_stock_to_leave_mm": 0.3,
        "axial_stock_to_leave_mm": 0.2,
    },
    LatheStrategyId.ID_FINISH: {
        "start_z_mm": 0.0,
        "end_z_mm": -30.0,
        "target_diameter_mm": 20.0,
        "finish_passes": 1,
        "spring_passes": 0,
    },
    LatheStrategyId.OD_GROOVE: {
        "center_z_mm": -20.0,
        "groove_width_mm": 3.0,
        "target_diameter_mm": 35.0,
        "max_step_mm": 1.0,
        "side_allowance_mm": 0.1,
    },
    LatheStrategyId.ID_GROOVE: {
        "center_z_mm": -20.0,
        "groove_width_mm": 3.0,
        "target_diameter_mm": 25.0,
        "max_step_mm": 1.0,
        "side_allowance_mm": 0.1,
    },
    LatheStrategyId.PART_OFF: {
        "cutoff_z_mm": -50.0,
        "target_diameter_mm": 0.0,
        "max_step_mm": 1.0,
        "side_clearance_mm": 0.2,
    },
    LatheStrategyId.OD_THREAD: {
        "start_z_mm": 0.0,
        "end_z_mm": -30.0,
        "major_diameter_mm": 20.0,
        "minor_diameter_mm": 18.0,
        "pitch_mm": 1.5,
        "thread_hand": LatheThreadHand.RIGHT,
        "pass_count": 8,
        "spring_passes": 1,
        "infeed_angle_deg": 29.0,
    },
    LatheStrategyId.ID_THREAD: {
        "start_z_mm": 0.0,
        "end_z_mm": -30.0,
        "major_diameter_mm": 20.0,
        "minor_diameter_mm": 18.0,
        "pitch_mm": 1.5,
        "thread_hand": LatheThreadHand.RIGHT,
        "pass_count": 8,
        "spring_passes": 1,
        "infeed_angle_deg": 29.0,
    },
    LatheStrategyId.AXIAL_DRILL: {
        "depth_mm": 30.0,
        "retract_plane_z_mm": 2.0,
        "peck_depth_mm": None,
        "dwell_seconds": 0.0,
    },
}


def build_lathe_v1_defaults(strategy_id: LatheStrategyId) -> LatheParameterState:
    """Build the exact deterministic V1 editor starting state."""

    if not isinstance(strategy_id, LatheStrategyId):
        raise TypeError("strategy_id must be LatheStrategyId")
    return LatheParameterState.build(
        strategy_id, {**_COMMON_DEFAULTS, **_SPECIFIC_DEFAULTS[strategy_id]}
    )


def decode_canonical_parameter_values(
    strategy_id: LatheStrategyId, values: object
) -> LatheParameterState:
    """Strictly decode canonical in-memory parameter entries."""

    if not isinstance(values, list) or any(
        not isinstance(item, dict) or set(item) != {"parameter_id", "value"}
        for item in values
    ):
        raise ValueError("Canonical Lathe parameter values are malformed")
    schema = lathe_parameter_schema(strategy_id)
    descriptor_by_id = {item.parameter_id: item for item in schema.descriptors}
    decoded: dict[str, object] = {}
    for item in values:
        parameter_id = item["parameter_id"]
        if not isinstance(parameter_id, str) or parameter_id not in descriptor_by_id:
            raise ValueError("Canonical Lathe parameter ID is unknown")
        descriptor = descriptor_by_id[parameter_id]
        value = item["value"]
        if descriptor.value_kind is LatheParameterValueKind.ENUM and value is not None:
            if not isinstance(value, str) or descriptor.enum_type is None:
                raise ValueError("Canonical Lathe enum value is malformed")
            try:
                value = descriptor.enum_type(value)
            except ValueError as error:
                raise ValueError("Canonical Lathe enum value is unknown") from error
        decoded[parameter_id] = value
    return LatheParameterState.build(strategy_id, decoded)


__all__ = [
    "COMMON_PARAMETER_DESCRIPTORS",
    "COMMON_PARAMETER_IDS",
    "LATHE_PARAMETER_SCHEMAS",
    "LatheParameterDescriptor",
    "LatheParameterSchema",
    "LatheParameterState",
    "LatheParameterUpdate",
    "LatheParameterValidationError",
    "build_lathe_v1_defaults",
    "decode_canonical_parameter_values",
    "lathe_parameter_schema",
]
