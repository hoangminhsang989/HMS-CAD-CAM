from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from hms_cadcam.ui.feature_flags import UiFeatureFlag, UiFeatureFlags


def test_feature_flag_defaults_are_explicit_and_fail_closed() -> None:
    flag = UiFeatureFlag.POST_ASSEMBLY_9A7
    assert flag.value == "post_assembly_9a7"
    assert not UiFeatureFlags.for_development_and_tests().is_enabled(flag)
    assert UiFeatureFlags.for_review_harness().is_enabled(flag)
    assert not UiFeatureFlags.for_production().is_enabled(flag)


def test_feature_flags_are_in_memory_and_explicit_false_is_preserved() -> None:
    flags = UiFeatureFlags({UiFeatureFlag.POST_ASSEMBLY_9A7: False})
    assert not flags.is_enabled(UiFeatureFlag.POST_ASSEMBLY_9A7)
    assert not flags.is_enabled("unknown-flag")  # type: ignore[arg-type]


@pytest.mark.parametrize("invalid", ["false", "true", 1, 0, None, object()])
def test_feature_flag_non_bool_values_are_rejected(invalid: object) -> None:
    with pytest.raises(TypeError, match="must be bool"):
        UiFeatureFlags(  # type: ignore[arg-type]
            {UiFeatureFlag.POST_ASSEMBLY_9A7: invalid}
        )


@pytest.mark.parametrize("invalid_key", ["post_assembly_9a7", 1, None, object()])
def test_feature_flag_wrong_key_types_are_rejected(invalid_key: object) -> None:
    with pytest.raises(TypeError, match="keys must be UiFeatureFlag"):
        UiFeatureFlags({invalid_key: False})  # type: ignore[dict-item]


def test_feature_flag_mapping_and_container_are_immutable() -> None:
    values = {UiFeatureFlag.POST_ASSEMBLY_9A7: False}
    flags = UiFeatureFlags(values)
    values[UiFeatureFlag.POST_ASSEMBLY_9A7] = True
    assert not flags.is_enabled(UiFeatureFlag.POST_ASSEMBLY_9A7)
    with pytest.raises(TypeError):
        flags._values[UiFeatureFlag.POST_ASSEMBLY_9A7] = True  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        flags._values = {}  # type: ignore[misc]
