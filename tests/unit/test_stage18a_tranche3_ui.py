"""R223 compact release center, I18N parity, and lifecycle tests."""

import json
from pathlib import Path
from time import perf_counter

from PySide6.QtWidgets import QApplication

from hms_cadcam.cam.qualification import ReleaseState
from hms_cadcam.ui.i18n import UiLanguage, translation_service
from hms_cadcam.ui.nc_release_center import NCReleaseCenter
from tests.unit._stage18a_tranche3_fixtures import release_context
from _qt_lifecycle import drain_test_owned_qt_state, qt_lifecycle_snapshot, top_level_baseline


def test_release_center_projects_truthful_status_and_filters(qtbot):
    *_prefix, session, candidate, _review, _ack, assessment = release_context()[2:]
    panel = NCReleaseCenter()
    qtbot.addWidget(panel)
    panel.set_release(session, candidate, assessment, filename="PROGRAM.fn")

    assert assessment.state is ReleaseState.READY_FOR_EXTERNAL_DRY_RUN_HANDOFF
    assert panel.machine_ready.text() in {"Không", "No", "아니요"}
    assert panel.machine_ready.property("machineReady") is False
    assert panel.nc_sha.text() == candidate.nc_sha256
    assert panel.trace.rowCount() == len(session.blocks)
    assert panel.export_button.isEnabled()
    panel.filter_combo.setCurrentIndex(1)
    assert panel.trace.rowCount() > 0
    assert panel.trace.rowCount() < len(session.blocks)


def test_tranche3_catalogs_have_key_order_parity_no_duplicates_and_utf8():
    root = Path("src/hms_cadcam/ui/catalogs")
    pairs = [
        json.loads((root / name).read_text(encoding="utf-8"), object_pairs_hook=lambda values: values)
        for name in ("vi_VN.json", "en_US.json", "ko_KR.json")
    ]
    assert all(len(values) == len({key for key, _value in values}) for values in pairs)
    assert [key for key, _ in pairs[0]] == [key for key, _ in pairs[1]] == [key for key, _ in pairs[2]]
    assert all("�" not in json.dumps(values, ensure_ascii=False) for values in pairs)
    required = {
        "NC verification and handoff", "Physical evidence unavailable", "Risk summary",
        "Compare revision", "Confirm operator", "Export dry-run package",
    }
    assert required <= {key for key, _ in pairs[0]}


def test_live_vi_en_ko_switch_preserves_release_identity(qtbot):
    *_prefix, session, candidate, _review, _ack, assessment = release_context()[2:]
    panel = NCReleaseCenter()
    qtbot.addWidget(panel)
    panel.set_release(session, candidate, assessment, filename="PROGRAM.fn")
    service = translation_service()
    previous = service.language
    try:
        titles = []
        for language in UiLanguage:
            service.set_language(language)
            QApplication.processEvents()
            titles.append(panel.title.text())
            assert panel.nc_sha.text() == candidate.nc_sha256
            assert panel.machine_ready.property("machineReady") is False
    finally:
        service.set_language(previous)
    assert titles == ["Xác minh & bàn giao NC", "NC verification and handoff", "NC 검증 및 인계"]


def test_24_release_center_cycles_have_zero_qt_lifecycle_delta(qapp, record_testsuite_property):
    *_prefix, session, candidate, _review, _ack, assessment = release_context()[2:]
    baseline_pointers = top_level_baseline(qapp)
    stable = qt_lifecycle_snapshot(qapp)
    durations = []
    service = translation_service()
    previous = service.language
    try:
        for index in range(24):
            started = perf_counter()
            service.set_language(tuple(UiLanguage)[index % 3])
            panel = NCReleaseCenter()
            panel.set_release(session, candidate, assessment, filename="PROGRAM.fn")
            panel.resize(900, 640)
            panel.show()
            panel.filter_combo.setCurrentIndex(index % panel.filter_combo.count())
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
    record_testsuite_property("r223_cycles", "24")
    record_testsuite_property("r223_top_hidden_modal_qthread", "0/0/0/0")
