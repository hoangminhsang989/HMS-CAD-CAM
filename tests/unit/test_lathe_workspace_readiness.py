"""Fail-closed workspace topology tests for the Stage 12 readiness boundary."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from hms_cadcam.cam.lathe.readiness import (  # noqa: E402
    LatheWorkspaceReadiness,
    STAGE12_LATHE_WORKSPACE_READINESS,
)
from hms_cadcam.cam.lathe.types import (  # noqa: E402
    LatheWorkspaceReadinessReason,
    LatheWorkspaceReadinessState,
)
from hms_cadcam.ui.workspace_shell import WorkspaceBar, WorkspaceId  # noqa: E402


def _application() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_feature_off_has_no_service_or_ui_and_fails_closed() -> None:
    _application()
    readiness = LatheWorkspaceReadiness.unavailable()
    assert readiness.state is LatheWorkspaceReadinessState.FOUNDATION_UNAVAILABLE
    assert readiness.reason is LatheWorkspaceReadinessReason.FOUNDATION_NOT_READY
    assert not readiness.foundation_available
    bar = WorkspaceBar()
    assert not bar.actions_by_workspace[WorkspaceId.LATHE].isEnabled()
    assert bar.set_active_workspace(WorkspaceId.LATHE) is WorkspaceId.HOME
    bar.deleteLater()


def test_foundation_on_keeps_lathe_disabled_with_presenter_not_implemented() -> None:
    _application()
    readiness = STAGE12_LATHE_WORKSPACE_READINESS
    assert readiness.state is LatheWorkspaceReadinessState.PRESENTER_IMPLEMENTATION_ALLOWED
    assert readiness.reason.value == "presenter_not_implemented"
    assert not readiness.presenter_active
    bar = WorkspaceBar()
    lathe = bar.actions_by_workspace[WorkspaceId.LATHE]
    assert not lathe.isEnabled()
    assert "chưa" in lathe.toolTip().casefold()
    bar.deleteLater()


def test_workspace_topology_is_unchanged_and_has_no_lathe_presenter_object() -> None:
    _application()
    bar = WorkspaceBar()
    assert tuple(bar.actions_by_workspace) == (
        WorkspaceId.HOME,
        WorkspaceId.CAD,
        WorkspaceId.MILL_2D,
        WorkspaceId.MILL_3D,
        WorkspaceId.LATHE,
        WorkspaceId.SIMULATION,
        WorkspaceId.POST,
    )
    assert len(bar.actions()) == 7
    assert not hasattr(bar, "lathe_presenter")
    assert not hasattr(bar, "lathe_widget")
    bar.deleteLater()
