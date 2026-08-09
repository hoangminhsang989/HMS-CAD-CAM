"""Tool Library localization, accessibility, responsiveness and geometry evidence."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QWidget,
)

from hms_cadcam.cam.domain import (
    DEFAULT_TOOL_PROFILE_REGISTRY,
    LengthUnit,
    ToolFamily,
)
from hms_cadcam.cam.tool_library import ToolDefinitionDraft
from hms_cadcam.project.service import ProjectService
from hms_cadcam.ui.i18n import UiLanguage, translation_service
from hms_cadcam.ui.tool_library import (
    ToolCommonDefaultsDialog,
    ToolDefinitionDialog,
    ToolLibraryDialog,
)
from hms_cadcam.ui.tool_program_profiles import ToolProfileEditorDialog


SCREENS = ((1280, 720), (1366, 768), (1500, 900), (1920, 1080))
SCALES = (1.0, 1.25, 1.5, 2.0)
SURFACES = ("main", "edit", "profile_defaults", "delete")


@pytest.fixture(scope="module")
def application() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def project_service(tmp_path_factory) -> ProjectService:
    root = tmp_path_factory.mktemp("r178_tool_library_ui")
    source = root / "source.step"
    source.write_text("ISO-10303-21;END-ISO-10303-21;", encoding="utf-8")
    service = ProjectService.create_default(root / "config")
    service.create_project_from_source(root, "R178 UI", source)
    draft = ToolDefinitionDraft(
        "R178 Ball Tool",
        ToolFamily.BALL_END_MILL,
        LengthUnit.MM,
        10.0,
        25.0,
        80.0,
        40.0,
        10.0,
        50.0,
        create_assembly=False,
    )
    service.execute_cam_command(lambda app: app.create_managed_tool(draft))
    return service


def _scale_font(widget: QWidget, scale: float) -> None:
    font = widget.font()
    size = font.pointSizeF()
    if size <= 0.0:
        size = 9.0
    font.setPointSizeF(size * scale)
    widget.setFont(font)


def _fit(widget: QWidget, available: QRect, scale: float) -> None:
    _scale_font(widget, scale)
    if isinstance(widget, ToolLibraryDialog):
        widget.apply_available_geometry(available, scale)
    else:
        widget.setMaximumSize(available.size())
        hint = widget.sizeHint()
        widget.resize(
            min(available.width(), max(360, hint.width())),
            min(available.height(), max(260, hint.height())),
        )


def _assert_interactive_geometry(widget: QWidget) -> None:
    root_rect = widget.rect().adjusted(-1, -1, 1, 1)
    for child_type in (QPushButton, QLineEdit, QComboBox, QTreeWidget):
        for child in widget.findChildren(child_type):
            if not child.isVisibleTo(widget):
                continue
            top_left = child.mapTo(widget, QPoint(0, 0))
            rect = child.rect().translated(top_left)
            assert root_rect.contains(rect.topLeft()), (
                widget.objectName(), child.objectName(), rect, root_rect
            )
            assert root_rect.contains(rect.bottomRight()), (
                widget.objectName(), child.objectName(), rect, root_rect
            )
            if child.isEnabled():
                assert child.accessibleName().strip() or isinstance(child, QPushButton)


@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize("screen", SCREENS)
@pytest.mark.parametrize("scale", SCALES)
@pytest.mark.parametrize("locale", tuple(UiLanguage))
def test_production_geometry_matrix_192_states(
    project_service: ProjectService,
    application: QApplication,
    locale: UiLanguage,
    scale: float,
    screen: tuple[int, int],
    surface: str,
) -> None:
    available = QRect(0, 0, *screen)
    service = translation_service()
    tool = project_service.cam_snapshot.tool_definitions[0]
    dialogs: tuple[QWidget, ...]
    with service.using(locale):
        library = ToolLibraryDialog(
            project_service, initial_tool_id=tool.tool_id
        )
        if surface == "main":
            dialogs = (library,)
        elif surface == "edit":
            dialogs = (
                ToolDefinitionDialog(
                    tool=tool,
                    holders=project_service.cam_snapshot.holder_definitions,
                ),
            )
        elif surface == "profile_defaults":
            dialogs = (
                ToolCommonDefaultsDialog(tool.common_defaults),
                ToolProfileEditorDialog(
                    DEFAULT_TOOL_PROFILE_REGISTRY.schema(
                        "parallel_finishing_3d"
                    )
                ),
            )
        else:
            dialogs = (library.build_delete_confirmation(tool),)
        for dialog in dialogs:
            _fit(dialog, available, scale)
            dialog.show()
            application.processEvents()
            assert dialog.width() <= screen[0]
            assert dialog.height() <= screen[1]
            _assert_interactive_geometry(dialog)
            if isinstance(dialog, QMessageBox):
                destructive = dialog.button(QMessageBox.StandardButton.Yes)
                cancel = dialog.button(QMessageBox.StandardButton.Cancel)
                assert destructive is not None and cancel is not None
                assert not destructive.isDefault()
                assert cancel.isDefault()
            dialog.close()
            dialog.deleteLater()
        library.close()
        library.deleteLater()
        application.processEvents()


def test_library_keyboard_and_accessibility_contract(
    project_service: ProjectService, application: QApplication
) -> None:
    dialog = ToolLibraryDialog(project_service)
    dialog.show()
    application.processEvents()

    assert dialog.search.focusPolicy() is not Qt.FocusPolicy.NoFocus
    assert dialog.table.focusPolicy() is not Qt.FocusPolicy.NoFocus
    for button in (
        dialog.create_button,
        dialog.edit_button,
        dialog.duplicate_button,
        dialog.archive_button,
        dialog.delete_button,
    ):
        assert button.accessibleName()
        assert button.focusPolicy() is not Qt.FocusPolicy.NoFocus
    assert not dialog.delete_button.isDefault()
    assert not dialog.delete_button.autoDefault()
    assert not dialog.archive_button.isEnabled()
    assert "SCHEMA" not in dialog.archive_button.toolTip()
    dialog.reject()


def test_cancel_create_has_zero_project_mutation(
    project_service: ProjectService, monkeypatch
) -> None:
    before = project_service.cam_snapshot
    dialog = ToolLibraryDialog(project_service)
    monkeypatch.setattr(
        ToolDefinitionDialog,
        "exec",
        lambda _self: QDialog.DialogCode.Rejected,
    )

    dialog._create()

    assert project_service.cam_snapshot == before


def test_definition_save_guard_prevents_second_accept(
    application: QApplication,
) -> None:
    dialog = ToolDefinitionDialog()
    dialog.name.setText("Double Save Guard")

    dialog._validate_and_accept()
    dialog._validate_and_accept()
    application.processEvents()

    assert dialog.result() == QDialog.DialogCode.Accepted
    assert not dialog.save_button.isEnabled()


def test_r178_catalog_keys_have_all_locales_without_fallback() -> None:
    service = translation_service()
    explicit = {
        "Production Tool Library",
        "Create Tool",
        "Edit Tool",
        "Duplicate Tool",
        "Delete Tool",
        "ARCHIVE_UNAVAILABLE_WITH_CURRENT_SCHEMA",
        "Search Tool Library",
        "Tool strategy compatibility",
        "Tool usage and references",
        "Edit common Tool defaults",
        "Configuration revision",
        "Delete is blocked while assemblies or operations reference this Tool.",
        "Cấu hình Tool theo chương trình",
        "Thêm cấu hình theo chương trình",
        "Chỉnh sửa cấu hình theo chương trình",
        "Gia công tinh song song",
        "Gia công tinh theo cao độ Z",
        "Khoan",
    }
    source_files = (
        Path("src/hms_cadcam/ui/tool_library.py"),
        Path("src/hms_cadcam/ui/tool_program_profiles.py"),
    )
    literal_keys = set(explicit)
    for path in source_files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "ui_text"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                literal_keys.add(node.args[0].value)
    for locale in UiLanguage:
        catalog = service.catalogs[locale]
        missing = sorted(key for key in literal_keys if key not in catalog.entries)
        assert missing == []
    for key in explicit:
        vi = service.catalogs[UiLanguage.VI_VN].entries[key]
        en = service.catalogs[UiLanguage.EN_US].entries[key]
        ko = service.catalogs[UiLanguage.KO_KR].entries[key]
        assert vi and en and ko
        if key.isascii() and any(character.isalpha() for character in key):
            assert vi != key or key in {"Khoan"}
            assert ko != key
