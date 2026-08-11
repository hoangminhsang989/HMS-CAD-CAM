"""Release Center projection and lifecycle checks."""

from __future__ import annotations

from hms_cadcam.cam.domain.revision import ContentFingerprint
from hms_cadcam.cam.qualification.manufacturing_job import (
    JobProgramBinding, JobReleasePolicy, JobQualificationState, JobSetupBinding, JobToolBinding,
    ManufacturingJob,
)
from hms_cadcam.cam.qualification.manufacturing_release import assess_job
from hms_cadcam.ui.manufacturing_release_center import ManufacturingReleaseCenter


def _job() -> ManufacturingJob:
    fp = ContentFingerprint.from_payload
    tool = JobToolBinding(1, fp({"tool": 1}), "Tool", "MILL", 10, 50, "BT30", 1, 1, ("P1",), ("S1",))
    setup = JobSetupBinding("S1", fp({"setup": 1}), "G54", None, None, "ROBODRILL_D21MIB", (1,), ("P1",), JobQualificationState.CURRENT)
    program = JobProgramBinding("P1", fp({"release": 1}), "a" * 64, "S1", "G54", "ROBODRILL_D21MIB", fp({"machine": 1}), fp({"post": 1}), (1,), JobQualificationState.CURRENT, 1)
    return ManufacturingJob("P", "J", "PART", "R1", fp({"project": 1}), "ROBODRILL_D21MIB", fp({"machine": 1}), "FANUC_31I_B", (program,), (setup,), (tool,), JobReleasePolicy(require_handoff_package=False))


def test_release_center_renders_job_and_closes_repeatedly(qtbot):
    job = _job()
    for _ in range(24):
        panel = ManufacturingReleaseCenter()
        qtbot.addWidget(panel)
        panel.set_job(job, assess_job(job, handoff_package_ready=False))
        assert panel.job_id.text() == "J"
        assert "Chưa nghiệm thu" in panel.machine.text()
        panel.close()
