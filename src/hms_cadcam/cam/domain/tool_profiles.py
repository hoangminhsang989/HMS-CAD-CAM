"""Typed optional Tool configuration profiles and deterministic resolution.

The module deliberately depends on stable IDs/revisions, not on ``ToolDefinition``.
That keeps the profile contract reusable without creating a tooling import cycle.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
import math
import re
from typing import Any, Protocol, TypeAlias

from hms_cadcam.cam.domain.errors import CamInvariantError, CamValidationError
from hms_cadcam.cam.domain.ids import ToolDefinitionId, ToolProgramProfileId
from hms_cadcam.cam.domain.revision import (
    ContentFingerprint,
    DependencyFingerprint,
    Revision,
)
from hms_cadcam.cam.domain.units import Length, LengthUnit


ProfilePrimitive: TypeAlias = str | int | float | bool | None
_FIELD_ID = re.compile(r"[a-z][a-z0-9_]{0,63}")
_STRATEGY_ID = re.compile(r"[a-z][a-z0-9_.-]{1,127}")
_PROFILE_FORMAT = "HMS_CAM_TOOL_PROGRAM_PROFILE"
_PROFILE_VERSION = 1
_COMMON_FORMAT = "HMS_CAM_TOOL_COMMON_DEFAULTS"
_COMMON_VERSION = 1
_MISSING = object()


def _stable_id(value: str, pattern: re.Pattern[str], subject: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise CamValidationError(f"{subject} is invalid")
    return value


def _display_name(value: str, subject: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CamValidationError(f"{subject} must not be empty")
    normalized = value.strip()
    if len(normalized) > 255:
        raise CamValidationError(f"{subject} is too long")
    return normalized


def _finite_optional(value: float | int | None, subject: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CamValidationError(f"{subject} must be numeric")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized < 0.0:
        raise CamValidationError(f"{subject} must be finite and non-negative")
    return normalized


def _timestamp(value: datetime, subject: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise CamValidationError(f"{subject} must be timezone-aware")
    normalized = value.astimezone(UTC)
    if normalized.microsecond:
        normalized = normalized.replace(microsecond=0)
    return normalized


def utc_profile_now() -> datetime:
    """Return a stable UTC timestamp suitable for persisted profile metadata."""
    return datetime.now(UTC).replace(microsecond=0)


def _timestamp_to_json(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _timestamp_from_json(value: object, subject: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise CamValidationError(f"{subject} timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise CamValidationError(f"{subject} timestamp is invalid") from error
    return _timestamp(parsed, subject)


def _strict_object(
    data: object,
    fields: set[str],
    subject: str,
) -> dict[str, Any]:
    if not isinstance(data, dict) or set(data) != fields:
        raise CamValidationError(f"{subject} payload is malformed")
    return data


def _primitive(value: object, subject: str) -> ProfilePrimitive:
    if value is None or type(value) in {str, int, bool}:
        if isinstance(value, str) and len(value) > 1024:
            raise CamValidationError(f"{subject} is too long")
        return value
    if type(value) is float and math.isfinite(value):
        return value
    raise CamValidationError(f"{subject} must be a finite JSON primitive")


class ToolProfileFieldType(StrEnum):
    """Finite field kinds supported by the profile editor and serializer."""

    NUMBER = "number"
    ENUM = "enum"
    BOOLEAN = "boolean"


class ToolProfileSafetyClass(StrEnum):
    """Whether a field can affect calculation or only presentation."""

    CALCULATION = "calculation"
    LINKING = "linking"
    PROCESS = "process"
    PRESENTATION = "presentation"


class ToolProfileValidationState(StrEnum):
    """Persisted review state; runtime compatibility may further restrict it."""

    CONFIGURED = "configured"
    NEEDS_REVIEW = "needs_review"
    INCOMPATIBLE = "incompatible"


class ToolProfileListState(StrEnum):
    """Compact state vocabulary displayed by the Tool editor."""

    NOT_CONFIGURED = "not_configured"
    CONFIGURED = "configured"
    CUSTOMIZED = "customized"
    INCOMPATIBLE = "incompatible"
    NEEDS_REVIEW = "needs_review"
    DISABLED = "disabled"


class ToolProfileValueSource(StrEnum):
    """Stable provenance enum with one reserved future source."""

    OPERATION_OVERRIDE = "operation_override"
    TOOL_PROGRAM_PROFILE = "tool_program_profile"
    TOOL_COMMON_DEFAULT = "tool_common_default"
    AUTOMATIC_POLICY = "automatic_policy"
    SAFE_DEFAULT = "safe_default"
    PROGRAM_TEMPLATE = "program_template"


class EffectiveValueMode(StrEnum):
    """Whether a resolved value expresses user intent or policy output."""

    MANUAL = "manual"
    AUTOMATIC = "automatic"


class EffectiveValueValidation(StrEnum):
    """Resolution result for one canonical value."""

    VALID = "valid"
    FALLBACK = "fallback"
    BLOCKED = "blocked"


class ToolProfileSaveMode(StrEnum):
    """Explicit profile capture choice; overrides-only is the safe default."""

    OVERRIDES_ONLY = "overrides_only"
    ALL_EFFECTIVE = "all_effective"


class ToolProfileDiffKind(StrEnum):
    """Preview classification shown before a profile mutation."""

    ADD = "add"
    CHANGE = "change"
    UNCHANGED = "unchanged"
    SKIPPED = "skipped"
    INVALID = "invalid"


@dataclass(frozen=True, slots=True)
class ToolCommonDefaults:
    """Sparse values that are genuinely reusable across strategies."""

    spindle_speed_rpm: float | None = None
    cutting_feed_mm_per_min: float | None = None
    plunge_feed_mm_per_min: float | None = None
    coolant_mode: str | None = None
    quality_profile: str | None = None
    maximum_cutting_depth_mm: float | None = None
    cutting_data_reference: str | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "spindle_speed_rpm",
            "cutting_feed_mm_per_min",
            "plunge_feed_mm_per_min",
            "maximum_cutting_depth_mm",
        ):
            object.__setattr__(
                self,
                field_name,
                _finite_optional(getattr(self, field_name), field_name),
            )
        if self.spindle_speed_rpm == 0.0:
            raise CamValidationError("Spindle speed must be greater than zero")
        if self.coolant_mode is not None and self.coolant_mode not in {
            "off",
            "flood",
            "mist",
            "air",
            "through_tool",
        }:
            raise CamValidationError("Tool common coolant mode is invalid")
        if self.quality_profile is not None and self.quality_profile not in {
            "fast",
            "balanced",
            "high",
        }:
            raise CamValidationError("Tool common quality profile is invalid")
        if self.cutting_data_reference is not None:
            object.__setattr__(
                self,
                "cutting_data_reference",
                _display_name(
                    self.cutting_data_reference, "Cutting-data reference"
                ),
            )

    @property
    def is_empty(self) -> bool:
        return all(
            value is None
            for value in (
                self.spindle_speed_rpm,
                self.cutting_feed_mm_per_min,
                self.plunge_feed_mm_per_min,
                self.coolant_mode,
                self.quality_profile,
                self.maximum_cutting_depth_mm,
                self.cutting_data_reference,
            )
        )

    @property
    def fingerprint(self) -> ContentFingerprint:
        return ContentFingerprint.from_payload(self.to_dict())

    def value_for(self, key: str) -> ProfilePrimitive | object:
        mapping: dict[str, ProfilePrimitive] = {
            "spindle_speed_rpm": self.spindle_speed_rpm,
            "cutting_feed_mm_per_min": self.cutting_feed_mm_per_min,
            "plunge_feed_mm_per_min": self.plunge_feed_mm_per_min,
            "coolant_mode": self.coolant_mode,
            "quality_profile": self.quality_profile,
            "maximum_cutting_depth_mm": self.maximum_cutting_depth_mm,
        }
        value = mapping.get(key, _MISSING)
        return _MISSING if value is None else value

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": _COMMON_FORMAT,
            "format_version": _COMMON_VERSION,
            "spindle_speed_rpm": self.spindle_speed_rpm,
            "cutting_feed_mm_per_min": self.cutting_feed_mm_per_min,
            "plunge_feed_mm_per_min": self.plunge_feed_mm_per_min,
            "coolant_mode": self.coolant_mode,
            "quality_profile": self.quality_profile,
            "maximum_cutting_depth_mm": self.maximum_cutting_depth_mm,
            "cutting_data_reference": self.cutting_data_reference,
        }

    @classmethod
    def from_dict(cls, data: object) -> "ToolCommonDefaults":
        fields = {
            "format",
            "format_version",
            "spindle_speed_rpm",
            "cutting_feed_mm_per_min",
            "plunge_feed_mm_per_min",
            "coolant_mode",
            "quality_profile",
            "maximum_cutting_depth_mm",
            "cutting_data_reference",
        }
        payload = _strict_object(data, fields, "Tool common defaults")
        if (
            payload["format"] != _COMMON_FORMAT
            or payload["format_version"] != _COMMON_VERSION
        ):
            raise CamValidationError("Tool common defaults version is unsupported")
        return cls(
            payload["spindle_speed_rpm"],
            payload["cutting_feed_mm_per_min"],
            payload["plunge_feed_mm_per_min"],
            payload["coolant_mode"],
            payload["quality_profile"],
            payload["maximum_cutting_depth_mm"],
            payload["cutting_data_reference"],
        )


@dataclass(frozen=True, slots=True)
class ToolProfileValue:
    """One explicitly configured sparse value."""

    field_id: str
    value: ProfilePrimitive

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "field_id", _stable_id(self.field_id, _FIELD_ID, "Profile field ID")
        )
        object.__setattr__(
            self, "value", _primitive(self.value, f"Profile field {self.field_id}")
        )

    def to_dict(self) -> dict[str, ProfilePrimitive]:
        return {"field_id": self.field_id, "value": self.value}

    @classmethod
    def from_dict(cls, data: object) -> "ToolProfileValue":
        payload = _strict_object(data, {"field_id", "value"}, "Tool profile value")
        return cls(payload["field_id"], payload["value"])


@dataclass(frozen=True, slots=True)
class ToolProgramProfile:
    """One optional strategy-specific sparse configuration attached to a Tool."""

    profile_id: ToolProgramProfileId
    tool_id: ToolDefinitionId
    strategy_id: str
    display_name: str
    enabled: bool
    profile_schema_version: int
    values: tuple[ToolProfileValue, ...]
    created_at: datetime
    updated_at: datetime
    source_tool_revision: Revision
    source_tool_fingerprint: ContentFingerprint
    revision: Revision = Revision(0)
    source_holder_fingerprint: ContentFingerprint | None = None
    validation_state: ToolProfileValidationState = (
        ToolProfileValidationState.CONFIGURED
    )

    def __post_init__(self) -> None:
        if not isinstance(self.profile_id, ToolProgramProfileId):
            raise CamValidationError("Tool profile ID is invalid")
        if not isinstance(self.tool_id, ToolDefinitionId):
            raise CamValidationError("Tool profile Tool ID is invalid")
        object.__setattr__(
            self,
            "strategy_id",
            _stable_id(self.strategy_id, _STRATEGY_ID, "Profile strategy ID"),
        )
        object.__setattr__(
            self,
            "display_name",
            _display_name(self.display_name, "Tool profile display name"),
        )
        if type(self.enabled) is not bool:
            raise CamValidationError("Tool profile enabled state is invalid")
        if (
            type(self.profile_schema_version) is not int
            or self.profile_schema_version <= 0
        ):
            raise CamValidationError("Tool profile schema version is invalid")
        if not isinstance(self.values, tuple) or any(
            not isinstance(item, ToolProfileValue) for item in self.values
        ):
            raise CamValidationError("Tool profile values must be a typed tuple")
        ordered = tuple(sorted(self.values, key=lambda item: item.field_id))
        if len({item.field_id for item in ordered}) != len(ordered):
            raise CamInvariantError("Tool profile field IDs must be unique")
        object.__setattr__(self, "values", ordered)
        created = _timestamp(self.created_at, "Tool profile creation")
        updated = _timestamp(self.updated_at, "Tool profile update")
        if updated < created:
            raise CamValidationError("Tool profile update precedes its creation")
        object.__setattr__(self, "created_at", created)
        object.__setattr__(self, "updated_at", updated)
        if not isinstance(self.source_tool_revision, Revision):
            raise CamValidationError("Tool profile source revision is invalid")
        if not isinstance(self.source_tool_fingerprint, ContentFingerprint):
            raise CamValidationError("Tool profile source fingerprint is invalid")
        if not isinstance(self.revision, Revision):
            raise CamValidationError("Tool profile revision is invalid")
        if (
            self.source_holder_fingerprint is not None
            and not isinstance(self.source_holder_fingerprint, ContentFingerprint)
        ):
            raise CamValidationError("Tool profile Holder fingerprint is invalid")
        if not isinstance(self.validation_state, ToolProfileValidationState):
            raise CamValidationError("Tool profile validation state is invalid")

    @property
    def sparse_mapping(self) -> dict[str, ProfilePrimitive]:
        return {item.field_id: item.value for item in self.values}

    @property
    def fingerprint(self) -> ContentFingerprint:
        """Hash calculation semantics, never display text or timestamps."""
        return ContentFingerprint.from_payload(
            {
                "strategy_id": self.strategy_id,
                "enabled": self.enabled,
                "profile_schema_version": self.profile_schema_version,
                "values": [item.to_dict() for item in self.values],
                "source_tool_revision": self.source_tool_revision.to_dict(),
                "source_tool_fingerprint": self.source_tool_fingerprint.to_dict(),
                "source_holder_fingerprint": (
                    self.source_holder_fingerprint.to_dict()
                    if self.source_holder_fingerprint is not None
                    else None
                ),
                "validation_state": self.validation_state.value,
            }
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": _PROFILE_FORMAT,
            "format_version": _PROFILE_VERSION,
            "profile_id": str(self.profile_id),
            "tool_id": str(self.tool_id),
            "strategy_id": self.strategy_id,
            "display_name": self.display_name,
            "enabled": self.enabled,
            "profile_schema_version": self.profile_schema_version,
            "values": [item.to_dict() for item in self.values],
            "created_at": _timestamp_to_json(self.created_at),
            "updated_at": _timestamp_to_json(self.updated_at),
            "source_tool_revision": self.source_tool_revision.to_dict(),
            "source_tool_fingerprint": self.source_tool_fingerprint.to_dict(),
            "source_holder_fingerprint": (
                self.source_holder_fingerprint.to_dict()
                if self.source_holder_fingerprint is not None
                else None
            ),
            "revision": self.revision.to_dict(),
            "validation_state": self.validation_state.value,
            "fingerprint": self.fingerprint.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: object) -> "ToolProgramProfile":
        fields = {
            "format",
            "format_version",
            "profile_id",
            "tool_id",
            "strategy_id",
            "display_name",
            "enabled",
            "profile_schema_version",
            "values",
            "created_at",
            "updated_at",
            "source_tool_revision",
            "source_tool_fingerprint",
            "source_holder_fingerprint",
            "revision",
            "validation_state",
            "fingerprint",
        }
        payload = _strict_object(data, fields, "Tool program profile")
        if (
            payload["format"] != _PROFILE_FORMAT
            or payload["format_version"] != _PROFILE_VERSION
        ):
            raise CamValidationError("Tool program profile version is unsupported")
        values = payload["values"]
        if not isinstance(values, list):
            raise CamValidationError("Tool profile values must be a list")
        holder_payload = payload["source_holder_fingerprint"]
        restored = cls(
            ToolProgramProfileId.parse(payload["profile_id"]),
            ToolDefinitionId.parse(payload["tool_id"]),
            payload["strategy_id"],
            payload["display_name"],
            payload["enabled"],
            payload["profile_schema_version"],
            tuple(ToolProfileValue.from_dict(item) for item in values),
            _timestamp_from_json(payload["created_at"], "Tool profile creation"),
            _timestamp_from_json(payload["updated_at"], "Tool profile update"),
            Revision.from_dict(payload["source_tool_revision"]),
            ContentFingerprint.from_dict(payload["source_tool_fingerprint"]),
            Revision.from_dict(payload["revision"]),
            (
                None
                if holder_payload is None
                else ContentFingerprint.from_dict(holder_payload)
            ),
            ToolProfileValidationState(payload["validation_state"]),
        )
        recorded = ContentFingerprint.from_dict(payload["fingerprint"])
        if recorded != restored.fingerprint:
            raise CamValidationError("Tool program profile fingerprint is inconsistent")
        return restored


@dataclass(frozen=True, slots=True)
class ToolProfileFieldDescriptor:
    """Typed schema for one strategy-owned profile field."""

    field_id: str
    operation_field_id: str
    display_name_vi: str
    field_type: ToolProfileFieldType
    unit: str = ""
    minimum: float | None = None
    maximum: float | None = None
    enum_values: tuple[str, ...] = ()
    enum_display_names_vi: tuple[str, ...] = ()
    optional: bool = True
    safety_classification: ToolProfileSafetyClass = (
        ToolProfileSafetyClass.CALCULATION
    )
    automatic_compatible: bool = True
    common_default_key: str | None = None
    safe_default: ProfilePrimitive | object = _MISSING
    override_flag_id: str | None = None
    advanced: bool = False

    def __post_init__(self) -> None:
        for value, subject in (
            (self.field_id, "Profile field ID"),
            (self.operation_field_id, "Operation field ID"),
        ):
            _stable_id(value, _FIELD_ID, subject)
        object.__setattr__(
            self, "display_name_vi", _display_name(self.display_name_vi, "Field name")
        )
        if not isinstance(self.field_type, ToolProfileFieldType):
            raise CamValidationError("Tool profile field type is invalid")
        for value, subject in (
            (self.minimum, "Field minimum"),
            (self.maximum, "Field maximum"),
        ):
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
            ):
                raise CamValidationError(f"{subject} is invalid")
        if (
            self.minimum is not None
            and self.maximum is not None
            and self.minimum > self.maximum
        ):
            raise CamValidationError("Profile field range is inverted")
        if self.field_type is ToolProfileFieldType.ENUM:
            if not self.enum_values or len(set(self.enum_values)) != len(
                self.enum_values
            ):
                raise CamValidationError("Profile enum values are invalid")
            if len(self.enum_display_names_vi) != len(self.enum_values):
                raise CamValidationError(
                    "Profile enum display names do not match its values"
                )
            for display_name in self.enum_display_names_vi:
                _display_name(display_name, "Profile enum display name")
        elif self.enum_values or self.enum_display_names_vi:
            raise CamValidationError("Only enum fields can declare enum values")
        if self.common_default_key is not None:
            _stable_id(
                self.common_default_key, _FIELD_ID, "Common-default binding"
            )
        if self.override_flag_id is not None:
            _stable_id(self.override_flag_id, _FIELD_ID, "Override flag ID")
        if self.safe_default is not _MISSING:
            self.normalize(self.safe_default)

    def normalize(self, value: object) -> ProfilePrimitive:
        """Deserialize and validate one canonical primitive."""
        if self.field_type is ToolProfileFieldType.NUMBER:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise CamValidationError(
                    f"{self.display_name_vi} phải là một số hữu hạn."
                )
            result = float(value)
            if not math.isfinite(result):
                raise CamValidationError(
                    f"{self.display_name_vi} phải là một số hữu hạn."
                )
            if self.minimum is not None and result < self.minimum:
                raise CamValidationError(
                    f"{self.display_name_vi} nhỏ hơn giới hạn cho phép."
                )
            if self.maximum is not None and result > self.maximum:
                raise CamValidationError(
                    f"{self.display_name_vi} lớn hơn giới hạn cho phép."
                )
            return result
        if self.field_type is ToolProfileFieldType.BOOLEAN:
            if type(value) is not bool:
                raise CamValidationError(f"{self.display_name_vi} phải là boolean.")
            return value
        if not isinstance(value, str) or value not in self.enum_values:
            raise CamValidationError(
                f"{self.display_name_vi} không thuộc danh sách được hỗ trợ."
            )
        return value

    def deserialize(
        self,
        value: object,
        *,
        source_unit: LengthUnit | None = None,
    ) -> ProfilePrimitive:
        """Convert one UI primitive into the canonical profile representation."""
        candidate = value
        if (
            self.field_type is ToolProfileFieldType.NUMBER
            and isinstance(value, str)
        ):
            try:
                candidate = float(value.strip().replace(",", "."))
            except ValueError as error:
                raise CamValidationError(
                    f"{self.display_name_vi} phải là một số hữu hạn."
                ) from error
        if (
            self.field_type is ToolProfileFieldType.NUMBER
            and self.unit == "mm"
            and source_unit is not None
            and source_unit is not LengthUnit.MM
        ):
            if isinstance(candidate, bool) or not isinstance(
                candidate,
                (int, float),
            ):
                raise CamValidationError(
                    f"{self.display_name_vi} phải là một số hữu hạn."
                )
            candidate = Length(float(candidate), source_unit).to(
                LengthUnit.MM
            ).value
        return self.normalize(candidate)

    def serialize(self, value: object) -> ProfilePrimitive:
        """Validate one canonical value before deterministic JSON encoding."""
        return self.normalize(value)

    def display_value(self, value: ProfilePrimitive) -> str:
        normalized = self.normalize(value)
        if isinstance(normalized, float):
            text = f"{normalized:g}"
        elif isinstance(normalized, bool):
            text = "Bật" if normalized else "Tắt"
        else:
            text = self.enum_display_names_vi[
                self.enum_values.index(normalized)
            ]
        return f"{text} {self.unit}".strip()

    def report_dict(self) -> dict[str, Any]:
        return {
            "field_id": self.field_id,
            "operation_field_id": self.operation_field_id,
            "display_name_vi": self.display_name_vi,
            "field_type": self.field_type.value,
            "unit": self.unit,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "enum_values": list(self.enum_values),
            "enum_display_names_vi": list(self.enum_display_names_vi),
            "optional": self.optional,
            "safety_classification": self.safety_classification.value,
            "automatic_compatible": self.automatic_compatible,
            "common_default_key": self.common_default_key,
            "has_safe_default": self.safe_default is not _MISSING,
            "override_flag_id": self.override_flag_id,
            "advanced": self.advanced,
        }


@dataclass(frozen=True, slots=True)
class ToolStrategyProfileSchema:
    """One strategy-owned finite profile contract."""

    strategy_id: str
    display_name_vi: str
    profile_schema_version: int
    supported_tool_families: tuple[str, ...]
    fields: tuple[ToolProfileFieldDescriptor, ...]

    def __post_init__(self) -> None:
        _stable_id(self.strategy_id, _STRATEGY_ID, "Strategy profile ID")
        object.__setattr__(
            self,
            "display_name_vi",
            _display_name(self.display_name_vi, "Strategy display name"),
        )
        if (
            type(self.profile_schema_version) is not int
            or self.profile_schema_version <= 0
        ):
            raise CamValidationError("Strategy profile schema version is invalid")
        if not isinstance(self.supported_tool_families, tuple) or not all(
            isinstance(item, str) and item for item in self.supported_tool_families
        ):
            raise CamValidationError("Supported Tool families are invalid")
        if len(set(self.supported_tool_families)) != len(
            self.supported_tool_families
        ):
            raise CamInvariantError("Supported Tool families must be unique")
        if not isinstance(self.fields, tuple) or any(
            not isinstance(item, ToolProfileFieldDescriptor) for item in self.fields
        ):
            raise CamValidationError("Strategy profile fields are invalid")
        ordered = tuple(sorted(self.fields, key=lambda item: item.field_id))
        if len({item.field_id for item in ordered}) != len(ordered):
            raise CamInvariantError("Strategy profile field IDs must be unique")
        object.__setattr__(self, "fields", ordered)

    def field(self, field_id: str) -> ToolProfileFieldDescriptor:
        try:
            return next(item for item in self.fields if item.field_id == field_id)
        except StopIteration as error:
            raise CamValidationError(
                f"Trường cấu hình không được hỗ trợ: {field_id}"
            ) from error

    def normalize_values(
        self, values: Mapping[str, object]
    ) -> tuple[ToolProfileValue, ...]:
        unknown = set(values) - {item.field_id for item in self.fields}
        if unknown:
            raise CamValidationError(
                f"Cấu hình chứa trường không được hỗ trợ: {sorted(unknown)[0]}"
            )
        normalized = tuple(
            ToolProfileValue(field_id, self.field(field_id).normalize(value))
            for field_id, value in sorted(values.items())
        )
        required = {
            item.field_id for item in self.fields if not item.optional
        } - {item.field_id for item in normalized}
        if required:
            raise CamValidationError(
                f"Cấu hình thiếu trường bắt buộc: {sorted(required)[0]}"
            )
        return normalized

    def validate_profile(
        self,
        profile: ToolProgramProfile,
        *,
        tool_family: str,
    ) -> None:
        if profile.strategy_id != self.strategy_id:
            raise CamValidationError("Tool profile uses the wrong strategy schema")
        if profile.profile_schema_version != self.profile_schema_version:
            raise CamValidationError("Tool profile schema version is unsupported")
        if tool_family not in self.supported_tool_families:
            raise CamValidationError(
                "Họ Tool không tương thích với chương trình này."
            )
        self.normalize_values(profile.sparse_mapping)

    def report_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "display_name_vi": self.display_name_vi,
            "profile_schema_version": self.profile_schema_version,
            "supported_tool_families": list(self.supported_tool_families),
            "fields": [item.report_dict() for item in self.fields],
        }


class ToolProfileSchemaRegistry:
    """Finite typed registry; duplicate and unknown strategies fail clearly."""

    def __init__(self, schemas: tuple[ToolStrategyProfileSchema, ...]) -> None:
        if not isinstance(schemas, tuple) or any(
            not isinstance(item, ToolStrategyProfileSchema) for item in schemas
        ):
            raise CamValidationError("Tool profile registry schemas are invalid")
        ordered = tuple(sorted(schemas, key=lambda item: item.strategy_id))
        if len({item.strategy_id for item in ordered}) != len(ordered):
            raise CamInvariantError("Tool profile strategies must be unique")
        self._schemas = ordered

    @property
    def schemas(self) -> tuple[ToolStrategyProfileSchema, ...]:
        return self._schemas

    def schema(self, strategy_id: str) -> ToolStrategyProfileSchema:
        try:
            return next(
                item for item in self._schemas if item.strategy_id == strategy_id
            )
        except StopIteration as error:
            raise CamValidationError(
                f"Chương trình Tool profile chưa được đăng ký: {strategy_id}"
            ) from error

    def validate_profile(
        self, profile: ToolProgramProfile, *, tool_family: str
    ) -> None:
        self.schema(profile.strategy_id).validate_profile(
            profile, tool_family=tool_family
        )


@dataclass(frozen=True, slots=True)
class ToolProfileCompatibility:
    """Runtime profile assessment for the selected Tool/Holder context."""

    state: ToolProfileListState
    usable: bool
    reason_vi: str


class ToolProfileToolContext(Protocol):
    tool_id: ToolDefinitionId
    family: object
    revision: Revision
    content_fingerprint: ContentFingerprint
    common_defaults: ToolCommonDefaults
    program_profiles: tuple[ToolProgramProfile, ...]


def assess_tool_program_profile(
    profile: ToolProgramProfile,
    tool: ToolProfileToolContext,
    registry: ToolProfileSchemaRegistry,
    *,
    holder_fingerprint: ContentFingerprint | None = None,
) -> ToolProfileCompatibility:
    """Fail closed for disabled, stale, malformed, or incompatible profiles."""
    if profile.tool_id != tool.tool_id:
        return ToolProfileCompatibility(
            ToolProfileListState.INCOMPATIBLE,
            False,
            "Cấu hình không thuộc Tool hiện tại.",
        )
    if not profile.enabled:
        return ToolProfileCompatibility(
            ToolProfileListState.DISABLED, False, "Cấu hình đang tắt."
        )
    if profile.validation_state is ToolProfileValidationState.NEEDS_REVIEW:
        return ToolProfileCompatibility(
            ToolProfileListState.NEEDS_REVIEW,
            False,
            "Cấu hình cần xem lại trước khi sử dụng.",
        )
    if profile.validation_state is ToolProfileValidationState.INCOMPATIBLE:
        return ToolProfileCompatibility(
            ToolProfileListState.INCOMPATIBLE,
            False,
            "Cấu hình đã được đánh dấu không tương thích.",
        )
    family = getattr(tool.family, "value", tool.family)
    if not isinstance(family, str):
        return ToolProfileCompatibility(
            ToolProfileListState.INCOMPATIBLE,
            False,
            "Họ Tool hiện tại không hợp lệ.",
        )
    try:
        registry.validate_profile(profile, tool_family=family)
    except CamValidationError as error:
        return ToolProfileCompatibility(
            ToolProfileListState.INCOMPATIBLE, False, str(error)
        )
    if (
        profile.source_tool_revision != tool.revision
        or profile.source_tool_fingerprint != tool.content_fingerprint
    ):
        return ToolProfileCompatibility(
            ToolProfileListState.NEEDS_REVIEW,
            False,
            "Tool đã thay đổi kể từ khi lưu cấu hình.",
        )
    if (
        profile.source_holder_fingerprint is not None
        and profile.source_holder_fingerprint != holder_fingerprint
    ):
        return ToolProfileCompatibility(
            ToolProfileListState.NEEDS_REVIEW,
            False,
            "Holder đã thay đổi kể từ khi lưu cấu hình.",
        )
    return ToolProfileCompatibility(
        (
            ToolProfileListState.CUSTOMIZED
            if profile.values
            else ToolProfileListState.CONFIGURED
        ),
        True,
        "Cấu hình tương thích.",
    )


@dataclass(frozen=True, slots=True)
class EffectiveToolValue:
    """One canonical resolved value with full provenance and dependency input."""

    field_id: str
    canonical_value: ProfilePrimitive
    display_value: str
    source: ToolProfileValueSource
    source_object_id: str
    validation_status: EffectiveValueValidation
    mode: EffectiveValueMode
    reason_vi: str
    dependency_contribution: DependencyFingerprint


@dataclass(frozen=True, slots=True)
class ToolProfileResolution:
    """Deterministic all-field result; blocked fields never receive a guess."""

    strategy_id: str
    values: tuple[EffectiveToolValue, ...]
    profile_compatibility: ToolProfileCompatibility

    @property
    def blocked(self) -> bool:
        return any(
            item.validation_status is EffectiveValueValidation.BLOCKED
            for item in self.values
        )

    @property
    def dependency_fingerprint(self) -> DependencyFingerprint:
        return DependencyFingerprint.from_payload(
            {
                "strategy_id": self.strategy_id,
                "values": [
                    {
                        "field_id": item.field_id,
                        "canonical_value": item.canonical_value,
                        "source": item.source.value,
                        "source_object_id": item.source_object_id,
                        "validation_status": item.validation_status.value,
                        "mode": item.mode.value,
                        "dependency_contribution": (
                            item.dependency_contribution.to_dict()
                        ),
                    }
                    for item in self.values
                ],
            }
        )

    def value(self, field_id: str) -> EffectiveToolValue:
        try:
            return next(item for item in self.values if item.field_id == field_id)
        except StopIteration as error:
            raise KeyError(field_id) from error


class ToolProfileResolver:
    """Resolve operation → profile → common → automatic → safe defaults."""

    _SOURCE_LABELS = {
        ToolProfileValueSource.OPERATION_OVERRIDE: "Nguyên công hiện tại",
        ToolProfileValueSource.TOOL_PROGRAM_PROFILE: (
            "Cấu hình Tool theo chương trình"
        ),
        ToolProfileValueSource.TOOL_COMMON_DEFAULT: "Cấu hình cơ bản của Tool",
        ToolProfileValueSource.AUTOMATIC_POLICY: "Chính sách tự động",
        ToolProfileValueSource.SAFE_DEFAULT: "Giá trị an toàn mặc định",
        ToolProfileValueSource.PROGRAM_TEMPLATE: "Chương trình mẫu",
    }

    def __init__(self, registry: ToolProfileSchemaRegistry) -> None:
        self._registry = registry

    def resolve(
        self,
        tool: ToolProfileToolContext,
        strategy_id: str,
        *,
        operation_overrides: Mapping[str, object] | None = None,
        automatic_values: Mapping[str, object] | None = None,
        safe_values: Mapping[str, object] | None = None,
        profile_id: ToolProgramProfileId | None = None,
        operation_id: str = "",
        automatic_policy_id: str = "",
        holder_fingerprint: ContentFingerprint | None = None,
    ) -> ToolProfileResolution:
        schema = self._registry.schema(strategy_id)
        overrides = operation_overrides or {}
        automatic = automatic_values or {}
        safe = safe_values or {}
        allowed = {item.field_id for item in schema.fields}
        for source_name, values in (
            ("operation", overrides),
            ("automatic", automatic),
            ("safe", safe),
        ):
            unknown = set(values) - allowed
            if unknown:
                raise CamValidationError(
                    f"{source_name} chứa trường không thuộc profile schema: "
                    f"{sorted(unknown)[0]}"
                )
        candidates = tuple(
            item
            for item in tool.program_profiles
            if item.strategy_id == strategy_id
            and (profile_id is None or item.profile_id == profile_id)
        )
        enabled = tuple(item for item in candidates if item.enabled)
        selected = (
            enabled[0]
            if len(enabled) == 1
            else candidates[0]
            if len(candidates) == 1
            else None
        )
        if len(enabled) > 1 and profile_id is None:
            compatibility = ToolProfileCompatibility(
                ToolProfileListState.INCOMPATIBLE,
                False,
                "Có nhiều cấu hình đang bật; cần chọn rõ cấu hình.",
            )
        elif selected is None:
            compatibility = ToolProfileCompatibility(
                ToolProfileListState.NOT_CONFIGURED,
                False,
                "Chưa cấu hình; Tool vẫn dùng chính sách tự động.",
            )
        else:
            compatibility = assess_tool_program_profile(
                selected,
                tool,
                self._registry,
                holder_fingerprint=holder_fingerprint,
            )
        profile_values = (
            selected.sparse_mapping
            if selected is not None and compatibility.usable
            else {}
        )
        resolved = tuple(
            self._resolve_field(
                descriptor,
                tool,
                overrides,
                profile_values,
                automatic,
                safe,
                profile=selected,
                compatibility=compatibility,
                operation_id=operation_id,
                automatic_policy_id=automatic_policy_id,
            )
            for descriptor in schema.fields
        )
        return ToolProfileResolution(strategy_id, resolved, compatibility)

    def _resolve_field(
        self,
        descriptor: ToolProfileFieldDescriptor,
        tool: ToolProfileToolContext,
        overrides: Mapping[str, object],
        profile_values: Mapping[str, ProfilePrimitive],
        automatic: Mapping[str, object],
        safe: Mapping[str, object],
        *,
        profile: ToolProgramProfile | None,
        compatibility: ToolProfileCompatibility,
        operation_id: str,
        automatic_policy_id: str,
    ) -> EffectiveToolValue:
        attempts: tuple[
            tuple[
                ToolProfileValueSource,
                object,
                str,
                EffectiveValueMode,
                str,
            ],
            ...,
        ] = (
            (
                ToolProfileValueSource.OPERATION_OVERRIDE,
                overrides.get(descriptor.field_id, _MISSING),
                operation_id,
                EffectiveValueMode.MANUAL,
                "Giá trị do người dùng tùy chỉnh trong nguyên công hiện tại.",
            ),
            (
                ToolProfileValueSource.TOOL_PROGRAM_PROFILE,
                profile_values.get(descriptor.field_id, _MISSING),
                str(profile.profile_id) if profile is not None else "",
                EffectiveValueMode.MANUAL,
                "Nguồn giá trị: Cấu hình Tool theo chương trình.",
            ),
            (
                ToolProfileValueSource.TOOL_COMMON_DEFAULT,
                (
                    tool.common_defaults.value_for(descriptor.common_default_key)
                    if descriptor.common_default_key is not None
                    else _MISSING
                ),
                str(tool.tool_id),
                EffectiveValueMode.MANUAL,
                "Giá trị lấy từ cấu hình cơ bản của Tool.",
            ),
            (
                ToolProfileValueSource.AUTOMATIC_POLICY,
                automatic.get(descriptor.field_id, _MISSING),
                automatic_policy_id,
                EffectiveValueMode.AUTOMATIC,
                "Giá trị do chính sách tự động của chương trình tính.",
            ),
            (
                ToolProfileValueSource.SAFE_DEFAULT,
                safe.get(
                    descriptor.field_id,
                    descriptor.safe_default,
                ),
                strategy_safe_source_id(descriptor.field_id),
                EffectiveValueMode.AUTOMATIC,
                "Giá trị an toàn mặc định được schema cho phép.",
            ),
        )
        invalid_reasons: list[str] = []
        for source, candidate, source_id, mode, reason in attempts:
            if candidate is _MISSING:
                continue
            try:
                canonical = descriptor.normalize(candidate)
            except CamValidationError as error:
                invalid_reasons.append(
                    f"{self._SOURCE_LABELS[source]}: {error}"
                )
                continue
            status = (
                EffectiveValueValidation.FALLBACK
                if invalid_reasons
                or (
                    source is not ToolProfileValueSource.TOOL_PROGRAM_PROFILE
                    and profile is not None
                    and not compatibility.usable
                )
                else EffectiveValueValidation.VALID
            )
            contribution = DependencyFingerprint.from_payload(
                {
                    "field_id": descriptor.field_id,
                    "canonical_value": canonical,
                    "source": source.value,
                    "source_object_id": source_id,
                    "source_profile_fingerprint": (
                        profile.fingerprint.to_dict()
                        if source is ToolProfileValueSource.TOOL_PROGRAM_PROFILE
                        and profile is not None
                        else None
                    ),
                    "safety_classification": (
                        descriptor.safety_classification.value
                    ),
                }
            )
            fallback_reason = (
                f"{reason} Nguồn ưu tiên cao hơn không dùng được: "
                + " · ".join(invalid_reasons)
                if invalid_reasons
                else reason
            )
            return EffectiveToolValue(
                descriptor.field_id,
                canonical,
                descriptor.display_value(canonical),
                source,
                source_id,
                status,
                mode,
                fallback_reason,
                contribution,
            )
        blocked_reason = (
            "Không có nguồn giá trị hợp lệ; phải bổ sung hoặc sửa cấu hình."
        )
        if invalid_reasons:
            blocked_reason += " " + " · ".join(invalid_reasons)
        return EffectiveToolValue(
            descriptor.field_id,
            None,
            "Không có",
            ToolProfileValueSource.SAFE_DEFAULT,
            strategy_safe_source_id(descriptor.field_id),
            EffectiveValueValidation.BLOCKED,
            EffectiveValueMode.AUTOMATIC,
            blocked_reason,
            DependencyFingerprint.from_payload(
                {
                    "field_id": descriptor.field_id,
                    "blocked": True,
                    "invalid_reasons": invalid_reasons,
                }
            ),
        )


def strategy_safe_source_id(field_id: str) -> str:
    return f"strategy-safe-default:{field_id}"


@dataclass(frozen=True, slots=True)
class ToolProfileDiffEntry:
    field_id: str
    display_name_vi: str
    kind: ToolProfileDiffKind
    previous_value: ProfilePrimitive
    candidate_value: ProfilePrimitive
    reason_vi: str


@dataclass(frozen=True, slots=True)
class ToolProfileSavePreview:
    """Immutable preview that can be confirmed through the application service."""

    tool_id: ToolDefinitionId
    strategy_id: str
    display_name: str
    mode: ToolProfileSaveMode
    entries: tuple[ToolProfileDiffEntry, ...]
    profile_id: ToolProgramProfileId | None = None

    @property
    def accepted_values(self) -> tuple[ToolProfileValue, ...]:
        return tuple(
            ToolProfileValue(item.field_id, item.candidate_value)
            for item in self.entries
            if item.kind
            in {
                ToolProfileDiffKind.ADD,
                ToolProfileDiffKind.CHANGE,
                ToolProfileDiffKind.UNCHANGED,
            }
        )


def preview_tool_profile_capture(
    tool: ToolProfileToolContext,
    strategy_id: str,
    display_name: str,
    current_values: Mapping[str, object],
    *,
    overridden_field_ids: frozenset[str] = frozenset(),
    mode: ToolProfileSaveMode = ToolProfileSaveMode.OVERRIDES_ONLY,
    profile_id: ToolProgramProfileId | None = None,
    source_unit: LengthUnit | None = None,
    registry: ToolProfileSchemaRegistry,
) -> ToolProfileSavePreview:
    """Classify a sparse save without mutating Tool or operation state."""
    schema = registry.schema(strategy_id)
    existing = next(
        (
            item
            for item in tool.program_profiles
            if item.strategy_id == strategy_id
            and (profile_id is None or item.profile_id == profile_id)
        ),
        None,
    )
    previous = existing.sparse_mapping if existing is not None else {}
    entries: list[ToolProfileDiffEntry] = []
    for descriptor in schema.fields:
        raw = current_values.get(
            descriptor.field_id,
            current_values.get(descriptor.operation_field_id, _MISSING),
        )
        selected = (
            mode is ToolProfileSaveMode.ALL_EFFECTIVE
            or descriptor.field_id in overridden_field_ids
            or descriptor.operation_field_id in overridden_field_ids
        )
        if not selected:
            entries.append(
                ToolProfileDiffEntry(
                    descriptor.field_id,
                    descriptor.display_name_vi,
                    ToolProfileDiffKind.SKIPPED,
                    previous.get(descriptor.field_id),
                    None,
                    "Không thuộc các trường người dùng đã tùy chỉnh.",
                )
            )
            continue
        if raw is _MISSING:
            entries.append(
                ToolProfileDiffEntry(
                    descriptor.field_id,
                    descriptor.display_name_vi,
                    ToolProfileDiffKind.SKIPPED,
                    previous.get(descriptor.field_id),
                    None,
                    "Nguyên công hiện tại không cung cấp trường này.",
                )
            )
            continue
        try:
            candidate = descriptor.deserialize(raw, source_unit=source_unit)
        except CamValidationError as error:
            entries.append(
                ToolProfileDiffEntry(
                    descriptor.field_id,
                    descriptor.display_name_vi,
                    ToolProfileDiffKind.INVALID,
                    previous.get(descriptor.field_id),
                    None,
                    str(error),
                )
            )
            continue
        old = previous.get(descriptor.field_id, _MISSING)
        kind = (
            ToolProfileDiffKind.ADD
            if old is _MISSING
            else ToolProfileDiffKind.UNCHANGED
            if old == candidate
            else ToolProfileDiffKind.CHANGE
        )
        entries.append(
            ToolProfileDiffEntry(
                descriptor.field_id,
                descriptor.display_name_vi,
                kind,
                None if old is _MISSING else old,
                candidate,
                {
                    ToolProfileDiffKind.ADD: "Thêm mới.",
                    ToolProfileDiffKind.CHANGE: "Thay đổi giá trị đã lưu.",
                    ToolProfileDiffKind.UNCHANGED: "Giữ nguyên.",
                }[kind],
            )
        )
    return ToolProfileSavePreview(
        tool.tool_id,
        strategy_id,
        _display_name(display_name, "Tool profile display name"),
        mode,
        tuple(entries),
        existing.profile_id if existing is not None else profile_id,
    )


def build_profile_from_preview(
    tool: ToolProfileToolContext,
    preview: ToolProfileSavePreview,
    *,
    holder_fingerprint: ContentFingerprint | None = None,
    now: datetime | None = None,
) -> ToolProgramProfile:
    """Create the confirmed immutable profile; no artifacts or safety state enter it."""
    existing = next(
        (
            item
            for item in tool.program_profiles
            if item.profile_id == preview.profile_id
        ),
        None,
    )
    timestamp = _timestamp(now or utc_profile_now(), "Tool profile update")
    if existing is not None and timestamp < existing.updated_at:
        timestamp = existing.updated_at
    previous_values = existing.sparse_mapping if existing is not None else {}
    selected = {item.field_id: item.value for item in preview.accepted_values}
    if preview.mode is ToolProfileSaveMode.OVERRIDES_ONLY:
        values = selected
    else:
        values = {**previous_values, **selected}
    return ToolProgramProfile(
        existing.profile_id if existing is not None else ToolProgramProfileId.new(),
        tool.tool_id,
        preview.strategy_id,
        preview.display_name,
        True,
        DEFAULT_TOOL_PROFILE_REGISTRY.schema(
            preview.strategy_id
        ).profile_schema_version,
        tuple(
            ToolProfileValue(field_id, value)
            for field_id, value in sorted(values.items())
        ),
        existing.created_at if existing is not None else timestamp,
        timestamp,
        tool.revision,
        tool.content_fingerprint,
        existing.revision.next() if existing is not None else Revision(0),
        holder_fingerprint,
        ToolProfileValidationState.CONFIGURED,
    )


def duplicate_tool_program_profile(
    profile: ToolProgramProfile,
    *,
    new_tool_id: ToolDefinitionId,
    now: datetime | None = None,
) -> ToolProgramProfile:
    """Copy one profile with a new identity and disabled ambiguity-safe state."""
    timestamp = _timestamp(now or utc_profile_now(), "Tool profile copy")
    return replace(
        profile,
        profile_id=ToolProgramProfileId.new(),
        tool_id=new_tool_id,
        display_name=f"{profile.display_name} — Bản sao",
        enabled=False,
        created_at=timestamp,
        updated_at=timestamp,
        revision=Revision(0),
    )


def _number(
    field_id: str,
    operation_field_id: str,
    name: str,
    unit: str,
    *,
    minimum: float,
    maximum: float | None = None,
    common_default_key: str | None = None,
    override_flag_id: str | None = None,
    advanced: bool = False,
    safety: ToolProfileSafetyClass = ToolProfileSafetyClass.CALCULATION,
) -> ToolProfileFieldDescriptor:
    return ToolProfileFieldDescriptor(
        field_id,
        operation_field_id,
        name,
        ToolProfileFieldType.NUMBER,
        unit,
        minimum,
        maximum,
        safety_classification=safety,
        common_default_key=common_default_key,
        override_flag_id=override_flag_id,
        advanced=advanced,
    )


def _enum(
    field_id: str,
    operation_field_id: str,
    name: str,
    values: tuple[str, ...],
    display_names_vi: tuple[str, ...],
    *,
    common_default_key: str | None = None,
    safe_default: ProfilePrimitive | object = _MISSING,
    override_flag_id: str | None = None,
    advanced: bool = False,
    safety: ToolProfileSafetyClass = ToolProfileSafetyClass.PROCESS,
) -> ToolProfileFieldDescriptor:
    return ToolProfileFieldDescriptor(
        field_id,
        operation_field_id,
        name,
        ToolProfileFieldType.ENUM,
        enum_values=values,
        enum_display_names_vi=display_names_vi,
        safety_classification=safety,
        common_default_key=common_default_key,
        safe_default=safe_default,
        override_flag_id=override_flag_id,
        advanced=advanced,
    )


Z_LEVEL_TOOL_PROFILE_SCHEMA = ToolStrategyProfileSchema(
    "z_level_finishing_3d",
    "Gia công tinh theo cao độ Z",
    1,
    ("ball_end_mill",),
    (
        _enum(
            "quality_profile",
            "quality_profile",
            "Chất lượng",
            ("fast", "balanced", "high"),
            ("Nhanh", "Cân bằng", "Chất lượng cao"),
            common_default_key="quality_profile",
            safe_default="balanced",
        ),
        _number(
            "stepdown_mm",
            "stepdown_mm",
            "Bước xuống",
            "mm",
            minimum=1.0e-6,
            override_flag_id="stepdown_override_enabled",
        ),
        _number(
            "tolerance_mm",
            "tolerance_mm",
            "Dung sai",
            "mm",
            minimum=1.0e-6,
            override_flag_id="tolerance_override_enabled",
            advanced=True,
        ),
        _number(
            "surface_allowance_mm",
            "surface_allowance_mm",
            "Lượng dư",
            "mm",
            minimum=0.0,
            override_flag_id="allowance_override_enabled",
            advanced=True,
        ),
        _enum(
            "linking_mode",
            "linking_mode",
            "Liên kết",
            ("retract_clearance", "conservative_direct"),
            ("Rút dao về cao độ an toàn", "Nối trực tiếp có kiểm tra"),
            override_flag_id="linking_override_enabled",
            advanced=True,
            safety=ToolProfileSafetyClass.LINKING,
        ),
        _enum(
            "approach_retract_policy",
            "approach_retract_policy",
            "Tiếp cận và rút dao",
            ("retract_then_rapid",),
            ("Rút dao rồi chạy nhanh",),
            override_flag_id="approach_override_enabled",
            advanced=True,
            safety=ToolProfileSafetyClass.LINKING,
        ),
    ),
)


PARALLEL_TOOL_PROFILE_SCHEMA = ToolStrategyProfileSchema(
    "parallel_finishing_3d",
    "Gia công tinh song song",
    1,
    ("ball_end_mill",),
    (
        _enum(
            "quality_profile",
            "quality_profile",
            "Chất lượng",
            ("fast", "balanced", "high"),
            ("Nhanh", "Cân bằng", "Chất lượng cao"),
            common_default_key="quality_profile",
            safe_default="balanced",
        ),
        _number(
            "stepover_mm",
            "stepover_mm",
            "Bước ngang",
            "mm",
            minimum=1.0e-6,
            override_flag_id="stepover_override_enabled",
        ),
        _number(
            "direction_angle_degrees",
            "direction_angle_degrees",
            "Góc chạy",
            "°",
            minimum=-360.0,
            maximum=360.0,
            override_flag_id="direction_override_enabled",
        ),
        _number(
            "tolerance_mm",
            "tolerance_mm",
            "Dung sai",
            "mm",
            minimum=1.0e-6,
            override_flag_id="tolerance_override_enabled",
            advanced=True,
        ),
        _number(
            "surface_allowance_mm",
            "surface_allowance_mm",
            "Lượng dư",
            "mm",
            minimum=0.0,
            override_flag_id="allowance_override_enabled",
            advanced=True,
        ),
        _enum(
            "cut_direction",
            "cut_direction",
            "Thứ tự cắt",
            ("one_way", "zigzag"),
            ("Một chiều", "Zigzag"),
            override_flag_id="ordering_override_enabled",
            advanced=True,
        ),
        _enum(
            "linking_mode",
            "linking_mode",
            "Liên kết",
            ("retract_between_segments",),
            ("Rút dao giữa các đoạn",),
            advanced=True,
            safety=ToolProfileSafetyClass.LINKING,
        ),
    ),
)


DRILLING_TOOL_PROFILE_SCHEMA = ToolStrategyProfileSchema(
    "drilling_v1",
    "Khoan",
    1,
    ("drill", "center_drill"),
    (
        _number(
            "peck_depth_mm",
            "peck_depth",
            "Chiều sâu phá phoi",
            "mm",
            minimum=1.0e-6,
        ),
        _number(
            "dwell_seconds",
            "dwell_seconds",
            "Dừng ở đáy",
            "s",
            minimum=0.0,
            advanced=True,
        ),
        _enum(
            "retract_policy",
            "retract_policy",
            "Mức rút giữa lần phá phoi",
            ("retract_height", "clearance_height"),
            ("Mức rút dao", "Cao độ an toàn"),
            advanced=True,
            safety=ToolProfileSafetyClass.LINKING,
        ),
    ),
)


DEFAULT_TOOL_PROFILE_REGISTRY = ToolProfileSchemaRegistry(
    (
        Z_LEVEL_TOOL_PROFILE_SCHEMA,
        PARALLEL_TOOL_PROFILE_SCHEMA,
        DRILLING_TOOL_PROFILE_SCHEMA,
    )
)
DEFAULT_TOOL_PROFILE_RESOLVER = ToolProfileResolver(
    DEFAULT_TOOL_PROFILE_REGISTRY
)


__all__ = [
    "DEFAULT_TOOL_PROFILE_REGISTRY",
    "DEFAULT_TOOL_PROFILE_RESOLVER",
    "DRILLING_TOOL_PROFILE_SCHEMA",
    "EffectiveToolValue",
    "EffectiveValueMode",
    "EffectiveValueValidation",
    "PARALLEL_TOOL_PROFILE_SCHEMA",
    "ProfilePrimitive",
    "ToolCommonDefaults",
    "ToolProfileCompatibility",
    "ToolProfileDiffEntry",
    "ToolProfileDiffKind",
    "ToolProfileFieldDescriptor",
    "ToolProfileFieldType",
    "ToolProfileListState",
    "ToolProfileResolver",
    "ToolProfileSafetyClass",
    "ToolProfileSaveMode",
    "ToolProfileSavePreview",
    "ToolProfileSchemaRegistry",
    "ToolProfileValidationState",
    "ToolProfileValue",
    "ToolProfileValueSource",
    "ToolProgramProfile",
    "ToolProfileResolution",
    "ToolStrategyProfileSchema",
    "Z_LEVEL_TOOL_PROFILE_SCHEMA",
    "assess_tool_program_profile",
    "build_profile_from_preview",
    "duplicate_tool_program_profile",
    "preview_tool_profile_capture",
    "utc_profile_now",
]
