"""Strict manual-only schema for R273 Rest Finishing 3-axis core.

This module owns the persisted parameter vocabulary and selected planar profile
authority only.  It does not calculate material, toolpaths, successors, or
application/project persistence; those remain later R273/R274 concerns.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from hms_cadcam.cam.domain.contour import ContourProfileDescriptor, ContourProfileSource
from hms_cadcam.cam.domain.errors import CamUnitError, CamValidationError, UnsupportedCamSchemaError
from hms_cadcam.cam.domain.operation import OperationParameterSet
from hms_cadcam.cam.domain.revision import ContentFingerprint
from hms_cadcam.cam.domain.units import FeedRate, FeedUnit, Length, LengthUnit, SpindleSpeed

REST_FINISHING_STRATEGY_KEY = "rest_finishing_3axis"
REST_FINISHING_STRATEGY_VERSION = 1
REST_FINISHING_PARAMETER_SCHEMA_VERSION = 1
REST_FINISHING_PARAMETER_FORMAT = "HMS_CAM_REST_FINISHING_3AXIS_PARAMETERS"


class RestFinishingDiagnosticCode(StrEnum):
    """Stable typed failures defined by the R273 domain/schema boundary."""

    INVALID_PARAMETERS = "rest_finishing.invalid_parameters"
    PROFILE_INVALID = "rest_finishing.profile_invalid"
    AUTOMATIC_FORBIDDEN = "rest_finishing.automatic_forbidden"
    UNSUPPORTED = "rest_finishing.unsupported"
    GEOMETRY_INVALID = "rest_finishing.geometry_invalid"
    MATERIAL_STATE_MISSING = "rest_finishing.material_state_missing"
    MATERIAL_STATE_STALE = "rest_finishing.material_state_stale"
    MATERIAL_STATE_AMBIGUOUS = "rest_finishing.material_state_ambiguous"
    MATERIAL_STATE_INVALID = "rest_finishing.material_state_invalid"
    MATERIAL_BELOW_TARGET = "rest_finishing.material_below_target"
    TOOL_INELIGIBLE = "rest_finishing.tool_ineligible"
    MACHINE_INCOMPATIBLE = "rest_finishing.machine_incompatible"
    UNREACHABLE_FINISHING_MATERIAL = "rest_finishing.unreachable_finishing_material"
    PATH_OUTSIDE_AUTHORITY = "rest_finishing.path_outside_authority"
    ENTRY_UNSAFE = "rest_finishing.entry_unsafe"
    LINK_UNSAFE = "rest_finishing.link_unsafe"
    TOOLPATH_LIMIT_EXCEEDED = "rest_finishing.toolpath_limit_exceeded"
    STEPDOWN_EXCEEDED = "rest_finishing.stepdown_exceeded"
    SUCCESSOR_INVALID = "rest_finishing.successor_invalid"
    CANCELLED = "rest_finishing.cancelled"


class RestFinishingValidationError(CamValidationError):
    """Fail-closed Rest Finishing validation error carrying one stable code."""

    def __init__(self, code: RestFinishingDiagnosticCode, message: str) -> None:
        if not isinstance(code, RestFinishingDiagnosticCode):
            raise TypeError("Rest Finishing diagnostic code is invalid")
        super().__init__(message)
        self.code = code


_FIELDS = (
    "unit", "profile_source", "nominal_target_z", "final_stock_allowance",
    "tolerance", "stepover", "max_stepdown", "clearance_height",
    "retract_height", "cutting_feed_rate", "plunge_feed_rate", "spindle_speed",
)
_AUTOMATIC_FIELD_NAMES = frozenset({
    "automatic_parameter_contract", "automatic_mode", "automatic_parameters",
    "parameter_mode", "mode",
})


def _length(
    value: Length,
    *,
    name: str,
    unit: LengthUnit,
    positive: bool = False,
    nonnegative: bool = False,
) -> None:
    if not isinstance(value, Length) or value.unit is not unit:
        raise CamUnitError(f"{name} must use the Rest Finishing unit")
    if (
        not math.isfinite(value.value)
        or (positive and value.value <= 0.0)
        or (nonnegative and value.value < 0.0)
    ):
        raise RestFinishingValidationError(
            RestFinishingDiagnosticCode.INVALID_PARAMETERS,
            f"{name} is invalid",
        )


@dataclass(frozen=True, slots=True)
class RestFinishingParameters:
    """Exact v1 manual Rest Finishing parameter payload.

    ``profile_source`` is deliberately persisted here while the actual selected
    profile remains a separate :class:`RestFinishingProfileSelection`, matching
    the repository's authoritative geometry-reference lifecycle.  AUTO fields
    have no representation in v1 and are rejected during decode.
    """

    unit: LengthUnit
    profile_source: ContourProfileSource
    nominal_target_z: Length
    final_stock_allowance: Length
    tolerance: Length
    stepover: Length
    max_stepdown: Length
    clearance_height: Length
    retract_height: Length
    cutting_feed_rate: FeedRate
    plunge_feed_rate: FeedRate
    spindle_speed: SpindleSpeed
    strategy_version: int = REST_FINISHING_STRATEGY_VERSION
    schema_version: int = REST_FINISHING_PARAMETER_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.unit, LengthUnit) or self.unit is LengthUnit.UNKNOWN:
            raise CamUnitError("Rest Finishing requires a known length unit")
        if not isinstance(self.profile_source, ContourProfileSource):
            raise RestFinishingValidationError(
                RestFinishingDiagnosticCode.PROFILE_INVALID,
                "Rest Finishing profile source is invalid",
            )
        if type(self.strategy_version) is not int or self.strategy_version != REST_FINISHING_STRATEGY_VERSION:
            raise UnsupportedCamSchemaError("Unsupported Rest Finishing strategy version")
        if type(self.schema_version) is not int or self.schema_version != REST_FINISHING_PARAMETER_SCHEMA_VERSION:
            raise UnsupportedCamSchemaError("Unsupported Rest Finishing parameter schema version")
        for value, name, positive, nonnegative in (
            (self.nominal_target_z, "Nominal target Z", False, False),
            (self.final_stock_allowance, "Final stock allowance", False, True),
            (self.tolerance, "Tolerance", True, False),
            (self.stepover, "Stepover", True, False),
            (self.max_stepdown, "Maximum stepdown", True, False),
            (self.clearance_height, "Clearance height", False, False),
            (self.retract_height, "Retract height", False, False),
        ):
            _length(value, name=name, unit=self.unit, positive=positive, nonnegative=nonnegative)
        cut_z = self.cut_z
        if not math.isfinite(cut_z):
            raise RestFinishingValidationError(
                RestFinishingDiagnosticCode.INVALID_PARAMETERS,
                "Rest Finishing cut Z is non-finite",
            )
        if self.retract_height.value <= cut_z or self.clearance_height.value < self.retract_height.value:
            raise RestFinishingValidationError(
                RestFinishingDiagnosticCode.INVALID_PARAMETERS,
                "Rest Finishing clearance/retract heights are invalid",
            )
        feed_unit = FeedUnit.MM_PER_MINUTE if self.unit is LengthUnit.MM else FeedUnit.INCH_PER_MINUTE
        if (
            not isinstance(self.cutting_feed_rate, FeedRate)
            or not isinstance(self.plunge_feed_rate, FeedRate)
            or self.cutting_feed_rate.unit is not feed_unit
            or self.plunge_feed_rate.unit is not feed_unit
            or not math.isfinite(self.cutting_feed_rate.value)
            or not math.isfinite(self.plunge_feed_rate.value)
            or self.cutting_feed_rate.value <= 0.0
            or self.plunge_feed_rate.value <= 0.0
            or not isinstance(self.spindle_speed, SpindleSpeed)
            or not math.isfinite(self.spindle_speed.value)
            or self.spindle_speed.value <= 0.0
        ):
            raise RestFinishingValidationError(
                RestFinishingDiagnosticCode.INVALID_PARAMETERS,
                "Rest Finishing feeds or spindle speed are invalid",
            )

    @property
    def cut_z(self) -> float:
        """Return the exact R273 `NOMINAL_TARGET_Z + FINAL_STOCK_ALLOWANCE` law."""
        return self.nominal_target_z.value + self.final_stock_allowance.value

    @property
    def fingerprint(self) -> ContentFingerprint:
        return ContentFingerprint.from_payload(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        """Serialize exactly the v1 manual parameter schema."""
        return {
            "format": REST_FINISHING_PARAMETER_FORMAT,
            "format_version": 1,
            "strategy_version": self.strategy_version,
            "schema_version": self.schema_version,
            "unit": self.unit.value,
            "profile_source": self.profile_source.value,
            "nominal_target_z": self.nominal_target_z.value,
            "final_stock_allowance": self.final_stock_allowance.value,
            "tolerance": self.tolerance.value,
            "stepover": self.stepover.value,
            "max_stepdown": self.max_stepdown.value,
            "clearance_height": self.clearance_height.value,
            "retract_height": self.retract_height.value,
            "cutting_feed_rate": self.cutting_feed_rate.value,
            "plunge_feed_rate": self.plunge_feed_rate.value,
            "spindle_speed": self.spindle_speed.value,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RestFinishingParameters":
        fields = {"format", "format_version", "strategy_version", "schema_version", *_FIELDS}
        if not isinstance(data, dict):
            raise RestFinishingValidationError(RestFinishingDiagnosticCode.INVALID_PARAMETERS, "Rest Finishing payload is invalid")
        if any(name in data for name in _AUTOMATIC_FIELD_NAMES):
            raise RestFinishingValidationError(
                RestFinishingDiagnosticCode.AUTOMATIC_FORBIDDEN,
                "Automatic Rest Finishing parameters are forbidden in R273",
            )
        if set(data) != fields or data.get("format") != REST_FINISHING_PARAMETER_FORMAT:
            raise RestFinishingValidationError(
                RestFinishingDiagnosticCode.INVALID_PARAMETERS,
                "Rest Finishing payload fields are invalid",
            )
        if type(data.get("format_version")) is not int or data["format_version"] != 1:
            raise UnsupportedCamSchemaError("Unsupported Rest Finishing parameter format")
        try:
            unit = LengthUnit(data["unit"])
            feed_unit = FeedUnit.MM_PER_MINUTE if unit is LengthUnit.MM else FeedUnit.INCH_PER_MINUTE
            return cls(
                unit, ContourProfileSource(data["profile_source"]),
                Length(data["nominal_target_z"], unit), Length(data["final_stock_allowance"], unit),
                Length(data["tolerance"], unit), Length(data["stepover"], unit),
                Length(data["max_stepdown"], unit), Length(data["clearance_height"], unit),
                Length(data["retract_height"], unit), FeedRate(data["cutting_feed_rate"], feed_unit),
                FeedRate(data["plunge_feed_rate"], feed_unit), SpindleSpeed(data["spindle_speed"]),
                data["strategy_version"], data["schema_version"],
            )
        except (KeyError, TypeError, ValueError, CamValidationError) as error:
            if isinstance(error, (RestFinishingValidationError, UnsupportedCamSchemaError)):
                raise
            raise RestFinishingValidationError(
                RestFinishingDiagnosticCode.INVALID_PARAMETERS,
                "Rest Finishing payload is invalid",
            ) from error

    def to_operation_parameters(self) -> OperationParameterSet:
        """Encode this typed v1 value through the existing generic operation codec."""
        payload = self.to_dict()
        excluded = {"format", "format_version", "strategy_version", "schema_version"}
        return OperationParameterSet(
            REST_FINISHING_STRATEGY_KEY,
            REST_FINISHING_STRATEGY_VERSION,
            tuple((key, value) for key, value in payload.items() if key not in excluded),
            self.schema_version,
        )

    @classmethod
    def from_operation_parameters(cls, value: OperationParameterSet) -> "RestFinishingParameters":
        """Recover strict typed parameters; unknown/AUTO values fail closed."""
        if not isinstance(value, OperationParameterSet):
            raise RestFinishingValidationError(RestFinishingDiagnosticCode.INVALID_PARAMETERS, "Operation parameters are invalid")
        if value.strategy_key != REST_FINISHING_STRATEGY_KEY:
            raise RestFinishingValidationError(RestFinishingDiagnosticCode.INVALID_PARAMETERS, "Operation is not Rest Finishing")
        payload = dict(value.values)
        if any(name in payload for name in _AUTOMATIC_FIELD_NAMES):
            raise RestFinishingValidationError(
                RestFinishingDiagnosticCode.AUTOMATIC_FORBIDDEN,
                "Automatic Rest Finishing parameters are forbidden in R273",
            )
        payload.update(
            format=REST_FINISHING_PARAMETER_FORMAT,
            format_version=1,
            strategy_version=value.strategy_version,
            schema_version=value.schema_version,
        )
        return cls.from_dict(payload)


@dataclass(frozen=True, slots=True)
class RestFinishingProfileSelection:
    """Explicit selected, authoritative closed planar profile for R273.

    The descriptor's existing provenance remains the source-of-truth.  The
    application/core layer must compare ``profile_source`` against parameters
    before any machining activity; R273 domain schema deliberately does not
    accept an arbitrary profile identifier or an unproven geometry substitute.
    """

    descriptor: ContourProfileDescriptor

    def __post_init__(self) -> None:
        if not isinstance(self.descriptor, ContourProfileDescriptor):
            raise RestFinishingValidationError(
                RestFinishingDiagnosticCode.PROFILE_INVALID,
                "Selected Rest Finishing profile descriptor is invalid",
            )
        if self.descriptor.inner_loops:
            raise RestFinishingValidationError(
                RestFinishingDiagnosticCode.UNSUPPORTED,
                "Rest Finishing v1 does not support profile holes",
            )

    @property
    def profile_source(self) -> ContourProfileSource:
        return self.descriptor.provenance.source_kind

    @property
    def fingerprint(self) -> ContentFingerprint:
        return ContentFingerprint.from_payload({
            "profile": self.descriptor.geometry_fingerprint.to_dict(),
            "reference": self.descriptor.reference.to_dict(),
            "profile_source": self.profile_source.value,
        })

    def validate_for(self, parameters: RestFinishingParameters) -> None:
        """Fail closed unless the persisted parameter/profile authorities agree."""
        if not isinstance(parameters, RestFinishingParameters):
            raise RestFinishingValidationError(
                RestFinishingDiagnosticCode.INVALID_PARAMETERS,
                "Rest Finishing parameters are invalid",
            )
        if self.profile_source is not parameters.profile_source or self.descriptor.unit is not parameters.unit:
            raise RestFinishingValidationError(
                RestFinishingDiagnosticCode.PROFILE_INVALID,
                "Rest Finishing selected profile does not match parameters",
            )
