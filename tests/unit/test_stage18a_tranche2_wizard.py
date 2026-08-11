"""VI/EN/KO, status-boundary, and lifecycle tests for the R221 wizard."""

import json
from pathlib import Path
from time import perf_counter

from PySide6.QtWidgets import QApplication, QLabel, QWizard

from hms_cadcam.cam.qualification import Level2WorkflowState, assess_level2_readiness
from hms_cadcam.ui.i18n import UiLanguage, translation_service
from hms_cadcam.ui.physical_qualification_wizard import PhysicalQualificationWizard
from tests.unit._stage18a_qualification_fixtures import qualification_input
from tests.unit._stage18a_tranche2_fixtures import (
    level1_report,
    level2_record,
    physical_readiness,
)
from _qt_lifecycle import (
    drain_test_owned_qt_state,
    qt_lifecycle_snapshot,
    top_level_baseline,
)


def readiness(record):
    setup = record.setup
    return assess_level2_readiness(
        level1_report=level1_report(),
        record=record,
        physical_readiness=physical_readiness(setup),
        current_nc_sha256=setup.nc_sha256,
        current_machine_profile_fingerprint=setup.machine_profile_fingerprint,
        current_post_fingerprint=setup.post_fingerprint,
        current_qualification_contract_fingerprint=qualification_input().machine_contract.fingerprint,
    )


def test_wizard_is_eight_compact_steps_with_required_vi_buttons(qtbot):
    service = translation_service()
    previous = service.language
    try:
        service.set_language(UiLanguage.VI_VN)
        wizard = PhysicalQualificationWizard()
        qtbot.addWidget(wizard)

        assert wizard.pageIds() == list(range(8))
        assert wizard.page(0).title() == "Bước 1 — Máy"
        assert wizard.page(7).title() == "Bước 8 — Kết quả"
        assert wizard.buttonText(QWizard.WizardButton.BackButton) == "Quay lại"
        assert wizard.buttonText(QWizard.WizardButton.NextButton) == "Tiếp tục"
        assert wizard.buttonText(QWizard.WizardButton.CustomButton1) == "Lưu"
        assert wizard.buttonText(QWizard.WizardButton.CustomButton2) == "Xuất gói kiểm tra"
    finally:
        service.set_language(previous)


def test_ready_context_is_truthful_and_never_displays_machine_ready(qtbot):
    record = level2_record()
    state = readiness(record)
    wizard = PhysicalQualificationWizard()
    qtbot.addWidget(wizard)
    wizard.set_context(record, state)

    assert state.workflow_state is Level2WorkflowState.READY_FOR_EXTERNAL_LEVEL2_EVIDENCE
    assert wizard.record is record
    assert wizard.result_status.text() in {
        "Sẵn sàng kiểm tra trên máy", "Ready for machine verification", "기계 검증 준비 완료"
    }
    assert wizard.acceptance_boundary.text() in {
        "Chưa nghiệm thu trên máy", "Not machine accepted", "기계 승인 전"
    }
    visible_text = "\n".join(label.text() for label in wizard.findChildren(QLabel))
    assert "Machine Ready" not in visible_text
    assert "MACHINE_READY=true" not in visible_text


def test_save_and_export_buttons_emit_typed_record_without_mutation(qtbot):
    record = level2_record()
    wizard = PhysicalQualificationWizard()
    qtbot.addWidget(wizard)
    wizard.set_context(record, readiness(record))
    saved = []
    exported = []
    wizard.save_requested.connect(saved.append)
    wizard.export_requested.connect(exported.append)

    wizard._custom_button_clicked(QWizard.WizardButton.CustomButton1)
    wizard._custom_button_clicked(QWizard.WizardButton.CustomButton2)

    assert saved == [record]
    assert exported == [record]
    assert wizard.record is record


def test_runtime_vi_en_ko_switch_preserves_record_and_readiness(qtbot):
    record = level2_record()
    state = readiness(record)
    wizard = PhysicalQualificationWizard()
    qtbot.addWidget(wizard)
    wizard.set_context(record, state)
    service = translation_service()
    previous = service.language
    observed = []
    try:
        for language in UiLanguage:
            service.set_language(language)
            QApplication.processEvents()
            observed.append((wizard.page(0).title(), wizard.result_status.text()))
            assert wizard.record is record
            assert wizard.readiness is state
    finally:
        service.set_language(previous)
    assert observed == [
        ("Bước 1 — Máy", "Sẵn sàng kiểm tra trên máy"),
        ("Step 1 — Machine", "Ready for machine verification"),
        ("1단계 — 기계", "기계 검증 준비 완료"),
    ]


def test_tranche2_catalogs_have_order_parity_no_duplicates_and_utf8():
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
    assert list(catalogs[0]) == list(catalogs[1]) == list(catalogs[2])
    required = {
        "Physical qualification", "Step 1 — Machine", "Step 8 — Result",
        "Ready for machine verification", "Waiting for dry-run", "Dry-run passed",
        "Dry-run failed", "Evidence is stale", "Not machine accepted",
        "Export verification package",
    }
    assert required <= set(catalogs[0])
    assert all("�" not in json.dumps(item, ensure_ascii=False) for item in catalogs)


def test_24_wizard_cycles_have_zero_modal_and_qthread_delta(qapp, record_testsuite_property):
    baseline_pointers = top_level_baseline(qapp)
    stable = qt_lifecycle_snapshot(qapp)
    record = level2_record()
    state = readiness(record)
    durations = []
    service = translation_service()
    previous = service.language
    try:
        for index in range(24):
            started = perf_counter()
            service.set_language(tuple(UiLanguage)[index % 3])
            wizard = PhysicalQualificationWizard()
            wizard.set_context(record, state)
            wizard.resize(720, 520)
            wizard.show()
            wizard.next()
            qapp.processEvents()
            wizard.close()
            wizard.deleteLater()
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
    record_testsuite_property("r221_cycles", "24")
    record_testsuite_property("r221_top_level_delta", str(final.top_levels - stable.top_levels))
    record_testsuite_property("r221_hidden_delta", str(final.hidden_top_levels - stable.hidden_top_levels))
    record_testsuite_property("r221_modal_delta", str(final.modal_top_levels - stable.modal_top_levels))
    record_testsuite_property("r221_qthread_delta", str(final.running_app_threads))
