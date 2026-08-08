"""Qt contract tests for the compact Stage16A three-step wizard."""

from __future__ import annotations

from uuid import uuid4

import pytest
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

from hms_cadcam.cam.domain import (
    CamJobId,
    CamNodeId,
    Revision,
    SetupId,
    ToolAssemblyId,
    ToolDefinitionId,
    ToolProfileListState,
    ToolProfileValueSource,
)
from hms_cadcam.cam.operation_creation import (
    OperationCreationSession,
    OperationCreationState,
    OperationCreationStep,
    OperationToolChoice,
    Stage16AStrategyRegistry,
)
from hms_cadcam.ui.function_editor.model import (
    FunctionEditorField,
    FunctionEditorFieldKind,
    FunctionEditorSection,
    FunctionEditorStrategyKey,
    FunctionEditorSummary,
    ParameterDisclosureLevel,
)
from hms_cadcam.ui.function_editor.schema import FunctionEditorSchema
from hms_cadcam.ui.i18n import UiLanguage, translation_service
from hms_cadcam.ui.operation_creation_wizard import (
    OperationCreationEditorBinding,
    OperationCreationWizard,
)


@pytest.fixture(scope="module")
def application() -> QApplication:
    return QApplication.instance() or QApplication([])


def _session() -> OperationCreationSession:
    return OperationCreationSession.start(
        project_id=uuid4(),
        project_generation=1,
        job_id=CamJobId.new(),
        setup_id=SetupId.new(),
        parent_node_id=CamNodeId.new(),
    )


def _tool(*, compatible: bool) -> OperationToolChoice:
    return OperationToolChoice(
        ToolAssemblyId.new(),
        ToolDefinitionId.new(),
        "Ball D10" if compatible else "Flat D10",
        "Assembly",
        "ball_end_mill" if compatible else "end_mill",
        "D10 mm / R5",
        "Holder HSK",
        compatible,
        (
            "Compatible Tool."
            if compatible
            else "Tool family is not supported by the selected strategy."
        ),
        ToolProfileListState.NOT_CONFIGURED,
        None,
        (ToolProfileValueSource.AUTOMATIC_POLICY,),
        Revision(0),
        Revision(0),
        Revision(0),
    )


def _schema() -> FunctionEditorSchema:
    return FunctionEditorSchema(
        "stage16a_test_editor",
        FunctionEditorStrategyKey("parallel_finishing_3d_stage16a_test"),
        FunctionEditorSummary("Operation", "Parallel"),
        (
            FunctionEditorSection(
                "basic",
                "BASIC",
                (
                    FunctionEditorField(
                        "operation_name",
                        "Operation name",
                        FunctionEditorFieldKind.TEXT,
                        "Finish",
                        required=True,
                        binding_key="node.name",
                    ),
                ),
            ),
            FunctionEditorSection(
                "advanced",
                "ADVANCED",
                (
                    FunctionEditorField(
                        "manual_override",
                        "Manual override",
                        FunctionEditorFieldKind.NUMBER,
                        "1",
                        disclosure_level=ParameterDisclosureLevel.ADVANCED,
                        binding_key="parameters.manual_override",
                    ),
                ),
                disclosure_level=ParameterDisclosureLevel.ADVANCED,
            ),
        ),
    )


class _Adapter:
    def __init__(self) -> None:
        self.compatible = _tool(compatible=True)
        self.incompatible = _tool(compatible=False)
        self.finish_calls = 0
        self.current = True
        self.management_calls = 0

    def strategy_choices(self):
        return Stage16AStrategyRegistry().choices()

    def tool_choices(self, _session, query=""):
        choices = (self.compatible, self.incompatible)
        return tuple(item for item in choices if query.casefold() in item.tool_name.casefold())

    def selected_tool_is_compatible(self, session, strategy_id):
        return (
            session.tool_assembly_id == self.compatible.assembly_id
            and strategy_id in {"parallel_finishing_3d", "z_level_finishing_3d"}
        )

    def build_editor(self, _session):
        def finish(_values):
            self.finish_calls += 1
            return CamNodeId.new()

        return OperationCreationEditorBinding(
            _schema(),
            {"operation_name": "Finish", "manual_override": "1"},
            lambda _values: (),
            finish,
        )

    def context_is_current(self, _session):
        return (self.current, "Project changed" if not self.current else "")

    def open_tool_management(self, _session, _parent):
        self.management_calls += 1


def _select_strategy(wizard: OperationCreationWizard, strategy_id: str) -> None:
    for row in range(wizard.strategy_list.count()):
        item = wizard.strategy_list.item(row)
        if item.data(Qt.ItemDataRole.UserRole) == strategy_id:
            wizard.strategy_list.setCurrentItem(item)
            return
    raise AssertionError(strategy_id)


def _to_step3(wizard: OperationCreationWizard, application: QApplication) -> None:
    _select_strategy(wizard, "parallel_finishing_3d")
    wizard.next_button.click()
    application.processEvents()
    wizard.next_button.click()
    application.processEvents()


def test_three_pages_and_safe_initial_navigation(application: QApplication) -> None:
    adapter = _Adapter()
    wizard = OperationCreationWizard(_session(), adapter)
    assert wizard.pages.count() == 3
    assert wizard.session.current_step is OperationCreationStep.SELECT_OPERATION
    assert wizard.next_button.isDefault()
    assert not wizard.finish_button.isVisible()
    assert adapter.finish_calls == 0
    wizard.close()


def test_incompatible_tool_is_disabled_with_explanation(application: QApplication) -> None:
    adapter = _Adapter()
    wizard = OperationCreationWizard(_session(), adapter)
    _select_strategy(wizard, "parallel_finishing_3d")
    wizard.next_button.click()
    application.processEvents()
    assert wizard.tool_list.topLevelItemCount() == 2
    incompatible = next(
        wizard.tool_list.topLevelItem(row)
        for row in range(wizard.tool_list.topLevelItemCount())
        if "Flat" in wizard.tool_list.topLevelItem(row).text(0)
    )
    assert incompatible.isDisabled()
    assert "không được" in incompatible.toolTip(4)
    wizard.close()


def test_search_and_tool_management_use_existing_ui_action(application: QApplication) -> None:
    adapter = _Adapter()
    wizard = OperationCreationWizard(_session(), adapter)
    _select_strategy(wizard, "parallel_finishing_3d")
    wizard.next_button.click()
    wizard.tool_search.setText("Ball")
    application.processEvents()
    assert wizard.tool_list.topLevelItemCount() == 1
    wizard.manage_tools.click()
    assert adapter.management_calls == 1
    wizard.close()


def test_step3_reuses_function_editor_basic_advanced_and_no_footer(
    application: QApplication,
) -> None:
    wizard = OperationCreationWizard(_session(), _Adapter())
    _to_step3(wizard, application)
    assert wizard.editor_page is not None
    assert wizard.editor_page.schema.editor_id == "stage16a_test_editor"
    assert wizard.editor_page.disclosure_selector.count() == 2
    assert wizard.editor_page.footer.isHidden()
    assert wizard.finish_button.isDefault()
    assert not wizard.next_button.isVisible()
    wizard.close()


def test_back_preserves_strategy_and_tool_but_discards_step3_widget(
    application: QApplication,
) -> None:
    wizard = OperationCreationWizard(_session(), _Adapter())
    _to_step3(wizard, application)
    tool_id = wizard.session.tool_id
    wizard.back_button.click()
    application.processEvents()
    assert wizard.session.current_step is OperationCreationStep.SELECT_TOOL
    assert wizard.session.tool_id == tool_id
    assert wizard.editor_page is None
    wizard.close()


def test_cancel_and_escape_create_nothing(application: QApplication) -> None:
    adapter = _Adapter()
    wizard = OperationCreationWizard(_session(), adapter)
    _to_step3(wizard, application)
    wizard.keyPressEvent(
        QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
    )
    assert wizard.session.state is OperationCreationState.CANCELLED
    assert adapter.finish_calls == 0


def test_finish_creates_once_and_repeated_signal_is_blocked(application: QApplication) -> None:
    adapter = _Adapter()
    wizard = OperationCreationWizard(_session(), adapter)
    created: list[str] = []
    wizard.operation_created.connect(created.append)
    _to_step3(wizard, application)
    wizard.finish_button.click()
    wizard._finish()
    application.processEvents()
    assert adapter.finish_calls == 1
    assert len(created) == 1
    assert wizard.session.state is OperationCreationState.CREATED


def test_stale_project_blocks_finish_without_calling_transaction(
    application: QApplication,
) -> None:
    adapter = _Adapter()
    wizard = OperationCreationWizard(_session(), adapter)
    _to_step3(wizard, application)
    adapter.current = False
    wizard.refresh_live_state()
    wizard.finish_button.click()
    assert adapter.finish_calls == 0
    assert "Project changed" in wizard.feedback.text()
    wizard.close()


@pytest.mark.parametrize("scale", (1.0, 1.25, 1.5, 2.0))
@pytest.mark.parametrize(
    "available",
    (
        QRect(0, 0, 1280, 720),
        QRect(0, 0, 1366, 768),
        QRect(0, 0, 1500, 900),
        QRect(0, 0, 1920, 1080),
    ),
)
def test_geometry_matrix_keeps_navigation_inside_available_screen(
    application: QApplication, scale: float, available: QRect
) -> None:
    wizard = OperationCreationWizard(_session(), _Adapter())
    wizard.apply_available_geometry(available, scale)
    wizard.show()
    application.processEvents()
    assert wizard.width() <= available.width()
    assert wizard.height() <= available.height()
    assert wizard.back_button.sizeHint().width() > 0
    assert wizard.next_button.sizeHint().width() > 0
    assert wizard.cancel_button.sizeHint().width() > 0
    wizard.close()


@pytest.mark.parametrize(
    ("language", "expected"),
    (
        (UiLanguage.VI_VN, "Bước 1"),
        (UiLanguage.EN_US, "Step 1"),
        (UiLanguage.KO_KR, "1단계"),
    ),
)
def test_locale_catalogs_render_complete_step_titles(
    application: QApplication, language: UiLanguage, expected: str
) -> None:
    service = translation_service()
    previous = service.language
    service.set_language(language)
    try:
        wizard = OperationCreationWizard(_session(), _Adapter())
        assert expected in wizard.step_labels[0].text()
        assert wizard.back_button.accessibleName()
        assert wizard.next_button.accessibleName()
        assert wizard.cancel_button.accessibleName()
        assert wizard.finish_button.accessibleName()
        wizard.close()
    finally:
        service.set_language(previous)
