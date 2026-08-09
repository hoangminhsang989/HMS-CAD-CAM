"""Adapters between optional Tool profiles and automatic CAM parameters."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

from hms_cadcam.cam.automatic_parameters import (
    AutomaticParameterContract,
    AutomaticParameterMode,
    AutomaticParameterStatus,
    AutomaticValidationResult,
    CamQualityProfile,
)
from hms_cadcam.cam.domain import (
    DEFAULT_TOOL_PROFILE_REGISTRY,
    DEFAULT_TOOL_PROFILE_RESOLVER,
    ContentFingerprint,
    EffectiveValueValidation,
    ToolDefinition,
    ToolProfileResolution,
    ToolProfileValueSource,
    ToolProgramProfileId,
)


@dataclass(frozen=True, slots=True)
class ToolProfileApplication:
    """One resolver result projected into typed contract and editor values."""

    resolution: ToolProfileResolution
    contract: AutomaticParameterContract | None
    editor_values: tuple[tuple[str, object], ...]
    winning_sources: tuple[ToolProfileValueSource, ...]

    def value(self, field_id: str) -> object:
        """Return one effective editor value by its stable profile field id."""
        try:
            return next(value for key, value in self.editor_values if key == field_id)
        except StopIteration as error:
            raise KeyError(field_id) from error


def resolve_tool_profile_application(
    tool: ToolDefinition,
    strategy_id: str,
    *,
    automatic_values: Mapping[str, object],
    operation_overrides: Mapping[str, object] | None = None,
    contract: AutomaticParameterContract | None = None,
    profile_id: ToolProgramProfileId | None = None,
    operation_id: str = "",
    automatic_policy_id: str = "",
    holder_fingerprint: ContentFingerprint | None = None,
) -> ToolProfileApplication:
    """Resolve once, then project the winners without reimplementing precedence."""
    resolution = DEFAULT_TOOL_PROFILE_RESOLVER.resolve(
        tool,
        strategy_id,
        operation_overrides=operation_overrides,
        automatic_values=automatic_values,
        profile_id=profile_id,
        operation_id=operation_id,
        automatic_policy_id=automatic_policy_id,
        holder_fingerprint=holder_fingerprint,
    )
    effective = tuple(
        (item.field_id, item.canonical_value)
        for item in resolution.values
        if item.validation_status is not EffectiveValueValidation.BLOCKED
    )
    sources = tuple(
        sorted(
            {
                item.source
                for item in resolution.values
                if item.validation_status is not EffectiveValueValidation.BLOCKED
            },
            key=lambda source: source.value,
        )
    )
    applied_contract = (
        None if contract is None else _apply_resolution_to_contract(contract, resolution)
    )
    return ToolProfileApplication(resolution, applied_contract, effective, sources)


def resolve_editor_tool_profile_application(
    tool: ToolDefinition,
    strategy_id: str,
    editor_values: Mapping[str, object],
    *,
    profile_id: ToolProgramProfileId | None = None,
    operation_id: str = "",
    holder_fingerprint: ContentFingerprint | None = None,
) -> ToolProfileApplication:
    """Map an existing editor schema into the authoritative profile resolver."""
    schema = DEFAULT_TOOL_PROFILE_REGISTRY.schema(strategy_id)
    automatic_values: dict[str, object] = {}
    operation_overrides: dict[str, object] = {}
    for field in schema.fields:
        if field.field_id not in editor_values:
            continue
        value = editor_values[field.field_id]
        if field.override_flag_id is not None and bool(
            editor_values.get(field.override_flag_id, False)
        ):
            operation_overrides[field.field_id] = value
        else:
            automatic_values[field.field_id] = value
    return resolve_tool_profile_application(
        tool,
        strategy_id,
        automatic_values=automatic_values,
        operation_overrides=operation_overrides,
        profile_id=profile_id,
        operation_id=operation_id,
        holder_fingerprint=holder_fingerprint,
    )


def apply_tool_profile_to_automatic_contract(
    contract: AutomaticParameterContract,
    tool: ToolDefinition,
    strategy_id: str,
    *,
    operation_override_keys: frozenset[str],
    operation_id: str,
    profile_id: ToolProgramProfileId | None = None,
    holder_fingerprint: ContentFingerprint | None = None,
) -> AutomaticParameterContract:
    """Overlay valid profile/common values while preserving operation overrides."""
    schema = DEFAULT_TOOL_PROFILE_REGISTRY.schema(strategy_id)
    supported = {item.field_id for item in schema.fields}
    automatic_values = {
        item.key: item.resolved_value
        for item in contract.values
        if item.key in supported
    }
    if "quality_profile" in supported:
        automatic_values["quality_profile"] = contract.quality_profile.value
    operation_values = {
        item.key: item.override_value
        for item in contract.values
        if item.key in supported
        and item.key in operation_override_keys
        and item.mode is AutomaticParameterMode.MANUAL
    }
    application = resolve_tool_profile_application(
        tool,
        strategy_id,
        automatic_values=automatic_values,
        operation_overrides=operation_values,
        contract=contract,
        profile_id=profile_id,
        operation_id=operation_id,
        automatic_policy_id=contract.policy_key,
        holder_fingerprint=holder_fingerprint,
    )
    assert application.contract is not None
    return application.contract


def _apply_resolution_to_contract(
    contract: AutomaticParameterContract,
    resolution: ToolProfileResolution,
) -> AutomaticParameterContract:
    """Apply already-resolved winners to their canonical typed locations."""
    quality_profile = contract.quality_profile
    try:
        quality = resolution.value("quality_profile")
    except KeyError:
        pass
    else:
        if quality.validation_status is not EffectiveValueValidation.BLOCKED:
            quality_profile = CamQualityProfile(str(quality.canonical_value))

    values = []
    for item in contract.values:
        try:
            effective = resolution.value(item.key)
        except KeyError:
            values.append(item)
            continue
        if (
            effective.validation_status is EffectiveValueValidation.BLOCKED
            or effective.source is ToolProfileValueSource.OPERATION_OVERRIDE
            or effective.source is ToolProfileValueSource.AUTOMATIC_POLICY
        ):
            values.append(item)
            continue
        values.append(
            replace(
                item,
                mode=AutomaticParameterMode.AUTO,
                resolved_value=effective.canonical_value,
                source={
                    ToolProfileValueSource.TOOL_PROGRAM_PROFILE: (
                        "Cấu hình Tool theo chương trình"
                    ),
                    ToolProfileValueSource.TOOL_COMMON_DEFAULT: (
                        "Cấu hình cơ bản của Tool"
                    ),
                    ToolProfileValueSource.SAFE_DEFAULT: (
                        "Giá trị an toàn mặc định"
                    ),
                }.get(effective.source, item.source),
                dependency_fingerprint=effective.dependency_contribution,
                status=AutomaticParameterStatus.RESOLVED,
                reason=effective.reason_vi,
                override_value=None,
                validation=AutomaticValidationResult(True),
            )
        )
    return replace(
        contract,
        quality_profile=quality_profile,
        values=tuple(values),
    )


__all__ = [
    "ToolProfileApplication",
    "apply_tool_profile_to_automatic_contract",
    "resolve_editor_tool_profile_application",
    "resolve_tool_profile_application",
]
