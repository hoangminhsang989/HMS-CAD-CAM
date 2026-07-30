import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from hms_cadcam.cam.application.cam3d_request import Cam3DCalculationJobId
from hms_cadcam.cam.application.cam3d_request import Cam3DResultIdentity
from hms_cadcam.cam.application.cam3d_workflow import (
    Cam3DWorkflowState,
    Cam3DWorkflowStatus,
)
from hms_cadcam.ui.cam3d_function_panel import Cam3DFunctionPanel
from tests.unit.test_cam3d_request_wp3 import _ready_fixture, _request


def test_feature_on_actions_and_legacy_topology_are_stable():
    app = QApplication.instance() or QApplication([])
    panel = Cam3DFunctionPanel(feature_enabled=True)
    assert len(panel.placeholder_controls) == 10
    assert panel.preview_button.objectName() == "Cam3DPreviewAction"
    assert panel.cancel_button.objectName() == "Cam3DCancelAction"
    assert not panel.preview_button.isEnabled()
    assert not panel.cancel_button.isEnabled()
    preview_calls = []
    cancel_calls = []
    panel.preview_requested.connect(lambda: preview_calls.append(True))
    panel.cancel_requested.connect(lambda: cancel_calls.append(True))

    fixture = _ready_fixture()
    request = _request(fixture)
    state = Cam3DWorkflowState(
        Cam3DWorkflowStatus.READY,
        fixture.setup.ownership,
        fixture.context.project_generation,
        preview_enabled=True,
    )
    panel.set_workflow_state(state)
    panel.preview_button.click()
    assert preview_calls == [True]
    assert panel.calculation_status_value.text()
    assert panel.preview_button.accessibleName()
    assert panel.preview_button.accessibleDescription()

    running = Cam3DWorkflowState(
        Cam3DWorkflowStatus.RUNNING,
        fixture.setup.ownership,
        fixture.context.project_generation,
        request.job_id,
        Cam3DResultIdentity.from_request(request),
        preview_enabled=True,
        cancel_enabled=True,
    )
    panel.set_workflow_state(running)
    panel.cancel_button.click()
    assert cancel_calls == [True]
    panel.close()
    app.processEvents()


def test_feature_off_creates_no_wp4_actions():
    panel = Cam3DFunctionPanel(feature_enabled=False)
    assert not hasattr(panel, "preview_button")
    assert not hasattr(panel, "cancel_button")
    assert len(panel.placeholder_controls) == 10