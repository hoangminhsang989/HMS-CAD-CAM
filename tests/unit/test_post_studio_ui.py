from __future__ import annotations

from hms_cadcam.cam.post_studio import PostDefinition, PostMachineBinding, PostSourceFormat, PostStudioService
from hms_cadcam.cam.domain.revision import ContentFingerprint
from hms_cadcam.ui.post_studio import PostProcessorStudioPanel


def test_post_studio_panel_renders_real_revision_and_does_not_activate(qtbot) -> None:
    at = "2026-08-12T12:00:00+07:00"
    binding = PostMachineBinding("fanuc_robodrill_alpha_d21mib", "fanuc_31i_b", "BT30", "FANUC-SHL", ContentFingerprint.from_payload({"machine": 1}))
    definition = PostDefinition("fanuc-shl", "FANUC-SHL", PostSourceFormat.WORKNC_DAT, binding, at, "owner")
    service = PostStudioService()
    revision = service.import_source(definition, b"G40\r\nM09\r\n", revision_id="fanuc-shl.original", created_at=at, created_by="owner", notes="source")
    panel = PostProcessorStudioPanel(service); qtbot.addWidget(panel); panel.refresh()
    assert panel.library.count() == 1
    panel.library.setCurrentRow(0)
    assert revision.source_sha256 in panel.source_editor.toPlainText() or "G40" in panel.source_editor.toPlainText()
    assert "NOT_ACTIVE_GLOBALLY" in panel.properties.item(5, 1).text()
    panel.close()
