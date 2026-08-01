"""Stage 12.4C explicit read-only conformance UI checks."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import Qt

from hms_cadcam.cam.lathe.lathe_post import (
    LatheBasicNcService,
    LatheNcConformanceStatus,
)
from hms_cadcam.ui.basic_nc_preview import BasicNcPreviewPanel
from hms_cadcam.ui.i18n import UiLanguage, translation_service
from tests.unit._lathe_post_conformance_fixtures import representative_program


def _service(scenario: str = "A") -> tuple[LatheBasicNcService, object]:
    program, mappings, metadata = representative_program(scenario)
    service = LatheBasicNcService(tool_mappings=mappings, metadata=metadata)
    return service, program


def test_review_action_is_explicit_accessible_and_read_only(qtbot) -> None:
    service, program = _service()
    panel = BasicNcPreviewPanel(service)
    qtbot.addWidget(panel)
    assert panel.conformance_group.title()
    assert panel.conformance_review_button.objectName() == "LatheBasicNcConformanceReviewAction"
    assert panel.conformance_review_button.accessibleName()
    assert panel.conformance_review_button.isEnabled() is False
    assert panel.listing.isReadOnly() is True
    assert service.state.conformance_report is None
    service.generate(program)  # type: ignore[arg-type]
    panel.show_result()
    assert panel.conformance_review_button.isEnabled() is True
    assert service.state.conformance_report is None
    before = service.latest.text, service.latest.sha256  # type: ignore[union-attr]
    qtbot.mouseClick(panel.conformance_review_button, Qt.MouseButton.LeftButton)
    report = service.state.conformance_report
    assert report is not None
    assert report.status is LatheNcConformanceStatus.CONFORMANT_WITH_INTENTIONAL_SAFE_DEVIATIONS
    assert (service.latest.text, service.latest.sha256) == before  # type: ignore[union-attr]
    visible = " ".join(
        panel.conformance_findings.item(index).text()
        for index in range(panel.conformance_findings.count())
    )
    assert "INTENTIONAL_SAFE_DEVIATION_WARNING_HEADER" in visible
    assert "SAMPLE_FEATURE_NOT_REPRESENTABLE_CURRENT_IR_ARC_IK" in visible
    assert "%" not in panel.conformance_status.text()
    assert all(term not in panel.conformance_status.text().casefold() for term in ("machine verified", "certified", "safe to run"))


def test_language_switch_changes_labels_not_nc_or_report(qtbot) -> None:
    service, program = _service()
    panel = BasicNcPreviewPanel(service)
    qtbot.addWidget(panel)
    service.generate(program)  # type: ignore[arg-type]
    panel.show_result()
    report = service.review_latest()
    panel._show_conformance_report(report)
    expected = (
        service.latest.text,  # type: ignore[union-attr]
        service.latest.sha256,  # type: ignore[union-attr]
        report.status,
        tuple(item.code for item in report.findings),
    )
    labels = []
    translations = translation_service()
    original = translations.language
    try:
        for language in (UiLanguage.VI_VN, UiLanguage.EN_US, UiLanguage.KO_KR):
            translations.set_language(language)
            labels.append(panel.conformance_group.title())
            current = service.state.conformance_report
            assert current is not None
            assert (
                service.latest.text,  # type: ignore[union-attr]
                service.latest.sha256,  # type: ignore[union-attr]
                current.status,
                tuple(item.code for item in current.findings),
            ) == expected
    finally:
        translations.set_language(original)
    assert len(set(labels)) == 3


def test_thread_review_displays_no_sample_coverage_and_keeps_ids_across_languages(
    qtbot,
) -> None:
    service, program = _service("C")
    panel = BasicNcPreviewPanel(service)
    qtbot.addWidget(panel)
    generated = service.generate(program)  # type: ignore[arg-type]
    assert generated.snapshot is not None
    panel.show_result()
    qtbot.mouseClick(panel.conformance_review_button, Qt.MouseButton.LeftButton)
    expected_ids = ("lathe.od_thread.v1", "lathe.id_thread.v1")
    report = service.state.conformance_report
    assert report is not None
    assert service.state.strategy_ids == expected_ids
    assert report.status is LatheNcConformanceStatus.NO_SAMPLE_COVERAGE
    assert "NO_SAMPLE_COVERAGE" in panel.conformance_status.text()
    translations = translation_service()
    original = translations.language
    try:
        for language in (UiLanguage.VI_VN, UiLanguage.EN_US, UiLanguage.KO_KR):
            translations.set_language(language)
            current = service.state.conformance_report
            assert current is not None
            assert service.state.strategy_ids == expected_ids
            assert current.status is LatheNcConformanceStatus.NO_SAMPLE_COVERAGE
            assert "NO_SAMPLE_COVERAGE" in panel.conformance_status.text()
    finally:
        translations.set_language(original)


def test_stage12_4c_catalog_keys_have_exact_parity_and_vi_fallback() -> None:
    root = Path(__file__).parents[2] / "src/hms_cadcam/ui/catalogs"
    catalogs = {
        name: json.loads((root / name).read_text(encoding="utf-8"))
        for name in ("vi_VN.json", "en_US.json", "ko_KR.json")
    }
    key_sets = [
        {key for key in catalog if key.startswith("lathe.basic_post.conformance.")}
        for catalog in catalogs.values()
    ]
    assert key_sets[0] == key_sets[1] == key_sets[2]
    assert len(key_sets[0]) == 17
    assert all(catalog[key].strip() for catalog in catalogs.values() for key in key_sets[0])
    assert all("{" not in catalog[key] and "}" not in catalog[key] for catalog in catalogs.values() for key in key_sets[0])
    translations = translation_service()
    assert translations.catalogs[UiLanguage.VI_VN].entries["lathe.basic_post.conformance.title"] == "Đối chiếu Post mẫu"


def test_review_panel_repeated_open_close_has_one_action(qtbot) -> None:
    service, _ = _service()
    panels = []
    for _ in range(5):
        panel = BasicNcPreviewPanel(service)
        qtbot.addWidget(panel)
        panels.append(panel)
        assert len(panel.findChildren(type(panel.conformance_review_button), "LatheBasicNcConformanceReviewAction")) == 1
        panel.close()
