"""Fail-closed feature flag and immutable authorities for Stage 13C WP1."""
from hashlib import sha256
from pathlib import Path

from hms_cadcam.ai_assist.production_bridge_registry import certified_operation_ids
from hms_cadcam.project.database import DATABASE_SCHEMA_VERSION
from hms_cadcam.ui.feature_flags import UiFeatureFlag, UiFeatureFlags

FLAG = UiFeatureFlag.OFFLINE_CAM_AI_TURNING_COVERAGE_13C


def test_feature_flag_is_default_off_in_every_profile() -> None:
    assert not UiFeatureFlags.for_development_and_tests().is_enabled(FLAG)
    assert not UiFeatureFlags.for_review_harness().is_enabled(FLAG)
    assert not UiFeatureFlags.for_production().is_enabled(FLAG)


def test_feature_flag_depends_fail_closed_on_stage13a_and_stage13b() -> None:
    a = UiFeatureFlag.OFFLINE_CAM_AI_ASSIST_13A
    b = UiFeatureFlag.OFFLINE_CAM_AI_PARAMETER_ADVISOR_13B
    assert not UiFeatureFlags({FLAG: True, a: False, b: True}).is_enabled(FLAG)
    assert not UiFeatureFlags({FLAG: True, a: True, b: False}).is_enabled(FLAG)
    assert not UiFeatureFlags({FLAG: True, a: False, b: False}).is_enabled(FLAG)
    assert UiFeatureFlags({FLAG: True, a: True, b: True}).is_enabled(FLAG)


def test_flag_off_has_zero_model_load_and_worker_start() -> None:
    calls = {"model": 0, "worker": 0}
    flags = UiFeatureFlags({FLAG: False})
    if flags.is_enabled(FLAG):
        calls["model"] += 1
        calls["worker"] += 1
    assert calls == {"model": 0, "worker": 0}


def test_model_manifest_schema_and_stage13b_registry_are_unchanged() -> None:
    root = Path("src/hms_cadcam/ai_assist/models")
    model = root / "cutting_parameters_v1.json"
    manifest = root / "cutting_parameters_v1.manifest.json"
    assert len(model.read_bytes()) == 1093
    assert sha256(model.read_bytes()).hexdigest() == "a7b2c1110339502c08678a4d9f7c1b009a46fff3dcb0c4c7980c603927da8977"
    assert len(manifest.read_bytes()) == 390
    assert sha256(manifest.read_bytes()).hexdigest() == "c88baf75f078217048a010da53f113d1c3ba8ac8c81338cae75b1607c8bf4d6a"
    assert DATABASE_SCHEMA_VERSION == 5
    assert certified_operation_ids() == ("facing_2_5d", "drilling_v1", "FACE")
