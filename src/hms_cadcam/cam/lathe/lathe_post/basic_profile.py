"""Editable-in-memory sample-derived Fanuc-style Lathe Post configuration."""

from __future__ import annotations

from dataclasses import dataclass
import re

from hms_cadcam.cam.lathe.lathe_post.basic_types import BasicFinalSafeTool
from hms_cadcam.cam.lathe.lathe_post.identity import canonical_id

_WORD = re.compile(r"^[GMT]\d{1,3}$")
_RETURN = re.compile(r"^G28 U0 W0$")


def _word(value: str, field: str) -> str:
    if not isinstance(value, str) or not _WORD.fullmatch(value):
        raise ValueError(f"{field} must be one typed controller word")
    return value


@dataclass(frozen=True, slots=True)
class BasicLathePostProfile:
    profile_id: str = "hms.lathe.fanuc_basic_sample_v1"
    schema_version: str = "lathe.basic.fanuc.profile.v1"
    sample_contract_revision: int = 1
    renderer_algorithm_version: str = "lathe.basic_fanuc.renderer.v1.1"
    display_name_key: str = "lathe.post.basic_fanuc_sample_v1"
    controller_family: str = "FANUC_STYLE_UNVERIFIED"
    machine_model: str = "UNSPECIFIED"
    controller_model: str = "UNSPECIFIED"
    preview_only: bool = False
    machine_output_supported: bool = True
    editable_profile: bool = True
    output_extension: str = ".NC"
    program_number: int = 0
    program_number_width: int = 4
    tool_number_width: int = 2
    offset_number_width: int = 2
    use_same_tool_and_offset: bool = True
    units: str = "MILLIMETRES"
    work_offset_code: str = "G54"
    feed_mode_code: str = "G99"
    spindle_mode_code: str = "G97"
    spindle_cw_code: str = "M03"
    spindle_ccw_code: str = "M04"
    spindle_stop_code: str = "M05"
    coolant_on_code: str = "M8"
    coolant_off_code: str = "M9"
    reference_return_code: str = "G28 U0 W0"
    optional_stop_code: str = "M01"
    program_end_code: str = "M30"
    final_safe_tool: BasicFinalSafeTool = BasicFinalSafeTool()
    emit_line_numbers: bool = False
    emit_g18: bool = False
    emit_g40: bool = False
    emit_g80: bool = False
    emit_spindle_stop_each_operation: bool = True
    emit_optional_stop_between_operations: bool = True
    optional_stop_after_last: bool = False
    coordinate_decimals: int = 3
    feed_decimals: int = 4
    pitch_decimals: int = 4
    suppress_leading_zero: bool = True
    trim_trailing_zero: bool = True
    uppercase_comments: bool = True
    warning_header_enabled: bool = True
    machine_verified: bool = False
    production_approved: bool = False
    default_coolant_enabled: bool = True
    optional_setup_m73: bool = False
    optional_setup_m74: bool = False
    optional_secondary_work_offset_g55: bool = False
    optional_initial_tool_call: BasicFinalSafeTool | None = None
    optional_manual_stop_after_initial_tool: bool = False
    optional_raw_setup_sequence: tuple[str, ...] = ()
    emit_final_safe_tool: bool = True
    line_number_start: int = 10
    line_number_step: int = 10

    def __post_init__(self) -> None:
        object.__setattr__(self, "profile_id", canonical_id(self.profile_id, "profile_id"))
        object.__setattr__(self, "schema_version", canonical_id(self.schema_version, "schema_version"))
        object.__setattr__(self, "renderer_algorithm_version", canonical_id(self.renderer_algorithm_version, "renderer_algorithm_version"))
        object.__setattr__(self, "display_name_key", canonical_id(self.display_name_key, "display_name_key"))
        for name in ("controller_family", "machine_model", "controller_model"):
            object.__setattr__(self, name, canonical_id(getattr(self, name), name))
        if self.output_extension != ".NC":
            raise ValueError("basic profile output extension must be .NC")
        if type(self.sample_contract_revision) is not int or self.sample_contract_revision <= 0:
            raise ValueError("sample_contract_revision must be a positive integer")
        if type(self.program_number) is not int or not 0 <= self.program_number <= 9999:
            raise ValueError("program_number must be an integer from 0 through 9999")
        if self.program_number_width != 4 or self.tool_number_width != 2 or self.offset_number_width != 2:
            raise ValueError("Stage 12.4B uses O#### and two-digit tool/offset fields")
        for name in ("preview_only", "machine_output_supported", "editable_profile", "use_same_tool_and_offset", "emit_line_numbers", "emit_g18", "emit_g40", "emit_g80", "emit_spindle_stop_each_operation", "emit_optional_stop_between_operations", "optional_stop_after_last", "suppress_leading_zero", "trim_trailing_zero", "uppercase_comments", "warning_header_enabled", "machine_verified", "production_approved", "default_coolant_enabled", "optional_setup_m73", "optional_setup_m74", "optional_secondary_work_offset_g55", "optional_manual_stop_after_initial_tool", "emit_final_safe_tool"):
            if type(getattr(self, name)) is not bool:
                raise TypeError(f"{name} must be bool")
        if self.preview_only and self.machine_output_supported:
            raise ValueError("preview_only profile cannot support machine output")
        for name in ("work_offset_code", "feed_mode_code", "spindle_mode_code", "spindle_cw_code", "spindle_ccw_code", "spindle_stop_code", "coolant_on_code", "coolant_off_code", "optional_stop_code", "program_end_code"):
            object.__setattr__(self, name, _word(getattr(self, name), name))
        if not isinstance(self.reference_return_code, str) or not _RETURN.fullmatch(self.reference_return_code):
            raise ValueError("reference_return_code must be exactly G28 U0 W0")
        for name in ("coordinate_decimals", "feed_decimals", "pitch_decimals"):
            value = getattr(self, name)
            if type(value) is not int or not 0 <= value <= 9:
                raise ValueError(f"{name} must be an integer from 0 through 9")
        if type(self.line_number_start) is not int or self.line_number_start <= 0 or type(self.line_number_step) is not int or self.line_number_step <= 0:
            raise ValueError("line number settings must be positive integers")
        if not isinstance(self.optional_raw_setup_sequence, tuple) or self.optional_raw_setup_sequence:
            raise ValueError("unrestricted raw setup sequence is disabled in Stage 12.4B")
        if self.optional_manual_stop_after_initial_tool and self.optional_initial_tool_call is None:
            raise ValueError("manual stop requires an initial typed tool call")

    @property
    def production_approved_state(self) -> bool:
        return self.production_approved

    @property
    def profile_state(self) -> str:
        return "BASIC_POST_UNVERIFIED"

    @property
    def output_readiness(self) -> str:
        return "BASIC_NC_OUTPUT_READY_UNVERIFIED"

    def program_word(self) -> str:
        return f"O{self.program_number:0{self.program_number_width}d}"

    def tool_word(self, tool_number: int, offset_number: int) -> str:
        if type(tool_number) is not int or type(offset_number) is not int or not 0 < tool_number <= 99 or not 0 < offset_number <= 99:
            raise ValueError("tool and offset numbers must be from 1 through 99")
        return f"T{tool_number:0{self.tool_number_width}d}{offset_number:0{self.offset_number_width}d}"


@dataclass(frozen=True, slots=True)
class BasicLathePostProfileRegistry:
    profiles: tuple[BasicLathePostProfile, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.profiles, tuple) or any(not isinstance(item, BasicLathePostProfile) for item in self.profiles):
            raise TypeError("profiles must be an immutable tuple")
        if len(self.profiles) > 1:
            raise ValueError("Stage 12.4B registers one basic profile only")

    def get(self, profile_id: str) -> BasicLathePostProfile | None:
        key = str(profile_id).strip()
        return next((profile for profile in self.profiles if profile.profile_id == key), None)


def basic_lathe_post_profile() -> BasicLathePostProfile:
    return BasicLathePostProfile()


def basic_lathe_post_profile_registry(*, enabled: bool, foundation_enabled: bool) -> BasicLathePostProfileRegistry:
    if type(enabled) is not bool or type(foundation_enabled) is not bool:
        raise TypeError("feature flags must be bool")
    return BasicLathePostProfileRegistry((basic_lathe_post_profile(),)) if enabled and foundation_enabled else BasicLathePostProfileRegistry()


__all__ = ["BasicLathePostProfile", "BasicLathePostProfileRegistry", "basic_lathe_post_profile", "basic_lathe_post_profile_registry"]
