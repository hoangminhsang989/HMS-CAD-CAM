from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDockWidget,
    QLabel,
    QLineEdit,
    QPushButton,
    QWidget,
)
import shiboken6

from hms_cadcam.cad.models import CadDocumentId
from hms_cadcam.cad.unavailable import UnavailableCadKernel
from hms_cadcam.cam.application.cam3d_editor import (
    Cam3DEditorDiagnosticCode,
    Cam3DEditorField,
    Cam3DEditorReadiness,
    Cam3DProjectContext,
    Cam3DToolAssemblyChoice,
    Cam3DToolProfileChoice,
)
from hms_cadcam.cam.application.cam3d_selection import Cam3DSelectionState
from hms_cadcam.cam.persistence.models import CamProjectSnapshot
from hms_cadcam.project.service import ProjectService
from hms_cadcam.ui.cam3d_editor_binding import Cam3DEditorBindingController
from hms_cadcam.ui.cam3d_editor_widget import Cam3DEditorWidget
from hms_cadcam.ui.cam3d_function_panel import Cam3DFunctionPanel
from hms_cadcam.ui.feature_flags import UiFeatureFlag, UiFeatureFlags
from hms_cadcam.ui.i18n import UiLanguage, translation_service
from hms_cadcam.ui.main_window import MainWindow
from hms_cadcam.viewer.unavailable_backend import UnavailableCadViewportBackend
from tests.unit.test_cam3d_editor_binding_wp2b import (
    _context,
    _resource,
    _selection,
)


EXPECTED_OBJECT_NAMES = (
    "Cam3DToolAssemblyCombo",
    "Cam3DToolProfileCombo",
    "Cam3DNumeric_tolerance_mm",
    "Cam3DNumeric_allowance_mm",
    "Cam3DNumeric_clearance_z_mm",
    "Cam3DNumeric_retract_z_mm",
    "Cam3DNumeric_approach_distance_mm",
    "Cam3DNumeric_link_clearance_mm",
)


@pytest.fixture(scope="module", autouse=True)
def _qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _restore_language() -> None:
    service = translation_service()
    service.set_language(UiLanguage.VI_VN)
    yield
    service.set_language(UiLanguage.VI_VN)


def _ready_controller() -> tuple[Cam3DEditorBindingController, object]:
    context = _context()
    value, assembly, _profile = _resource()
    controller = Cam3DEditorBindingController()
    render = controller.bind(
        context,
        _selection(context),
        tools=(value,),
        assemblies=(assembly,),
    )
    render = controller.assign_tool_assembly(render.tool_options[0].choice)
    render = controller.assign_tool_profile(render.profile_options[0].choice)
    return controller, render


def _flags(enabled: bool) -> UiFeatureFlags:
    return UiFeatureFlags(
        {
            UiFeatureFlag.POST_ASSEMBLY_9A7: False,
            UiFeatureFlag.CAM_3D_9A8: enabled,
        }
    )


def _window(tmp_path: Path, *, enabled: bool) -> MainWindow:
    return MainWindow(
        ProjectService.create_default(tmp_path / ("on" if enabled else "off")),
        UnavailableCadKernel("WP2B-B panel test"),
        UnavailableCadViewportBackend("WP2B-B panel test"),
        ui_feature_flags=_flags(enabled),
    )


def test_panel_mounts_exact_eight_fields_in_locked_sections() -> None:
    panel = Cam3DFunctionPanel(feature_enabled=True)
    controls = panel.editor_widget.mutation_controls

    assert len(controls) == 8
    assert tuple(control.objectName() for control in controls) == (
        EXPECTED_OBJECT_NAMES
    )
    assert isinstance(controls[0], QComboBox)
    assert isinstance(controls[1], QComboBox)
    assert all(isinstance(control, QLineEdit) for control in controls[2:])
    for key in ("tool", "tolerance", "allowance", "safe_motion", "diagnostics"):
        section = panel.editor_widget.section_widget(key)
        assert section.parent().objectName() == f"Cam3DSection_{key}"
        assert not section.isHidden()
    assert all(
        control.accessibleName() and control.accessibleDescription()
        for control in controls
    )
    assert all(not control.isEnabled() for control in controls)
    panel.deleteLater()


def test_tab_order_and_object_names_are_unique_and_deterministic() -> None:
    widget = Cam3DEditorWidget()
    controls = widget.mutation_controls
    names = [
        child.objectName()
        for section_key in (
            "tool",
            "tolerance",
            "allowance",
            "safe_motion",
            "diagnostics",
        )
        for child in widget.section_widget(section_key).findChildren(QWidget)
        if child.objectName()
    ]

    assert len(names) == len(set(names))
    cursor = controls[0]
    ordered = [cursor]
    for _index in range(100):
        cursor = cursor.nextInFocusChain()
        if cursor is controls[0]:
            break
        if cursor in controls:
            ordered.append(cursor)
    assert tuple(ordered) == controls
    widget.deleteLater()


def test_render_uses_signal_blockers_and_typed_combo_item_data() -> None:
    controller, render = _ready_controller()
    widget = Cam3DEditorWidget()
    assembly_emissions: list[object] = []
    profile_emissions: list[object] = []
    numeric_emissions: list[tuple[object, object]] = []
    widget.tool_assembly_changed.connect(assembly_emissions.append)
    widget.tool_profile_changed.connect(profile_emissions.append)
    widget.numeric_field_changed.connect(
        lambda field, value: numeric_emissions.append((field, value))
    )

    widget.set_render_state(render)
    widget.set_render_state(render)

    assert assembly_emissions == profile_emissions == numeric_emissions == []
    assert isinstance(widget.tool_assembly_combo.itemData(1), Cam3DToolAssemblyChoice)
    assert isinstance(widget.tool_profile_combo.itemData(1), Cam3DToolProfileChoice)
    assert widget.tool_assembly_combo.currentIndex() == 1
    assert widget.tool_profile_combo.currentIndex() == 1
    assert controller.state.parameters == render.parameters
    widget.deleteLater()


def test_user_intents_emit_once_with_typed_identity_and_raw_numeric_text() -> None:
    context = _context()
    value, assembly, _profile = _resource()
    controller = Cam3DEditorBindingController()
    initial = controller.bind(
        context,
        _selection(context),
        tools=(value,),
        assemblies=(assembly,),
    )
    widget = Cam3DEditorWidget()
    assemblies: list[object] = []
    profiles: list[object] = []
    numeric: list[tuple[object, object]] = []
    widget.tool_assembly_changed.connect(assemblies.append)
    widget.tool_profile_changed.connect(profiles.append)
    widget.numeric_field_changed.connect(
        lambda field, value: numeric.append((field, value))
    )

    widget.set_render_state(initial)
    widget.tool_assembly_combo.setCurrentIndex(1)
    widget.tool_assembly_combo.setCurrentIndex(0)
    assert len(assemblies) == 2
    assert isinstance(assemblies[0], Cam3DToolAssemblyChoice)
    assert assemblies[1] is None

    selected = controller.assign_tool_assembly(initial.tool_options[0].choice)
    widget.set_render_state(selected)
    widget.tool_profile_combo.setCurrentIndex(1)
    assert len(profiles) == 1
    assert isinstance(profiles[0], Cam3DToolProfileChoice)

    edit = widget.numeric_control(Cam3DEditorField.TOLERANCE_MM)
    edit.setText("not-a-number")
    edit.editingFinished.emit()
    assert numeric == (
        [(Cam3DEditorField.TOLERANCE_MM, "not-a-number")]
    )
    widget.deleteLater()


def test_invalid_numeric_input_is_not_clamped_and_maps_to_its_field() -> None:
    context = _context()
    controller = Cam3DEditorBindingController()
    state = controller.bind(context, _selection(context, with_part=False))
    widget = Cam3DEditorWidget()
    widget.set_render_state(state)
    before = controller.state.parameters

    invalid = controller.replace_numeric_text(
        Cam3DEditorField.TOLERANCE_MM,
        "999",
    )
    widget.set_render_state(invalid)

    edit = widget.numeric_control(Cam3DEditorField.TOLERANCE_MM)
    assert controller.state.parameters == before
    assert edit.text() == "0.01"
    assert edit.property("diagnosticCount") == 1
    assert invalid.field_diagnostics[0][0] is Cam3DEditorField.TOLERANCE_MM
    assert invalid.field_diagnostics[0][1][0].code is (
        Cam3DEditorDiagnosticCode.VALUE_ABOVE_MAXIMUM
    )
    assert all("999" not in widget.diagnostics_list.item(row).text() for row in range(widget.diagnostics_list.count()))
    widget.deleteLater()


def test_read_only_renders_values_readiness_and_diagnostics_but_disables_mutation() -> None:
    context = _context(read_only=True)
    controller = Cam3DEditorBindingController()
    render = controller.bind(
        context,
        _selection(context),
    )
    widget = Cam3DEditorWidget()
    widget.set_render_state(render)

    assert render.readiness is Cam3DEditorReadiness.READ_ONLY
    assert all(not control.isEnabled() for control in widget.mutation_controls)
    assert widget.readiness_value.text()
    assert widget.diagnostics_list.count() > 0
    visible = "\n".join(
        widget.diagnostics_list.item(row).text()
        for row in range(widget.diagnostics_list.count())
    )
    assert str(context.project_id) not in visible
    assert all(code.value not in visible for code in Cam3DEditorDiagnosticCode)
    assert "Traceback" not in visible
    assert "ValueError" not in visible
    widget.deleteLater()


def test_vi_en_ko_retranslation_preserves_typed_state_and_domain_labels() -> None:
    context = _context()
    value, assembly, _profile = _resource("Tool Profile")
    controller = Cam3DEditorBindingController()
    render = controller.bind(
        context,
        _selection(context),
        tools=(value,),
        assemblies=(assembly,),
    )
    render = controller.assign_tool_assembly(render.tool_options[0].choice)
    before = controller.state
    widget = Cam3DEditorWidget()
    widget.set_render_state(render)
    prompts: list[str] = []

    for language in (UiLanguage.VI_VN, UiLanguage.EN_US, UiLanguage.KO_KR):
        translation_service().set_language(language)
        widget.retranslate_ui()
        prompts.append(widget.tool_assembly_combo.itemText(0))
        assert widget.tool_assembly_combo.itemText(1) == "Tool Profile"
        assert controller.state is before

    assert len(set(prompts)) == 3
    widget.deleteLater()


def test_feature_topology_is_exact_and_initialization_is_singleton(
    tmp_path: Path,
) -> None:
    app = QApplication.instance() or QApplication([])
    feature_off = _window(tmp_path, enabled=False)
    assert len(feature_off.findChildren(QDockWidget)) == 8
    assert not hasattr(feature_off, "_cam3d_selection_service")
    assert not hasattr(feature_off, "_cam3d_editor_binding_controller")
    assert not hasattr(feature_off, "cam3d_function_panel")
    assert not any(
        action.objectName() == "Cam3DFunctionOpenAction"
        for action in feature_off.findChildren(QAction)
    )
    feature_off.close()
    feature_off.deleteLater()
    app.processEvents()

    feature_on = _window(tmp_path, enabled=True)
    assert len(feature_on.findChildren(QDockWidget)) == 9
    assert len(feature_on.findChildren(Cam3DFunctionPanel)) == 1
    assert len(feature_on.findChildren(Cam3DEditorWidget)) == 1
    assert sum(
        action.objectName() == "Cam3DFunctionOpenAction"
        for action in feature_on.findChildren(QAction)
    ) == 1
    controller = feature_on._cam3d_editor_binding_controller
    feature_on.cam3d_function_action.trigger()
    feature_on.cam3d_function_action.trigger()
    app.processEvents()
    assert feature_on._cam3d_editor_binding_controller is controller
    assert len(feature_on.findChildren(Cam3DEditorWidget)) == 1
    feature_on.close()
    feature_on.deleteLater()
    app.processEvents()


def test_main_window_editor_edits_are_runtime_only_and_close_resets(
    tmp_path: Path,
) -> None:
    source = tmp_path / "part.step"
    source.write_text(
        "ISO-10303-21;END-ISO-10303-21;",
        encoding="utf-8",
    )
    service = ProjectService.create_default(tmp_path / "runtime_only")
    session = service.create_project_from_source(
        tmp_path / "projects",
        "WP2B Runtime Only",
        source,
    )
    value, assembly, _profile = _resource()
    session.cam_snapshot = CamProjectSnapshot(
        tool_definitions=(value,),
        tool_assemblies=(assembly,),
    )
    window = MainWindow(
        service,
        UnavailableCadKernel("WP2B-B runtime-only test"),
        UnavailableCadViewportBackend("WP2B-B runtime-only test"),
        ui_feature_flags=_flags(True),
    )
    window._handle_project_change(session)
    before = (
        session.is_dirty,
        session.cam_snapshot,
        session.cam3d_config,
    )

    window.cam3d_function_panel.editor_widget.tool_assembly_combo.setCurrentIndex(
        1
    )
    window.cam3d_function_panel.editor_widget.tool_profile_combo.setCurrentIndex(
        1
    )
    window.cam3d_function_panel.numeric_field_changed.emit(
        Cam3DEditorField.ALLOWANCE_MM,
        "0.25",
    )
    window.cam3d_function_panel.numeric_field_changed.emit(
        Cam3DEditorField.TOLERANCE_MM,
        "invalid",
    )
    QApplication.processEvents()

    assert (
        session.is_dirty,
        session.cam_snapshot,
        session.cam3d_config,
    ) == before
    window._handle_project_change(None)
    assert not window._cam3d_editor_binding_controller.state.context.is_open
    assert window._cam3d_selection_service.state.project_id is None
    window.close()

def test_widget_destruction_disconnects_control_callbacks_safely() -> None:
    app = QApplication.instance() or QApplication([])
    widget = Cam3DEditorWidget()
    assert shiboken6.isValid(widget)
    widget.deleteLater()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    assert not shiboken6.isValid(widget)


def test_editor_exposes_no_calculation_or_out_of_scope_controls() -> None:
    widget = Cam3DEditorWidget()
    assert widget.findChildren(QPushButton) == []
    visible = " ".join(
        label.text()
        for key in ("tool", "tolerance", "allowance", "safe_motion", "diagnostics")
        for label in widget.section_widget(key).findChildren(QLabel)
    ).casefold()
    assert "calculate" not in visible
    assert "simulation" not in visible
    assert "g-code" not in visible
    assert "post" not in visible
    widget.deleteLater()
