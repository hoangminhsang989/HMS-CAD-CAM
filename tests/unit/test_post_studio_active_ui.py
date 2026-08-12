from __future__ import annotations

from hms_cadcam.cam.domain.revision import ContentFingerprint
from hms_cadcam.cam.post_studio import ActiveLifecycleProjection, ManagedActiveStatus
from hms_cadcam.ui.post_active_status import PostActiveStatusPanel
from hms_cadcam.ui.post_studio import PostProcessorStudioPanel
from hms_cadcam.ui.i18n import UiLanguage, translation_service


def test_active_status_panel_projects_vietnamese_managed_state_without_machine_ready(qtbot) -> None:
    state = ActiveLifecycleProjection("fanuc-shl", "FANUC-SHL", "fanuc-shl.r233-g40", "1" * 64, "1" * 64, "fanuc-shl.original", "0" * 64, "FANUC ROBODRILL α-D21MiB", "FANUC 31i-B", "BT30", "2026-08-12T14:02:28+07:00", "Sáng Hoàng Minh", ManagedActiveStatus.ACTIVE_MANAGED_REVISION, True, False, True, False, "a" * 64, "0" * 64, ContentFingerprint.from_payload({"active": True}))
    panel = PostActiveStatusPanel(); qtbot.addWidget(panel); panel.project(state)
    assert panel.fields["state"].text() == "ĐANG KÍCH HOẠT / ACTIVE"
    assert panel.fields["rollback"].text() == "SẴN SÀNG"
    assert panel.fields["drift"].text() == "KHÔNG PHÁT HIỆN"
    assert all("MACHINE_READY" not in field.text() for field in panel.fields.values())


def test_post_studio_integrates_compact_active_projection(qtbot) -> None:
    state = ActiveLifecycleProjection("fanuc-shl", "FANUC-SHL", "fanuc-shl.r233-g40", "1" * 64, "1" * 64, "fanuc-shl.original", "0" * 64, "FANUC ROBODRILL α-D21MiB", "FANUC 31i-B", "BT30", "2026-08-12T14:02:28+07:00", "Sáng Hoàng Minh", ManagedActiveStatus.ACTIVE_MANAGED_REVISION, True, False, True, False, "a" * 64, "0" * 64, ContentFingerprint.from_payload({"active": True}))
    studio = PostProcessorStudioPanel(); qtbot.addWidget(studio)
    assert not studio.active_status.isVisible()
    studio.show(); studio.set_active_lifecycle(state)
    assert studio.active_status.isVisible()
    assert studio.active_status.fields["post"].text() == "FANUC-SHL"


def test_record_reconciliation_is_not_mislabelled_as_external_drift(qtbot) -> None:
    state = ActiveLifecycleProjection("fanuc-shl", "FANUC-SHL", "fanuc-shl.r233-g40", "1" * 64, "1" * 64, "fanuc-shl.original", "0" * 64, "FANUC ROBODRILL α-D21MiB", "FANUC 31i-B", "BT30", "2026-08-12T14:02:28+07:00", "Sáng Hoàng Minh", ManagedActiveStatus.RECORD_RECONCILIATION_REQUIRED, False, False, True, False, "a" * 64, "0" * 64, ContentFingerprint.from_payload({"active": False}))
    panel = PostActiveStatusPanel(); qtbot.addWidget(panel); panel.project(state)
    assert panel.fields["drift"].text() == "CẦN ĐỐI CHIẾU"
    assert panel.fields["drift"].text() != "POST ĐÃ BỊ THAY ĐỔI NGOÀI HMS"


def test_active_status_has_vi_en_ko_catalog_parity(qtbot) -> None:
    panel = PostActiveStatusPanel(); qtbot.addWidget(panel)
    state = ActiveLifecycleProjection("fanuc-shl", "FANUC-SHL", "fanuc-shl.r233-g40", "1" * 64, "1" * 64, "fanuc-shl.original", "0" * 64, "FANUC ROBODRILL α-D21MiB", "FANUC 31i-B", "BT30", "2026-08-12T14:02:28+07:00", "Sáng Hoàng Minh", ManagedActiveStatus.ACTIVE_MANAGED_REVISION, True, False, True, False, "a" * 64, "0" * 64, ContentFingerprint.from_payload({"active": True}))
    panel.project(state)
    service = translation_service(); original = service.language
    try:
        expected = {UiLanguage.VI_VN: ("Post đang dùng", "SẴN SÀNG"), UiLanguage.EN_US: ("Active Post", "READY"), UiLanguage.KO_KR: ("활성 포스트", "준비됨")}
        for language, (label, ready) in expected.items():
            service.set_language(language)
            assert panel.captions["post"].text() == label
            assert panel.fields["rollback"].text() == ready
            assert service.translate_key("post_studio.active.external_drift") != "post_studio.active.external_drift"
    finally:
        service.set_language(original)
