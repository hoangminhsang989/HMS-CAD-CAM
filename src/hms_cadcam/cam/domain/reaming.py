"""Pure-Python versioned reaming strategy contracts."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, ClassVar

from hms_cadcam.cam.domain.drilling import (
    DrillDepthDefinition,
    DrillGeometryInput,
    DrillValidationError,
)
from hms_cadcam.cam.domain.errors import CamValidationError
from hms_cadcam.cam.domain.machine import SpindleDirection
from hms_cadcam.cam.domain.operation import DiagnosticCode, OperationParameterSet
from hms_cadcam.cam.domain.revision import ContentFingerprint
from hms_cadcam.cam.domain.units import (
    FeedRate,
    FeedUnit,
    Length,
    LengthUnit,
    SpindleSpeed,
)

_VERSION = 1
_TOLERANCE = 1.0e-8
_GEOMETRY_CHUNK_SIZE = 4000
_STRATEGY_FORMAT = "HMS_CAM_REAMING_STRATEGY"
REAMING_STRATEGY_KEY = "reaming_v1"
REAMING_STRATEGY_VERSION = 1


class ReamingValidationError(CamValidationError):
    """Reaming validation failed with one stable diagnostic code."""

    def __init__(self, code: DiagnosticCode, message: str) -> None:
        super().__init__(message)
        self.code = code


class ReamingRetractPolicy(StrEnum):
    """Controller-neutral retract policies implemented by Reaming v1."""

    CONTROLLED_FEED = "controlled_feed"


class ReamingCoolantMode(StrEnum):
    """Controller-neutral coolant request supported by the current Toolpath IR."""

    OFF = "off"
    FLOOD = "flood"
    MIST = "mist"
    THROUGH_TOOL = "through_tool"


@dataclass(frozen=True, slots=True)
class ReamingStrategy:
    """Immutable deterministic reaming strategy v1."""

    unit: LengthUnit
    geometry: DrillGeometryInput
    depth: DrillDepthDefinition
    nominal_diameter: Length
    pre_hole_diameter: Length | None
    spindle_speed: SpindleSpeed
    feed_per_revolution: FeedRate
    clearance_height: Length
    retract_height: Length
    spindle_direction: SpindleDirection
    retract_policy: ReamingRetractPolicy = ReamingRetractPolicy.CONTROLLED_FEED
    coolant: ReamingCoolantMode = ReamingCoolantMode.OFF
    dwell_seconds: float = 0.0
    tolerance: Length | None = None
    strategy_version: int = REAMING_STRATEGY_VERSION
    schema_version: int = _VERSION
    SERIALIZATION_VERSION: ClassVar[int] = _VERSION

    def __post_init__(self) -> None:
        if self.unit not in {LengthUnit.MM, LengthUnit.INCH}:
            raise ReamingValidationError(
                DiagnosticCode.REAM_INVALID_PARAMETERS,
                "Reaming requires an explicit MM or INCH unit",
            )
        if not isinstance(self.geometry, DrillGeometryInput):
            raise ReamingValidationError(
                DiagnosticCode.REAM_GEOMETRY_MISSING,
                "Reaming geometry is missing",
            )
        if not isinstance(self.depth, DrillDepthDefinition):
            raise ReamingValidationError(
                DiagnosticCode.REAM_DEPTH_INVALID,
                "Reaming depth is invalid",
            )
        if self.geometry.unit is not self.unit or self.depth.unit is not self.unit:
            raise ReamingValidationError(
                DiagnosticCode.REAM_INVALID_PARAMETERS,
                "Reaming geometry and depth must use the strategy unit",
            )
        if (
            not isinstance(self.nominal_diameter, Length)
            or self.nominal_diameter.unit is not self.unit
            or self.nominal_diameter.value <= 0.0
        ):
            raise ReamingValidationError(
                DiagnosticCode.REAM_INVALID_PARAMETERS,
                "Reaming nominal diameter must be positive in the strategy unit",
            )
        if self.pre_hole_diameter is None:
            raise ReamingValidationError(
                DiagnosticCode.REAM_PREHOLE_MISSING,
                "Reaming requires an explicit pre-hole diameter",
            )
        if (
            not isinstance(self.pre_hole_diameter, Length)
            or self.pre_hole_diameter.unit is not self.unit
            or self.pre_hole_diameter.value <= 0.0
            or self.pre_hole_diameter.value >= self.nominal_diameter.value
        ):
            raise ReamingValidationError(
                DiagnosticCode.REAM_PREHOLE_INVALID,
                "Pre-hole diameter must be positive and smaller than nominal diameter",
            )
        if not isinstance(self.spindle_speed, SpindleSpeed):
            raise ReamingValidationError(
                DiagnosticCode.REAM_INVALID_PARAMETERS,
                "Reaming spindle speed is invalid",
            )
        expected_feed = (
            FeedUnit.MM_PER_REVOLUTION
            if self.unit is LengthUnit.MM
            else FeedUnit.INCH_PER_REVOLUTION
        )
        if (
            not isinstance(self.feed_per_revolution, FeedRate)
            or self.feed_per_revolution.unit is not expected_feed
        ):
            raise ReamingValidationError(
                DiagnosticCode.REAM_INVALID_PARAMETERS,
                "Reaming feed must be positive distance per revolution",
            )
        for value, subject in (
            (self.clearance_height, "clearance height"),
            (self.retract_height, "retract height"),
        ):
            if not isinstance(value, Length) or value.unit is not self.unit:
                raise ReamingValidationError(
                    DiagnosticCode.REAM_INVALID_PARAMETERS,
                    f"Reaming {subject} must use the strategy unit",
                )
        if (
            self.retract_height.value <= self.depth.top_z.value
            or self.clearance_height.value <= self.retract_height.value
        ):
            raise ReamingValidationError(
                DiagnosticCode.REAM_UNSAFE_CLEARANCE,
                "Reaming retract must be above top Z and clearance above retract",
            )
        if not isinstance(self.spindle_direction, SpindleDirection):
            raise ReamingValidationError(
                DiagnosticCode.REAM_INVALID_PARAMETERS,
                "Reaming spindle direction is invalid",
            )
        if self.retract_policy is not ReamingRetractPolicy.CONTROLLED_FEED:
            raise ReamingValidationError(
                DiagnosticCode.REAM_INVALID_PARAMETERS,
                "Reaming v1 supports only controlled feed retract",
            )
        if not isinstance(self.coolant, ReamingCoolantMode):
            raise ReamingValidationError(
                DiagnosticCode.REAM_INVALID_PARAMETERS,
                "Reaming coolant mode is invalid",
            )
        if isinstance(self.dwell_seconds, bool) or not isinstance(
            self.dwell_seconds, (int, float)
        ):
            raise ReamingValidationError(
                DiagnosticCode.REAM_INVALID_PARAMETERS,
                "Reaming dwell must be finite and non-negative",
            )
        dwell = float(self.dwell_seconds)
        if not math.isfinite(dwell) or dwell < 0.0:
            raise ReamingValidationError(
                DiagnosticCode.REAM_INVALID_PARAMETERS,
                "Reaming dwell must be finite and non-negative",
            )
        object.__setattr__(self, "dwell_seconds", dwell)
        tolerance = self.tolerance or Length(_TOLERANCE, self.unit)
        if (
            not isinstance(tolerance, Length)
            or tolerance.unit is not self.unit
            or tolerance.value <= 0.0
        ):
            raise ReamingValidationError(
                DiagnosticCode.REAM_INVALID_PARAMETERS,
                "Reaming tolerance must be positive in the strategy unit",
            )
        object.__setattr__(self, "tolerance", tolerance)
        stock = self.stock_per_side.value
        if (
            stock <= tolerance.value
            or stock >= self.nominal_diameter.value / 2.0 - tolerance.value
        ):
            raise ReamingValidationError(
                DiagnosticCode.REAM_STOCK_INVALID,
                "Reaming stock per side is outside geometric limits",
            )
        if (
            type(self.strategy_version) is not int
            or self.strategy_version != REAMING_STRATEGY_VERSION
            or type(self.schema_version) is not int
            or self.schema_version != _VERSION
        ):
            raise ReamingValidationError(
                DiagnosticCode.REAM_INVALID_PARAMETERS,
                "Unsupported reaming strategy version",
            )

    @property
    def top_z(self) -> Length:
        return self.depth.top_z

    @property
    def final_depth(self) -> Length:
        return self.depth.bottom_z

    @property
    def cutting_depth(self) -> Length:
        return self.depth.depth

    @property
    def stock_per_side(self) -> Length:
        assert self.pre_hole_diameter is not None
        return Length(
            (self.nominal_diameter.value - self.pre_hole_diameter.value) / 2.0,
            self.unit,
        )

    @property
    def feed_per_minute(self) -> FeedRate:
        unit = (
            FeedUnit.MM_PER_MINUTE
            if self.unit is LengthUnit.MM
            else FeedUnit.INCH_PER_MINUTE
        )
        return FeedRate(
            self.feed_per_revolution.value * self.spindle_speed.value,
            unit,
        )

    @property
    def fingerprint(self) -> ContentFingerprint:
        return ContentFingerprint.from_payload(self.to_dict())

    def to_operation_parameters(self) -> OperationParameterSet:
        geometry_json = json.dumps(
            self.geometry.to_dict(), sort_keys=True, separators=(",", ":")
        )
        chunks = tuple(
            geometry_json[index:index + _GEOMETRY_CHUNK_SIZE]
            for index in range(0, len(geometry_json), _GEOMETRY_CHUNK_SIZE)
        )
        values: list[tuple[str, object]] = [
            ("unit", self.unit.value),
            ("top_z", self.top_z.value),
            ("final_depth", self.final_depth.value),
            ("nominal_diameter", self.nominal_diameter.value),
            ("pre_hole_diameter", self.pre_hole_diameter.value),
            ("spindle_speed", self.spindle_speed.value),
            ("feed_per_revolution", self.feed_per_revolution.value),
            ("clearance_height", self.clearance_height.value),
            ("retract_height", self.retract_height.value),
            ("spindle_direction", self.spindle_direction.value),
            ("retract_policy", self.retract_policy.value),
            ("coolant", self.coolant.value),
            ("dwell_seconds", self.dwell_seconds),
            ("tolerance", self.tolerance.value),
            ("geometry_chunk_count", len(chunks)),
        ]
        values.extend(
            (f"geometry_{index:04d}", chunk)
            for index, chunk in enumerate(chunks)
        )
        return OperationParameterSet(
            REAMING_STRATEGY_KEY,
            REAMING_STRATEGY_VERSION,
            tuple(values),
        )

    @classmethod
    def from_operation_parameters(
        cls, value: OperationParameterSet
    ) -> "ReamingStrategy":
        if value.strategy_key != REAMING_STRATEGY_KEY:
            raise ReamingValidationError(
                DiagnosticCode.REAM_INVALID_PARAMETERS,
                "Operation is not a reaming strategy",
            )
        data = dict(value.values)
        try:
            # Stage17A automatic setup is additive metadata, not a domain field.
            data.pop("automatic_parameter_contract", None)
            chunk_count = data.pop("geometry_chunk_count")
            if type(chunk_count) is not int or not 1 <= chunk_count <= 1024:
                raise ValueError("invalid geometry chunk count")
            chunk_keys = tuple(
                f"geometry_{index:04d}" for index in range(chunk_count)
            )
            if any(type(data.get(key)) is not str for key in chunk_keys):
                raise ValueError("invalid geometry chunks")
            geometry_text = "".join(str(data.pop(key)) for key in chunk_keys)
            if "pre_hole_diameter" not in data or data["pre_hole_diameter"] is None:
                raise ReamingValidationError(
                    DiagnosticCode.REAM_PREHOLE_MISSING,
                    "Reaming requires an explicit pre-hole diameter",
                )
            fields = {
                "unit", "top_z", "final_depth", "nominal_diameter",
                "pre_hole_diameter", "spindle_speed", "feed_per_revolution",
                "clearance_height", "retract_height", "spindle_direction",
                "retract_policy", "coolant", "dwell_seconds", "tolerance",
            }
            if set(data) != fields:
                raise ValueError("unexpected reaming parameters")
            unit = LengthUnit(data["unit"])
            feed_unit = (
                FeedUnit.MM_PER_REVOLUTION
                if unit is LengthUnit.MM
                else FeedUnit.INCH_PER_REVOLUTION
            )
            return cls(
                unit=unit,
                geometry=DrillGeometryInput.from_dict(json.loads(geometry_text)),
                depth=DrillDepthDefinition(
                    unit,
                    Length(data["top_z"], unit),
                    Length(data["final_depth"], unit),
                ),
                nominal_diameter=Length(data["nominal_diameter"], unit),
                pre_hole_diameter=Length(data["pre_hole_diameter"], unit),
                spindle_speed=SpindleSpeed(data["spindle_speed"]),
                feed_per_revolution=FeedRate(
                    data["feed_per_revolution"], feed_unit
                ),
                clearance_height=Length(data["clearance_height"], unit),
                retract_height=Length(data["retract_height"], unit),
                spindle_direction=SpindleDirection(data["spindle_direction"]),
                retract_policy=ReamingRetractPolicy(data["retract_policy"]),
                coolant=ReamingCoolantMode(data["coolant"]),
                dwell_seconds=data["dwell_seconds"],
                tolerance=Length(data["tolerance"], unit),
                strategy_version=value.strategy_version,
                schema_version=value.schema_version,
            )
        except ReamingValidationError:
            raise
        except DrillValidationError as error:
            code = (
                DiagnosticCode.REAM_DEPTH_INVALID
                if error.code in {
                    DiagnosticCode.DRILL_INVALID_DEPTH,
                    DiagnosticCode.DRILL_DEPTH_INVALID,
                }
                else DiagnosticCode.REAM_INVALID_PARAMETERS
            )
            raise ReamingValidationError(code, str(error)) from error
        except (KeyError, TypeError, ValueError, CamValidationError) as error:
            raise ReamingValidationError(
                DiagnosticCode.REAM_INVALID_PARAMETERS,
                "Reaming operation parameters are malformed",
            ) from error

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": _STRATEGY_FORMAT,
            "format_version": _VERSION,
            "strategy_key": REAMING_STRATEGY_KEY,
            "strategy_version": self.strategy_version,
            "schema_version": self.schema_version,
            "unit": self.unit.value,
            "geometry": self.geometry.to_dict(),
            "depth": self.depth.to_dict(),
            "nominal_diameter": self.nominal_diameter.value,
            "pre_hole_diameter": self.pre_hole_diameter.value,
            "spindle_speed": self.spindle_speed.value,
            "feed_per_revolution": self.feed_per_revolution.value,
            "clearance_height": self.clearance_height.value,
            "retract_height": self.retract_height.value,
            "spindle_direction": self.spindle_direction.value,
            "retract_policy": self.retract_policy.value,
            "coolant": self.coolant.value,
            "dwell_seconds": self.dwell_seconds,
            "tolerance": self.tolerance.value,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ReamingStrategy":
        fields = {
            "format", "format_version", "strategy_key", "strategy_version",
            "schema_version", "unit", "geometry", "depth",
            "nominal_diameter", "pre_hole_diameter", "spindle_speed",
            "feed_per_revolution", "clearance_height", "retract_height",
            "spindle_direction", "retract_policy", "coolant",
            "dwell_seconds", "tolerance",
        }
        if (
            isinstance(data, dict)
            and data.get("format") == _STRATEGY_FORMAT
            and (
                "pre_hole_diameter" not in data
                or data.get("pre_hole_diameter") is None
            )
        ):
            raise ReamingValidationError(
                DiagnosticCode.REAM_PREHOLE_MISSING,
                "Reaming requires an explicit pre-hole diameter",
            )
        if (
            not isinstance(data, dict)
            or set(data) != fields
            or data.get("format") != _STRATEGY_FORMAT
            or data.get("format_version") != _VERSION
            or data.get("strategy_key") != REAMING_STRATEGY_KEY
        ):
            raise ReamingValidationError(
                DiagnosticCode.REAM_INVALID_PARAMETERS,
                "Reaming strategy payload is malformed or unsupported",
            )
        try:
            unit = LengthUnit(data["unit"])
            feed_unit = (
                FeedUnit.MM_PER_REVOLUTION
                if unit is LengthUnit.MM
                else FeedUnit.INCH_PER_REVOLUTION
            )
            return cls(
                unit=unit,
                geometry=DrillGeometryInput.from_dict(data["geometry"]),
                depth=DrillDepthDefinition.from_dict(data["depth"]),
                nominal_diameter=Length(data["nominal_diameter"], unit),
                pre_hole_diameter=Length(data["pre_hole_diameter"], unit),
                spindle_speed=SpindleSpeed(data["spindle_speed"]),
                feed_per_revolution=FeedRate(
                    data["feed_per_revolution"], feed_unit
                ),
                clearance_height=Length(data["clearance_height"], unit),
                retract_height=Length(data["retract_height"], unit),
                spindle_direction=SpindleDirection(data["spindle_direction"]),
                retract_policy=ReamingRetractPolicy(data["retract_policy"]),
                coolant=ReamingCoolantMode(data["coolant"]),
                dwell_seconds=data["dwell_seconds"],
                tolerance=Length(data["tolerance"], unit),
                strategy_version=data["strategy_version"],
                schema_version=data["schema_version"],
            )
        except ReamingValidationError:
            raise
        except DrillValidationError as error:
            code = (
                DiagnosticCode.REAM_DEPTH_INVALID
                if error.code in {
                    DiagnosticCode.DRILL_INVALID_DEPTH,
                    DiagnosticCode.DRILL_DEPTH_INVALID,
                }
                else DiagnosticCode.REAM_INVALID_PARAMETERS
            )
            raise ReamingValidationError(code, str(error)) from error
        except (TypeError, ValueError, CamValidationError) as error:
            raise ReamingValidationError(
                DiagnosticCode.REAM_INVALID_PARAMETERS,
                "Reaming strategy payload is invalid",
            ) from error
