"""Pure-Python versioned tapping strategy contracts."""

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
from hms_cadcam.cam.domain.operation import (
    DiagnosticCode,
    OperationParameterSet,
)
from hms_cadcam.cam.domain.revision import ContentFingerprint
from hms_cadcam.cam.domain.units import Length, LengthUnit, SpindleSpeed

_VERSION = 1
_TOLERANCE = 1.0e-8
_GEOMETRY_CHUNK_SIZE = 4000
_STRATEGY_FORMAT = "HMS_CAM_TAPPING_STRATEGY"
TAPPING_STRATEGY_KEY = "tapping_v1"
TAPPING_STRATEGY_VERSION = 1


class TappingValidationError(CamValidationError):
    """Tapping validation failed with one stable diagnostic code."""

    def __init__(self, code: DiagnosticCode, message: str) -> None:
        super().__init__(message)
        self.code = code


class TappingHand(StrEnum):
    """Thread hand expressed independently from signed pitch or Z direction."""

    RIGHT_HAND_TAP = "right_hand_tap"
    LEFT_HAND_TAP = "left_hand_tap"


class TappingSynchronizationPolicy(StrEnum):
    """Controller-neutral spindle/feed synchronization policy."""

    RIGID = "rigid"
    FLOATING = "floating"


@dataclass(frozen=True, slots=True)
class TappingStrategy:
    """Immutable deterministic tapping strategy v1."""

    unit: LengthUnit
    geometry: DrillGeometryInput
    depth: DrillDepthDefinition
    nominal_diameter: Length
    pitch: Length
    hand: TappingHand
    spindle_speed: SpindleSpeed
    clearance_height: Length
    retract_height: Length
    synchronization_policy: TappingSynchronizationPolicy
    dwell_seconds: float = 0.0
    tolerance: Length | None = None
    strategy_version: int = TAPPING_STRATEGY_VERSION
    schema_version: int = _VERSION
    SERIALIZATION_VERSION: ClassVar[int] = _VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.unit, LengthUnit) or self.unit is LengthUnit.UNKNOWN:
            raise TappingValidationError(
                DiagnosticCode.TAP_INVALID_PARAMETERS,
                "Tapping requires an explicit known length unit",
            )
        if not isinstance(self.geometry, DrillGeometryInput):
            raise TappingValidationError(
                DiagnosticCode.TAP_GEOMETRY_MISSING,
                "Tapping geometry is missing",
            )
        if not isinstance(self.depth, DrillDepthDefinition):
            raise TappingValidationError(
                DiagnosticCode.TAP_DEPTH_INVALID,
                "Tapping depth is invalid",
            )
        if self.geometry.unit is not self.unit or self.depth.unit is not self.unit:
            raise TappingValidationError(
                DiagnosticCode.TAP_INVALID_PARAMETERS,
                "Tapping geometry and depth must use the strategy unit",
            )
        for value, subject in (
            (self.nominal_diameter, "nominal diameter"),
            (self.pitch, "pitch"),
        ):
            if (
                not isinstance(value, Length)
                or value.unit is not self.unit
                or value.value <= 0.0
            ):
                raise TappingValidationError(
                    DiagnosticCode.TAP_INVALID_PARAMETERS,
                    f"Tapping {subject} must be positive in the strategy unit",
                )
        if not isinstance(self.hand, TappingHand):
            raise TappingValidationError(
                DiagnosticCode.TAP_INVALID_PARAMETERS,
                "Tapping hand is invalid",
            )
        if not isinstance(self.spindle_speed, SpindleSpeed):
            raise TappingValidationError(
                DiagnosticCode.TAP_INVALID_PARAMETERS,
                "Tapping spindle speed is invalid",
            )
        for value, subject in (
            (self.clearance_height, "clearance height"),
            (self.retract_height, "retract height"),
        ):
            if not isinstance(value, Length) or value.unit is not self.unit:
                raise TappingValidationError(
                    DiagnosticCode.TAP_INVALID_PARAMETERS,
                    f"Tapping {subject} must use the strategy unit",
                )
        if (
            self.retract_height.value <= self.depth.top_z.value
            or self.clearance_height.value <= self.retract_height.value
        ):
            raise TappingValidationError(
                DiagnosticCode.TAP_UNSAFE_CLEARANCE,
                "Tapping retract must be above top Z and clearance above retract",
            )
        if not isinstance(
            self.synchronization_policy, TappingSynchronizationPolicy
        ):
            raise TappingValidationError(
                DiagnosticCode.TAP_INVALID_PARAMETERS,
                "Tapping synchronization policy is invalid",
            )
        if isinstance(self.dwell_seconds, bool) or not isinstance(
            self.dwell_seconds, (int, float)
        ):
            raise TappingValidationError(
                DiagnosticCode.TAP_INVALID_PARAMETERS,
                "Tapping dwell must be finite and non-negative",
            )
        dwell = float(self.dwell_seconds)
        if not math.isfinite(dwell) or dwell < 0.0:
            raise TappingValidationError(
                DiagnosticCode.TAP_INVALID_PARAMETERS,
                "Tapping dwell must be finite and non-negative",
            )
        object.__setattr__(self, "dwell_seconds", dwell)
        tolerance = self.tolerance or Length(_TOLERANCE, self.unit)
        if (
            not isinstance(tolerance, Length)
            or tolerance.unit is not self.unit
            or tolerance.value <= 0.0
        ):
            raise TappingValidationError(
                DiagnosticCode.TAP_INVALID_PARAMETERS,
                "Tapping tolerance must be positive in the strategy unit",
            )
        object.__setattr__(self, "tolerance", tolerance)
        if (
            type(self.strategy_version) is not int
            or self.strategy_version != TAPPING_STRATEGY_VERSION
            or type(self.schema_version) is not int
            or self.schema_version != _VERSION
        ):
            raise TappingValidationError(
                DiagnosticCode.TAP_INVALID_PARAMETERS,
                "Unsupported tapping strategy version",
            )

    @property
    def top_z(self) -> Length:
        return self.depth.top_z

    @property
    def final_depth(self) -> Length:
        return self.depth.bottom_z

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
            ("pitch", self.pitch.value),
            ("hand", self.hand.value),
            ("spindle_speed", self.spindle_speed.value),
            ("clearance_height", self.clearance_height.value),
            ("retract_height", self.retract_height.value),
            ("synchronization_policy", self.synchronization_policy.value),
            ("dwell_seconds", self.dwell_seconds),
            ("tolerance", self.tolerance.value),
            ("geometry_chunk_count", len(chunks)),
        ]
        values.extend(
            (f"geometry_{index:04d}", chunk)
            for index, chunk in enumerate(chunks)
        )
        return OperationParameterSet(
            TAPPING_STRATEGY_KEY,
            TAPPING_STRATEGY_VERSION,
            tuple(values),
        )

    @classmethod
    def from_operation_parameters(
        cls, value: OperationParameterSet
    ) -> "TappingStrategy":
        if value.strategy_key != TAPPING_STRATEGY_KEY:
            raise TappingValidationError(
                DiagnosticCode.TAP_INVALID_PARAMETERS,
                "Operation is not a tapping strategy",
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
            fields = {
                "unit", "top_z", "final_depth", "nominal_diameter", "pitch",
                "hand", "spindle_speed", "clearance_height", "retract_height",
                "synchronization_policy", "dwell_seconds", "tolerance",
            }
            if set(data) != fields:
                raise ValueError("unexpected tapping parameters")
            unit = LengthUnit(data["unit"])
            return cls(
                unit=unit,
                geometry=DrillGeometryInput.from_dict(json.loads(geometry_text)),
                depth=DrillDepthDefinition(
                    unit,
                    Length(data["top_z"], unit),
                    Length(data["final_depth"], unit),
                ),
                nominal_diameter=Length(data["nominal_diameter"], unit),
                pitch=Length(data["pitch"], unit),
                hand=TappingHand(data["hand"]),
                spindle_speed=SpindleSpeed(data["spindle_speed"]),
                clearance_height=Length(data["clearance_height"], unit),
                retract_height=Length(data["retract_height"], unit),
                synchronization_policy=TappingSynchronizationPolicy(
                    data["synchronization_policy"]
                ),
                dwell_seconds=data["dwell_seconds"],
                tolerance=Length(data["tolerance"], unit),
                strategy_version=value.strategy_version,
                schema_version=value.schema_version,
            )
        except TappingValidationError:
            raise
        except DrillValidationError as error:
            code = (
                DiagnosticCode.TAP_DEPTH_INVALID
                if error.code in {
                    DiagnosticCode.DRILL_INVALID_DEPTH,
                    DiagnosticCode.DRILL_DEPTH_INVALID,
                }
                else DiagnosticCode.TAP_INVALID_PARAMETERS
            )
            raise TappingValidationError(code, str(error)) from error
        except (KeyError, TypeError, ValueError, CamValidationError) as error:
            raise TappingValidationError(
                DiagnosticCode.TAP_INVALID_PARAMETERS,
                "Tapping operation parameters are malformed",
            ) from error

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": _STRATEGY_FORMAT,
            "format_version": _VERSION,
            "strategy_key": TAPPING_STRATEGY_KEY,
            "strategy_version": self.strategy_version,
            "schema_version": self.schema_version,
            "unit": self.unit.value,
            "geometry": self.geometry.to_dict(),
            "depth": self.depth.to_dict(),
            "nominal_diameter": self.nominal_diameter.value,
            "pitch": self.pitch.value,
            "hand": self.hand.value,
            "spindle_speed": self.spindle_speed.value,
            "clearance_height": self.clearance_height.value,
            "retract_height": self.retract_height.value,
            "synchronization_policy": self.synchronization_policy.value,
            "dwell_seconds": self.dwell_seconds,
            "tolerance": self.tolerance.value,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TappingStrategy":
        fields = {
            "format", "format_version", "strategy_key", "strategy_version",
            "schema_version", "unit", "geometry", "depth",
            "nominal_diameter", "pitch", "hand", "spindle_speed",
            "clearance_height", "retract_height", "synchronization_policy",
            "dwell_seconds", "tolerance",
        }
        if (
            not isinstance(data, dict)
            or set(data) != fields
            or data.get("format") != _STRATEGY_FORMAT
            or data.get("format_version") != _VERSION
            or data.get("strategy_key") != TAPPING_STRATEGY_KEY
        ):
            raise TappingValidationError(
                DiagnosticCode.TAP_INVALID_PARAMETERS,
                "Tapping strategy payload is malformed or unsupported",
            )
        try:
            unit = LengthUnit(data["unit"])
            return cls(
                unit=unit,
                geometry=DrillGeometryInput.from_dict(data["geometry"]),
                depth=DrillDepthDefinition.from_dict(data["depth"]),
                nominal_diameter=Length(data["nominal_diameter"], unit),
                pitch=Length(data["pitch"], unit),
                hand=TappingHand(data["hand"]),
                spindle_speed=SpindleSpeed(data["spindle_speed"]),
                clearance_height=Length(data["clearance_height"], unit),
                retract_height=Length(data["retract_height"], unit),
                synchronization_policy=TappingSynchronizationPolicy(
                    data["synchronization_policy"]
                ),
                dwell_seconds=data["dwell_seconds"],
                tolerance=Length(data["tolerance"], unit),
                strategy_version=data["strategy_version"],
                schema_version=data["schema_version"],
            )
        except TappingValidationError:
            raise
        except DrillValidationError as error:
            code = (
                DiagnosticCode.TAP_DEPTH_INVALID
                if error.code in {
                    DiagnosticCode.DRILL_INVALID_DEPTH,
                    DiagnosticCode.DRILL_DEPTH_INVALID,
                }
                else DiagnosticCode.TAP_INVALID_PARAMETERS
            )
            raise TappingValidationError(code, str(error)) from error
        except (TypeError, ValueError, CamValidationError) as error:
            raise TappingValidationError(
                DiagnosticCode.TAP_INVALID_PARAMETERS,
                "Tapping strategy payload is invalid",
            ) from error
