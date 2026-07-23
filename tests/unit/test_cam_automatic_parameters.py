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
