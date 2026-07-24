"""Adapters between optional Tool profiles and automatic CAM parameters."""

from __future__ import annotations

from dataclasses import replace

from hms_cadcam.cam.automatic_parameters import (
    AutomaticParameterContract,
    AutomaticParameterMode,
    AutomaticParameterStatus,
    AutomaticValidationResult,
)
from hms_cadcam.cam.domain import (
    DEFAULT_TOOL_PROFILE_REGISTRY,
    DEFAULT_TOOL_PROFILE_RESOLVER,
    ContentFingerprint,
    EffectiveValueValidation,
    ToolDefinition,
    ToolProfileValueSource,
)


def apply_tool_profile_to_automatic_contract(
    contract: AutomaticParameterContract,
    tool: ToolDefinition,
    strategy_id: str,
    *,
    operation_override_keys: frozenset[str],
    operation_id: str,
    holder_fingerprint: ContentFingerprint | None = None,
) -> AutomaticParameterContract:
    """Overlay valid profile/common values while preserving operation overrides."""
    schema = DEFAULT_TOOL_PROFILE_REGISTRY.schema(strategy_id)
    contract_keys = {item.key for item in contract.values}
    supported = {item.field_id for item in schema.fields}
    automatic_values = {
        item.key: item.resolved_value
        for item in contract.values
        if item.key in supported
    }
    operation_values = {
        item.key: item.override_value
        for item in contract.values
        if item.key in supported
        and item.key in operation_override_keys
        and item.mode is AutomaticParameterMode.MANUAL
    }
    resolution = DEFAULT_TOOL_PROFILE_RESOLVER.resolve(
        tool,
        strategy_id,
        operation_overrides=operation_values,
        automatic_values=automatic_values,
        operation_id=operation_id,
        automatic_policy_id=contract.policy_key,
        holder_fingerprint=holder_fingerprint,
    )
    values = []
    for item in contract.values:
        if item.key not in supported or item.key not in contract_keys:
            values.append(item)
            continue
        effective = resolution.value(item.key)
        if (
            item.key in operation_override_keys
            or effective.validation_status is EffectiveValueValidation.BLOCKED
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
    return replace(contract, values=tuple(values))


__all__ = ["apply_tool_profile_to_automatic_contract"]
