"""Validated, toolpath-free Rest Contour 3-axis foundation.

This module owns the persisted parameter vocabulary and semantic dependency
intent only.  It deliberately does not construct motions or material states.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from hms_cadcam.cam.domain.contour import (
    ContourCutDirection,
    ContourProfileDescriptor,
    ContourProfileSource,
    ContourSide,
)
from hms_cadcam.cam.domain.errors import CamInvariantError, CamUnitError, CamValidationError, UnsupportedCamSchemaError
from hms_cadcam.cam.domain.operation import OperationParameterSet
from hms_cadcam.cam.domain.revision import ContentFingerprint
from hms_cadcam.cam.domain.units import FeedRate, FeedUnit, Length, LengthUnit, SpindleSpeed

REST_CONTOUR_STRATEGY_KEY = "rest_contour_3axis"
REST_CONTOUR_STRATEGY_VERSION = 1
REST_CONTOUR_PARAMETER_SCHEMA_VERSION = 1
REST_CONTOUR_PARAMETER_FORMAT = "HMS_CAM_REST_CONTOUR_3AXIS_PARAMETERS"


class RestContourDiagnosticCode(StrEnum):
    INVALID_PARAMETERS = "rest_contour.invalid_parameters"
    PROFILE_INVALID = "rest_contour.profile_invalid"
    MATERIAL_STATE_MISSING = "rest_contour.material_state_missing"
    MATERIAL_STATE_STALE = "rest_contour.material_state_stale"
    MATERIAL_STATE_AMBIGUOUS = "rest_contour.material_state_ambiguous"
    MATERIAL_STATE_INVALID = "rest_contour.material_state_invalid"
    TOOL_INELIGIBLE = "rest_contour.tool_ineligible"
    MACHINE_INCOMPATIBLE = "rest_contour.machine_incompatible"
    AUTOMATIC_UNRESOLVED = "rest_contour.automatic_unresolved"
    RESIDUAL_UNSUPPORTED = "rest_contour.residual_unsupported"
    RESIDUAL_INVALID = "rest_contour.residual_invalid"
    PATH_OUTSIDE_AUTHORITY = "rest_contour.path_outside_authority"
    ENTRY_UNSAFE = "rest_contour.entry_unsafe"
    TOOLPATH_LIMIT_EXCEEDED = "rest_contour.toolpath_limit_exceeded"
    SUCCESSOR_INVALID = "rest_contour.successor_invalid"
    PUBLICATION_FAILED = "rest_contour.publication_failed"
    CANCELLED = "rest_contour.cancelled"


class RestContourValidationError(CamValidationError):
    """Fail-closed Rest Contour validation failure with one stable code."""

    def __init__(self, code: RestContourDiagnosticCode, message: str) -> None:
        if not isinstance(code, RestContourDiagnosticCode):
            raise TypeError("Rest Contour diagnostic code is invalid")
        super().__init__(message)
        self.code = code


class RestContourLinkingPolicy(StrEnum):
    RETRACT_CLEARANCE = "retract_clearance"


_FIELDS = (
    "unit", "profile_source", "side", "top_height", "final_depth", "stepdown",
    "radial_stock_allowance", "axial_stock_allowance", "clearance_height",
    "retract_height", "cutting_feed_rate", "plunge_feed_rate", "spindle_speed",
    "direction", "tolerance", "lead_in_length", "lead_out_length", "linking_policy",
)
_OPTIONAL = "automatic_parameter_contract"
_AUTOMATIC_POLICY_KEY = "contour.operation_intelligence"
_AUTOMATIC_POLICY_VERSION = 1
_AUTOMATIC_KEYS = (
    "entry_segment_index", "lead_form", "lead_in_length", "lead_out_length", "stepdown",
)


def _length(
    value: Length,
    name: str,
    unit: LengthUnit,
    *,
    positive: bool = False,
    nonnegative: bool = False,
) -> None:
    if not isinstance(value, Length) or value.unit is not unit:
        raise CamUnitError(f"{name} must use the Rest Contour unit")
    if (
        not math.isfinite(value.value)
        or (positive and value.value <= 0.0)
        or (nonnegative and value.value < 0.0)
    ):
        raise RestContourValidationError(RestContourDiagnosticCode.INVALID_PARAMETERS, f"{name} is invalid")


@dataclass(frozen=True, slots=True)
class RestContourParameters:
    """Exact v1 Rest Contour parameter payload; no unknown persisted fields."""

    unit: LengthUnit
    profile_source: ContourProfileSource
    side: ContourSide
    top_height: Length
    final_depth: Length
    stepdown: Length
    radial_stock_allowance: Length
    axial_stock_allowance: Length
    clearance_height: Length
    retract_height: Length
    cutting_feed_rate: FeedRate
    plunge_feed_rate: FeedRate
    spindle_speed: SpindleSpeed
    direction: ContourCutDirection
    tolerance: Length
    lead_in_length: Length
    lead_out_length: Length
    linking_policy: RestContourLinkingPolicy = RestContourLinkingPolicy.RETRACT_CLEARANCE
    automatic_parameter_contract: str | None = None
    strategy_version: int = REST_CONTOUR_STRATEGY_VERSION
    schema_version: int = REST_CONTOUR_PARAMETER_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.unit, LengthUnit) or self.unit is LengthUnit.UNKNOWN:
            raise CamUnitError("Rest Contour requires a known length unit")
        if not all(isinstance(value, expected) for value, expected in (
            (self.profile_source, ContourProfileSource), (self.side, ContourSide),
            (self.direction, ContourCutDirection), (self.linking_policy, RestContourLinkingPolicy),
        )):
            raise RestContourValidationError(RestContourDiagnosticCode.INVALID_PARAMETERS, "Rest Contour enum is invalid")
        if self.strategy_version != REST_CONTOUR_STRATEGY_VERSION or type(self.strategy_version) is not int:
            raise UnsupportedCamSchemaError("Unsupported Rest Contour strategy version")
        if self.schema_version != REST_CONTOUR_PARAMETER_SCHEMA_VERSION or type(self.schema_version) is not int:
            raise UnsupportedCamSchemaError("Unsupported Rest Contour parameter schema version")
        for value, name, positive, nonnegative in (
            (self.top_height, "Top height", False, False), (self.final_depth, "Final depth", False, False),
            (self.stepdown, "Stepdown", True, False), (self.radial_stock_allowance, "Radial allowance", False, True),
            (self.axial_stock_allowance, "Axial allowance", False, True), (self.clearance_height, "Clearance", False, False),
            (self.retract_height, "Retract", False, False), (self.tolerance, "Tolerance", True, False),
            (self.lead_in_length, "Lead-in", False, True), (self.lead_out_length, "Lead-out", False, True),
        ):
            _length(value, name, self.unit, positive=positive, nonnegative=nonnegative)
        if self.final_depth.value + self.axial_stock_allowance.value >= self.top_height.value:
            raise RestContourValidationError(RestContourDiagnosticCode.INVALID_PARAMETERS, "Final depth must be below top height")
        if self.retract_height.value <= self.top_height.value or self.clearance_height.value < self.retract_height.value:
            raise RestContourValidationError(RestContourDiagnosticCode.INVALID_PARAMETERS, "Clearance/retract heights are invalid")
        feed_unit = FeedUnit.MM_PER_MINUTE if self.unit is LengthUnit.MM else FeedUnit.INCH_PER_MINUTE
        if (not isinstance(self.cutting_feed_rate, FeedRate) or not isinstance(self.plunge_feed_rate, FeedRate)
                or self.cutting_feed_rate.unit is not feed_unit or self.plunge_feed_rate.unit is not feed_unit
                or not math.isfinite(self.cutting_feed_rate.value) or not math.isfinite(self.plunge_feed_rate.value)
                or self.cutting_feed_rate.value <= 0.0 or self.plunge_feed_rate.value <= 0.0
                or not isinstance(self.spindle_speed, SpindleSpeed) or not math.isfinite(self.spindle_speed.value)
                or self.spindle_speed.value <= 0.0):
            raise RestContourValidationError(RestContourDiagnosticCode.INVALID_PARAMETERS, "Feeds or spindle speed are invalid")
        if self.automatic_parameter_contract is not None:
            if not isinstance(self.automatic_parameter_contract, str) or not self.automatic_parameter_contract.strip():
                raise RestContourValidationError(RestContourDiagnosticCode.INVALID_PARAMETERS, "Automatic contract is invalid")
            try:
                # Deferred import avoids a domain-package initialization cycle.
                from hms_cadcam.cam.automatic_parameters import AutomaticParameterContract
                contract = AutomaticParameterContract.from_json(self.automatic_parameter_contract)
            except (TypeError, ValueError) as error:
                raise RestContourValidationError(
                    RestContourDiagnosticCode.AUTOMATIC_UNRESOLVED,
                    "Automatic contract is malformed",
                ) from error
            if (
                contract.policy_key != _AUTOMATIC_POLICY_KEY
                or contract.policy_version != _AUTOMATIC_POLICY_VERSION
                or tuple(value.key for value in contract.values) != _AUTOMATIC_KEYS
                or self.automatic_parameter_contract != contract.to_json()
            ):
                raise RestContourValidationError(
                    RestContourDiagnosticCode.AUTOMATIC_UNRESOLVED,
                    "Automatic contract policy or key set is stale",
                )

    @property
    def fingerprint(self) -> ContentFingerprint:
        return ContentFingerprint.from_payload(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "format": REST_CONTOUR_PARAMETER_FORMAT, "format_version": 1,
            "strategy_version": self.strategy_version, "schema_version": self.schema_version,
            "unit": self.unit.value, "profile_source": self.profile_source.value, "side": self.side.value,
            "top_height": self.top_height.value, "final_depth": self.final_depth.value,
            "stepdown": self.stepdown.value, "radial_stock_allowance": self.radial_stock_allowance.value,
            "axial_stock_allowance": self.axial_stock_allowance.value, "clearance_height": self.clearance_height.value,
            "retract_height": self.retract_height.value, "cutting_feed_rate": self.cutting_feed_rate.value,
            "plunge_feed_rate": self.plunge_feed_rate.value, "spindle_speed": self.spindle_speed.value,
            "direction": self.direction.value, "tolerance": self.tolerance.value,
            "lead_in_length": self.lead_in_length.value, "lead_out_length": self.lead_out_length.value,
            "linking_policy": self.linking_policy.value,
        }
        if self.automatic_parameter_contract is not None:
            data[_OPTIONAL] = self.automatic_parameter_contract
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RestContourParameters":
        fields = {"format", "format_version", "strategy_version", "schema_version", *_FIELDS}
        if not isinstance(data, dict) or set(data) not in (fields, fields | {_OPTIONAL}) or data.get("format") != REST_CONTOUR_PARAMETER_FORMAT:
            raise RestContourValidationError(RestContourDiagnosticCode.INVALID_PARAMETERS, "Rest Contour payload fields are invalid")
        if data.get("format_version") != 1:
            raise UnsupportedCamSchemaError("Unsupported Rest Contour parameter format")
        try:
            unit = LengthUnit(data["unit"])
            feed_unit = FeedUnit.MM_PER_MINUTE if unit is LengthUnit.MM else FeedUnit.INCH_PER_MINUTE
            return cls(unit, ContourProfileSource(data["profile_source"]), ContourSide(data["side"]),
                Length(data["top_height"], unit), Length(data["final_depth"], unit), Length(data["stepdown"], unit),
                Length(data["radial_stock_allowance"], unit), Length(data["axial_stock_allowance"], unit),
                Length(data["clearance_height"], unit), Length(data["retract_height"], unit),
                FeedRate(data["cutting_feed_rate"], feed_unit), FeedRate(data["plunge_feed_rate"], feed_unit),
                SpindleSpeed(data["spindle_speed"]), ContourCutDirection(data["direction"]), Length(data["tolerance"], unit),
                Length(data["lead_in_length"], unit), Length(data["lead_out_length"], unit),
                RestContourLinkingPolicy(data["linking_policy"]), data.get(_OPTIONAL),
                data["strategy_version"], data["schema_version"])
        except (KeyError, TypeError, ValueError, CamValidationError) as error:
            if isinstance(error, (RestContourValidationError, UnsupportedCamSchemaError)):
                raise
            raise RestContourValidationError(RestContourDiagnosticCode.INVALID_PARAMETERS, "Rest Contour payload is invalid") from error

    def to_operation_parameters(self) -> OperationParameterSet:
        payload = self.to_dict()
        excluded = {"format", "format_version", "strategy_version", "schema_version"}
        return OperationParameterSet(REST_CONTOUR_STRATEGY_KEY, REST_CONTOUR_STRATEGY_VERSION,
            tuple((key, value) for key, value in payload.items() if key not in excluded), self.schema_version)

    @classmethod
    def from_operation_parameters(cls, value: OperationParameterSet) -> "RestContourParameters":
        if value.strategy_key != REST_CONTOUR_STRATEGY_KEY:
            raise RestContourValidationError(RestContourDiagnosticCode.INVALID_PARAMETERS, "Operation is not Rest Contour")
        payload = dict(value.values)
        payload.update(format=REST_CONTOUR_PARAMETER_FORMAT, format_version=1,
                       strategy_version=value.strategy_version, schema_version=value.schema_version)
        return cls.from_dict(payload)


@dataclass(frozen=True, slots=True)
class RestContourProfileSelection:
    """Explicit persistent profile authority, deliberately separate from residue."""

    descriptor: ContourProfileDescriptor

    def __post_init__(self) -> None:
        if not isinstance(self.descriptor, ContourProfileDescriptor):
            raise RestContourValidationError(RestContourDiagnosticCode.PROFILE_INVALID, "Selected profile descriptor is invalid")

    @property
    def fingerprint(self) -> ContentFingerprint:
        return ContentFingerprint.from_payload({"profile": self.descriptor.geometry_fingerprint.to_dict(), "reference": self.descriptor.reference.to_dict()})
