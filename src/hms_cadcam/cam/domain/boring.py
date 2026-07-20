"""Pure-Python versioned single-point axial boring strategy contracts."""

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
_DEFAULT_TOLERANCE = 1.0e-8
_GEOMETRY_CHUNK_SIZE = 4000
_STRATEGY_FORMAT = "HMS_CAM_BORING_STRATEGY"
BORING_STRATEGY_KEY = "boring_v1"
BORING_STRATEGY_VERSION = 1


class BoringValidationError(CamValidationError):
    """Boring validation failed with one stable diagnostic code."""

    def __init__(self, code: DiagnosticCode, message: str) -> None:
        super().__init__(message)
        self.code = code


class BoringRetractPolicy(StrEnum):
    """Controller-neutral retract policies implemented by Boring v1."""

    CONTROLLED_FEED = "controlled_feed"


class BoringCoolantMode(StrEnum):
    """Controller-neutral coolant request supported by Toolpath IR v1."""

    OFF = "off"
    FLOOD = "flood"
    MIST = "mist"
    THROUGH_TOOL = "through_tool"


@dataclass(frozen=True, slots=True)
class BoringStrategy:
    """Immutable deterministic single-point axial boring strategy v1."""

    unit: LengthUnit
    geometry: DrillGeometryInput
    depth: DrillDepthDefinition
    finished_bore_diameter: Length
    pre_bore_diameter: Length | None
    spindle_rpm: SpindleSpeed
    feed_per_revolution: FeedRate
    clearance_height: Length
    retract_height: Length
    spindle_direction: SpindleDirection
    retract_policy: BoringRetractPolicy = BoringRetractPolicy.CONTROLLED_FEED
    coolant: BoringCoolantMode = BoringCoolantMode.OFF
    dwell_seconds: float = 0.0
    tolerance: Length | None = None
    strategy_version: int = BORING_STRATEGY_VERSION
    schema_version: int = _VERSION
    SERIALIZATION_VERSION: ClassVar[int] = _VERSION

    def __post_init__(self) -> None:
        if self.unit not in {LengthUnit.MM, LengthUnit.INCH}:
            raise BoringValidationError(
                DiagnosticCode.BORE_INVALID_PARAMETERS,
                "Boring requires an explicit MM or INCH unit",
            )
        if not isinstance(self.geometry, DrillGeometryInput):
            raise BoringValidationError(
                DiagnosticCode.BORE_GEOMETRY_MISSING,
                "Boring geometry is missing",
            )
        if not isinstance(self.depth, DrillDepthDefinition):
            raise BoringValidationError(
                DiagnosticCode.BORE_DEPTH_INVALID,
                "Boring depth is invalid",
            )
        if self.geometry.unit is not self.unit or self.depth.unit is not self.unit:
            raise BoringValidationError(
                DiagnosticCode.BORE_INVALID_PARAMETERS,
                "Boring geometry and depth must use the strategy unit",
            )
        if (
            not isinstance(self.finished_bore_diameter, Length)
            or self.finished_bore_diameter.unit is not self.unit
            or self.finished_bore_diameter.value <= 0.0
        ):
            raise BoringValidationError(
                DiagnosticCode.BORE_INVALID_PARAMETERS,
                "Finished bore diameter must be positive in the strategy unit",
            )
        if self.pre_bore_diameter is None:
            raise BoringValidationError(
                DiagnosticCode.BORE_PREBORE_MISSING,
                "Boring requires an explicit pre-bore diameter",
            )
        if (
            not isinstance(self.pre_bore_diameter, Length)
            or self.pre_bore_diameter.unit is not self.unit
            or self.pre_bore_diameter.value <= 0.0
            or self.pre_bore_diameter.value >= self.finished_bore_diameter.value
        ):
            raise BoringValidationError(
                DiagnosticCode.BORE_PREBORE_INVALID,
                "Pre-bore diameter must be positive and smaller than finished diameter",
            )
        if not isinstance(self.spindle_rpm, SpindleSpeed):
            raise BoringValidationError(
                DiagnosticCode.BORE_INVALID_PARAMETERS,
                "Boring spindle RPM is invalid",
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
            raise BoringValidationError(
                DiagnosticCode.BORE_INVALID_PARAMETERS,
                "Boring feed must be positive distance per revolution",
            )
        for value, subject in (
            (self.clearance_height, "clearance height"),
            (self.retract_height, "retract height"),
        ):
            if not isinstance(value, Length) or value.unit is not self.unit:
                raise BoringValidationError(
                    DiagnosticCode.BORE_INVALID_PARAMETERS,
                    f"Boring {subject} must use the strategy unit",
                )
        if (
            self.retract_height.value <= self.depth.top_z.value
            or self.clearance_height.value <= self.retract_height.value
        ):
            raise BoringValidationError(
                DiagnosticCode.BORE_UNSAFE_CLEARANCE,
                "Boring retract must be above top Z and clearance above retract",
            )
        if not isinstance(self.spindle_direction, SpindleDirection):
            raise BoringValidationError(
                DiagnosticCode.BORE_INVALID_PARAMETERS,
                "Boring spindle direction is invalid",
            )
        if self.retract_policy is not BoringRetractPolicy.CONTROLLED_FEED:
            raise BoringValidationError(
                DiagnosticCode.BORE_INVALID_PARAMETERS,
                "Boring v1 supports only controlled feed retract",
            )
        if not isinstance(self.coolant, BoringCoolantMode):
            raise BoringValidationError(
                DiagnosticCode.BORE_INVALID_PARAMETERS,
                "Boring coolant mode is invalid",
            )
        if isinstance(self.dwell_seconds, bool) or not isinstance(
            self.dwell_seconds, (int, float)
        ):
            raise BoringValidationError(
                DiagnosticCode.BORE_INVALID_PARAMETERS,
                "Boring dwell must be finite and non-negative",
            )
        dwell = float(self.dwell_seconds)
        if not math.isfinite(dwell) or dwell < 0.0:
            raise BoringValidationError(
                DiagnosticCode.BORE_INVALID_PARAMETERS,
                "Boring dwell must be finite and non-negative",
            )
        object.__setattr__(self, "dwell_seconds", dwell)
        tolerance = self.tolerance or Length(_DEFAULT_TOLERANCE, self.unit)
        if (
            not isinstance(tolerance, Length)
            or tolerance.unit is not self.unit
            or tolerance.value <= 0.0
        ):
            raise BoringValidationError(
                DiagnosticCode.BORE_INVALID_PARAMETERS,
                "Boring tolerance must be positive in the strategy unit",
            )
        object.__setattr__(self, "tolerance", tolerance)
        stock = self.radial_stock.value
        if (
            stock <= tolerance.value
            or stock >= self.finished_bore_diameter.value / 2.0 - tolerance.value
        ):
            raise BoringValidationError(
                DiagnosticCode.BORE_STOCK_INVALID,
                "Boring radial stock is outside geometric limits",
            )
        if (
            type(self.strategy_version) is not int
            or self.strategy_version != BORING_STRATEGY_VERSION
            or type(self.schema_version) is not int
            or self.schema_version != _VERSION
        ):
            raise BoringValidationError(
                DiagnosticCode.BORE_INVALID_PARAMETERS,
                "Unsupported boring strategy version",
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
    def radial_stock(self) -> Length:
        assert self.pre_bore_diameter is not None
        return Length(
            (
                self.finished_bore_diameter.value
                - self.pre_bore_diameter.value
            )
            / 2.0,
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
            self.feed_per_revolution.value * self.spindle_rpm.value,
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
            ("finished_bore_diameter", self.finished_bore_diameter.value),
            ("pre_bore_diameter", self.pre_bore_diameter.value),
            ("spindle_rpm", self.spindle_rpm.value),
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
            BORING_STRATEGY_KEY,
            BORING_STRATEGY_VERSION,
            tuple(values),
        )

    @classmethod
    def from_operation_parameters(
        cls, value: OperationParameterSet
    ) -> "BoringStrategy":
        if value.strategy_key != BORING_STRATEGY_KEY:
            raise BoringValidationError(
                DiagnosticCode.BORE_INVALID_PARAMETERS,
                "Operation is not a boring strategy",
            )
        data = dict(value.values)
        try:
            chunk_count = data.pop("geometry_chunk_count")
            if type(chunk_count) is not int or not 1 <= chunk_count <= 1024:
                raise ValueError("invalid geometry chunk count")
            chunk_keys = tuple(
                f"geometry_{index:04d}" for index in range(chunk_count)
            )
            if any(type(data.get(key)) is not str for key in chunk_keys):
                raise ValueError("invalid geometry chunks")
            geometry_text = "".join(str(data.pop(key)) for key in chunk_keys)
            if "pre_bore_diameter" not in data or data["pre_bore_diameter"] is None:
                raise BoringValidationError(
                    DiagnosticCode.BORE_PREBORE_MISSING,
                    "Boring requires an explicit pre-bore diameter",
                )
            fields = {
                "unit", "top_z", "final_depth", "finished_bore_diameter",
                "pre_bore_diameter", "spindle_rpm", "feed_per_revolution",
                "clearance_height", "retract_height", "spindle_direction",
                "retract_policy", "coolant", "dwell_seconds", "tolerance",
            }
            if set(data) != fields:
                raise ValueError("unexpected boring parameters")
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
                finished_bore_diameter=Length(
                    data["finished_bore_diameter"], unit
                ),
                pre_bore_diameter=Length(data["pre_bore_diameter"], unit),
                spindle_rpm=SpindleSpeed(data["spindle_rpm"]),
                feed_per_revolution=FeedRate(
                    data["feed_per_revolution"], feed_unit
                ),
                clearance_height=Length(data["clearance_height"], unit),
                retract_height=Length(data["retract_height"], unit),
                spindle_direction=SpindleDirection(data["spindle_direction"]),
                retract_policy=BoringRetractPolicy(data["retract_policy"]),
                coolant=BoringCoolantMode(data["coolant"]),
                dwell_seconds=data["dwell_seconds"],
                tolerance=Length(data["tolerance"], unit),
                strategy_version=value.strategy_version,
                schema_version=value.schema_version,
            )
        except BoringValidationError:
            raise
        except DrillValidationError as error:
            code = (
                DiagnosticCode.BORE_DEPTH_INVALID
                if error.code in {
                    DiagnosticCode.DRILL_INVALID_DEPTH,
                    DiagnosticCode.DRILL_DEPTH_INVALID,
                }
                else DiagnosticCode.BORE_INVALID_PARAMETERS
            )
            raise BoringValidationError(code, str(error)) from error
        except (KeyError, TypeError, ValueError, CamValidationError) as error:
            raise BoringValidationError(
                DiagnosticCode.BORE_INVALID_PARAMETERS,
                "Boring operation parameters are malformed",
            ) from error

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": _STRATEGY_FORMAT,
            "format_version": _VERSION,
            "strategy_key": BORING_STRATEGY_KEY,
            "strategy_version": self.strategy_version,
            "schema_version": self.schema_version,
            "unit": self.unit.value,
            "geometry": self.geometry.to_dict(),
            "depth": self.depth.to_dict(),
            "finished_bore_diameter": self.finished_bore_diameter.value,
            "pre_bore_diameter": self.pre_bore_diameter.value,
            "spindle_rpm": self.spindle_rpm.value,
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
    def from_dict(cls, data: dict[str, Any]) -> "BoringStrategy":
        fields = {
            "format", "format_version", "strategy_key", "strategy_version",
            "schema_version", "unit", "geometry", "depth",
            "finished_bore_diameter", "pre_bore_diameter", "spindle_rpm",
            "feed_per_revolution", "clearance_height", "retract_height",
            "spindle_direction", "retract_policy", "coolant",
            "dwell_seconds", "tolerance",
        }
        if (
            isinstance(data, dict)
            and data.get("format") == _STRATEGY_FORMAT
            and (
                "pre_bore_diameter" not in data
                or data.get("pre_bore_diameter") is None
            )
        ):
            raise BoringValidationError(
                DiagnosticCode.BORE_PREBORE_MISSING,
                "Boring requires an explicit pre-bore diameter",
            )
        if (
            not isinstance(data, dict)
            or set(data) != fields
            or data.get("format") != _STRATEGY_FORMAT
            or data.get("format_version") != _VERSION
            or data.get("strategy_key") != BORING_STRATEGY_KEY
        ):
            raise BoringValidationError(
                DiagnosticCode.BORE_INVALID_PARAMETERS,
                "Boring strategy payload is malformed or unsupported",
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
                finished_bore_diameter=Length(
                    data["finished_bore_diameter"], unit
                ),
                pre_bore_diameter=Length(data["pre_bore_diameter"], unit),
                spindle_rpm=SpindleSpeed(data["spindle_rpm"]),
                feed_per_revolution=FeedRate(
                    data["feed_per_revolution"], feed_unit
                ),
                clearance_height=Length(data["clearance_height"], unit),
                retract_height=Length(data["retract_height"], unit),
                spindle_direction=SpindleDirection(data["spindle_direction"]),
                retract_policy=BoringRetractPolicy(data["retract_policy"]),
                coolant=BoringCoolantMode(data["coolant"]),
                dwell_seconds=data["dwell_seconds"],
                tolerance=Length(data["tolerance"], unit),
                strategy_version=data["strategy_version"],
                schema_version=data["schema_version"],
            )
        except BoringValidationError:
            raise
        except DrillValidationError as error:
            code = (
                DiagnosticCode.BORE_DEPTH_INVALID
                if error.code in {
                    DiagnosticCode.DRILL_INVALID_DEPTH,
                    DiagnosticCode.DRILL_DEPTH_INVALID,
                }
                else DiagnosticCode.BORE_INVALID_PARAMETERS
            )
            raise BoringValidationError(code, str(error)) from error
        except (TypeError, ValueError, CamValidationError) as error:
            raise BoringValidationError(
                DiagnosticCode.BORE_INVALID_PARAMETERS,
                "Boring strategy payload is invalid",
            ) from error
