"""Regression coverage for cumulative Qt state across pytest test boundaries."""

from __future__ import annotations

import shiboken6
from PySide6.QtWidgets import QApplication

from hms_cadcam.ui.i18n import UiLanguage, translation_service
from hms_cadcam.ui.tool_library import ToolDefinitionDialog

from _qt_lifecycle import (
    drain_test_owned_qt_state,
    qt_lifecycle_snapshot,
    top_level_baseline,
)


def test_retranslated_geometry_cycles_deliver_deferred_delete_and_stay_bounded(
    qapp: QApplication,
) -> None:
    service = translation_service()
    baseline_pointers = top_level_baseline(qapp)

    # Counterfactual: the previous close/deleteLater/processEvents sequence leaves
    # both the C++ widget and its hidden top-level entry alive.
    counterfactual = ToolDefinitionDialog()
    counterfactual.show()
    qapp.processEvents()
    counterfactual.close()
    counterfactual.deleteLater()
    qapp.processEvents()
    assert shiboken6.isValid(counterfactual)
    assert qt_lifecycle_snapshot(qapp).top_levels > len(baseline_pointers)
    drain_test_owned_qt_state(qapp, baseline_pointers)
    assert not shiboken6.isValid(counterfactual)
    del counterfactual

    stable = qt_lifecycle_snapshot(qapp)
    maximum_top_levels = stable.top_levels
    maximum_hidden_top_levels = stable.hidden_top_levels
    maximum_modal_top_levels = stable.modal_top_levels
    maximum_all_widgets = stable.all_widgets
    maximum_app_owned_qobjects = stable.app_owned_qobjects

    for cycle in range(24):
        language = tuple(UiLanguage)[cycle % len(UiLanguage)]
        with service.using(language):
            dialog = ToolDefinitionDialog()
            dialog.resize(480 + (cycle % 4) * 80, 360 + (cycle % 3) * 60)
            dialog.show()
            qapp.processEvents()
            dialog.close()
            dialog.deleteLater()
            qapp.processEvents()
            drain_test_owned_qt_state(qapp, baseline_pointers)
            del dialog

        current = qt_lifecycle_snapshot(qapp)
        maximum_top_levels = max(maximum_top_levels, current.top_levels)
        maximum_hidden_top_levels = max(
            maximum_hidden_top_levels, current.hidden_top_levels
        )
        maximum_modal_top_levels = max(
            maximum_modal_top_levels, current.modal_top_levels
        )
        maximum_all_widgets = max(maximum_all_widgets, current.all_widgets)
        maximum_app_owned_qobjects = max(
            maximum_app_owned_qobjects, current.app_owned_qobjects
        )

    final = qt_lifecycle_snapshot(qapp)
    assert maximum_top_levels <= stable.top_levels
    assert maximum_hidden_top_levels <= stable.hidden_top_levels
    assert maximum_modal_top_levels <= stable.modal_top_levels
    assert maximum_all_widgets <= stable.all_widgets + 2
    assert maximum_app_owned_qobjects <= stable.app_owned_qobjects + 4
    assert final.top_levels <= stable.top_levels
    assert final.hidden_top_levels <= stable.hidden_top_levels
    assert final.modal_top_levels <= stable.modal_top_levels
    assert final.running_app_threads == 0
