from __future__ import annotations

from uuid import uuid4

import pytest

from hms_cadcam.ui.cam3d_function_state import (
    Cam3DPresentationState,
    Cam3DUiCommandPolicy,
    Cam3DUiReason,
    Cam3DUiState,
)
from hms_cadcam.ui.feature_flags import UiFeatureFlag, UiFeatureFlags


def test_cam3d_feature_flag_is_explicit_and_fail_closed() -> None:
    flag = UiFeatureFlag.CAM_3D_9A8
    assert flag.value == "cam_3d_9a8"
    assert not UiFeatureFlags.for_development_and_tests().is_enabled(flag)
    assert UiFeatureFlags.for_review_harness().is_enabled(flag)
    assert not UiFeatureFlags.for_production().is_enabled(flag)
    assert not UiFeatureFlags({}).is_enabled(flag)


def test_cam3d_state_defaults_are_immutable_and_fail_closed() -> None:
    disabled = Cam3DPresentationState.feature_disabled()
    empty = Cam3DPresentationState.empty()
    assert disabled.state is Cam3DUiState.FEATURE_DISABLED
    assert disabled.command_policy is Cam3DUiCommandPolicy.HIDDEN
    assert empty.state is Cam3DUiState.EMPTY
    assert empty.reason is Cam3DUiReason.NO_PROJECT
    assert empty.command_policy is Cam3DUiCommandPolicy.DISABLED
    assert not empty.resolved


def test_cam3d_ready_read_only_stale_and_error_factories_preserve_identity() -> None:
    project_id = uuid4()
    ready = Cam3DPresentationState.ready(project_id, 7)
    read_only = Cam3DPresentationState.for_read_only(project_id, 7)
    stale = Cam3DPresentationState.stale(project_id, 8)
    error = Cam3DPresentationState.error("validation", project_id, 8)
    assert ready.resolved
    assert ready.command_policy is Cam3DUiCommandPolicy.AVAILABLE
    assert read_only.read_only
    assert read_only.command_policy is Cam3DUiCommandPolicy.READ_ONLY
    assert stale.command_policy is Cam3DUiCommandPolicy.DISABLED
    assert error.diagnostic_count == 1
    assert error.message == "validation"


def test_cam3d_transition_is_typed_and_rejects_invalid_route() -> None:
    project_id = uuid4()
    calculating = Cam3DPresentationState.ready(project_id, 2).transition(
        Cam3DUiState.CALCULATING,
        reason=Cam3DUiReason.CALCULATION_IN_PROGRESS,
    )
    cancelled = calculating.transition(
        Cam3DUiState.CANCELLED,
        reason=Cam3DUiReason.CALCULATION_CANCELLED,
    )
    assert cancelled.project_id == project_id
    assert cancelled.project_generation == 2
    with pytest.raises(ValueError, match="Invalid CAM 3D UI transition"):
        cancelled.transition(Cam3DUiState.READ_ONLY)
    with pytest.raises(TypeError, match="target state"):
        cancelled.transition("ready")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"state": "ready", "reason": Cam3DUiReason.READY_FOR_INPUT}, "state"),
        ({"state": Cam3DUiState.READY, "reason": "ready_for_input"}, "reason"),
        (
            {
                "state": Cam3DUiState.READ_ONLY,
                "reason": Cam3DUiReason.PROJECT_READ_ONLY,
                "project_id": uuid4(),
                "project_generation": 1,
            },
            "read_only",
        ),
        (
            {
                "state": Cam3DUiState.READY,
                "reason": Cam3DUiReason.READY_FOR_INPUT,
                "project_generation": -1,
            },
            "generation",
        ),
    ],
)
def test_cam3d_state_rejects_untyped_or_inconsistent_values(
    kwargs: dict[str, object], message: str
) -> None:
    with pytest.raises((TypeError, ValueError), match=message):
        Cam3DPresentationState(**kwargs)  # type: ignore[arg-type]