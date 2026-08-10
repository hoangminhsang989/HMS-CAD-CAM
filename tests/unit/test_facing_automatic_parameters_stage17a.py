"""Stage17A shared automatic-parameter policy and Facing integration tests."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

from hms_cadcam.cam.automatic_facing import (
    FacingAutomaticContext,
    FacingAutomaticVariant,
    resolve_facing_automatic_contract,
)
from hms_cadcam.cam.automatic_parameters import (
    AUTOMATIC_PARAMETER_CONTRACT_KEY,
    AutomaticParameterContract,
    AutomaticParameterMode,
    AutomaticParameterStatus,
    CamQualityProfile,
)
from hms_cadcam.cam.domain import LengthUnit, ToolFamily
from hms_cadcam.ui.function_editor.strategies.common_milling import (
    FacingEditorDraftContext,
    FacingEditorVariant,
    facing_applied_values,
    facing_draft_transform,
    prepare_facing_update,
)
from tests.unit.test_facing_function_editors_9a51 import _context


_STAGE17A_CATALOG_KEYS = {
    "Chế độ tự động tính lại từ bằng chứng; tùy chỉnh giữ ý định người dùng.",
    "Chế độ Bước ngang",
    "Chế độ Bước xuống",
    "Chế độ Vượt biên",
    "Dùng tự động",
    "Giá trị suy ra từ Tool, hình học, đơn vị và hồ sơ chất lượng.",
    "Hồ sơ chất lượng",
    "Bước ngang tự động",
    "Bước xuống tự động",
    "Vượt biên tự động",
    "THAM SỐ TỰ ĐỘNG",
    "Trạng thái tham số tự động",
    "Tóm tắt chế độ tự động, giá trị tùy chỉnh và tham số thiếu bằng chứng.",
    "Tùy chỉnh",
    "Tự động",
    "Điều chỉnh tỷ lệ stepover/stepdown trong giới hạn hình học thực.",
}


def _policy_context(
    *,
    variant: FacingAutomaticVariant = FacingAutomaticVariant.STOCK_BOX,
    family: ToolFamily | None = ToolFamily.END_MILL,
    diameter: float | None = 10.0,
    corner_radius: float | None = None,
    axial: float | None = 20.0,
    depth: float | None = 4.0,
    tolerance: float | None = 0.01,
) -> FacingAutomaticContext:
    return FacingAutomaticContext(
        variant,
        LengthUnit.MM,
        family,
        diameter,
        corner_radius,
        axial,
        depth,
        tolerance,
        "boundary",
        "tool",
    )


def test_end_mill_policy_is_deterministic_and_bounded() -> None:
    context = _policy_context()
    first = resolve_facing_automatic_contract(context)
    second = resolve_facing_automatic_contract(context)
    assert first == second
    assert first.value("stepover").effective_value == 5.0
    assert first.value("stepdown").effective_value == 4.0
    assert first.value("overtravel").effective_value == 2.5
    assert first.value("stepover").lower_bound == 0.01
    assert dict(first.value("stepover").inputs)["tool_family"] == "end_mill"
    assert AutomaticParameterContract.from_json(first.to_json()) == first


@pytest.mark.parametrize(
    ("profile", "expected"),
    (
        (CamQualityProfile.FAST, 6.5),
        (CamQualityProfile.BALANCED, 5.0),
        (CamQualityProfile.HIGH, 3.5),
    ),
)
def test_quality_profiles_are_monotonic(
    profile: CamQualityProfile,
    expected: float,
) -> None:
    contract = resolve_facing_automatic_contract(
        _policy_context(),
        quality_profile=profile,
    )
    assert contract.value("stepover").effective_value == expected


def test_bull_nose_uses_corner_radius_and_records_clamp() -> None:
    contract = resolve_facing_automatic_contract(
        _policy_context(
            family=ToolFamily.BULL_NOSE_END_MILL,
            corner_radius=5.0,
        ),
        quality_profile=CamQualityProfile.FAST,
    )
    stepover = contract.value("stepover")
    assert stepover.effective_value == 5.0
    assert stepover.upper_bound == 5.0
    assert stepover.clamped
    assert "corner radius" in stepover.reason


def test_ball_end_and_custom_geometry_are_not_treated_as_one_family() -> None:
    ball = resolve_facing_automatic_contract(
        _policy_context(family=ToolFamily.BALL_END_MILL)
    )
    custom = resolve_facing_automatic_contract(
        _policy_context(family=ToolFamily.CUSTOM)
    )
    assert dict(ball.value("stepover").inputs)["tool_family"] == "ball_end_mill"
    assert dict(custom.value("stepover").inputs)["tool_family"] == "custom"


def test_unsupported_tool_and_missing_depth_fail_closed() -> None:
    contract = resolve_facing_automatic_contract(
        _policy_context(family=ToolFamily.DRILL, depth=None)
    )
    assert contract.value("stepover").mode is AutomaticParameterMode.NOT_APPLICABLE
    assert contract.value("stepover").effective_value is None
    assert contract.value("stepdown").status is AutomaticParameterStatus.UNSUPPORTED


def test_stepdown_never_clamps_above_the_operation_depth_span() -> None:
    contract = resolve_facing_automatic_contract(
        _policy_context(depth=0.001, tolerance=0.01)
    )
    stepdown = contract.value("stepdown")
    assert stepdown.mode is AutomaticParameterMode.NOT_APPLICABLE
    assert stepdown.effective_value is None


def test_planar_policy_marks_stock_only_overtravel_not_applicable() -> None:
    contract = resolve_facing_automatic_contract(
        _policy_context(variant=FacingAutomaticVariant.PLANAR_FACE)
    )
    assert contract.value("overtravel").mode is AutomaticParameterMode.NOT_APPLICABLE
    assert contract.value("stepover").mode is AutomaticParameterMode.AUTO


def test_policy_respects_internal_unit_without_display_conversion() -> None:
    context = replace(
        _policy_context(),
        unit=LengthUnit.INCH,
        diameter=0.5,
        axial_cutting_length=1.0,
        depth_span=0.1,
        tolerance=0.0001,
    )
    contract = resolve_facing_automatic_contract(context)
    assert contract.value("stepover").effective_value == 0.25
    assert contract.value("stepdown").effective_value == 0.1
    assert dict(contract.value("stepover").inputs)["diameter"] == 0.5


def test_malformed_numeric_evidence_is_rejected_before_derivation() -> None:
    with pytest.raises(ValueError, match="diameter"):
        replace(_policy_context(), diameter=-1.0)


def test_legacy_facing_round_trip_does_not_add_metadata_or_mutate_operation() -> None:
    context = _context(FacingEditorVariant.STOCK)
    values = dict(facing_applied_values(context, FacingEditorVariant.STOCK))
    update = prepare_facing_update(
        context,
        FacingEditorDraftContext(None),
        FacingEditorVariant.STOCK,
        values,
    )
    assert update.operation is context.operation
    assert AUTOMATIC_PARAMETER_CONTRACT_KEY not in dict(update.operation.parameters.values)
    assert values["stepover_mode"] == AutomaticParameterMode.MANUAL_OVERRIDE.value


def test_use_auto_then_manual_override_and_reset_round_trip() -> None:
    context = _context(FacingEditorVariant.STOCK)
    values = dict(facing_applied_values(context, FacingEditorVariant.STOCK))
    values.update(
        {
            "stepover_mode": AutomaticParameterMode.AUTO.value,
            "stepdown_mode": AutomaticParameterMode.AUTO.value,
            "overtravel_mode": AutomaticParameterMode.AUTO.value,
        }
    )
    values.update(facing_draft_transform(context, FacingEditorVariant.STOCK, values))
    automatic_update = prepare_facing_update(
        context,
        FacingEditorDraftContext(None),
        FacingEditorVariant.STOCK,
        values,
    )
    raw = dict(automatic_update.operation.parameters.values)[
        AUTOMATIC_PARAMETER_CONTRACT_KEY
    ]
    automatic = AutomaticParameterContract.from_json(raw)
    assert all(
        automatic.value(key).mode is AutomaticParameterMode.AUTO
        for key in ("stepover", "stepdown", "overtravel")
    )

    current = replace(context, operation=automatic_update.operation)
    manual_values = dict(facing_applied_values(current, FacingEditorVariant.STOCK))
    manual_values["stepover_mode"] = AutomaticParameterMode.MANUAL_OVERRIDE.value
    manual_values["stepover"] = "3.25"
    manual_update = prepare_facing_update(
        current,
        FacingEditorDraftContext(None),
        FacingEditorVariant.STOCK,
        manual_values,
    )
    manual = AutomaticParameterContract.from_json(
        dict(manual_update.operation.parameters.values)[AUTOMATIC_PARAMETER_CONTRACT_KEY]
    )
    assert manual.value("stepover").has_manual_override
    assert manual.value("stepover").effective_value == 3.25

    reset_context = replace(context, operation=manual_update.operation)
    reset_values = dict(facing_applied_values(reset_context, FacingEditorVariant.STOCK))
    reset_values["stepover_mode"] = AutomaticParameterMode.AUTO.value
    transformed = facing_draft_transform(
        reset_context,
        FacingEditorVariant.STOCK,
        reset_values,
    )
    assert transformed["stepover_mode"] == AutomaticParameterMode.AUTO.value
    assert transformed["stepover"] != "3.25"


def test_valid_draft_depth_change_recomputes_auto_stepdown() -> None:
    context = _context(FacingEditorVariant.STOCK)
    values = dict(facing_applied_values(context, FacingEditorVariant.STOCK))
    values.update(
        {
            "stepdown_mode": AutomaticParameterMode.AUTO.value,
            "top_height": "2.0",
            "target_height": "0.0",
            "stock_allowance": "0.5",
        }
    )
    transformed = facing_draft_transform(
        context,
        FacingEditorVariant.STOCK,
        values,
    )
    assert transformed["stepdown"] == "1.5"


def test_missing_tool_cannot_be_switched_to_auto() -> None:
    context = replace(_context(FacingEditorVariant.STOCK), tool_definitions=())
    values = dict(facing_applied_values(context, FacingEditorVariant.STOCK))
    values["stepover_mode"] = AutomaticParameterMode.AUTO.value
    with pytest.raises(ValueError, match="chưa đủ evidence"):
        facing_draft_transform(context, FacingEditorVariant.STOCK, values)


def test_stage17a_catalog_keys_have_vi_en_ko_parity_and_utf8() -> None:
    root = Path("src/hms_cadcam/ui/catalogs")
    catalogs = {
        name: json.loads((root / name).read_text(encoding="utf-8"))
        for name in ("vi_VN.json", "en_US.json", "ko_KR.json")
    }
    for catalog in catalogs.values():
        assert _STAGE17A_CATALOG_KEYS <= set(catalog)
        assert all(catalog[key].strip() for key in _STAGE17A_CATALOG_KEYS)
        assert all("\ufffd" not in catalog[key] for key in _STAGE17A_CATALOG_KEYS)
    assert catalogs["vi_VN.json"]["Dùng tự động"] == "Dùng tự động"
    assert catalogs["en_US.json"]["Dùng tự động"] == "Use automatic values"
    assert catalogs["ko_KR.json"]["Dùng tự động"] == "자동 값 사용"
