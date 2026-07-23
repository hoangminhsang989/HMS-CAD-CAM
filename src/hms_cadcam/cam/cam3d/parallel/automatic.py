"""Deterministic automatic-parameter policy for Parallel Finishing.

This module consumes immutable, OCP-free evidence.  It does not generate a
toolpath and does not weaken the existing Parallel safety validator.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from collections.abc import Mapping

from hms_cadcam.cam.automatic_parameters import (
    AutomaticParameterContract,
    AutomaticParameterMode,
    AutomaticParameterStatus,
    AutomaticParameterValue,
    AutomaticValidationResult,
    CamQualityProfile,
)
from hms_cadcam.cam.domain import (
    ContentFingerprint,
    DependencyFingerprint,
    GeometryFingerprint,
)

from .models import ParallelCutDirection, ParallelLinkingMode


PARALLEL_AUTOMATIC_POLICY_KEY = "parallel.finishing.automatic"
PARALLEL_AUTOMATIC_POLICY_VERSION = 1
PARALLEL_AUTOMATIC_PARAMETER_KEYS = (
    "cut_direction",
    "direction_angle_degrees",
    "holder_context",
    "linking_mode",
    "stepover_mm",
    "surface_allowance_mm",
    "tolerance_mm",
)
_PASS_GUARDRAIL = 20_000


@dataclass(frozen=True, slots=True)
class ParallelGeometryEvidence:
    """Selected-region extents already projected into Setup U/V coordinates."""

    u_min: float
    u_max: float
    v_min: float
    v_max: float
    source: str = "Hộp bao các bề mặt đã chọn"

    def __post_init__(self) -> None:
        values = (self.u_min, self.u_max, self.v_min, self.v_max)
        if not all(type(value) is float and math.isfinite(value) for value in values):
            raise ValueError("Parallel geometry evidence must be finite")
        if self.u_min > self.u_max or self.v_min > self.v_max:
            raise ValueError("Parallel geometry evidence extents are reversed")
        if not isinstance(self.source, str) or not self.source.strip():
            raise ValueError("Parallel geometry evidence source is required")

    @property
    def u_span(self) -> float:
        return self.u_max - self.u_min

    @property
    def v_span(self) -> float:
        return self.v_max - self.v_min

    def to_dict(self) -> dict[str, object]:
        return {
            "u_min": self.u_min,
            "u_max": self.u_max,
            "v_min": self.v_min,
            "v_max": self.v_max,
            "source": self.source,
        }


@dataclass(frozen=True, slots=True)
class ParallelAutomaticContext:
    """Stable dependencies required by the Parallel automatic policy."""

    geometry_fingerprint: GeometryFingerprint
    selection_fingerprint: DependencyFingerprint
    setup_fingerprint: DependencyFingerprint
    tool_fingerprint: ContentFingerprint
    holder_fingerprint: ContentFingerprint | None
    tool_diameter_mm: float
    declared_tolerance_mm: float
    declared_allowance_mm: float
    surface_count: int
    geometry_evidence: ParallelGeometryEvidence | None = None
    tool_supported: bool = True

    def __post_init__(self) -> None:
        fingerprints = (
            self.geometry_fingerprint,
            self.selection_fingerprint,
            self.setup_fingerprint,
            self.tool_fingerprint,
        )
        if not isinstance(self.geometry_fingerprint, GeometryFingerprint) or not all(
            isinstance(item, (ContentFingerprint, DependencyFingerprint))
            for item in fingerprints[1:]
        ):
            raise TypeError("Parallel automatic fingerprints are invalid")
        if self.holder_fingerprint is not None and not isinstance(
            self.holder_fingerprint, ContentFingerprint
        ):
            raise TypeError("Parallel automatic holder fingerprint is invalid")
        for name in (
            "tool_diameter_mm",
            "declared_tolerance_mm",
            "declared_allowance_mm",
        ):
            value = getattr(self, name)
            if type(value) is not float or not math.isfinite(value):
                raise ValueError(f"{name} must be finite")
        if self.tool_diameter_mm <= 0.0 or self.declared_tolerance_mm <= 0.0:
            raise ValueError("Parallel automatic tool/tolerance is invalid")
        if self.declared_allowance_mm < 0.0:
            raise ValueError("Parallel automatic allowance is invalid")
        if type(self.surface_count) is not int or self.surface_count < 0:
            raise ValueError("Parallel automatic surface count is invalid")
        if not isinstance(self.tool_supported, bool):
            raise TypeError("Parallel automatic tool support flag is invalid")
        if self.geometry_evidence is not None and not isinstance(
            self.geometry_evidence, ParallelGeometryEvidence
        ):
            raise TypeError("Parallel automatic geometry evidence is invalid")


def _dependency(
    context: ParallelAutomaticContext,
    key: str,
    profile: CamQualityProfile,
) -> DependencyFingerprint:
    payload: dict[str, object] = {
        "policy": PARALLEL_AUTOMATIC_POLICY_KEY,
        "policy_version": PARALLEL_AUTOMATIC_POLICY_VERSION,
        "parameter": key,
    }
    if key in {"direction_angle_degrees", "cut_direction"}:
        payload.update(
            geometry=context.geometry_fingerprint.to_dict(),
            selection=context.selection_fingerprint.to_dict(),
            setup=context.setup_fingerprint.to_dict(),
        )
    elif key == "stepover_mm":
        payload.update(
            geometry=context.geometry_fingerprint.to_dict(),
            tool=context.tool_fingerprint.to_dict(),
            diameter=context.tool_diameter_mm,
            quality=profile.value,
        )
    elif key == "tolerance_mm":
        payload.update(
            tool=context.tool_fingerprint.to_dict(),
            diameter=context.tool_diameter_mm,
            declared=context.declared_tolerance_mm,
            quality=profile.value,
        )
    elif key == "surface_allowance_mm":
        payload.update(
            geometry=context.geometry_fingerprint.to_dict(),
            declared=context.declared_allowance_mm,
        )
    elif key == "holder_context":
        payload.update(
            tool=context.tool_fingerprint.to_dict(),
            holder=(
                context.holder_fingerprint.to_dict()
                if context.holder_fingerprint is not None
                else None
            ),
        )
    else:
        payload.update(
            setup=context.setup_fingerprint.to_dict(),
            geometry=context.geometry_fingerprint.to_dict(),
        )
    return DependencyFingerprint.from_payload(payload)


def _stored_resolution(
    stored: AutomaticParameterContract | None,
    key: str,
    dependency: DependencyFingerprint,
) -> AutomaticParameterValue | None:
    if (
        stored is None
        or stored.policy_key != PARALLEL_AUTOMATIC_POLICY_KEY
        or stored.policy_version != PARALLEL_AUTOMATIC_POLICY_VERSION
    ):
        return None
    try:
        value = stored.value(key)
    except KeyError:
        return None
    return value if value.dependency_fingerprint == dependency else None


def _numeric_override(
    value: object,
    *,
    minimum: float,
    maximum: float,
    label: str,
) -> tuple[object, AutomaticValidationResult]:
    if isinstance(value, bool):
        return value, AutomaticValidationResult(False, f"{label} phải là một số hữu hạn.")
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return value, AutomaticValidationResult(False, f"{label} phải là một số hữu hạn.")
    if not math.isfinite(number):
        return value, AutomaticValidationResult(False, f"{label} phải là một số hữu hạn.")
    if number < minimum or number > maximum:
        return value, AutomaticValidationResult(
            False, f"{label} phải nằm trong khoảng {minimum:g} đến {maximum:g}."
        )
    return number, AutomaticValidationResult(True)


def _manual_state(
    key: str,
    stored: AutomaticParameterContract | None,
    manual_flags: Mapping[str, bool],
    override_values: Mapping[str, object],
) -> tuple[AutomaticParameterMode, object]:
    previous: AutomaticParameterValue | None = None
    if stored is not None:
        try:
            previous = stored.value(key)
        except KeyError:
            previous = None
    manual = manual_flags.get(
        key, previous is not None and previous.mode is AutomaticParameterMode.MANUAL
    )
    override = override_values.get(
        key, previous.override_value if previous is not None else None
    )
    return (
        AutomaticParameterMode.MANUAL if manual else AutomaticParameterMode.AUTO,
        override,
    )


def _automatic_direction(
    context: ParallelAutomaticContext,
    previous: AutomaticParameterValue | None,
    profile: CamQualityProfile,
) -> tuple[float, str, AutomaticParameterStatus, str, float | None]:
    evidence = context.geometry_evidence
    if evidence is not None and (evidence.u_span > 0.0 or evidence.v_span > 0.0):
        estimate_spacing = context.tool_diameter_mm * {
            CamQualityProfile.FAST: 0.2,
            CamQualityProfile.BALANCED: 0.1,
            CamQualityProfile.HIGH: 0.05,
        }[profile]
        passes_along_u = max(1, math.ceil(evidence.v_span / estimate_spacing) + 1)
        passes_along_v = max(1, math.ceil(evidence.u_span / estimate_spacing) + 1)
        # Both candidates retain conservative retract linking.  The secondary
        # term estimates non-cutting/link events; equal cost deterministically
        # selects Setup U.
        cost_along_u = passes_along_u + max(0, passes_along_u - 1) * 0.25
        cost_along_v = passes_along_v + max(0, passes_along_v - 1) * 0.25
        angle = 0.0 if cost_along_u <= cost_along_v else 90.0
        cross_span = evidence.v_span if angle == 0.0 else evidence.u_span
        reason = (
            f"Trục chạy dao theo chiều chính dài hơn của vùng chọn "
            f"(U={evidence.u_span:g} mm, V={evidence.v_span:g} mm); "
            f"ước tính {passes_along_u} lượt theo U so với {passes_along_v} lượt theo V, "
            "kèm chi phí liên kết rút dao bảo thủ."
        )
        return angle, evidence.source, AutomaticParameterStatus.RESOLVED, reason, cross_span
    if previous is not None:
        return (
            float(previous.resolved_value),
            previous.source,
            previous.status,
            previous.reason,
            None,
        )
    return (
        0.0,
        "Trục X của Thiết lập",
        AutomaticParameterStatus.NEEDS_CONFIRMATION,
        "Chưa có hộp bao mặt được chọn; HMS dùng trục X của Thiết lập và yêu cầu người dùng xác nhận.",
        None,
    )


def _automatic_stepover(
    context: ParallelAutomaticContext,
    profile: CamQualityProfile,
    cross_span: float | None,
) -> tuple[float, str, str]:
    radius = context.tool_diameter_mm / 2.0
    height_fraction = {
        CamQualityProfile.FAST: 0.0025,
        CamQualityProfile.BALANCED: 0.001,
        CamQualityProfile.HIGH: 0.00025,
    }[profile]
    target_height = max(1.0e-6, context.tool_diameter_mm * height_fraction)
    stepover = 2.0 * math.sqrt(max(0.0, 2.0 * radius * target_height - target_height**2))
    stepover = min(context.tool_diameter_mm, max(1.0e-6, stepover))
    guardrail_note = ""
    if cross_span is not None and cross_span > 0.0:
        minimum_for_guardrail = cross_span / float(_PASS_GUARDRAIL - 1)
        if stepover < minimum_for_guardrail:
            stepover = min(context.tool_diameter_mm, minimum_for_guardrail)
            guardrail_note = " Đã tăng để không vượt giới hạn 20.000 lượt cắt."
    return (
        stepover,
        "Đường kính dao cầu + hồ sơ chất lượng",
        (
            f"Tính từ D={context.tool_diameter_mm:g} mm, bán kính dao và hệ số chất lượng "
            f"của hồ sơ {profile.value}; đây không phải cam kết độ nhám.{guardrail_note}"
        ),
    )


def _automatic_cut_direction(
    context: ParallelAutomaticContext,
    *,
    direction_angle_degrees: float,
    stepover_mm: float,
) -> tuple[str, str, AutomaticParameterStatus, str]:
    """Choose pass ordering from deterministic non-cutting cost estimates."""
    evidence = context.geometry_evidence
    if evidence is None or context.surface_count == 0:
        return (
            ParallelCutDirection.ONE_WAY.value,
            "Chính sách dự phòng khi thiếu dữ liệu hình học",
            AutomaticParameterStatus.NEEDS_CONFIRMATION,
            "Chưa có hình học bao; dùng Một chiều cho đến khi chọn bề mặt.",
        )

    along_span = (
        evidence.u_span
        if math.isclose(direction_angle_degrees, 0.0, abs_tol=1.0e-9)
        else evidence.v_span
    )
    cross_span = (
        evidence.v_span
        if math.isclose(direction_angle_degrees, 0.0, abs_tol=1.0e-9)
        else evidence.u_span
    )
    pass_count = (
        max(1, math.ceil(cross_span / stepover_mm) + 1)
        if cross_span > 0.0
        else 1
    )
    transition_count = max(0, pass_count - 1)
    # Both candidates use the same conservative retract/safety pipeline. A
    # one-way transition returns across the machining span before approaching
    # the next pass; zigzag only advances to the adjacent pass. This remains a
    # topology-bounds estimate, not a safety claim.
    one_way_non_cutting_mm = transition_count * math.hypot(
        along_span, stepover_mm
    )
    zigzag_non_cutting_mm = transition_count * stepover_mm
    if zigzag_non_cutting_mm < one_way_non_cutting_mm:
        value = ParallelCutDirection.ZIGZAG.value
        label = "Zíc zắc"
        selected_cost = zigzag_non_cutting_mm
    else:
        value = ParallelCutDirection.ONE_WAY.value
        label = "Một chiều"
        selected_cost = one_way_non_cutting_mm
    return (
        value,
        "Ước tính cấu trúc từ hình học bao và chính sách rút dao",
        AutomaticParameterStatus.RESOLVED,
        (
            f"Chọn {label} cho khoảng {pass_count} lượt cắt; quãng chạy không cắt "
            f"ước tính {selected_cost:g} mm so với Một chiều "
            f"{one_way_non_cutting_mm:g} mm và Zíc zắc {zigzag_non_cutting_mm:g} mm. "
            "Mọi liên kết vẫn rút dao và phải qua kiểm tra an toàn hiện hữu."
        ),
    )


def resolve_parallel_automatic_contract(
    context: ParallelAutomaticContext,
    quality_profile: CamQualityProfile,
    *,
    stored: AutomaticParameterContract | None = None,
    manual_flags: Mapping[str, bool] | None = None,
    override_values: Mapping[str, object] | None = None,
) -> AutomaticParameterContract:
    """Resolve effective Parallel values and preserve every manual draft value."""
    flags = manual_flags or {}
    overrides = override_values or {}
    dependencies = {
        key: _dependency(context, key, quality_profile)
        for key in PARALLEL_AUTOMATIC_PARAMETER_KEYS
    }
    previous_direction = _stored_resolution(
        stored, "direction_angle_degrees", dependencies["direction_angle_degrees"]
    )
    direction, direction_source, direction_status, direction_reason, cross_span = (
        _automatic_direction(context, previous_direction, quality_profile)
    )
    stepover, stepover_source, stepover_reason = _automatic_stepover(
        context, quality_profile, cross_span
    )
    tolerance_target = context.tool_diameter_mm * {
        CamQualityProfile.FAST: 0.002,
        CamQualityProfile.BALANCED: 0.001,
        CamQualityProfile.HIGH: 0.0005,
    }[quality_profile]
    tolerance = min(context.declared_tolerance_mm, max(1.0e-6, tolerance_target))
    allowance = context.declared_allowance_mm
    cut_direction, cut_direction_source, cut_direction_status, cut_direction_reason = (
        _automatic_cut_direction(
            context,
            direction_angle_degrees=direction,
            stepover_mm=stepover,
        )
    )
    holder_value = (
        "Đã nhận diện holder; kiểm tra va chạm vẫn do bộ kiểm tra an toàn hiện hữu quyết định."
        if context.holder_fingerprint is not None
        else "Cụm dao không khai báo holder; phạm vi kiểm tra holder chưa được xác minh."
    )
    automatic: dict[str, tuple[object, str, AutomaticParameterStatus, str]] = {
        "direction_angle_degrees": (
            direction,
            direction_source,
            direction_status,
            direction_reason,
        ),
        "stepover_mm": (
            stepover,
            stepover_source,
            (
                AutomaticParameterStatus.RESOLVED
                if context.tool_supported
                else AutomaticParameterStatus.UNSUPPORTED
            ),
            (
                stepover_reason
                if context.tool_supported
                else "Chưa có dao cầu hợp lệ; giá trị chỉ là tạm thời và không thể áp dụng."
            ),
        ),
        "tolerance_mm": (
            tolerance,
            "Dung sai nguyên công + đường kính dao + hồ sơ chất lượng",
            AutomaticParameterStatus.RESOLVED,
            (
                f"Ưu tiên dung sai đã khai báo {context.declared_tolerance_mm:g} mm; "
                f"hồ sơ {quality_profile.value} giới hạn ở {tolerance_target:g} mm."
            ),
        ),
        "surface_allowance_mm": (
            allowance,
            "Lượng dư vùng gia công" if allowance > 0.0 else "Chính sách gia công tinh",
            AutomaticParameterStatus.RESOLVED,
            (
                f"Giữ lượng dư đã khai báo {allowance:g} mm."
                if allowance > 0.0
                else "Gia công tinh mặc định không để lượng dư bề mặt."
            ),
        ),
        "cut_direction": (
            cut_direction,
            cut_direction_source,
            cut_direction_status,
            cut_direction_reason,
        ),
        "linking_mode": (
            ParallelLinkingMode.RETRACT_BETWEEN_SEGMENTS.value,
            "Chính sách liên kết an toàn hiện hữu",
            AutomaticParameterStatus.RESOLVED,
            "Chỉ dùng rút dao giữa các đoạn; không công khai chế độ chưa được hỗ trợ.",
        ),
        "holder_context": (
            holder_value,
            "Cụm dao",
            (
                AutomaticParameterStatus.RESOLVED
                if context.holder_fingerprint is not None
                else AutomaticParameterStatus.NEEDS_CONFIRMATION
            ),
            holder_value,
        ),
    }
    values: list[AutomaticParameterValue] = []
    for key in PARALLEL_AUTOMATIC_PARAMETER_KEYS:
        resolved, source, status, reason = automatic[key]
        mode, override = _manual_state(key, stored, flags, overrides)
        validation = AutomaticValidationResult(True)
        normalized_override = override
        if key == "direction_angle_degrees":
            normalized_override, validation = _numeric_override(
                override,
                minimum=0.0,
                maximum=360.0,
                label="Góc hướng chạy dao",
            )
        elif key == "stepover_mm":
            normalized_override, validation = _numeric_override(
                override,
                minimum=1.0e-6,
                maximum=context.tool_diameter_mm,
                label="Bước ngang",
            )
        elif key == "tolerance_mm":
            normalized_override, validation = _numeric_override(
                override,
                minimum=1.0e-6,
                maximum=10.0,
                label="Dung sai",
            )
        elif key == "surface_allowance_mm":
            normalized_override, validation = _numeric_override(
                override,
                minimum=0.0,
                maximum=1_000.0,
                label="Lượng dư bề mặt",
            )
        elif key == "cut_direction" and override not in {
            ParallelCutDirection.ONE_WAY.value,
            ParallelCutDirection.ZIGZAG.value,
        }:
            validation = AutomaticValidationResult(
                False, "Thứ tự cắt thủ công không được hỗ trợ."
            )
        if mode is AutomaticParameterMode.AUTO:
            validation = AutomaticValidationResult(True)
        values.append(
            AutomaticParameterValue(
                key,
                mode,
                resolved,  # type: ignore[arg-type]
                source,
                PARALLEL_AUTOMATIC_POLICY_VERSION,
                dependencies[key],
                status,
                reason,
                normalized_override,  # type: ignore[arg-type]
                validation,
            )
        )
    return AutomaticParameterContract(
        PARALLEL_AUTOMATIC_POLICY_KEY,
        PARALLEL_AUTOMATIC_POLICY_VERSION,
        quality_profile,
        tuple(values),
    )


__all__ = [
    "PARALLEL_AUTOMATIC_PARAMETER_KEYS",
    "PARALLEL_AUTOMATIC_POLICY_KEY",
    "PARALLEL_AUTOMATIC_POLICY_VERSION",
    "ParallelAutomaticContext",
    "ParallelGeometryEvidence",
    "resolve_parallel_automatic_contract",
]
