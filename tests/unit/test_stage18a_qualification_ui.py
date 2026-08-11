"""Stage18A compact status UI, localization, and lifecycle tests."""

import json
from dataclasses import replace
from pathlib import Path
from time import perf_counter

import pytest
from PySide6.QtWidgets import QApplication

from hms_cadcam.cam.qualification import (
    QualificationLevel,
    qualify_static_nc,
    render_qualification_report_vi,
    robodrill_alpha_d21mib_contract,
)
from hms_cadcam.ui.i18n import UiLanguage, translation_service
from hms_cadcam.ui.machine_qualification_panel import MachineQualificationPanel
from tests.unit._stage18a_qualification_fixtures import qualification_input
from _qt_lifecycle import (
    drain_test_owned_qt_state,
    qt_lifecycle_snapshot,
    top_level_baseline,
)


def test_level1_panel_never_displays_machine_ready_true(qtbot):
    value = qualification_input()
    report = qualify_static_nc(value)
    panel = MachineQualificationPanel()
    qtbot.addWidget(panel)

    panel.set_report(report, value.machine_contract)

    assert report.qualification_level is QualificationLevel.STATICALLY_VALIDATED
    assert panel.level_value.text() in {"Đạt kiểm tra tĩnh", "Statically validated", "정적 검증 완료"}
    assert panel.ready_value.text() == "false"
    assert panel.ready_value.property("qualificationReady") is False
    assert panel.machine_value.text() == "FANUC ROBODRILL α-D21MiB — FANUC 31i-B — BT30"
    assert panel.tapping.text() == "TAPPING_MACHINE_READY_OUTPUT_NOT_QUALIFIED"
    assert panel.travel.text() == "PHYSICAL_TRAVEL_NOT_FULLY_VERIFIED"


def test_panel_rejects_stale_profile_report(qtbot):
    value = qualification_input()
    report = qualify_static_nc(value)
    changed = replace(value.machine_contract, contract_revision=2)
    panel = MachineQualificationPanel()
    qtbot.addWidget(panel)

    with pytest.raises(ValueError, match="stale"):
        panel.set_report(report, changed)


def test_vietnamese_report_distinguishes_static_from_machine_acceptance():
    value = qualification_input()
    report = qualify_static_nc(value)

    text = render_qualification_report_vi(report, value.machine_contract)

    assert text.startswith("BÁO CÁO XÁC NHẬN NC")
    assert "Mức xác nhận: Đạt kiểm tra tĩnh" in text
    assert "MACHINE_READY: false" in text
    assert "Physical acceptance: NOT_PERFORMED" in text
    assert "TAPPING_MACHINE_READY_OUTPUT_NOT_QUALIFIED" in text


def test_stage18a_catalogs_have_exact_key_order_parity_and_utf8():
    root = Path("src/hms_cadcam/ui/catalogs")
    pairs = [
        json.loads(
            (root / name).read_text(encoding="utf-8"),
            object_pairs_hook=lambda values: values,
        )
        for name in ("vi_VN.json", "en_US.json", "ko_KR.json")
    ]
    assert all(len(values) == len({key for key, _value in values}) for values in pairs)
    catalogs = [dict(values) for values in pairs]
    required = {
        "Machine qualification",
        "Qualification level",
        "Statically validated",
        "Dry-run qualified",
        "Machine accepted",
        "Unverified physical items",
    }

    assert list(catalogs[0]) == list(catalogs[1]) == list(catalogs[2])
    assert required <= set(catalogs[0])
    assert all("�" not in json.dumps(item, ensure_ascii=False) for item in catalogs)


@pytest.mark.parametrize("language", tuple(UiLanguage))
def test_panel_localizes_each_supported_language_at_creation(qtbot, language):
    service = translation_service()
    previous = service.language
    try:
        service.set_language(language)
        panel = MachineQualificationPanel()
        qtbot.addWidget(panel)
        expected = {
            UiLanguage.VI_VN: "Chưa có báo cáo xác nhận.",
            UiLanguage.EN_US: "No qualification report.",
            UiLanguage.KO_KR: "검증 보고서가 없습니다.",
        }[language]
        assert panel.machine_value.text() == expected
    finally:
        service.set_language(previous)


def test_live_vi_en_ko_switch_preserves_typed_level_and_truthful_ready(qtbot):
    value = qualification_input()
    report = qualify_static_nc(value)
    panel = MachineQualificationPanel()
    qtbot.addWidget(panel)
    panel.set_report(report, value.machine_contract)
    service = translation_service()
    previous = service.language
    try:
        observed = []
        for language in UiLanguage:
            service.set_language(language)
            QApplication.processEvents()
            observed.append(panel.level_value.text())
            assert panel.report is report
            assert panel.ready_value.text() == "false"
    finally:
        service.set_language(previous)
    assert observed == ["Đạt kiểm tra tĩnh", "Statically validated", "정적 검증 완료"]


def test_24_open_language_advanced_close_cycles_have_zero_lifecycle_delta(
    qapp,
    record_testsuite_property,
):
    baseline_pointers = top_level_baseline(qapp)
    stable = qt_lifecycle_snapshot(qapp)
    durations = []
    service = translation_service()
    previous = service.language
    try:
        for index in range(24):
            started = perf_counter()
            service.set_language(tuple(UiLanguage)[index % 3])
            panel = MachineQualificationPanel()
            panel.resize(420 + (index % 3) * 80, 260 + (index % 2) * 80)
            panel.show()
            panel.advanced.setChecked(True)
            qapp.processEvents()
            panel.close()
            panel.deleteLater()
            drain_test_owned_qt_state(qapp, baseline_pointers)
            durations.append(perf_counter() - started)
            current = qt_lifecycle_snapshot(qapp)
            assert current.top_levels <= stable.top_levels
            assert current.hidden_top_levels <= stable.hidden_top_levels
            assert current.modal_top_levels <= stable.modal_top_levels
            assert current.running_app_threads == 0
    finally:
        service.set_language(previous)
    final = qt_lifecycle_snapshot(qapp)
    assert final.top_levels <= stable.top_levels
    assert final.hidden_top_levels <= stable.hidden_top_levels
    assert final.modal_top_levels <= stable.modal_top_levels
    assert final.running_app_threads == 0
    assert max(durations[-6:]) <= max(1.0, max(durations[:6]) * 5.0)
    record_testsuite_property("r218_cycles", "24")
    record_testsuite_property("r218_top_level_delta", str(final.top_levels - stable.top_levels))
    record_testsuite_property("r218_hidden_delta", str(final.hidden_top_levels - stable.hidden_top_levels))
    record_testsuite_property("r218_modal_delta", str(final.modal_top_levels - stable.modal_top_levels))
    record_testsuite_property("r218_qthread_delta", str(final.running_app_threads))
