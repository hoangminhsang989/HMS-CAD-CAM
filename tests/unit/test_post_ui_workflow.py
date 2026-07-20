"""Production Post 7D.2.3 UI/controller contract tests."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from hms_cadcam.cam.post import (
    CutterCompensationPolicy,
    ExportOverwritePolicy,
    SimulationGateMode,
)
from hms_cadcam.cam.post.service import PostRuntimeService
from hms_cadcam.ui.post_ui import (
    PostPanelDraft,
    PostProcessorPanel,
    build_production_post_request,
    sanitize_post_filename,
)
from tests.unit._post_fixtures import source_snapshot


class _ExportRuntime:
    def artifacts(self):
        return ()

    def mark_operation_stale(self, _operation_id):
        return None


class _Service:
    def __init__(self, source):
        self.source = source
        self.runtime = PostRuntimeService()
        self.export_runtime = _ExportRuntime()

    def capture_post_source(self, _operation_id):
        return self.source

    @property
    def post_service(self):
        return self.runtime

    @property
    def nc_export_service(self):
        return self.export_runtime

    @property
    def cam_generation(self):
        return 1


def test_filename_policy_is_safe_and_adds_profile_extension() -> None:
    assert sanitize_post_filename("Facing") == "Facing.fn"
    assert sanitize_post_filename("Facing.fn") == "Facing.fn"
    for value in ("../Facing", "CON", "Facing.nc.fn", "Facing.nc"):
        with pytest.raises(ValueError):
            sanitize_post_filename(value)


def test_production_request_uses_g54_mm_and_applied_cutter_policy() -> None:
    source = source_snapshot(with_motion=False)
    draft = PostPanelDraft(
        "robodrill_fanuc_21i_worknc_expanded_v1",
        "face",
        "review",
        10.0,
        "G54",
        1,
        1,
        7,
        "Face mill",
        CutterCompensationPolicy.DISABLED,
        SimulationGateMode.OPTIONAL,
        ExportOverwritePolicy.FAIL_IF_EXISTS,
    )
    request = build_production_post_request(source, draft)
    assert request.program_context is not None
    assert request.program_context.file_name == "face.fn"
    assert request.program_context.tool_binding.diameter_offset is None
    assert request.simulation_gate_policy.mode is SimulationGateMode.OPTIONAL


def test_panel_keeps_generate_disabled_until_safe_z_and_apply() -> None:
    QApplication.instance() or QApplication([])
    source = source_snapshot(with_motion=False)
    panel = PostProcessorPanel(_Service(source))
    panel.set_operation(source.operation.operation_id, generation=1, operation_name="Facing")
    assert not panel.generate_button.isEnabled()
    panel.safe_z_spin.setValue(10.0)
    assert not panel.generate_button.isEnabled()  # draft is not applied
    assert panel.apply_draft()
    assert not panel.generate_button.isEnabled()  # REQUIRE_PASS blocks missing simulation
    panel.gate_combo.setCurrentText("OPTIONAL")
    assert panel.apply_draft()
    assert panel.generate_button.isEnabled()
    panel.deleteLater()
