"""Shared automatic-parameter contract tests."""

from __future__ import annotations

from dataclasses import replace

from hms_cadcam.cam.automatic_parameters import (
    AutomaticParameterContract,
    AutomaticParameterMode,
    AutomaticParameterStatus,
    AutomaticParameterValue,
    AutomaticValidationResult,
    CamQualityProfile,
)
from hms_cadcam.cam.domain import DependencyFingerprint


def _value(*, mode: AutomaticParameterMode = AutomaticParameterMode.AUTO):
    return AutomaticParameterValue(
        "stepover_mm",
        mode,
        0.8,
        "Đường kính dao + hồ sơ chất lượng",
        1,
        DependencyFingerprint.from_payload({"tool": "A", "quality": "balanced"}),
        AutomaticParameterStatus.RESOLVED,
        "Tính từ bán kính dao cầu.",
        0.8,
        AutomaticValidationResult(True),
    )


def test_shared_contract_round_trip_is_canonical_and_bounded() -> None:
    contract = AutomaticParameterContract(
        "parallel.finishing.automatic",
        1,
        CamQualityProfile.BALANCED,
        (_value(),),
    )
    payload = contract.to_json()
    restored = AutomaticParameterContract.from_json(payload)
    assert restored == contract
    assert restored.to_json() == payload
    assert len(payload) <= 4096
    assert restored.value("stepover_mm").effective_value == 0.8


def test_effective_hash_includes_mode_policy_dependency_and_effective_value() -> None:
    base = AutomaticParameterContract(
        "parallel.finishing.automatic",
        1,
        CamQualityProfile.BALANCED,
        (_value(),),
    )
    manual = replace(
        base,
        values=(replace(_value(), mode=AutomaticParameterMode.MANUAL),),
    )
    policy = replace(base, policy_version=2)
    dependency = replace(
        base,
        values=(
            replace(
                _value(),
                dependency_fingerprint=DependencyFingerprint.from_payload(
                    {"tool": "B", "quality": "balanced"}
                ),
            ),
        ),
    )
    effective = replace(
        manual,
        values=(replace(manual.values[0], override_value=0.7),),
    )
    fingerprints = {
        item.effective_fingerprint.digest
        for item in (base, manual, policy, dependency, effective)
    }
    assert len(fingerprints) == 5


def test_invalid_manual_value_is_preserved_in_contract() -> None:
    invalid = replace(
        _value(mode=AutomaticParameterMode.MANUAL),
        override_value="không-phải-số",
        validation=AutomaticValidationResult(False, "Bước ngang phải là số."),
    )
    contract = AutomaticParameterContract(
        "parallel.finishing.automatic",
        1,
        CamQualityProfile.BALANCED,
        (invalid,),
    )
    restored = AutomaticParameterContract.from_json(contract.to_json())
    assert restored.value("stepover_mm").override_value == "không-phải-số"
    assert not restored.value("stepover_mm").validation.valid


def test_stage17a_modes_and_provenance_round_trip() -> None:
    value = replace(
        _value(),
        mode=AutomaticParameterMode.MANUAL_OVERRIDE,
        inputs=(("diameter", 10.0), ("quality", "balanced")),
        lower_bound=0.01,
        upper_bound=10.0,
        clamped=True,
    )
    restored = AutomaticParameterValue.from_dict(value.to_dict())
    assert restored == value
    assert restored.has_manual_override
    assert restored.inputs == (("diameter", 10.0), ("quality", "balanced"))
    assert restored.lower_bound == 0.01
    assert restored.upper_bound == 10.0
    assert restored.clamped


def test_legacy_value_without_stage17a_provenance_still_loads() -> None:
    payload = _value(mode=AutomaticParameterMode.MANUAL).to_dict()
    for key in ("inputs", "lower_bound", "upper_bound", "clamped"):
        payload.pop(key)
    restored = AutomaticParameterValue.from_dict(payload)
    assert restored.mode is AutomaticParameterMode.MANUAL
    assert restored.has_manual_override
    assert restored.inputs == ()
    assert restored.lower_bound is None
    assert restored.upper_bound is None
    assert not restored.clamped


def test_not_applicable_has_no_effective_numeric_value() -> None:
    value = replace(
        _value(),
        mode=AutomaticParameterMode.NOT_APPLICABLE,
        resolved_value=None,
        status=AutomaticParameterStatus.UNSUPPORTED,
    )
    assert value.effective_value is None
    assert not value.has_manual_override
