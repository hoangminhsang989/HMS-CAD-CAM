"""UI-choice tests for autosave and .replaced recovery prompts."""

from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox  # noqa: E402

from hms_cadcam.project.exceptions import (  # noqa: E402
    RecoveryRequiredError,
    ReplacedProjectRecoveryRequiredError,
)
from hms_cadcam.project.service import ProjectService  # noqa: E402
from hms_cadcam.ui.project_controller import ProjectUiController  # noqa: E402


def _controller(tmp_path):
    QApplication.instance() or QApplication([])
    service = ProjectService.create_default(tmp_path / "config")
    return ProjectUiController(QMainWindow(), service), service


def test_recovery_prompt_can_select_autosave(tmp_path, monkeypatch) -> None:
    controller, service = _controller(tmp_path)
    assessment = SimpleNamespace(project_root=tmp_path / "Part.HMS")
    monkeypatch.setattr(
        "hms_cadcam.ui.project_controller.QMessageBox.warning",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr(service, "recover_project", lambda selected: selected)

    controller._request_autosave_recovery(RecoveryRequiredError(assessment))

    assert controller._pending_operation is not None
    assert controller._pending_operation() is assessment


def test_recovery_prompt_can_open_main_data_without_recovery(tmp_path, monkeypatch) -> None:
    controller, service = _controller(tmp_path)
    assessment = SimpleNamespace(project_root=tmp_path / "Part.HMS")
    calls = []
    monkeypatch.setattr(
        "hms_cadcam.ui.project_controller.QMessageBox.warning",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.No,
    )
    monkeypatch.setattr(
        service,
        "open_project",
        lambda path, *, discard_recovery=False: calls.append((path, discard_recovery)),
    )

    controller._request_autosave_recovery(RecoveryRequiredError(assessment))
    assert controller._pending_operation is not None
    controller._pending_operation()

    assert calls == [(assessment.project_root, True)]


def test_replaced_prompt_requires_explicit_approval(tmp_path, monkeypatch) -> None:
    controller, service = _controller(tmp_path)
    assessment = SimpleNamespace(target_path=tmp_path / "Part.HMS")
    monkeypatch.setattr(
        "hms_cadcam.ui.project_controller.QMessageBox.warning",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr(service, "restore_replaced_and_open", lambda selected: selected)

    controller._request_replaced_recovery(
        ReplacedProjectRecoveryRequiredError(assessment)
    )

    assert controller._pending_operation is not None
    assert controller._pending_operation() is assessment
