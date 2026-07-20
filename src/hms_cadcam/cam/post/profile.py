"""Versioned production-controller profiles and per-program production context."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, ClassVar

from hms_cadcam.cam.domain.errors import CamInvariantError, CamValidationError, UnsupportedCamSchemaError
from hms_cadcam.cam.domain.ids import ProductionControllerProfileId
from hms_cadcam.cam.domain.machine import MachineKind, SpindleDirection
from hms_cadcam.cam.domain.revision import ContentFingerprint, DependencyFingerprint
from hms_cadcam.cam.domain.units import FeedRate, FeedUnit, Length, LengthUnit
from hms_cadcam.cam.post.model import CoordinateMode, Plane
from hms_cadcam.cam.toolpath.events import CoolantState, FeedMode


PRODUCTION_PROFILE_FORMAT = "HMS_CAM_PRODUCTION_CONTROLLER_PROFILE"
PRODUCTION_PROFILE_VERSION = 1
TOOL_BINDING_FORMAT = "HMS_CAM_CONTROLLER_TOOL_BINDING"
TOOL_BINDING_VERSION = 1
PROGRAM_CONTEXT_FORMAT = "HMS_CAM_PRODUCTION_PROGRAM_CONTEXT"
PROGRAM_CONTEXT_VERSION = 1

_KEY = re.compile(r"[a-z][a-z0-9_.-]{1,127}")
_CODE = re.compile(r"[GMT]\d{1,3}")
_EXTENSION = re.compile(r"\.[A-Za-z0-9]{1,15}")
_FILE_NAME = re.compile(r"[A-Za-z0-9_. -]{1,128}")


class ArcOutputMode(StrEnum):
    IJK_INCREMENTAL_FROM_START = "ijk_incremental_from_start"


class CutterCompensationPolicy(StrEnum):
    LEGACY_WORKNC_LEFT = "legacy_worknc_left"
    DISABLED = "disabled"
    FROM_PROGRAM_IR_ONLY = "from_program_ir_only"


class DwellPolicy(StrEnum):
    UNSUPPORTED = "unsupported"
    SECONDS = "seconds"
    MILLISECONDS = "milliseconds"


class ProgramNumberPolicy(StrEnum):
    DISABLED = "disabled"
    OPTIONAL = "optional"


class BlockNumberPolicy(StrEnum):
    DISABLED = "disabled"


class ToolActivationPolicy(StrEnum):
    WORKNC_M06_TH = "worknc_m06_th"


class SafeSequenceToken(StrEnum):
    PROGRAM_DELIMITER = "program_delimiter"
    COMMENTS = "comments"
    MODAL_CANCEL = "modal_cancel"
    MACHINE_Z_REFERENCE = "machine_z_reference"
    TOOL_CHANGE = "tool_change"
    WORK_OFFSET_ORIGIN = "work_offset_origin"
    LENGTH_COMPENSATION = "length_compensation"
    CUTTER_COMPENSATION = "cutter_compensation"
    PROCESS_STATE = "process_state"
    PROGRAM_MOTIONS = "program_motions"
    CUTTER_CANCEL = "cutter_cancel"
    COOLANT_OFF = "coolant_off"
    SPINDLE_STOP = "spindle_stop"
    MACHINE_Y_REFERENCE = "machine_y_reference"
    PROGRAM_END = "program_end"


@dataclass(frozen=True, slots=True)
class ArcPolicy:
    output_mode: ArcOutputMode
    plane: Plane
    allow_large_sweep: bool
    allow_full_circle: bool
    allow_helical: bool
    allow_multi_turn: bool
    radius_tolerance: float

    def __post_init__(self) -> None:
        if not isinstance(self.output_mode, ArcOutputMode) or not isinstance(self.plane, Plane):
            raise CamValidationError("Production arc policy is invalid")
        flags = (self.allow_large_sweep, self.allow_full_circle, self.allow_helical, self.allow_multi_turn)
        if any(type(value) is not bool for value in flags):
            raise CamValidationError("Production arc policy flags are invalid")
        if isinstance(self.radius_tolerance, bool) or not isinstance(self.radius_tolerance, (int, float)):
            raise CamValidationError("Production arc tolerance is invalid")
        value = float(self.radius_tolerance)
        if not math.isfinite(value) or value <= 0.0:
            raise CamValidationError("Production arc tolerance must be positive")
        object.__setattr__(self, "radius_tolerance", value)

    def to_dict(self) -> dict[str, Any]:
        return {
            "output_mode": self.output_mode.value,
            "plane": self.plane.value,
            "allow_large_sweep": self.allow_large_sweep,
            "allow_full_circle": self.allow_full_circle,
            "allow_helical": self.allow_helical,
            "allow_multi_turn": self.allow_multi_turn,
            "radius_tolerance": self.radius_tolerance,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ArcPolicy":
        _fields(data, {"output_mode", "plane", "allow_large_sweep", "allow_full_circle", "allow_helical", "allow_multi_turn", "radius_tolerance"}, "Arc policy")
        try:
            return cls(ArcOutputMode(data["output_mode"]), Plane(data["plane"]), data["allow_large_sweep"], data["allow_full_circle"], data["allow_helical"], data["allow_multi_turn"], data["radius_tolerance"])
        except (TypeError, ValueError) as error:
            raise CamValidationError("Arc policy enum is invalid") from error


@dataclass(frozen=True, slots=True)
class NumericFormatPolicy:
    xyz_precision: int
    ijk_precision: int
    feed_precision: int
    spindle_precision: int
    dwell_precision: int
    trim_trailing_zeros: bool = True
    force_decimal_point_addresses: tuple[str, ...] = ("X", "Y", "Z", "I", "J")

    def __post_init__(self) -> None:
        values = (self.xyz_precision, self.ijk_precision, self.feed_precision, self.spindle_precision, self.dwell_precision)
        if any(type(value) is not int or not 0 <= value <= 9 for value in values):
            raise CamValidationError("Production numeric precision is invalid")
        if type(self.trim_trailing_zeros) is not bool:
            raise CamValidationError("Trailing-zero policy is invalid")
        addresses = _unique_text(self.force_decimal_point_addresses, "decimal-point addresses", re.compile(r"[A-Z]"))
        object.__setattr__(self, "force_decimal_point_addresses", addresses)

    def to_dict(self) -> dict[str, Any]:
        return {
            "xyz_precision": self.xyz_precision,
            "ijk_precision": self.ijk_precision,
            "feed_precision": self.feed_precision,
            "spindle_precision": self.spindle_precision,
            "dwell_precision": self.dwell_precision,
            "trim_trailing_zeros": self.trim_trailing_zeros,
            "force_decimal_point_addresses": list(self.force_decimal_point_addresses),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NumericFormatPolicy":
        _fields(data, {"xyz_precision", "ijk_precision", "feed_precision", "spindle_precision", "dwell_precision", "trim_trailing_zeros", "force_decimal_point_addresses"}, "Numeric policy")
        if not isinstance(data["force_decimal_point_addresses"], list):
            raise CamValidationError("Numeric address payload is invalid")
        return cls(data["xyz_precision"], data["ijk_precision"], data["feed_precision"], data["spindle_precision"], data["dwell_precision"], data["trim_trailing_zeros"], tuple(data["force_decimal_point_addresses"]))


@dataclass(frozen=True, slots=True)
class WorkOffsetMapping:
    logical_name: str
    numeric_slot: int | None
    controller_code: str

    def __post_init__(self) -> None:
        if not isinstance(self.logical_name, str) or _KEY.fullmatch(self.logical_name.casefold()) is None:
            raise CamValidationError("Logical work offset is invalid")
        object.__setattr__(self, "logical_name", self.logical_name.upper())
        if self.numeric_slot is not None and (type(self.numeric_slot) is not int or self.numeric_slot < 0):
            raise CamValidationError("Work-offset slot is invalid")
        if not isinstance(self.controller_code, str) or re.fullmatch(r"G\d{2}", self.controller_code) is None:
            raise CamValidationError("Controller work-offset code is invalid")

    def to_dict(self) -> dict[str, Any]:
        return {"logical_name": self.logical_name, "numeric_slot": self.numeric_slot, "controller_code": self.controller_code}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorkOffsetMapping":
        _fields(data, {"logical_name", "numeric_slot", "controller_code"}, "Work-offset mapping")
        return cls(data["logical_name"], data["numeric_slot"], data["controller_code"])


@dataclass(frozen=True, slots=True)
class SpindleCodeMapping:
    clockwise: str
    counterclockwise: str
    stop: str

    def __post_init__(self) -> None:
        if any(not isinstance(value, str) or _CODE.fullmatch(value) is None for value in (self.clockwise, self.counterclockwise, self.stop)):
            raise CamValidationError("Spindle code mapping is invalid")

    def to_dict(self) -> dict[str, str]:
        return {"clockwise": self.clockwise, "counterclockwise": self.counterclockwise, "stop": self.stop}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SpindleCodeMapping":
        _fields(data, {"clockwise", "counterclockwise", "stop"}, "Spindle mapping")
        return cls(data["clockwise"], data["counterclockwise"], data["stop"])


@dataclass(frozen=True, slots=True)
class CoolantCodeMapping:
    flood: str
    off: str

    def __post_init__(self) -> None:
        if any(not isinstance(value, str) or _CODE.fullmatch(value) is None for value in (self.flood, self.off)):
            raise CamValidationError("Coolant code mapping is invalid")

    def to_dict(self) -> dict[str, str]:
        return {"flood": self.flood, "off": self.off}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CoolantCodeMapping":
        _fields(data, {"flood", "off"}, "Coolant mapping")
        return cls(data["flood"], data["off"])


@dataclass(frozen=True, slots=True)
class ProductionControllerProfile:
    profile_id: ProductionControllerProfileId
    profile_key: str
    profile_version: int
    adapter_key: str
    adapter_version: int
    controller_family: str
    controller_model: str
    machine_family: str
    machine_type: MachineKind
    axes: tuple[str, ...]
    supported_units: tuple[LengthUnit, ...]
    supported_planes: tuple[Plane, ...]
    coordinate_mode: CoordinateMode
    supported_feed_modes: tuple[FeedMode, ...]
    supported_spindle_directions: tuple[SpindleDirection, ...]
    minimum_rpm: float | None
    maximum_rpm: float | None
    feed_limits: tuple[FeedRate, ...]
    arc_policy: ArcPolicy
    work_offset_mapping: tuple[WorkOffsetMapping, ...]
    tool_activation_policy: ToolActivationPolicy
    cutter_compensation_policy: CutterCompensationPolicy
    spindle_mapping: SpindleCodeMapping
    coolant_mapping: CoolantCodeMapping
    dwell_policy: DwellPolicy
    program_number_policy: ProgramNumberPolicy
    block_number_policy: BlockNumberPolicy
    numeric_format: NumericFormatPolicy
    comment_prefix: str
    comment_suffix: str
    maximum_comment_length: int
    newline: str
    encoding: str
    maximum_line_length: int
    maximum_program_size: int
    allowed_extensions: tuple[str, ...]
    supported_operation_strategies: tuple[str, ...]
    safe_start_records: tuple[SafeSequenceToken, ...]
    safe_end_records: tuple[SafeSequenceToken, ...]
    display_name: str | None = None
    schema_version: int = PRODUCTION_PROFILE_VERSION
    SERIALIZATION_VERSION: ClassVar[int] = PRODUCTION_PROFILE_VERSION

    def __post_init__(self) -> None:
        if (
            type(self.schema_version) is not int
            or self.schema_version != PRODUCTION_PROFILE_VERSION
            or type(self.profile_version) is not int
            or self.profile_version <= 0
            or type(self.adapter_version) is not int
            or self.adapter_version <= 0
        ):
            raise UnsupportedCamSchemaError("Unsupported production profile version")
        if not isinstance(self.profile_id, ProductionControllerProfileId):
            raise CamValidationError("Production profile ID is invalid")
        for value, name in ((self.profile_key, "profile key"), (self.adapter_key, "adapter key")):
            if not isinstance(value, str) or _KEY.fullmatch(value) is None:
                raise CamValidationError(f"Production {name} is invalid")
        for value, name in ((self.controller_family, "controller family"), (self.controller_model, "controller model"), (self.machine_family, "machine family")):
            if not isinstance(value, str) or not value.strip() or len(value) > 128:
                raise CamValidationError(f"Production {name} is invalid")
        if not isinstance(self.machine_type, MachineKind) or not isinstance(self.coordinate_mode, CoordinateMode):
            raise CamValidationError("Production machine or coordinate mode is invalid")
        object.__setattr__(self, "axes", _unique_text(self.axes, "profile axes", re.compile(r"[A-Z]")))
        for values, typ, name in ((self.supported_units, LengthUnit, "units"), (self.supported_planes, Plane, "planes"), (self.supported_feed_modes, FeedMode, "feed modes"), (self.supported_spindle_directions, SpindleDirection, "spindle directions")):
            if not isinstance(values, tuple) or not values or any(not isinstance(item, typ) for item in values) or len(set(values)) != len(values):
                raise CamValidationError(f"Production supported {name} are invalid")
        if LengthUnit.UNKNOWN in self.supported_units or FeedMode.INVERSE_TIME in self.supported_feed_modes:
            raise CamValidationError("Production profile cannot support unknown/inverse-time units")
        for value, name in ((self.minimum_rpm, "minimum RPM"), (self.maximum_rpm, "maximum RPM")):
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or float(value) <= 0.0):
                raise CamValidationError(f"Production {name} is invalid")
            if value is not None:
                object.__setattr__(self, "minimum_rpm" if name == "minimum RPM" else "maximum_rpm", float(value))
        if self.minimum_rpm is not None and self.maximum_rpm is not None and self.minimum_rpm > self.maximum_rpm:
            raise CamInvariantError("Production RPM limits are inverted")
        if not isinstance(self.feed_limits, tuple) or any(not isinstance(item, FeedRate) for item in self.feed_limits):
            raise CamValidationError("Production feed limits are invalid")
        if len({item.unit for item in self.feed_limits}) != len(self.feed_limits):
            raise CamInvariantError("Production feed-limit units must be unique")
        for value, typ, name in ((self.arc_policy, ArcPolicy, "arc"), (self.tool_activation_policy, ToolActivationPolicy, "tool activation"), (self.cutter_compensation_policy, CutterCompensationPolicy, "cutter compensation"), (self.spindle_mapping, SpindleCodeMapping, "spindle mapping"), (self.coolant_mapping, CoolantCodeMapping, "coolant mapping"), (self.dwell_policy, DwellPolicy, "dwell"), (self.program_number_policy, ProgramNumberPolicy, "program number"), (self.block_number_policy, BlockNumberPolicy, "block number"), (self.numeric_format, NumericFormatPolicy, "numeric format")):
            if not isinstance(value, typ):
                raise CamValidationError(f"Production {name} policy is invalid")
        if not isinstance(self.work_offset_mapping, tuple) or not self.work_offset_mapping or any(not isinstance(item, WorkOffsetMapping) for item in self.work_offset_mapping):
            raise CamValidationError("Production work-offset mapping is invalid")
        offset_keys = {(item.logical_name, item.numeric_slot) for item in self.work_offset_mapping}
        if len(offset_keys) != len(self.work_offset_mapping):
            raise CamInvariantError("Production work-offset mappings must be unique")
        if self.comment_prefix != "(" or self.comment_suffix != ")":
            raise CamValidationError("Production v1 requires parenthesized comments")
        if type(self.maximum_comment_length) is not int or self.maximum_comment_length <= 0:
            raise CamValidationError("Production comment limit is invalid")
        if self.newline not in {"\n", "\r\n"} or not isinstance(self.encoding, str) or self.encoding.casefold() != "utf-8":
            raise CamValidationError("Production text policy is invalid")
        if type(self.maximum_line_length) is not int or self.maximum_line_length <= 0 or type(self.maximum_program_size) is not int or self.maximum_program_size <= 0:
            raise CamValidationError("Production size policy is invalid")
        object.__setattr__(self, "allowed_extensions", _unique_text(self.allowed_extensions, "extensions", _EXTENSION))
        object.__setattr__(self, "supported_operation_strategies", _unique_text(self.supported_operation_strategies, "strategies", _KEY))
        for values, name in ((self.safe_start_records, "safe start"), (self.safe_end_records, "safe end")):
            if not isinstance(values, tuple) or not values or any(not isinstance(item, SafeSequenceToken) for item in values) or len(set(values)) != len(values):
                raise CamValidationError(f"Production {name} records are invalid")
        if self.display_name is not None and (not isinstance(self.display_name, str) or not self.display_name.strip() or len(self.display_name) > 255):
            raise CamValidationError("Production profile display name is invalid")

    @property
    def fingerprint(self) -> ContentFingerprint:
        data = self.to_dict().copy()
        data.pop("profile_id")
        data.pop("display_name")
        return ContentFingerprint.from_payload(data)

    def to_dict(self) -> dict[str, Any]:
        return profile_to_dict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProductionControllerProfile":
        return profile_from_dict(data)


@dataclass(frozen=True, slots=True)
class ControllerToolBinding:
    tool_assembly_fingerprint: ContentFingerprint
    tool_station: int
    length_offset: int
    diameter_offset: int | None
    tool_comment: str
    schema_version: int = TOOL_BINDING_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != TOOL_BINDING_VERSION:
            raise UnsupportedCamSchemaError("Unsupported controller tool-binding version")
        if not isinstance(self.tool_assembly_fingerprint, ContentFingerprint):
            raise CamValidationError("Tool-binding assembly fingerprint is invalid")
        for value, name in ((self.tool_station, "station"), (self.length_offset, "length offset")):
            if type(value) is not int or not 1 <= value <= 9999:
                raise CamValidationError(f"Tool-binding {name} is invalid")
        if self.diameter_offset is not None and (type(self.diameter_offset) is not int or not 1 <= self.diameter_offset <= 9999):
            raise CamValidationError("Tool-binding diameter offset is invalid")
        object.__setattr__(self, "tool_comment", sanitize_comment_fragment(self.tool_comment, maximum=128))

    @property
    def fingerprint(self) -> ContentFingerprint:
        return ContentFingerprint.from_payload(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": TOOL_BINDING_FORMAT,
            "format_version": self.schema_version,
            "tool_assembly_fingerprint": self.tool_assembly_fingerprint.to_dict(),
            "tool_station": self.tool_station,
            "length_offset": self.length_offset,
            "diameter_offset": self.diameter_offset,
            "tool_comment": self.tool_comment,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ControllerToolBinding":
        _strict(data, TOOL_BINDING_FORMAT, TOOL_BINDING_VERSION, {"tool_assembly_fingerprint", "tool_station", "length_offset", "diameter_offset", "tool_comment"})
        fingerprint = _fingerprint_from_dict(data["tool_assembly_fingerprint"])
        return cls(fingerprint, data["tool_station"], data["length_offset"], data["diameter_offset"], data["tool_comment"], data["format_version"])


@dataclass(frozen=True, slots=True)
class ProductionProgramContext:
    file_name: str
    safe_z: Length
    tool_binding: ControllerToolBinding
    tool_radius: Length
    stock_allowance: Length
    cut_depth: Length
    use_legacy_cutter_compensation: bool
    program_identity: str | None = None
    schema_version: int = PROGRAM_CONTEXT_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != PROGRAM_CONTEXT_VERSION:
            raise UnsupportedCamSchemaError("Unsupported production program-context version")
        if not isinstance(self.file_name, str) or _FILE_NAME.fullmatch(self.file_name) is None or "/" in self.file_name or "\\" in self.file_name:
            raise CamValidationError("Production file name is invalid")
        if not self.file_name.casefold().endswith(".fn"):
            raise CamValidationError("Production file name must use the .fn extension")
        if not isinstance(self.tool_binding, ControllerToolBinding):
            raise CamValidationError("Production tool binding is invalid")
        lengths = (self.safe_z, self.tool_radius, self.stock_allowance, self.cut_depth)
        if any(not isinstance(value, Length) for value in lengths) or len({value.unit for value in lengths}) != 1 or self.safe_z.unit is LengthUnit.UNKNOWN:
            raise CamValidationError("Production program lengths are invalid")
        if self.tool_radius.value < 0.0 or self.stock_allowance.value < 0.0:
            raise CamValidationError("Production tool radius/allowance cannot be negative")
        if type(self.use_legacy_cutter_compensation) is not bool:
            raise CamValidationError("Production cutter-compensation selection is invalid")
        if self.program_identity is not None:
            object.__setattr__(self, "program_identity", sanitize_comment_fragment(self.program_identity, maximum=128))

    @property
    def fingerprint(self) -> ContentFingerprint:
        return ContentFingerprint.from_payload(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": PROGRAM_CONTEXT_FORMAT,
            "format_version": self.schema_version,
            "file_name": self.file_name,
            "safe_z": _length_to_dict(self.safe_z),
            "tool_binding": self.tool_binding.to_dict(),
            "tool_radius": _length_to_dict(self.tool_radius),
            "stock_allowance": _length_to_dict(self.stock_allowance),
            "cut_depth": _length_to_dict(self.cut_depth),
            "use_legacy_cutter_compensation": self.use_legacy_cutter_compensation,
            "program_identity": self.program_identity,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProductionProgramContext":
        _strict(data, PROGRAM_CONTEXT_FORMAT, PROGRAM_CONTEXT_VERSION, {"file_name", "safe_z", "tool_binding", "tool_radius", "stock_allowance", "cut_depth", "use_legacy_cutter_compensation", "program_identity"})
        return cls(data["file_name"], _length_from_dict(data["safe_z"]), ControllerToolBinding.from_dict(data["tool_binding"]), _length_from_dict(data["tool_radius"]), _length_from_dict(data["stock_allowance"]), _length_from_dict(data["cut_depth"]), data["use_legacy_cutter_compensation"], data["program_identity"], data["format_version"])


def sanitize_comment_fragment(value: str, *, maximum: int) -> str:
    """Validate and normalize one fragment that will remain inside a comment."""
    if not isinstance(value, str):
        raise CamValidationError("Comment fragment must be text")
    normalized = " ".join(value.strip().split())
    if not normalized or len(normalized) > maximum:
        raise CamValidationError("Comment fragment length is invalid")
    if any(ord(char) < 32 or char in "()\r\n" for char in normalized):
        raise CamValidationError("Comment fragment contains unsafe characters")
    return normalized


def profile_to_dict(value: ProductionControllerProfile) -> dict[str, Any]:
    return {
        "format": PRODUCTION_PROFILE_FORMAT,
        "format_version": value.schema_version,
        "profile_id": str(value.profile_id),
        "profile_key": value.profile_key,
        "profile_version": value.profile_version,
        "adapter_key": value.adapter_key,
        "adapter_version": value.adapter_version,
        "controller_family": value.controller_family,
        "controller_model": value.controller_model,
        "machine_family": value.machine_family,
        "machine_type": value.machine_type.value,
        "axes": list(value.axes),
        "supported_units": [item.value for item in value.supported_units],
        "supported_planes": [item.value for item in value.supported_planes],
        "coordinate_mode": value.coordinate_mode.value,
        "supported_feed_modes": [item.value for item in value.supported_feed_modes],
        "supported_spindle_directions": [item.value for item in value.supported_spindle_directions],
        "minimum_rpm": value.minimum_rpm,
        "maximum_rpm": value.maximum_rpm,
        "feed_limits": [_feed_to_dict(item) for item in value.feed_limits],
        "arc_policy": value.arc_policy.to_dict(),
        "work_offset_mapping": [item.to_dict() for item in value.work_offset_mapping],
        "tool_activation_policy": value.tool_activation_policy.value,
        "cutter_compensation_policy": value.cutter_compensation_policy.value,
        "spindle_mapping": value.spindle_mapping.to_dict(),
        "coolant_mapping": value.coolant_mapping.to_dict(),
        "dwell_policy": value.dwell_policy.value,
        "program_number_policy": value.program_number_policy.value,
        "block_number_policy": value.block_number_policy.value,
        "numeric_format": value.numeric_format.to_dict(),
        "comment_prefix": value.comment_prefix,
        "comment_suffix": value.comment_suffix,
        "maximum_comment_length": value.maximum_comment_length,
        "newline": value.newline,
        "encoding": value.encoding,
        "maximum_line_length": value.maximum_line_length,
        "maximum_program_size": value.maximum_program_size,
        "allowed_extensions": list(value.allowed_extensions),
        "supported_operation_strategies": list(value.supported_operation_strategies),
        "safe_start_records": [item.value for item in value.safe_start_records],
        "safe_end_records": [item.value for item in value.safe_end_records],
        "display_name": value.display_name,
    }


def profile_from_dict(data: dict[str, Any]) -> ProductionControllerProfile:
    fields = {"profile_id", "profile_key", "profile_version", "adapter_key", "adapter_version", "controller_family", "controller_model", "machine_family", "machine_type", "axes", "supported_units", "supported_planes", "coordinate_mode", "supported_feed_modes", "supported_spindle_directions", "minimum_rpm", "maximum_rpm", "feed_limits", "arc_policy", "work_offset_mapping", "tool_activation_policy", "cutter_compensation_policy", "spindle_mapping", "coolant_mapping", "dwell_policy", "program_number_policy", "block_number_policy", "numeric_format", "comment_prefix", "comment_suffix", "maximum_comment_length", "newline", "encoding", "maximum_line_length", "maximum_program_size", "allowed_extensions", "supported_operation_strategies", "safe_start_records", "safe_end_records", "display_name"}
    _strict(data, PRODUCTION_PROFILE_FORMAT, PRODUCTION_PROFILE_VERSION, fields)
    list_fields = ("axes", "supported_units", "supported_planes", "supported_feed_modes", "supported_spindle_directions", "feed_limits", "work_offset_mapping", "allowed_extensions", "supported_operation_strategies", "safe_start_records", "safe_end_records")
    if any(not isinstance(data[name], list) for name in list_fields):
        raise CamValidationError("Production profile collections must be lists")
    try:
        return ProductionControllerProfile(
            ProductionControllerProfileId.parse(data["profile_id"]), data["profile_key"], data["profile_version"], data["adapter_key"], data["adapter_version"], data["controller_family"], data["controller_model"], data["machine_family"], MachineKind(data["machine_type"]), tuple(data["axes"]), tuple(LengthUnit(item) for item in data["supported_units"]), tuple(Plane(item) for item in data["supported_planes"]), CoordinateMode(data["coordinate_mode"]), tuple(FeedMode(item) for item in data["supported_feed_modes"]), tuple(SpindleDirection(item) for item in data["supported_spindle_directions"]), data["minimum_rpm"], data["maximum_rpm"], tuple(_feed_from_dict(item) for item in data["feed_limits"]), ArcPolicy.from_dict(data["arc_policy"]), tuple(WorkOffsetMapping.from_dict(item) for item in data["work_offset_mapping"]), ToolActivationPolicy(data["tool_activation_policy"]), CutterCompensationPolicy(data["cutter_compensation_policy"]), SpindleCodeMapping.from_dict(data["spindle_mapping"]), CoolantCodeMapping.from_dict(data["coolant_mapping"]), DwellPolicy(data["dwell_policy"]), ProgramNumberPolicy(data["program_number_policy"]), BlockNumberPolicy(data["block_number_policy"]), NumericFormatPolicy.from_dict(data["numeric_format"]), data["comment_prefix"], data["comment_suffix"], data["maximum_comment_length"], data["newline"], data["encoding"], data["maximum_line_length"], data["maximum_program_size"], tuple(data["allowed_extensions"]), tuple(data["supported_operation_strategies"]), tuple(SafeSequenceToken(item) for item in data["safe_start_records"]), tuple(SafeSequenceToken(item) for item in data["safe_end_records"]), data["display_name"], data["format_version"])
    except (TypeError, ValueError) as error:
        raise CamValidationError("Production profile enum payload is invalid") from error


def _strict(data: dict[str, Any], format_name: str, version: int, fields: set[str]) -> None:
    if not isinstance(data, dict) or set(data) != fields | {"format", "format_version"}:
        raise CamValidationError(f"{format_name} payload is malformed")
    if data["format"] != format_name:
        raise UnsupportedCamSchemaError(f"Unsupported {format_name} format")
    if type(data["format_version"]) is not int or data["format_version"] != version:
        raise UnsupportedCamSchemaError(f"Unsupported {format_name} version")


def _fields(data: dict[str, Any], fields: set[str], subject: str) -> None:
    if not isinstance(data, dict) or set(data) != fields:
        raise CamValidationError(f"{subject} payload is malformed")


def _unique_text(values: tuple[str, ...], name: str, pattern: re.Pattern[str]) -> tuple[str, ...]:
    if not isinstance(values, tuple) or not values or any(not isinstance(item, str) or pattern.fullmatch(item) is None for item in values):
        raise CamValidationError(f"Production {name} are invalid")
    if len(set(values)) != len(values):
        raise CamInvariantError(f"Production {name} must be unique")
    return values


def _length_to_dict(value: Length) -> dict[str, Any]:
    return {"value": value.value, "unit": value.unit.value}


def _length_from_dict(data: dict[str, Any]) -> Length:
    _fields(data, {"value", "unit"}, "Length")
    try:
        return Length(data["value"], LengthUnit(data["unit"]))
    except (TypeError, ValueError) as error:
        raise CamValidationError("Length payload is invalid") from error


def _feed_to_dict(value: FeedRate) -> dict[str, Any]:
    return {"value": value.value, "unit": value.unit.value}


def _feed_from_dict(data: dict[str, Any]) -> FeedRate:
    _fields(data, {"value", "unit"}, "Feed")
    try:
        return FeedRate(data["value"], FeedUnit(data["unit"]))
    except (TypeError, ValueError) as error:
        raise CamValidationError("Feed payload is invalid") from error


def _fingerprint_from_dict(data: dict[str, Any]) -> ContentFingerprint:
    if not isinstance(data, dict):
        raise CamValidationError("Fingerprint payload is invalid")
    return DependencyFingerprint.from_dict(data) if data.get("kind") == DependencyFingerprint.KIND else ContentFingerprint.from_dict(data)
