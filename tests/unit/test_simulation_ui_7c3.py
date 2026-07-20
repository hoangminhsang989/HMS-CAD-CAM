"""Qt Simulation 7C.3 panel state, policy, and issue-model tests."""

from __future__ import annotations

import dataclasses

from PySide6.QtWidgets import QApplication

from hms_cadcam.cam.simulation import (
    InMemoryAabbBackend,
    SimulationInputSnapshot,
    SimulationRuntimeService,
    SimulationSamplingPolicy,
)
from hms_cadcam.ui.simulation_ui import SimulationPanel
from hms_cadcam.viewer.simulation import SimulationDisplayPolicy
from tests.unit.test_simulation_service import _source


def _inputs_and_result(*, warning: bool = False):
    operation, artifact, setup, tool, holder, assembly, request, scene = _source()
    if warning:
        request = dataclasses.replace(request, safe_height=25.0)
    inputs = SimulationInputSnapshot(
        operation,
        artifact,
        setup,
        tool,
        assembly,
        holder,
        None,
        request,
    )
    execution = SimulationRuntimeService().run(
        request=request,
        artifact=artifact,
        setup=setup,
        tool=tool,
        assembly=assembly,
        holder=holder,
        scene=scene,
        backend=InMemoryAabbBackend(),
    )
    assert execution.accepted
    return inputs, execution.result


def test_panel_run_enable_clear_show_hide_and_result_status() -> None:
    QApplication.instance() or QApplication([])
    inputs, result = _inputs_and_result()
    panel = SimulationPanel()
    panel.show_source(inputs, can_run=True)
    assert panel.run_button.isEnabled()
    assert not panel.cancel_button.isEnabled()

    panel.set_result(result, None, current=True)
    assert panel.source_labels["status"].text() == "PASS"
    assert panel.clear_button.isEnabled()
    panel.visibility_button.click()
    assert panel.visibility_button.text() == "Show Overlay"
    panel.clear_result_display()
    assert panel.source_labels["current"].text() == "No current result"
    panel.deleteLater()

def test_policy_invalid_draft_does_not_mutate_and_reset_is_deterministic() -> None:
    QApplication.instance() or QApplication([])
    panel = SimulationPanel()
    before = panel.sampling_policy
    panel.policy_fields["maximum_samples"].setText("1000001")
    assert not panel.apply_policy_draft()
    assert panel.sampling_policy == before
    assert panel.policy_error.text()

    panel.policy_fields["maximum_samples"].setText("1234")
    assert panel.apply_policy_draft()
    assert panel.sampling_policy.maximum_samples == 1234
    panel.reset_policy_defaults()
    assert panel.sampling_policy == SimulationSamplingPolicy()
    assert panel.display_policy == SimulationDisplayPolicy()
    panel.deleteLater()


def test_warning_issue_filter_sort_selection_and_technical_copy() -> None:
    application = QApplication.instance() or QApplication([])
    inputs, result = _inputs_and_result(warning=True)
    panel = SimulationPanel()
    panel.show_source(inputs, can_run=True)
    panel.set_result(result, None, current=True)
    assert panel.source_labels["status"].text() == "WARN"
    assert panel.issue_table.rowCount() == len(result.issues)

    panel.issue_filter.setCurrentText("ERROR")
    assert panel.issue_table.rowCount() == 0
    panel.issue_filter.setCurrentText("WARNING")
    assert panel.issue_table.rowCount() == len(result.issues)
    panel.issue_table.selectRow(0)
    application.processEvents()
    assert "sim.rapid_below_safe" in panel.issue_details.text()
    panel.copy_issue_details()
    assert "rapid_below_safe" in QApplication.clipboard().text()
    panel.clear_issue_selection()
    assert panel.issue_details.text() == "—"
    panel.deleteLater()


def test_project_switch_style_clear_drops_old_issue_model_and_draft() -> None:
    QApplication.instance() or QApplication([])
    inputs, result = _inputs_and_result(warning=True)
    panel = SimulationPanel()
    panel.show_source(inputs, can_run=True)
    panel.set_result(result, None, current=True)
    panel.policy_fields["max_linear_step"].setText("not-a-number")
    panel.clear_source()
    assert panel.inputs is None
    assert panel.issue_table.rowCount() == 0
    assert not panel.run_button.isEnabled()
    panel.deleteLater()
