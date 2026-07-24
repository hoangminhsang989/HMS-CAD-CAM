"""Deterministic automatic-parameter policy for Z-Level Finishing.

The policy consumes immutable, OCP-free evidence and produces the shared
automatic-parameter contract.  It never changes the Stage 8A.3.2 safety
semantics and does not generate toolpaths.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import math

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

from .models import (
    ZLevelBoundaryPolicy,
    ZLevelLinkingMode,
    ZLevelOrientation,
)


Z_LEVEL_AUTOMATIC_POLICY_KEY = "z_level.finishing.automatic"
Z_LEVEL_AUTOMATIC_POLICY_VERSION = 1
Z_LEVEL_AUTOMATIC_PARAMETER_KEYS = (
    "approach_retract_policy",
    "bottom_level",
    "boundary_policy",
    "contour_ordering",
    "linking_mode",
    "machining_frame",
    "maximum_segment_length_mm",
    "normal_variation_limit_degrees",
    "orientation",
    "protected_geometry_scope",
    "safety_sampling_policy",
    "safety_scope",
    "stepdown_mm",
    "surface_allowance_mm",
    "tolerance_mm",
    "top_level",
)
_LEVEL_GUARDRAIL = 20_000


@dataclass(frozen=True, slots=True)
class ZLevelGeometryEvidence:
    """Selected-region extents projected into Setup U/V/W coordinates."""

    u_min: float
    u_max: float
    v_min: float
    v_max: float
    w_min: float
    w_max: float
    source: str = "Hộp bao các bề mặt đã chọn"

    def __post_init__(self) -> None:
        values = (
            self.u_min,
            self.u_max,
            self.v_min,
            self.v_max,
            self.w_min,
            self.w_max,
        )
        if not all(type(value) is float and math.isfinite(value) for value in values):
            raise ValueError("Z-Level geometry evidence must be finite")
        if (
            self.u_min > self.u_max
            or self.v_min > self.v_max
            or self.w_min > self.w_max
        ):
            raise ValueError("Z-Level geometry evidence extents are reversed")
        if not isinstance(self.source, str) or not self.source.strip():
            raise ValueError("Z-Level geometry evidence source is required")

    @property
    def height(self) -> float:
        """Return the non-negative Setup-W span."""
        return self.w_max - self.w_min

    def to_dict(self) -> dict[str, object]:
        return {
            "u_min": self.u_min,
            "u_max": self.u_max,
            "v_min": self.v_min,
            "v_max": self.v_max,
            "w_min": self.w_min,
            "w_max": self.w_max,
            "source": self.source,
        }


@dataclass(frozen=True, slots=True)
class ZLevelAutomaticContext:
    """Stable dependencies required by the Z-Level automatic policy."""

    geometry_fingerprint: GeometryFingerprint
    selection_fingerprint: DependencyFingerprint
    setup_fingerprint: DependencyFingerprint
    tool_fingerprint: ContentFingerprint
    holder_fingerprint: ContentFingerprint | None
    tool_diameter_mm: float
    declared_tolerance_mm: float
    declared_allowance_mm: float
    surface_count: int
    geometry_evidence: ZLevelGeometryEvidence | None = None
    tool_supported: bool = True
    protected_geometry_count: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.geometry_fingerprint, GeometryFingerprint):
            raise TypeError("Z-Level geometry fingerprint is invalid")
        if not isinstance(self.selection_fingerprint, DependencyFingerprint):
            raise TypeError("Z-Level selection fingerprint is invalid")
        if not isinstance(self.setup_fingerprint, DependencyFingerprint):
            raise TypeError("Z-Level Setup fingerprint is invalid")
        if not isinstance(self.tool_fingerprint, ContentFingerprint):
            raise TypeError("Z-Level Tool fingerprint is invalid")
        if self.holder_fingerprint is not None and not isinstance(
            self.holder_fingerprint, ContentFingerprint
        ):
            raise TypeError("Z-Level Holder fingerprint is invalid")
        numeric = (
            self.tool_diameter_mm,
            self.declared_tolerance_mm,
            self.declared_allowance_mm,
        )
        if not all(type(value) is float and math.isfinite(value) for value in numeric):
            raise ValueError("Z-Level automatic numeric context is invalid")
        if self.tool_diameter_mm <= 0.0 or self.declared_tolerance_mm <= 0.0:
            raise ValueError("Z-Level automatic Tool/tolerance is invalid")
        if self.declared_allowance_mm < 0.0:
            raise ValueError("Z-Level automatic allowance is invalid")
        if type(self.surface_count) is not int or self.surface_count < 0:
            raise ValueError("Z-Level surface count is invalid")
        if (
            type(self.protected_geometry_count) is not int
            or self.protected_geometry_count < 0
        ):
            raise ValueError("Z-Level protected geometry count is invalid")
        if self.geometry_evidence is not None and not isinstance(
            self.geometry_evidence, ZLevelGeometryEvidence
        ):
            raise TypeError("Z-Level geometry evidence is invalid")


def _dependency(
    context: ZLevelAutomaticContext,
    key: str,
    profile: CamQualityProfile,
) -> DependencyFingerprint:
    payload: dict[str, object] = {
        "policy": Z_LEVEL_AUTOMATIC_POLICY_KEY,
        "policy_version": Z_LEVEL_AUTOMATIC_POLICY_VERSION,
        "parameter": key,
        "geometry": context.geometry_fingerprint.to_dict(),
        "selection": context.selection_fingerprint.to_dict(),
        "setup": context.setup_fingerprint.to_dict(),
    }
    if key in {
        "stepdown_mm",
        "tolerance_mm",
        "maximum_segment_length_mm",
        "normal_variation_limit_degrees",
        "safety_sampling_policy",
    }:
        payload.update(
            tool=context.tool_fingerprint.to_dict(),
            diameter=context.tool_diameter_mm,
            quality=profile.value,
        )
    if key in {"safety_scope", "protected_geometry_scope"}:
        payload.update(
            holder=(
                context.holder_fingerprint.to_dict()
                if context.holder_fingerprint is not None
                else None
            ),
            protected_geometry_count=context.protected_geometry_count,
        )
    if key == "surface_allowance_mm":
        payload["declared_allowance_mm"] = context.declared_allowance_mm
    if key == "tolerance_mm":
        payload["declared_tolerance_mm"] = context.declared_tolerance_mm
    return DependencyFingerprint.from_payload(payload)


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
        key,
        previous is not None and previous.mode is AutomaticParameterMode.MANUAL,
    )
    override = override_values.get(
        key,
        previous.override_value if previous is not None else None,
    )
    return (
        AutomaticParameterMode.MANUAL if manual else AutomaticParameterMode.AUTO,
        override,
    )


def _numeric_override(
    value: object,
    *,
    minimum: float,
    maximum: float,
    label: str,
) -> tuple[object, AutomaticValidationResult]:
    if isinstance(value, bool):
        return value, AutomaticValidationResult(
            False, f"{label} phải là một số hữu hạn."
        )
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return value, AutomaticValidationResult(
            False, f"{label} phải là một số hữu hạn."
        )
    if not math.isfinite(number):
        return value, AutomaticValidationResult(
            False, f"{label} phải là một số hữu hạn."
        )
    if number < minimum or number > maximum:
        return value, AutomaticValidationResult(
            False, f"{label} phải nằm trong khoảng {minimum:g} đến {maximum:g}."
        )
    return number, AutomaticValidationResult(True)


def _choice_override(
    value: object,
    choices: set[str],
    label: str,
) -> AutomaticValidationResult:
    if isinstance(value, str) and value in choices:
        return AutomaticValidationResult(True)
    return AutomaticValidationResult(False, f"{label} không được hỗ trợ.")


def resolve_z_level_automatic_contract(
    context: ZLevelAutomaticContext,
    quality_profile: CamQualityProfile,
    *,
    stored: AutomaticParameterContract | None = None,
    manual_flags: Mapping[str, bool] | None = None,
    override_values: Mapping[str, object] | None = None,
) -> AutomaticParameterContract:
    """Resolve effective Z-Level values and preserve manual override intent."""
    flags = manual_flags or {}
    overrides = override_values or {}
    evidence = context.geometry_evidence
    geometry_status = (
        AutomaticParameterStatus.RESOLVED
        if evidence is not None and context.surface_count > 0
        else AutomaticParameterStatus.UNRESOLVED
    )
    top = evidence.w_max if evidence is not None else 0.0
    bottom = evidence.w_min if evidence is not None else 0.0
    height = evidence.height if evidence is not None else 0.0
    step_fraction = {
        CamQualityProfile.FAST: 0.45,
        CamQualityProfile.BALANCED: 0.30,
        CamQualityProfile.HIGH: 0.18,
    }[quality_profile]
    stepdown = max(1.0e-6, context.tool_diameter_mm * step_fraction)
    if height > 0.0:
        minimum_for_guardrail = height / float(_LEVEL_GUARDRAIL - 1)
        stepdown = max(stepdown, minimum_for_guardrail)
        stepdown = min(stepdown, height)
    tolerance_target = context.tool_diameter_mm * {
        CamQualityProfile.FAST: 0.002,
        CamQualityProfile.BALANCED: 0.001,
        CamQualityProfile.HIGH: 0.0005,
    }[quality_profile]
    tolerance = min(
        context.declared_tolerance_mm,
        max(1.0e-6, tolerance_target),
    )
    segment_length = max(
        tolerance * 2.0,
        context.tool_diameter_mm
        * {
            CamQualityProfile.FAST: 0.35,
            CamQualityProfile.BALANCED: 0.22,
            CamQualityProfile.HIGH: 0.12,
        }[quality_profile],
    )
    normal_limit = {
        CamQualityProfile.FAST: 12.0,
        CamQualityProfile.BALANCED: 8.0,
        CamQualityProfile.HIGH: 4.0,
    }[quality_profile]
    sampling = {
        CamQualityProfile.FAST: "standard",
        CamQualityProfile.BALANCED: "dense",
        CamQualityProfile.HIGH: "very_dense",
    }[quality_profile]
    holder_known = context.holder_fingerprint is not None
    safety_status = (
        AutomaticParameterStatus.RESOLVED
        if holder_known
        else AutomaticParameterStatus.NEEDS_CONFIRMATION
    )
    automatic: dict[str, tuple[object, str, AutomaticParameterStatus, str]] = {
        "machining_frame": (
            "setup_wcs",
            "Thiết lập hiện hành",
            AutomaticParameterStatus.RESOLVED,
            "Dùng hệ U/V/W cố định của Thiết lập; không thay đổi trục dao.",
        ),
        "top_level": (
            top,
            evidence.source if evidence is not None else "Chưa có hình học",
            geometry_status,
            "Lấy cao độ lớn nhất của các bề mặt đã chọn trong trục W.",
        ),
        "bottom_level": (
            bottom,
            evidence.source if evidence is not None else "Chưa có hình học",
            geometry_status,
            "Lấy cao độ nhỏ nhất của các bề mặt đã chọn trong trục W.",
        ),
        "stepdown_mm": (
            stepdown,
            "Đường kính Tool + hồ sơ chất lượng + phạm vi cao độ",
            (
                AutomaticParameterStatus.RESOLVED
                if context.tool_supported and geometry_status
                is AutomaticParameterStatus.RESOLVED
                else AutomaticParameterStatus.UNRESOLVED
            ),
            "Bước xuống được giới hạn theo đường kính dao và guardrail 20.000 lớp.",
        ),
        "tolerance_mm": (
            tolerance,
            "Dung sai vùng gia công + đường kính Tool + hồ sơ chất lượng",
            AutomaticParameterStatus.RESOLVED,
            "Không nới rộng vượt dung sai đã khai báo của vùng gia công.",
        ),
        "surface_allowance_mm": (
            context.declared_allowance_mm,
            "Lượng dư vùng gia công",
            AutomaticParameterStatus.RESOLVED,
            "Giữ nguyên lượng dư đã khai báo; mặc định gia công tinh là 0 mm.",
        ),
        "orientation": (
            ZLevelOrientation.AUTOMATIC.value,
            "Hướng đường đồng mức theo cấu trúc liên kết hình học",
            geometry_status,
            "Thuật toán xác định chiều quay theo loại vòng và trục W của Thiết lập.",
        ),
        "boundary_policy": (
            ZLevelBoundaryPolicy.TRIMMED_FACE.value,
            "Biên BRep đã chọn",
            geometry_status,
            "Giữ đường đồng mức trong miền mặt đã cắt xén.",
        ),
        "contour_ordering": (
            "top_down_nearest_safe",
            "Phạm vi cao độ + liên kết bảo thủ",
            geometry_status,
            "Xử lý từ cao xuống thấp; chỉ tối ưu thứ tự khi không làm yếu safety.",
        ),
        "linking_mode": (
            ZLevelLinkingMode.RETRACT_CLEARANCE.value,
            "Chính sách liên kết an toàn",
            AutomaticParameterStatus.RESOLVED,
            "Mặc định rút dao; liên kết trực tiếp chỉ là ứng viên và phải qua bộ kiểm tra an toàn.",
        ),
        "safety_scope": (
            "declared_geometry_and_tool_assembly",
            "Hướng dẫn an toàn giai đoạn 8A.3.2",
            safety_status,
            (
                "Dao cắt, cán dao và Holder đã khai báo thuộc phạm vi kiểm tra."
                if holder_known
                else "Thiếu Holder; safety phải giữ UNKNOWN hoặc fail-closed."
            ),
        ),
        "protected_geometry_scope": (
            (
                f"{context.protected_geometry_count} protected surface(s)"
                if context.protected_geometry_count
                else "part_boundary_only"
            ),
            "Vùng gia công",
            (
                AutomaticParameterStatus.RESOLVED
                if context.protected_geometry_count > 0
                else AutomaticParameterStatus.NEEDS_CONFIRMATION
            ),
            "Không tự suy đoán đồ gá hoặc hình học bảo vệ chưa được khai báo.",
        ),
        "approach_retract_policy": (
            "retract_then_rapid",
            "Safe motion policy",
            AutomaticParameterStatus.RESOLVED,
            "Rút dao theo W, chạy nhanh tại cao độ an toàn rồi tiếp cận.",
        ),
        "maximum_segment_length_mm": (
            segment_length,
            "Đường kính Tool + hồ sơ chất lượng",
            AutomaticParameterStatus.RESOLVED,
            "Điều chỉnh mật độ rời rạc; không thay thế dung sai hoặc safety sampling.",
        ),
        "normal_variation_limit_degrees": (
            normal_limit,
            "Hồ sơ chất lượng",
            AutomaticParameterStatus.RESOLVED,
            "Giới hạn biến thiên pháp tuyến dùng cho chính sách rời rạc.",
        ),
        "safety_sampling_policy": (
            sampling,
            "Hồ sơ chất lượng + hướng dẫn an toàn",
            AutomaticParameterStatus.RESOLVED,
            "Hồ sơ nhanh không được bỏ qua swept collision hoặc safety scope.",
        ),
    }
    dependencies = {
        key: _dependency(context, key, quality_profile)
        for key in Z_LEVEL_AUTOMATIC_PARAMETER_KEYS
    }
    numeric_limits = {
        "top_level": (-1.0e9, 1.0e9, "Cao độ trên"),
        "bottom_level": (-1.0e9, 1.0e9, "Cao độ dưới"),
        "stepdown_mm": (1.0e-6, 1_000.0, "Bước xuống"),
        "tolerance_mm": (1.0e-6, 10.0, "Dung sai"),
        "surface_allowance_mm": (0.0, 1_000.0, "Lượng dư"),
        "maximum_segment_length_mm": (1.0e-6, 1_000.0, "Độ dài đoạn"),
    }
    choice_limits = {
        "orientation": {item.value for item in ZLevelOrientation},
        "boundary_policy": {item.value for item in ZLevelBoundaryPolicy},
        "contour_ordering": {"top_down_nearest_safe", "top_down_lexicographic"},
        "linking_mode": {item.value for item in ZLevelLinkingMode},
        "approach_retract_policy": {"retract_then_rapid"},
        "safety_scope": {"declared_geometry_and_tool_assembly"},
        "protected_geometry_scope": {
            "part_boundary_only",
            "declared_protected_geometry",
        },
    }
    values: list[AutomaticParameterValue] = []
    for key in Z_LEVEL_AUTOMATIC_PARAMETER_KEYS:
        resolved, source, status, reason = automatic[key]
        mode, override = _manual_state(key, stored, flags, overrides)
        validation = AutomaticValidationResult(True)
        normalized_override = override
        if key in numeric_limits:
            minimum, maximum, label = numeric_limits[key]
            normalized_override, validation = _numeric_override(
                override,
                minimum=minimum,
                maximum=maximum,
                label=label,
            )
        elif key in choice_limits:
            validation = _choice_override(override, choice_limits[key], key)
        if mode is AutomaticParameterMode.AUTO:
            validation = AutomaticValidationResult(True)
        values.append(
            AutomaticParameterValue(
                key,
                mode,
                resolved,  # type: ignore[arg-type]
                source,
                Z_LEVEL_AUTOMATIC_POLICY_VERSION,
                dependencies[key],
                status,
                reason,
                normalized_override,  # type: ignore[arg-type]
                validation,
            )
        )
    return AutomaticParameterContract(
        Z_LEVEL_AUTOMATIC_POLICY_KEY,
        Z_LEVEL_AUTOMATIC_POLICY_VERSION,
        quality_profile,
        tuple(values),
    )


__all__ = [
    "Z_LEVEL_AUTOMATIC_PARAMETER_KEYS",
    "Z_LEVEL_AUTOMATIC_POLICY_KEY",
    "Z_LEVEL_AUTOMATIC_POLICY_VERSION",
    "ZLevelAutomaticContext",
    "ZLevelGeometryEvidence",
    "resolve_z_level_automatic_contract",
]
