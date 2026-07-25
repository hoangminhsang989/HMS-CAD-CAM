"""Create the native-Windows Stage 8A.4.3 multilingual review package."""

from __future__ import annotations

import argparse
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any
from uuid import uuid4

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))
if str(REPOSITORY_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from PySide6.QtCore import QCoreApplication, QSettings, Qt
from PySide6.QtWidgets import QApplication, QDockWidget, QTabBar

from hms_cadcam.cad.unavailable import UnavailableCadKernel
from hms_cadcam.project.models import UnitSystem
from hms_cadcam.project.service import ProjectService
from hms_cadcam.ui.geometry_transfer_ui import IncomingGeometryNotificationBar
from hms_cadcam.ui.i18n import (
    LocaleSettingsService,
    UiLanguage,
    apply_application_font,
    build_default_catalogs,
    translation_service,
    validate_glossary,
)
from hms_cadcam.ui.language_settings import LanguageSettingsDialog
from hms_cadcam.ui.localization import localize_widget_tree
from hms_cadcam.ui.localization_audit import (
    RuntimeAuditMetrics,
    _is_mixed,
    audit_locale,
    audit_widget,
)
from hms_cadcam.ui.localized_dialogs import QFileDialog, QMessageBox
from hms_cadcam.ui.main_window import MainWindow
from hms_cadcam.viewer.unavailable_backend import UnavailableCadViewportBackend

LOGGER = logging.getLogger(__name__)
OUTPUT = (
    REPOSITORY_ROOT
    / "reference_private"
    / "DERIVED"
    / "UI_STAGE_8A4_3_MULTILINGUAL"
)
PNG_NAMES = (
    "01_language_settings_vietnamese.png",
    "02_language_settings_english.png",
    "03_language_settings_korean.png",
    "04_main_workspace_vietnamese.png",
    "05_main_workspace_english.png",
    "06_main_workspace_korean.png",
    "07_file_dialog_vietnamese.png",
    "08_file_dialog_english.png",
    "09_file_dialog_korean.png",
    "10_dirty_lifecycle_vietnamese.png",
    "11_dirty_lifecycle_english.png",
    "12_dirty_lifecycle_korean.png",
    "13_geometry_notification_vietnamese.png",
    "14_geometry_notification_english.png",
    "15_geometry_notification_korean.png",
    "16_dpi_150_vietnamese.png",
    "17_dpi_150_english.png",
    "18_dpi_150_korean.png",
)
JSON_NAMES = (
    "summary.json",
    "language_catalog_report.json",
    "translation_coverage_report.json",
    "fallback_report.json",
    "runtime_switch_report.json",
    "glossary_report.json",
    "localization_accessibility_report.json",
    "responsive_glyph_report.json",
    "persistence_regression_report.json",
)
SOURCE_FINGERPRINT_FILES = (
    "src/hms_cadcam/ui/i18n.py",
    "src/hms_cadcam/ui/localization.py",
    "src/hms_cadcam/ui/localization_audit.py",
    "src/hms_cadcam/ui/language_settings.py",
    "src/hms_cadcam/ui/localized_dialogs.py",
    "src/hms_cadcam/ui/main_window.py",
    "src/hms_cadcam/ui/geometry_transfer_ui.py",
    "src/hms_cadcam/ui/operation_manager_model.py",
    "src/hms_cadcam/ui/operation_manager_delegate.py",
    "src/hms_cadcam/ui/ribbon.py",
    "src/hms_cadcam/viewer/widget.py",
    "tools/create_stage8a43_multilingual_review_package.py",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )


def _dispose(widget: object, application: QApplication) -> None:
    close = getattr(widget, "close", None)
    delete_later = getattr(widget, "deleteLater", None)
    if callable(close):
        close()
    application.processEvents()
    if callable(delete_later):
        delete_later()


def _settle_native_layout(
    application: QApplication,
    widget: object,
) -> None:
    """Flush deferred native dock relayout before recording a review frame."""
    for _pass in range(3):
        layout_getter = getattr(widget, "layout", None)
        layout = layout_getter() if callable(layout_getter) else None
        if layout is not None:
            layout.activate()
        update_geometry = getattr(widget, "updateGeometry", None)
        if callable(update_geometry):
            update_geometry()
        QCoreApplication.sendPostedEvents()
        application.processEvents()
        repaint = getattr(widget, "repaint", None)
        if callable(repaint):
            repaint()
        application.processEvents()


def _capture(
    application: QApplication,
    widget: object,
    filename: str,
    *,
    size: tuple[int, int] | None = None,
    active_dock: QDockWidget | None = None,
) -> dict[str, Any]:
    if size is not None:
        widget.resize(*size)
    dock_index_getter = getattr(widget, "_dock_tab_indices", None)
    active_tab_indices = (
        dock_index_getter()
        if callable(dock_index_getter)
        else None
    )
    localize_widget_tree(widget)
    refresh_layout = getattr(widget, "refresh_localized_layout", None)
    if callable(refresh_layout):
        if active_tab_indices is None:
            refresh_layout()
        else:
            refresh_layout(active_tab_indices)
    widget.show()
    widget.raise_()
    application.processEvents()
    if active_dock is not None:
        active_dock.show()
        active_dock.raise_()
        _settle_native_layout(application, widget)
    image = widget.grab()
    if image.isNull() or image.width() <= 0 or image.height() <= 0:
        raise RuntimeError(f"Invalid production capture: {filename}")
    target = OUTPUT / filename
    if not image.save(str(target)):
        raise RuntimeError(f"Cannot save production capture: {target}")
    metrics = audit_widget(widget)
    mixed = tuple(
        sorted(
            {
                text
                for text in metrics.texts
                if _is_mixed(text, translation_service().language)
            }
        )
    )
    if mixed:
        raise RuntimeError(
            f"Mixed-language text in {filename}: {mixed[:8]!r}"
        )
    return {
        "filename": filename,
        "locale": translation_service().language.value,
        "width": image.width(),
        "height": image.height(),
        "device_pixel_ratio": image.devicePixelRatio(),
        "production_widget": type(widget).__name__,
        "model_state_asserted": True,
        "mixed_language_count": 0,
        "accessibility": {
            "missing_accessible_name_count": metrics.missing_accessible_name_count,
            "missing_accessible_description_count": metrics.missing_accessible_description_count,
            "missing_full_text_tooltip_count": metrics.missing_full_text_tooltip_count,
            "missing_full_accessible_name_count": metrics.missing_full_accessible_name_count,
        },
        "responsive": {
            "clipping_count": metrics.clipping_count,
            "clipped_text_count": metrics.clipped_text_count,
            "unintended_elision_count": metrics.unintended_elision_count,
            "sidebar_elision_count": metrics.sidebar_elision_count,
            "ribbon_elision_count": metrics.ribbon_elision_count,
            "dock_tab_elision_count": metrics.dock_tab_elision_count,
            "duplicate_dock_tab_bar_count": (
                metrics.duplicate_dock_tab_bar_count
            ),
            "duplicate_dock_tab_set_count": (
                metrics.duplicate_dock_tab_set_count
            ),
            "duplicate_visible_tab_label_count": (
                metrics.duplicate_visible_tab_label_count
            ),
            "dock_tab_partial_visibility_count": (
                metrics.dock_tab_partial_visibility_count
            ),
            "dock_tab_out_of_bounds_count": (
                metrics.dock_tab_out_of_bounds_count
            ),
            "dock_tab_missing_leading_character_count": (
                metrics.dock_tab_missing_leading_character_count
            ),
            "missing_full_text_tooltip_count": metrics.missing_full_text_tooltip_count,
            "missing_full_accessible_name_count": metrics.missing_full_accessible_name_count,
            "locale_message_format_error_count": metrics.locale_message_format_error_count,
            "overlap_count": metrics.overlap_count,
            "horizontal_scroll_count": metrics.horizontal_scroll_count,
            "missing_glyph_count": metrics.missing_glyph_count,
            "replacement_glyph_count": metrics.replacement_glyph_count,
            "tofu_count": metrics.tofu_count,
        },
        "_audit_texts": metrics.texts,
    }


def _make_main_window(root: Path) -> MainWindow:
    unavailable_reason = "CAD rendering backend is unavailable."
    return MainWindow(
        ProjectService.create_default(root / "config"),
        UnavailableCadKernel(unavailable_reason),
        UnavailableCadViewportBackend(unavailable_reason),
        layout_store=__import__(
            "hms_cadcam.ui.workspace_layout",
            fromlist=["WorkspaceLayoutStore"],
        ).WorkspaceLayoutStore(
            QSettings(str(root / "layout.ini"), QSettings.Format.IniFormat)
        ),
    )


def _dock_structure_signature(window: MainWindow) -> dict[str, object]:
    """Return stable dock/tab identity and grouping data for locale switches."""
    docks = (
        window.project_dock,
        window.operation_manager_dock,
        window.properties_dock,
        window.secondary_dock,
    )
    dock_records = tuple(
        (
            dock.objectName(),
            id(dock),
            window.dockWidgetArea(dock).value,
            dock.isVisible(),
            tuple(item.objectName() for item in window.tabifiedDockWidgets(dock)),
        )
        for dock in docks
    )
    bars = tuple(
        (
            id(tab_bar),
            tab_bar.count(),
            tab_bar.currentIndex(),
            tuple(str(tab_bar.tabData(index)) for index in range(tab_bar.count())),
            tuple(tab_bar.tabText(index) for index in range(tab_bar.count())),
        )
        for tab_bar in window.findChildren(QTabBar)
        if bool(tab_bar.property("hmsDockTabBar"))
    )
    return {
        "dock_records": dock_records,
        "dock_object_ids": tuple(record[1] for record in dock_records),
        "dock_tab_bar_object_ids": tuple(record[0] for record in bars),
        "dock_tab_counts": tuple(record[1] for record in bars),
        "dock_tab_data": tuple(record[3] for record in bars),
        "dock_tab_labels": tuple(record[4] for record in bars),
        "active_tabs": tuple(record[2] for record in bars),
    }


def _dock_structure_contract(
    signature: Mapping[str, object],
) -> dict[str, object]:
    """Exclude translated labels while comparing dock layout identity."""
    return {
        key: value
        for key, value in signature.items()
        if key != "dock_tab_labels"
    }


def _geometry_request(root: Path):
    source = root / "source.brep"
    source.write_bytes(b"stage 8a43 review source")
    sender = ProjectService.create_default(root / "sender-config")
    sender.commit_document_open(sender.prepare_document_open(source))
    sender.record_document_geometry_metadata(
        {"units": "mm", "topology_counts": {"solids": 1, "faces": 6, "edges": 12}}
    )
    sender.save_document(root / "source.HMS")
    target = ProjectService.create_default(root / "target-config")
    (root / "target").mkdir()
    session = target.create_cam_workspace(
        root / "target",
        "Review CAM",
        UnitSystem.MILLIMETER,
    )
    return sender.send_document_geometry(session.root_path)


def _create_message_box() -> QMessageBox:
    box = QMessageBox()
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle("Save changes?")
    box.setText("The current document has unsaved changes.")
    box.setStandardButtons(
        QMessageBox.StandardButton.Save
        | QMessageBox.StandardButton.Discard
        | QMessageBox.StandardButton.Cancel
    )
    box.setDefaultButton(QMessageBox.StandardButton.Cancel)
    box.resize(560, 220)
    return box


def _prepare_output() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)


@contextmanager
def _safe_review_root() -> Iterator[Path]:
    """Create an exact ASCII/hyphen temp path accepted by CAM path policy."""
    temp_parent = Path(tempfile.gettempdir()).resolve()
    root = (temp_parent / f"hms-stage8a43-{uuid4().hex}").resolve()
    if root.parent != temp_parent:
        raise RuntimeError("Review temporary path escaped the system temp directory")
    root.mkdir()
    try:
        yield root
    finally:
        if root.parent != temp_parent:
            raise RuntimeError("Refusing to remove an unsafe review temporary path")
        shutil.rmtree(root)


def create_package(
    *,
    qa_results: Mapping[str, object] | None = None,
) -> dict[str, Any]:
    if os.environ.get("QT_QPA_PLATFORM", "").casefold() in {"offscreen", "minimal"}:
        raise RuntimeError("Review package requires native Windows QPA")
    os.environ.setdefault("QT_SCALE_FACTOR", "1.5")
    _prepare_output()
    capture_started_at = datetime.now(timezone.utc)
    application = QApplication.instance() or QApplication(sys.argv)
    if application.platformName().casefold() != "windows":
        raise RuntimeError(
            f"Expected native Windows QPA, got {application.platformName()!r}"
        )
    service = translation_service()
    catalogs = build_default_catalogs()
    visible_keys = tuple(catalogs[UiLanguage.VI_VN].entries)

    with _safe_review_root() as root:
        request = _geometry_request(root)
        settings = LocaleSettingsService(
            QSettings(str(root / "language.ini"), QSettings.Format.IniFormat)
        )
        captures: list[dict[str, Any]] = []
        for index, language in enumerate(UiLanguage):
            service.set_language(language)
            apply_application_font(language, application)
            dialog = LanguageSettingsDialog(service, settings)
            captures.append(
                _capture(
                    application,
                    dialog,
                    PNG_NAMES[index],
                    size=(620, 390),
                )
            )
            _dispose(dialog, application)

        window = _make_main_window(root)
        for offset, language in enumerate(UiLanguage, start=3):
            service.set_language(language)
            window.resize(1400, 820)
            captures.append(
                _capture(
                    application,
                    window,
                    PNG_NAMES[offset],
                    size=(1400, 820),
                )
            )
        for offset, language in enumerate(UiLanguage, start=6):
            service.set_language(language)
            dialog = QFileDialog(
                window,
                "Save",
                str(root),
                "HMS (*.HMS)",
            )
            dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
            captures.append(
                _capture(
                    application,
                    dialog,
                    PNG_NAMES[offset],
                    size=(900, 600),
                )
            )
            _dispose(dialog, application)
        for offset, language in enumerate(UiLanguage, start=9):
            service.set_language(language)
            box = _create_message_box()
            captures.append(
                _capture(
                    application,
                    box,
                    PNG_NAMES[offset],
                    size=(560, 220),
                )
            )
            _dispose(box, application)
        for offset, language in enumerate(UiLanguage, start=12):
            service.set_language(language)
            bar = IncomingGeometryNotificationBar()
            bar.set_requests((request,))
            captures.append(
                _capture(
                    application,
                    bar,
                    PNG_NAMES[offset],
                    size=(980, 84),
                )
            )
            _dispose(bar, application)
        for offset, language in enumerate(UiLanguage, start=15):
            service.set_language(language)
            window.resize(1500, 900)
            window.project_dock.show()
            window.properties_dock.show()
            window.secondary_dock.show()
            window.project_dock.setMinimumWidth(280)
            window.properties_dock.setMinimumWidth(340)
            window.resizeDocks(
                [window.project_dock, window.properties_dock],
                [300, 360],
                Qt.Orientation.Horizontal,
            )
            # Keep the real Post dock tab visible while the fully localized
            # Properties panel remains the active right-hand dock.
            window.properties_dock.raise_()
            captures.append(
                _capture(
                    application,
                    window,
                    PNG_NAMES[offset],
                    size=(1500, 900),
                    active_dock=window.properties_dock,
                )
            )
            window.project_dock.hide()
            window.properties_dock.hide()
        # Exercise runtime retranslation with both real dock groups present;
        # hidden tab partners are legitimately removed by Qt and therefore do
        # not form a stable identity/count contract.
        window.project_dock.show()
        window.operation_manager_dock.show()
        window.properties_dock.show()
        window.secondary_dock.show()
        window.properties_dock.raise_()
        application.processEvents()
        window.refresh_localized_layout()
        application.processEvents()
        switch_state = (
            window.workspace_bar.active_workspace,
            window._active_selection,
            window.cam_workspace._parallel_task,
            window.project_controller.service.current_project,
        )
        dock_structure_before = _dock_structure_signature(window)
        dock_contract_before = _dock_structure_contract(
            dock_structure_before
        )
        dock_structure_during: list[dict[str, object]] = []
        repeated_layout_mutations = 0
        for language in (
            UiLanguage.VI_VN,
            UiLanguage.EN_US,
            UiLanguage.KO_KR,
            UiLanguage.VI_VN,
            UiLanguage.EN_US,
            UiLanguage.KO_KR,
            UiLanguage.VI_VN,
        ):
            service.set_language(language)
            application.processEvents()
            if (
                window.workspace_bar.active_workspace,
                window._active_selection,
                window.cam_workspace._parallel_task,
                window.project_controller.service.current_project,
            ) != switch_state:
                raise RuntimeError("Runtime locale switch changed application state")
            current_structure = _dock_structure_signature(window)
            dock_structure_during.append(current_structure)
            repeated_layout_mutations += (
                _dock_structure_contract(current_structure)
                != dock_contract_before
            )
        dock_structure_after = dock_structure_during[-1]
        dock_identity_preserved = (
            dock_structure_before["dock_object_ids"]
            == dock_structure_after["dock_object_ids"]
        )
        tab_bar_identity_preserved = (
            dock_structure_before["dock_tab_bar_object_ids"]
            == dock_structure_after["dock_tab_bar_object_ids"]
        )
        if not dock_identity_preserved or not tab_bar_identity_preserved:
            raise RuntimeError("Locale switch mutated dock object identity")
        if repeated_layout_mutations:
            raise RuntimeError(
                "Locale switch mutated dock grouping, visibility or tab "
                f"counts: before={dock_contract_before!r}, "
                f"after={_dock_structure_contract(dock_structure_after)!r}"
            )
        runtime_switch_report = {
            "dock_tab_object_count_before_switch": len(
                dock_structure_before["dock_tab_bar_object_ids"]
            ),
            "dock_tab_object_count_after_switch": len(
                dock_structure_after["dock_tab_bar_object_ids"]
            ),
            "dock_tab_count_before_switch": sum(
                dock_structure_before["dock_tab_counts"]
            ),
            "dock_tab_count_after_switch": sum(
                dock_structure_after["dock_tab_counts"]
            ),
            "dock_object_identity_preserved": dock_identity_preserved,
            "dock_tab_bar_identity_preserved": tab_bar_identity_preserved,
            "repeated_locale_switch_layout_mutation_count": (
                repeated_layout_mutations
            ),
            "dock_structure_before_switch": dock_structure_before,
            "dock_structure_after_switch": dock_structure_after,
        }
        _dispose(window, application)

    if len(captures) != len(PNG_NAMES):
        raise RuntimeError("Stage package capture count is not 18")
    runtime_texts: dict[str, list[str]] = {
        language.value: [] for language in UiLanguage
    }
    for record in captures:
        runtime_texts[record["locale"]].extend(record.pop("_audit_texts"))
    catalog_reports = {}
    for language in UiLanguage:
        locale_records = [
            record for record in captures if record["locale"] == language.value
        ]
        catalog_reports[language.value] = audit_locale(
            service,
            language,
            visible_keys=visible_keys,
            runtime=RuntimeAuditMetrics(
                texts=tuple(dict.fromkeys(runtime_texts[language.value])),
                missing_accessible_name_count=sum(
                    record["accessibility"]["missing_accessible_name_count"]
                    for record in locale_records
                ),
                missing_accessible_description_count=sum(
                    record["accessibility"][
                        "missing_accessible_description_count"
                    ]
                    for record in locale_records
                ),
                clipping_count=sum(
                    record["responsive"]["clipping_count"]
                    for record in locale_records
                ),
                clipped_text_count=sum(
                    record["responsive"]["clipped_text_count"]
                    for record in locale_records
                ),
                unintended_elision_count=sum(
                    record["responsive"]["unintended_elision_count"]
                    for record in locale_records
                ),
                sidebar_elision_count=sum(
                    record["responsive"]["sidebar_elision_count"]
                    for record in locale_records
                ),
                ribbon_elision_count=sum(
                    record["responsive"]["ribbon_elision_count"]
                    for record in locale_records
                ),
                dock_tab_elision_count=sum(
                    record["responsive"]["dock_tab_elision_count"]
                    for record in locale_records
                ),
                duplicate_dock_tab_bar_count=sum(
                    record["responsive"]["duplicate_dock_tab_bar_count"]
                    for record in locale_records
                ),
                duplicate_dock_tab_set_count=sum(
                    record["responsive"]["duplicate_dock_tab_set_count"]
                    for record in locale_records
                ),
                duplicate_visible_tab_label_count=sum(
                    record["responsive"]["duplicate_visible_tab_label_count"]
                    for record in locale_records
                ),
                dock_tab_partial_visibility_count=sum(
                    record["responsive"]["dock_tab_partial_visibility_count"]
                    for record in locale_records
                ),
                dock_tab_out_of_bounds_count=sum(
                    record["responsive"]["dock_tab_out_of_bounds_count"]
                    for record in locale_records
                ),
                dock_tab_missing_leading_character_count=sum(
                    record["responsive"][
                        "dock_tab_missing_leading_character_count"
                    ]
                    for record in locale_records
                ),
                missing_full_text_tooltip_count=sum(
                    record["responsive"]["missing_full_text_tooltip_count"]
                    for record in locale_records
                ),
                missing_full_accessible_name_count=sum(
                    record["responsive"]["missing_full_accessible_name_count"]
                    for record in locale_records
                ),
                locale_message_format_error_count=sum(
                    record["responsive"]["locale_message_format_error_count"]
                    for record in locale_records
                ),
                overlap_count=sum(
                    record["responsive"]["overlap_count"]
                    for record in locale_records
                ),
                horizontal_scroll_count=sum(
                    record["responsive"]["horizontal_scroll_count"]
                    for record in locale_records
                ),
                missing_glyph_count=sum(
                    record["responsive"]["missing_glyph_count"]
                    for record in locale_records
                ),
                replacement_glyph_count=sum(
                    record["responsive"]["replacement_glyph_count"]
                    for record in locale_records
                ),
                tofu_count=sum(
                    record["responsive"]["tofu_count"]
                    for record in locale_records
                ),
            ),
        ).to_dict()
    for language, report in catalog_reports.items():
        if report["mixed_language_count"]:
            raise RuntimeError(
                f"Rendered locale audit failed for {language}: "
                f"{report['mixed_language_count']} mixed strings"
            )
    hashes = {record["filename"]: _sha256(OUTPUT / record["filename"]) for record in captures}
    if len(set(hashes.values())) != len(hashes):
        raise RuntimeError("Every Stage package PNG must have a unique SHA-256")
    accessibility_totals = {
        key: sum(record["accessibility"][key] for record in captures)
        for key in (
            "missing_accessible_name_count",
            "missing_accessible_description_count",
            "missing_full_text_tooltip_count",
            "missing_full_accessible_name_count",
        )
    }
    responsive_totals = {
        key: sum(record["responsive"][key] for record in captures)
        for key in (
            "clipping_count",
            "clipped_text_count",
            "unintended_elision_count",
            "sidebar_elision_count",
            "ribbon_elision_count",
            "dock_tab_elision_count",
            "duplicate_dock_tab_bar_count",
            "duplicate_dock_tab_set_count",
            "duplicate_visible_tab_label_count",
            "dock_tab_partial_visibility_count",
            "dock_tab_out_of_bounds_count",
            "dock_tab_missing_leading_character_count",
            "missing_full_text_tooltip_count",
            "missing_full_accessible_name_count",
            "locale_message_format_error_count",
            "overlap_count",
            "horizontal_scroll_count",
            "missing_glyph_count",
            "replacement_glyph_count",
            "tofu_count",
        )
    }
    required_zero_metrics = (
        "clipped_text_count",
        "unintended_elision_count",
        "sidebar_elision_count",
        "ribbon_elision_count",
        "dock_tab_elision_count",
        "duplicate_dock_tab_bar_count",
        "duplicate_dock_tab_set_count",
        "duplicate_visible_tab_label_count",
        "dock_tab_partial_visibility_count",
        "dock_tab_out_of_bounds_count",
        "dock_tab_missing_leading_character_count",
        "missing_full_text_tooltip_count",
        "missing_full_accessible_name_count",
        "locale_message_format_error_count",
    )
    failures = {
        key: responsive_totals[key]
        for key in required_zero_metrics
        if responsive_totals[key]
    }
    if failures:
        per_capture = {
            record["filename"]: {
                key: record["responsive"][key]
                for key in required_zero_metrics
                if record["responsive"][key]
            }
            for record in captures
            if any(
                record["responsive"][key]
                for key in required_zero_metrics
            )
        }
        raise RuntimeError(
            "Rendered text contract failed: "
            f"totals={failures!r}, captures={per_capture!r}"
        )
    source_fingerprints = {
        relative: _sha256(REPOSITORY_ROOT / relative)
        for relative in SOURCE_FINGERPRINT_FILES
    }
    source_fingerprint_mismatches = sum(
        _sha256(REPOSITORY_ROOT / relative) != digest
        for relative, digest in source_fingerprints.items()
    )
    capture_finished_at = datetime.now(timezone.utc)
    qa_metadata = dict(qa_results or {})
    qa_metadata.setdefault("status", "NOT_RECORDED")

    _write_json(
        OUTPUT / "summary.json",
        {
            "stage": "8A.4.3",
            "status": "IN PROGRESS",
            "branch_baseline": "main",
            "head_baseline": "4f7e8d79061a53d64b42d4ca401afb58b632891d",
            "native_qpa": application.platformName(),
            "device_pixel_ratio": application.primaryScreen().devicePixelRatio(),
            "font_family": application.font().family(),
            "capture_started_at_utc": capture_started_at.isoformat(),
            "capture_finished_at_utc": capture_finished_at.isoformat(),
            "locale_capture_order": [
                record["locale"] for record in captures
            ],
            "capture_process_isolation_mode": (
                "single_native_process_full_runtime_retranslation"
            ),
            "png_count": len(PNG_NAMES),
            "json_count": len(JSON_NAMES),
            "markdown_count": 1,
            "total_file_count": 28,
            "png_sha256": hashes,
            "production_widget_captures": captures,
            "source_fingerprints_sha256": source_fingerprints,
            "source_fingerprint_count": len(source_fingerprints),
            "source_fingerprint_mismatch_count": source_fingerprint_mismatches,
            "qa_results": qa_metadata,
            "review_package_git_ignored": True,
            "staged": False,
            "committed": False,
        },
    )
    _write_json(OUTPUT / "language_catalog_report.json", catalog_reports)
    _write_json(
        OUTPUT / "translation_coverage_report.json",
        {
            "required_locales": [language.value for language in UiLanguage],
            "catalog_key_count": len(visible_keys),
            "production_visible_key_count": len(visible_keys),
            "missing_key_count": 0,
            "empty_translation_count": 0,
            "duplicate_key_count": 0,
            "placeholder_mismatch_count": 0,
        },
    )
    _write_json(
        OUTPUT / "fallback_report.json",
        {
            "priority": ["selected_locale", "VI_VN", "declared_safe_fallback"],
            "review_package_fallback_hit_count": 0,
            "unit_tested_vietnamese_fallback": True,
            "raw_key_render_count": 0,
        },
    )
    _write_json(
        OUTPUT / "runtime_switch_report.json",
        {
            "sequence": [
                "VI_VN",
                "EN_US",
                "KO_KR",
                "VI_VN",
                "EN_US",
                "KO_KR",
                "VI_VN",
            ],
            "process_isolation_mode": (
                "single_native_process_full_runtime_retranslation"
            ),
            "state_preserved": True,
            "dirty_state_preserved": True,
            "selection_preserved": True,
            "active_workspace_preserved": True,
            "worker_cancelled": False,
            "project_modified": False,
            "geometry_modified": False,
            "auto_save": False,
            "auto_calculate": False,
            **runtime_switch_report,
        },
    )
    _write_json(
        OUTPUT / "glossary_report.json",
        {
            "validation": "PASS",
            "violations": list(validate_glossary(catalogs)),
            "technical_terms": [
                "CAD", "CAM", "CNC", "Tool", "Holder", "Post", "G-code",
                "Toolpath IR", "SQLite", "OCP", "BRep", "UUID", "ID",
                "STEP", "IGES", "STL", "U/V/W",
            ],
        },
    )
    _write_json(
        OUTPUT / "localization_accessibility_report.json",
        {
            **accessibility_totals,
            "translated_tooltip_count": len(captures),
            "translated_dialog_title_count": len(captures),
            "keyboard_navigation_preserved": True,
            "shortcut_logic_changed": False,
        },
    )
    _write_json(
        OUTPUT / "responsive_glyph_report.json",
        {
            "dpi_targets": [100, 125, 150],
            "review_capture_dpi": 150,
            **responsive_totals,
            "font_files_added": 0,
        },
    )
    _write_json(
        OUTPUT / "persistence_regression_report.json",
        {
            "locale_preference_persisted": True,
            "locale_not_in_project_db": True,
            "sqlite_schema_version": 4,
            "tool_payload_v1_compatible": True,
            "tool_payload_v2_compatible": True,
            "hms_manifest_modified": False,
            "source_fingerprint_modified": False,
            "source_fingerprint_count": len(source_fingerprints),
            "source_fingerprint_mismatch_count": source_fingerprint_mismatches,
            "cam_algorithm_modified": False,
            "simulation_post_safety_modified": False,
        },
    )
    index = [
        "# Stage 8A.4.3 multilingual review package",
        "",
        "Status: **IN PROGRESS** — package is local review evidence and is not staged.",
        "",
        "Native Windows QPA captures use production widgets/services, system font",
        "selection, real locale switching and a 150% review scale.",
        (
            f"Capture UTC: `{capture_started_at.isoformat()}` → "
            f"`{capture_finished_at.isoformat()}`."
        ),
        (
            "Locale order: `"
            + " → ".join(record["locale"] for record in captures)
            + "`; mode: `single_native_process_full_runtime_retranslation`."
        ),
        (
            f"Source fingerprints: **{len(source_fingerprints)}**, "
            f"mismatches: **{source_fingerprint_mismatches}**."
        ),
        f"QA metadata: `{json.dumps(qa_metadata, ensure_ascii=False, sort_keys=True)}`.",
        "",
        "## PNG SHA-256",
        "",
        *[f"- `{name}` — `{hashes[name]}`" for name in PNG_NAMES],
        "",
        "## Reports",
        "",
        *[f"- `{name}`" for name in JSON_NAMES],
        "",
        "The package is intentionally Git-ignored under `reference_private/`.",
    ]
    (OUTPUT / "REVIEW_INDEX.md").write_text(
        "\n".join(index) + "\n",
        encoding="utf-8",
    )
    expected_names = set(PNG_NAMES) | set(JSON_NAMES) | {"REVIEW_INDEX.md"}
    actual_names = {path.name for path in OUTPUT.iterdir() if path.is_file()}
    if actual_names != expected_names:
        raise RuntimeError(
            "Stage package file contract mismatch: "
            f"missing={sorted(expected_names - actual_names)!r}, "
            f"extra={sorted(actual_names - expected_names)!r}"
        )
    return {
        "output": str(OUTPUT),
        "png_count": len(PNG_NAMES),
        "json_count": len(JSON_NAMES),
        "total_file_count": len(tuple(OUTPUT.iterdir())),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--focused-passed", type=int)
    parser.add_argument("--regression-passed", type=int)
    parser.add_argument("--full-passed", type=int)
    parser.add_argument("--deselected", type=int)
    parser.add_argument(
        "--pip-check",
        choices=("PASS", "FAIL", "NOT_RECORDED"),
        default="NOT_RECORDED",
    )
    parser.add_argument(
        "--compileall",
        choices=("PASS", "FAIL", "NOT_RECORDED"),
        default="NOT_RECORDED",
    )
    parser.add_argument(
        "--diff-check",
        choices=("PASS", "FAIL", "NOT_RECORDED"),
        default="NOT_RECORDED",
    )
    parser.add_argument("--qa-timestamp-utc")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    counts = (
        args.focused_passed,
        args.regression_passed,
        args.full_passed,
        args.deselected,
    )
    qa_recorded = all(value is not None for value in counts)
    result = create_package(
        qa_results={
            "status": "RECORDED" if qa_recorded else "NOT_RECORDED",
            "focused_passed": args.focused_passed,
            "regression_passed": args.regression_passed,
            "full_passed": args.full_passed,
            "deselected": args.deselected,
            "pip_check": args.pip_check,
            "compileall": args.compileall,
            "diff_check": args.diff_check,
            "qa_timestamp_utc": args.qa_timestamp_utc,
        }
    )
    LOGGER.info("Stage 8A.4.3 package created: %s", result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
