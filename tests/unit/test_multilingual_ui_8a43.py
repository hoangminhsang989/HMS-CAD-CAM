"""Stage 8A.4.3 multilingual model, settings, runtime and widget tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QSettings, Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDockWidget,
    QLabel,
    QLineEdit,
    QMessageBox as QtMessageBox,
    QPushButton,
    QTabBar,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from hms_cadcam.cad.unavailable import UnavailableCadKernel
from hms_cadcam.project.service import ProjectService
from hms_cadcam.ui.i18n import (
    CORE_TRANSLATIONS,
    LANGUAGE_SETTINGS_KEY,
    LocaleSettingsService,
    TranslationCatalog,
    TranslationService,
    UiLanguage,
    build_default_catalogs,
    format_geometry_update_message,
    language_display_name,
    set_translation_service,
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
from hms_cadcam.ui.geometry_transfer_ui import IncomingGeometryNotificationBar
from hms_cadcam.ui.operation_manager_types import (
    OperationManagerDomainIdentity,
    OperationManagerEntityKind,
    OperationManagerHeader,
    OperationManagerNode,
    OperationManagerNodeId,
    OperationManagerNodeKind,
    OperationManagerProjection,
    OperationManagerSemanticStatus,
    OperationManagerStatus,
    OperationManagerStatusCategory,
)
from hms_cadcam.ui.workspace_layout import WorkspaceLayoutStore
from hms_cadcam.ui.workspace_shell import WorkspaceId
from hms_cadcam.viewer.unavailable_backend import UnavailableCadViewportBackend


def _application() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _ensure_application() -> QApplication:
    return _application()


@pytest.fixture
def i18n_service():
    previous = translation_service()
    service = TranslationService(build_default_catalogs())
    set_translation_service(service)
    yield service
    set_translation_service(previous)


@dataclass
class _MemorySettings:
    values: dict[str, object] = field(default_factory=dict)
    fail_read: bool = False
    fail_write: bool = False
    sync_count: int = 0

    def value(self, key: str, default_value: object = None) -> object:
        if self.fail_read:
            raise OSError("read blocked")
        return self.values.get(key, default_value)

    def setValue(self, key: str, value: object) -> None:  # noqa: N802
        if self.fail_write:
            raise OSError("write blocked")
        self.values[key] = value

    def sync(self) -> None:
        if self.fail_write:
            raise OSError("sync blocked")
        self.sync_count += 1


def _window(tmp_path: Path) -> MainWindow:
    settings = QSettings(
        str(tmp_path / "workspace.ini"),
        QSettings.Format.IniFormat,
    )
    unavailable_reason = "CAD rendering backend is unavailable."
    return MainWindow(
        ProjectService.create_default(tmp_path / "config"),
        UnavailableCadKernel(unavailable_reason),
        UnavailableCadViewportBackend(unavailable_reason),
        layout_store=WorkspaceLayoutStore(settings),
    )


def _dispose(widget: QWidget) -> None:
    widget.close()
    _application().processEvents()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_typed_locale_defaults_and_invalid_values_never_follow_windows_locale() -> None:
    assert tuple(UiLanguage) == (
        UiLanguage.VI_VN,
        UiLanguage.EN_US,
        UiLanguage.KO_KR,
    )
    assert UiLanguage.coerce(None) is UiLanguage.VI_VN
    assert UiLanguage.coerce("") is UiLanguage.VI_VN
    assert UiLanguage.coerce("en-US") is UiLanguage.VI_VN
    assert UiLanguage.coerce("EN_US") is UiLanguage.EN_US


def test_locale_settings_default_persist_restart_and_invalid_fallback() -> None:
    backend = _MemorySettings()
    settings = LocaleSettingsService(backend)
    assert settings.load() is UiLanguage.VI_VN
    assert settings.save(UiLanguage.EN_US)
    assert backend.values[LANGUAGE_SETTINGS_KEY] == "EN_US"
    assert LocaleSettingsService(backend).load() is UiLanguage.EN_US
    assert settings.save(UiLanguage.KO_KR)
    assert LocaleSettingsService(backend).load() is UiLanguage.KO_KR
    backend.values[LANGUAGE_SETTINGS_KEY] = "invalid"
    assert settings.load() is UiLanguage.VI_VN


def test_locale_settings_io_errors_do_not_block_startup() -> None:
    assert LocaleSettingsService(
        _MemorySettings(fail_read=True)
    ).load() is UiLanguage.VI_VN
    assert not LocaleSettingsService(
        _MemorySettings(fail_write=True)
    ).save(UiLanguage.KO_KR)


def test_catalogs_are_complete_unique_nonempty_and_placeholder_safe(
    i18n_service: TranslationService,
) -> None:
    vietnamese = i18n_service.catalogs[UiLanguage.VI_VN]
    required = tuple(vietnamese.entries)
    assert len(required) >= 800
    assert validate_glossary(i18n_service.catalogs) == ()
    for language in UiLanguage:
        report = i18n_service.catalogs[language].validate(
            required,
            source_entries=vietnamese.entries,
        )
        assert report.valid
        assert report.key_count == len(required)


def test_catalog_factory_retains_duplicate_key_evidence() -> None:
    catalog = TranslationCatalog.from_pairs(
        UiLanguage.EN_US,
        (("save", "Save"), ("save", "Store")),
    )
    assert catalog.duplicate_keys == ("save",)
    assert not catalog.validate(("save",)).valid


@pytest.mark.parametrize("language", (UiLanguage.EN_US, UiLanguage.KO_KR))
def test_missing_translation_falls_back_to_vietnamese_not_english(
    language: UiLanguage,
) -> None:
    catalogs = {
        UiLanguage.VI_VN: TranslationCatalog.from_pairs(
            UiLanguage.VI_VN,
            (("dialog.safe", "Nội dung an toàn"),),
        ),
        language: TranslationCatalog.from_pairs(language, ()),
    }
    service = TranslationService(catalogs, language=language)
    assert service.translate_key("dialog.safe") == "Nội dung an toàn"
    assert service.diagnostics[-1].resolution == "VI_VN_FALLBACK"
    assert service.diagnostics[-1].requested_locale is language


def test_missing_catalog_and_raw_typed_key_use_safe_declared_text() -> None:
    service = TranslationService(
        {
            UiLanguage.VI_VN: TranslationCatalog.from_pairs(
                UiLanguage.VI_VN,
                (),
            )
        },
        language=UiLanguage.KO_KR,
    )
    assert service.language is UiLanguage.VI_VN
    assert service.translate_key("dialog.missing") == "Nội dung giao diện"
    assert "dialog.missing" not in service.translate_key("dialog.missing")


def test_language_names_follow_active_catalog(
    i18n_service: TranslationService,
) -> None:
    expected = {
        UiLanguage.VI_VN: "Tiếng Việt — Mặc định",
        UiLanguage.EN_US: "Vietnamese — Default",
        UiLanguage.KO_KR: "베트남어 — 기본값",
    }
    for language, text in expected.items():
        i18n_service.set_language(language)
        assert language_display_name(
            UiLanguage.VI_VN,
            service=i18n_service,
        ) == text


def test_widget_tree_retranslates_text_tooltip_accessibility_tabs_and_placeholder(
    i18n_service: TranslationService,
) -> None:
    root = QWidget()
    layout = QVBoxLayout(root)
    label = QLabel("Cài đặt")
    label.setAccessibleName("Cài đặt")
    label.setAccessibleDescription("Cài đặt ngôn ngữ")
    button = QPushButton("Áp dụng")
    button.setToolTip("Ngôn ngữ thay đổi ngay mà không sửa dữ liệu dự án.")
    edit = QLineEdit()
    edit.setPlaceholderText("Tên")
    tabs = QTabWidget()
    tabs.addTab(QWidget(), "Giao diện")
    layout.addWidget(label)
    layout.addWidget(button)
    layout.addWidget(edit)
    layout.addWidget(tabs)

    expected = (
        (
            UiLanguage.VI_VN,
            "Cài đặt",
            "Áp dụng",
            "Tên",
            "Giao diện",
        ),
        (
            UiLanguage.EN_US,
            "Settings",
            "Apply",
            "Name",
            "Interface",
        ),
        (
            UiLanguage.KO_KR,
            "설정",
            "적용",
            "이름",
            "인터페이스",
        ),
        (
            UiLanguage.VI_VN,
            "Cài đặt",
            "Áp dụng",
            "Tên",
            "Giao diện",
        ),
    )
    for language, title, action, placeholder, tab in expected:
        i18n_service.set_language(language)
        localize_widget_tree(root)
        assert (
            label.text(),
            button.text(),
            edit.placeholderText(),
            tabs.tabText(0),
        ) == (title, action, placeholder, tab)
        assert label.accessibleName() == title
        assert label.accessibleDescription()
        assert button.toolTip()
    _dispose(root)


def test_language_settings_applies_and_persists_without_restart(
    i18n_service: TranslationService,
) -> None:
    backend = _MemorySettings()
    dialog = LanguageSettingsDialog(
        i18n_service,
        LocaleSettingsService(backend),
    )
    dialog.show()
    _application().processEvents()
    dialog.language_combo.setCurrentIndex(
        dialog.language_combo.findData(UiLanguage.KO_KR)
    )
    dialog.apply_selection()
    _application().processEvents()
    assert i18n_service.language is UiLanguage.KO_KR
    assert backend.values[LANGUAGE_SETTINGS_KEY] == "KO_KR"
    assert dialog.windowTitle() == "언어 설정"
    assert tuple(
        dialog.language_combo.itemText(index)
        for index in range(dialog.language_combo.count())
    ) == ("베트남어 — 기본값", "영어", "한국어")
    metrics = audit_widget(dialog)
    assert metrics.replacement_glyph_count == 0
    assert metrics.tofu_count == 0
    _dispose(dialog)


@pytest.mark.parametrize(
    ("language", "labels", "buttons", "sidebar_labels"),
    (
        (
            UiLanguage.VI_VN,
            ("Thư mục:", "Tên tệp:", "Loại tệp:", "Lưu", "Hủy"),
            ("Lưu", "Không lưu", "Hủy"),
            ("Máy tính", "Thư mục người dùng"),
        ),
        (
            UiLanguage.EN_US,
            ("Folder:", "File name:", "File type:", "Save", "Cancel"),
            ("Save", "Don’t save", "Cancel"),
            ("Computer", "User folder"),
        ),
        (
            UiLanguage.KO_KR,
            ("폴더:", "파일 이름:", "파일 형식:", "저장", "취소"),
            ("저장", "저장 안 함", "취소"),
            ("내 컴퓨터", "사용자 폴더"),
        ),
    ),
)
def test_file_dialog_and_dirty_lifecycle_follow_hms_language(
    i18n_service: TranslationService,
    tmp_path: Path,
    language: UiLanguage,
    labels: tuple[str, ...],
    buttons: tuple[str, ...],
    sidebar_labels: tuple[str, ...],
) -> None:
    i18n_service.set_language(language)
    dialog = QFileDialog(None, "Save", str(tmp_path), "HMS (*.HMS)")
    dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
    dialog.show()
    _application().processEvents()
    assert dialog.testOption(QFileDialog.Option.DontUseNativeDialog)
    assert (
        dialog.labelText(QFileDialog.DialogLabel.LookIn),
        dialog.labelText(QFileDialog.DialogLabel.FileName),
        dialog.labelText(QFileDialog.DialogLabel.FileType),
        dialog.labelText(QFileDialog.DialogLabel.Accept),
        dialog.labelText(QFileDialog.DialogLabel.Reject),
    ) == labels
    sidebar = dialog.findChild(QAbstractItemView, "sidebar")
    assert sidebar is not None
    model = sidebar.model()
    assert model is not None
    actual_sidebar_labels = tuple(
        str(model.data(model.index(row, 0), Qt.ItemDataRole.DisplayRole))
        for row in range(model.rowCount())
    )
    assert actual_sidebar_labels == sidebar_labels
    assert all("..." not in label and "…" not in label for label in actual_sidebar_labels)
    for row, full_text in enumerate(actual_sidebar_labels):
        index = model.index(row, 0)
        assert model.data(index, Qt.ItemDataRole.ToolTipRole) == full_text
        assert model.data(index, Qt.ItemDataRole.AccessibleTextRole) == full_text
        available = sidebar.viewport().width() - max(24, sidebar.iconSize().width()) - 24
        assert (
            sidebar.fontMetrics().elidedText(
                full_text,
                Qt.TextElideMode.ElideRight,
                available,
            )
            == full_text
        )
    box = QMessageBox()
    box.setStandardButtons(
        QMessageBox.StandardButton.Save
        | QMessageBox.StandardButton.Discard
        | QMessageBox.StandardButton.Cancel
    )
    assert tuple(
        box.button(button).text()
        for button in (
            QMessageBox.StandardButton.Save,
            QMessageBox.StandardButton.Discard,
            QMessageBox.StandardButton.Cancel,
        )
    ) == buttons
    _dispose(dialog)
    _dispose(box)


def test_main_window_runtime_switch_preserves_workspace_selection_worker_and_shortcut(
    i18n_service: TranslationService,
    tmp_path: Path,
) -> None:
    window = _window(tmp_path)
    window.show()
    _application().processEvents()
    window.workspace_bar.set_active_workspace(WorkspaceId.MILL_2D)
    sentinel_selection = (object(),)
    sentinel_worker = object()
    window._active_selection = sentinel_selection  # type: ignore[assignment]
    window.cam_workspace._parallel_task = sentinel_worker  # type: ignore[assignment]
    project_before = window.project_controller.service.current_project
    files_before = tuple(sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*") if path.is_file()))
    exit_action = next(
        action
        for action in window.findChildren(type(window._language_action))
        if action.shortcut() == QKeySequence("Ctrl+Q")
    )

    i18n_service.set_language(UiLanguage.EN_US)
    _application().processEvents()
    assert [action.text() for action in window.menuBar().actions()][:3] == [
        "File",
        "Edit",
        "View",
    ]
    assert window.workspace_bar.active_workspace is WorkspaceId.MILL_2D
    assert window._active_selection is sentinel_selection
    assert window.cam_workspace._parallel_task is sentinel_worker
    assert window.project_controller.service.current_project is project_before
    assert exit_action.shortcut() == QKeySequence("Ctrl+Q")

    i18n_service.set_language(UiLanguage.KO_KR)
    _application().processEvents()
    assert [action.text() for action in window.menuBar().actions()][:3] == [
        "파일",
        "편집",
        "보기",
    ]
    assert window.workspace_bar.active_workspace is WorkspaceId.MILL_2D
    assert window._active_selection is sentinel_selection
    assert window.cam_workspace._parallel_task is sentinel_worker
    assert tuple(sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*") if path.is_file())) == files_before

    window.cam_workspace._parallel_task = None
    _dispose(window)


def test_three_locale_audit_has_complete_core_coverage_and_zero_fallback(
    i18n_service: TranslationService,
) -> None:
    visible_keys = tuple(dict.fromkeys(item[0] for item in CORE_TRANSLATIONS))
    for language in UiLanguage:
        report = audit_locale(
            i18n_service,
            language,
            visible_keys=visible_keys,
        )
        assert report.catalog_key_count >= 800
        assert report.production_visible_key_count == len(visible_keys)
        assert report.missing_key_count == 0
        assert report.fallback_hit_count == 0
        assert report.empty_translation_count == 0
        assert report.duplicate_key_count == 0
        assert report.raw_key_count == 0
        assert report.mixed_language_count == 0
        assert report.unapproved_term_count == 0


def test_three_locale_main_window_has_complete_interactive_accessibility(
    i18n_service: TranslationService,
    tmp_path: Path,
) -> None:
    window = _window(tmp_path)
    _show_review_docks(window)
    for language in UiLanguage:
        i18n_service.set_language(language)
        _application().processEvents()
        metrics = audit_widget(window)
        assert metrics.missing_accessible_name_count == 0
        assert metrics.missing_accessible_description_count == 0
    _dispose(window)


def test_unicode_catalogs_have_accents_hangul_and_no_replacement_characters(
    i18n_service: TranslationService,
) -> None:
    vi_values = tuple(i18n_service.catalogs[UiLanguage.VI_VN].entries.values())
    ko_values = tuple(i18n_service.catalogs[UiLanguage.KO_KR].entries.values())
    assert any("Tiếng Việt" in value for value in vi_values)
    assert any("한국어" in value for value in ko_values)
    assert all("\ufffd" not in value and "\u25a1" not in value for value in vi_values)
    assert all("\ufffd" not in value and "\u25a1" not in value for value in ko_values)


def test_language_apply_button_does_not_use_color_as_the_only_signal(
    i18n_service: TranslationService,
) -> None:
    dialog = LanguageSettingsDialog(
        i18n_service,
        LocaleSettingsService(_MemorySettings()),
    )
    dialog.language_combo.setCurrentIndex(
        dialog.language_combo.findData(UiLanguage.EN_US)
    )
    assert dialog.apply_button.isEnabled()
    assert dialog.apply_button.text() == "Áp dụng"
    assert dialog.apply_button.accessibleName() == "Áp dụng"
    assert dialog.apply_button.toolTip()
    _dispose(dialog)


def test_property_viewport_status_diagnostics_and_search_retranslate_cleanly(
    i18n_service: TranslationService,
    tmp_path: Path,
) -> None:
    window = _window(tmp_path)
    window.resize(1400, 820)
    window.show()
    _application().processEvents()
    expected = (
        (
            UiLanguage.VI_VN,
            "Chưa có",
            "HỆ MÉT",
            "TRÌNH XEM CAD KHÔNG KHẢ DỤNG",
            "Bộ dựng hình CAD không khả dụng.",
            "Tên, chiến lược, dao, trạng thái hoặc ID…",
            "Trình xem CAD không khả dụng:",
        ),
        (
            UiLanguage.EN_US,
            "None",
            "METRIC",
            "CAD VIEWER UNAVAILABLE",
            "CAD rendering backend is unavailable.",
            "Name, strategy, Tool, status or ID…",
            "CAD Viewer unavailable:",
        ),
        (
            UiLanguage.KO_KR,
            "없음",
            "미터법",
            "CAD 뷰어 사용 불가",
            "CAD 렌더링 백엔드를 사용할 수 없습니다.",
            "이름, 전략, Tool, 상태 또는 ID…",
            "CAD 뷰어 사용 불가:",
        ),
        (
            UiLanguage.VI_VN,
            "Chưa có",
            "HỆ MÉT",
            "TRÌNH XEM CAD KHÔNG KHẢ DỤNG",
            "Bộ dựng hình CAD không khả dụng.",
            "Tên, chiến lược, dao, trạng thái hoặc ID…",
            "Trình xem CAD không khả dụng:",
        ),
    )
    for (
        language,
        none_text,
        metric,
        title,
        reason,
        placeholder,
        diagnostic,
    ) in expected:
        i18n_service.set_language(language)
        _application().processEvents()
        assert window._properties_table.item(0, 1).text() == none_text
        assert metric in {
            label.text() for label in window.statusBar().findChildren(QLabel)
        }
        assert window.viewport._status_label.text() == f"{title}\n{reason}"
        assert window.operation_manager_host.search.placeholderText() == placeholder
        assert diagnostic in window._output.toPlainText()
        assert reason in window._output.toPlainText()
        runtime = audit_widget(window)
        report = audit_locale(
            i18n_service,
            language,
            visible_keys=(),
            runtime=runtime,
        )
        assert report.mixed_language_count == 0
    _dispose(window)


def test_operation_model_and_delegate_emit_and_resolve_current_locale(
    i18n_service: TranslationService,
    tmp_path: Path,
) -> None:
    window = _window(tmp_path)
    model = window.operation_manager_host.model
    node_id = OperationManagerNodeId("setup:test")
    node = OperationManagerNode(
        node_id=node_id,
        domain_identity=OperationManagerDomainIdentity(
            OperationManagerEntityKind.SETUP,
            "setup:test",
        ),
        parent_id=None,
        kind=OperationManagerNodeKind.SETUP,
        label="Setup",
        secondary_summary="Setup chưa có operation",
        statuses=(
            OperationManagerStatus(
                OperationManagerStatusCategory.DOMAIN,
                OperationManagerSemanticStatus.NEEDS_INPUT,
                "Needs calculation",
                "No current result to delete.",
            ),
        ),
        enabled=True,
        order=0,
    )
    model.set_projection(
        OperationManagerProjection(
            uuid4(),
            (node_id,),
            (node,),
            OperationManagerHeader("", "", "", "", 0, 0, 0),
        )
    )
    data_spy = QSignalSpy(model.dataChanged)
    header_spy = QSignalSpy(model.headerDataChanged)
    index = model.index(0, 0)

    i18n_service.set_language(UiLanguage.KO_KR)
    _application().processEvents()

    assert data_spy.count() >= 1
    assert header_spy.count() >= 1
    assert model.data(index) == "가공 설정"
    assert model.headerData(
        0,
        Qt.Orientation.Horizontal,
        Qt.ItemDataRole.DisplayRole,
    ) == "이름"
    delegate = window.operation_manager_host.view.itemDelegateForIndex(index)
    assert delegate.audit_texts(index) == ("가공 설정",)
    _dispose(window)


def test_geometry_notification_is_one_locale_aware_message_and_retranslates(
    i18n_service: TranslationService,
) -> None:
    bar = IncomingGeometryNotificationBar()
    request = SimpleNamespace(source_display_name="source")
    bar.set_requests((request,))  # type: ignore[arg-type]
    assert not hasattr(bar, "badge")
    assert (
        bar.message_label.text()
        == "Có 1 bản cập nhật 3D mới từ “source.HMS”."
    )

    i18n_service.set_language(UiLanguage.EN_US)
    _application().processEvents()
    assert (
        bar.message_label.text()
        == "1 new 3D update is available from “source.HMS”."
    )

    i18n_service.set_language(UiLanguage.KO_KR)
    _application().processEvents()
    assert (
        bar.message_label.text()
        == "“source.HMS”에서 새 3D 업데이트 1건이 도착했습니다."
    )
    assert not _is_mixed(bar.message_label.text(), UiLanguage.KO_KR)
    assert audit_widget(bar).locale_message_format_error_count == 0
    _dispose(bar)


@pytest.mark.parametrize(
    ("language", "count", "source", "expected"),
    (
        (UiLanguage.VI_VN, 0, "nguồn", "Không có dữ liệu 3D mới."),
        (
            UiLanguage.VI_VN,
            1,
            "Chi tiết Ω có khoảng trắng",
            "Có 1 bản cập nhật 3D mới từ “Chi tiết Ω có khoảng trắng.HMS”.",
        ),
        (
            UiLanguage.EN_US,
            1,
            "source",
            "1 new 3D update is available from “source.HMS”.",
        ),
        (
            UiLanguage.EN_US,
            2,
            "source file.HMS",
            "2 new 3D updates are available from “source file.HMS”.",
        ),
        (
            UiLanguage.KO_KR,
            2,
            "도면 A",
            "“도면 A.HMS”에서 새 3D 업데이트 2건이 도착했습니다.",
        ),
    ),
)
def test_geometry_notification_formatter_handles_count_order_and_unicode(
    i18n_service: TranslationService,
    language: UiLanguage,
    count: int,
    source: str,
    expected: str,
) -> None:
    i18n_service.set_language(language)
    assert (
        format_geometry_update_message(count, source, service=i18n_service)
        == expected
    )


def test_vietnamese_ribbon_uses_full_labels_without_elision(
    i18n_service: TranslationService,
    tmp_path: Path,
) -> None:
    i18n_service.set_language(UiLanguage.VI_VN)
    window = _window(tmp_path)
    window.resize(1400, 820)
    window.show()
    _application().processEvents()
    buttons = window._ribbon.findChildren(QToolButton, "RibbonButton")
    full_labels = {str(button.property("fullText")) for button in buttons}
    assert {
        "Tạo dự án CAM",
        "Nạp 3D mới cho dự án CAM",
        "Sao chép",
        "Thuộc tính",
        "Thống kê",
    } <= full_labels
    for button in buttons:
        full_text = str(button.property("fullText") or "")
        if full_text not in {
            "Tạo dự án CAM",
            "Nạp 3D mới cho dự án CAM",
            "Sao chép",
            "Thuộc tính",
            "Thống kê",
        }:
            continue
        assert "..." not in button.text()
        assert "…" not in button.text()
        assert full_text in button.toolTip()
        assert button.accessibleName() == full_text
    metrics = audit_widget(window)
    assert metrics.ribbon_elision_count == 0
    assert metrics.missing_full_text_tooltip_count == 0
    assert metrics.missing_full_accessible_name_count == 0
    _dispose(window)


def _visible_semantic_dock_bars(window: MainWindow) -> tuple[QTabBar, ...]:
    visible: list[QTabBar] = []
    seen: set[tuple[tuple[int, int, int, int], tuple[str, ...]]] = set()
    for tab_bar in window.findChildren(QTabBar):
        if (
            not bool(tab_bar.property("hmsDockTabBar"))
            or not tab_bar.isVisibleTo(window)
            or not tab_bar.geometry().intersects(window.rect())
        ):
            continue
        key = (
            tab_bar.geometry().getRect(),
            tuple(
                str(tab_bar.tabData(index))
                for index in range(tab_bar.count())
            ),
        )
        if key in seen:
            continue
        seen.add(key)
        visible.append(tab_bar)
    return tuple(visible)


def _dock_identity_signature(window: MainWindow) -> tuple[object, ...]:
    docks = (
        window.project_dock,
        window.operation_manager_dock,
        window.properties_dock,
        window.secondary_dock,
    )
    tab_bars = _visible_semantic_dock_bars(window)
    return (
        tuple(id(dock) for dock in docks),
        tuple(id(tab_bar) for tab_bar in tab_bars),
        tuple(tab_bar.count() for tab_bar in tab_bars),
        tuple(
            tuple(
                str(tab_bar.tabData(index))
                for index in range(tab_bar.count())
            )
            for tab_bar in tab_bars
        ),
        tuple(tab_bar.currentIndex() for tab_bar in tab_bars),
        tuple(window.dockWidgetArea(dock).value for dock in docks),
        tuple(dock.isVisible() for dock in docks),
        tuple(
            tuple(
                item.objectName()
                for item in window.tabifiedDockWidgets(dock)
            )
            for dock in docks
        ),
    )


def _show_review_docks(window: MainWindow) -> None:
    window.resize(1500, 900)
    window.project_dock.show()
    window.operation_manager_dock.show()
    window.properties_dock.show()
    window.secondary_dock.show()
    window.show()
    _application().processEvents()
    window._refresh_compact_dock_titles()
    _application().processEvents()


def test_default_dock_lifecycle_has_one_semantic_bar_per_group(
    i18n_service: TranslationService,
    tmp_path: Path,
) -> None:
    i18n_service.set_language(UiLanguage.VI_VN)
    window = _window(tmp_path)
    _show_review_docks(window)
    assert len(window.findChildren(type(window.project_dock))) == 8
    bars = _visible_semantic_dock_bars(window)
    assert len(bars) == 2
    label_sets = tuple(
        tuple(tab_bar.tabText(index) for index in range(tab_bar.count()))
        for tab_bar in bars
    )
    assert sum(
        set(labels) == {"Hình học / Dự án", "Nguyên công"}
        for labels in label_sets
    ) == 1
    assert sum("Post" in labels for labels in label_sets) == 1
    metrics = audit_widget(window)
    assert metrics.duplicate_dock_tab_bar_count == 0
    assert metrics.duplicate_dock_tab_set_count == 0
    assert metrics.duplicate_visible_tab_label_count == 0
    assert metrics.dock_tab_partial_visibility_count == 0
    assert metrics.dock_tab_out_of_bounds_count == 0
    _dispose(window)


def test_repeated_locale_switch_preserves_dock_and_tab_identity(
    i18n_service: TranslationService,
    tmp_path: Path,
) -> None:
    window = _window(tmp_path)
    _show_review_docks(window)
    window.properties_dock.raise_()
    _application().processEvents()
    baseline = _dock_identity_signature(window)
    spy = QSignalSpy(i18n_service.language_changed)
    sequence = (
        UiLanguage.VI_VN,
        UiLanguage.EN_US,
        UiLanguage.KO_KR,
        UiLanguage.VI_VN,
        UiLanguage.EN_US,
        UiLanguage.KO_KR,
        UiLanguage.VI_VN,
    )
    for language in sequence:
        i18n_service.set_language(language)
        _application().processEvents()
        assert _dock_identity_signature(window) == baseline
        post_bars = tuple(
            tab_bar
            for tab_bar in _visible_semantic_dock_bars(window)
            if "Post"
            in {
                tab_bar.tabText(index)
                for index in range(tab_bar.count())
            }
        )
        assert len(post_bars) == 1
        metrics = audit_widget(window)
        assert metrics.duplicate_dock_tab_bar_count == 0
        assert metrics.dock_tab_out_of_bounds_count == 0
        assert metrics.dock_tab_missing_leading_character_count == 0
    assert spy.count() == 6
    _dispose(window)


@pytest.mark.parametrize("language", tuple(UiLanguage))
@pytest.mark.parametrize("dpi_percent", (100, 125, 150))
def test_post_dock_tab_is_fully_visible_for_every_locale_and_dpi(
    i18n_service: TranslationService,
    tmp_path: Path,
    language: UiLanguage,
    dpi_percent: int,
) -> None:
    i18n_service.set_language(language)
    window = _window(tmp_path)
    font = window.font()
    font.setPointSizeF(max(8.0, 9.0 * dpi_percent / 100.0))
    window.setFont(font)
    for tab_bar in window.findChildren(QTabBar):
        tab_bar.setFont(font)
    _show_review_docks(window)
    post_matches = tuple(
        (tab_bar, index)
        for tab_bar in _visible_semantic_dock_bars(window)
        for index in range(tab_bar.count())
        if tab_bar.tabText(index) == "Post"
    )
    assert len(post_matches) == 1
    tab_bar, index = post_matches[0]
    tab_rect = tab_bar.tabRect(index)
    assert tab_rect.x() >= 0
    assert tab_bar.rect().contains(tab_rect.topLeft())
    assert tab_bar.rect().contains(tab_rect.bottomRight())
    assert (
        tab_bar.fontMetrics().elidedText(
            "Post",
            Qt.TextElideMode.ElideRight,
            tab_rect.width(),
        )
        == "Post"
    )
    assert not tab_bar.usesScrollButtons()
    for other in range(tab_bar.count()):
        if other != index:
            assert not tab_rect.intersects(tab_bar.tabRect(other))
    metrics = audit_widget(window)
    assert metrics.dock_tab_elision_count == 0
    assert metrics.dock_tab_partial_visibility_count == 0
    assert metrics.dock_tab_out_of_bounds_count == 0
    assert metrics.dock_tab_missing_leading_character_count == 0
    _dispose(window)


def test_rendered_audit_rejects_duplicate_semantic_dock_tab_bars() -> None:
    root = QWidget()
    root.resize(420, 100)
    for y in (0, 34):
        tab_bar = QTabBar(root)
        tab_bar.addTab("Geometry / Project")
        tab_bar.addTab("Operations")
        tab_bar.setTabData(0, 101)
        tab_bar.setTabData(1, 102)
        tab_bar.setTabToolTip(
            0,
            "Geometry structure / Project Manager",
        )
        tab_bar.setTabToolTip(1, "Operation Manager")
        tab_bar.setAccessibleName(
            "Geometry structure / Project Manager · Operation Manager"
        )
        tab_bar.setProperty("hmsDockTabBar", True)
        tab_bar.setProperty(
            "dockTabCompactSources",
            ("Geometry / Project", "Operations"),
        )
        tab_bar.setGeometry(0, y, 360, 30)
        tab_bar.show()
    root.show()
    _application().processEvents()
    metrics = audit_widget(root)
    assert metrics.duplicate_dock_tab_bar_count == 1
    assert metrics.duplicate_dock_tab_set_count == 1
    assert metrics.duplicate_visible_tab_label_count == 2
    _dispose(root)


def test_rendered_audit_rejects_partial_missing_and_empty_post_tabs() -> None:
    root = QWidget()
    root.resize(180, 90)
    for text, data, x, y in (
        ("ost", 201, -8, 0),
        ("", 202, -110, 40),
    ):
        tab_bar = QTabBar(root)
        tab_bar.setExpanding(False)
        tab_bar.addTab(text)
        tab_bar.setTabData(0, data)
        tab_bar.setTabToolTip(0, "Simulation / Post")
        tab_bar.setAccessibleName("Simulation / Post")
        tab_bar.setProperty("hmsDockTabBar", True)
        tab_bar.setProperty("dockTabCompactSources", ("Post",))
        tab_bar.setGeometry(x, y, 120, 30)
        tab_bar.show()
    root.show()
    _application().processEvents()
    metrics = audit_widget(root)
    assert metrics.dock_tab_partial_visibility_count >= 1
    assert metrics.dock_tab_out_of_bounds_count >= 1
    assert metrics.dock_tab_missing_leading_character_count == 2
    _dispose(root)


def test_rendered_audit_rejects_dock_tab_row_beside_its_visible_dock() -> None:
    root = QWidget()
    root.resize(480, 140)
    visible_dock = QDockWidget("Property", root)
    visible_dock.setGeometry(280, 10, 180, 80)
    visible_dock.show()
    tab_bar = QTabBar(root)
    tab_bar.addTab("Property")
    tab_bar.addTab("Post")
    tab_bar.setTabData(0, id(visible_dock))
    tab_bar.setTabData(1, 302)
    tab_bar.setTabToolTip(1, "Simulation / Post")
    tab_bar.setAccessibleName("Simulation / Post")
    tab_bar.setProperty("hmsDockTabBar", True)
    tab_bar.setProperty("dockTabCompactSources", ("", "Post"))
    tab_bar.setGeometry(0, 91, 180, 30)
    tab_bar.show()
    root.show()
    _application().processEvents()
    metrics = audit_widget(root)
    assert metrics.dock_tab_out_of_bounds_count >= 1
    _dispose(root)


def test_dock_retranslation_contains_no_layout_mutation_api() -> None:
    names = set(
        MainWindow._refresh_compact_dock_titles.__code__.co_names
    )
    assert "tabifyDockWidget" not in names
    assert "addDockWidget" not in names
    assert "restoreState" not in names


@pytest.mark.parametrize(
    ("language", "compact", "full"),
    (
        (
            UiLanguage.VI_VN,
            ("Hình học / Dự án", "Nguyên công"),
            (
                "Cấu trúc hình học / Quản lý dự án",
                "Quản lý nguyên công",
            ),
        ),
        (
            UiLanguage.EN_US,
            ("Geometry / Project", "Operations"),
            (
                "Geometry structure / Project Manager",
                "Operation Manager",
            ),
        ),
        (
            UiLanguage.KO_KR,
            ("형상 / 프로젝트", "작업"),
            ("형상 구조 / 프로젝트 관리자", "작업 관리자"),
        ),
    ),
)
def test_dock_tabs_use_catalog_compact_text_with_full_accessibility(
    i18n_service: TranslationService,
    tmp_path: Path,
    language: UiLanguage,
    compact: tuple[str, str],
    full: tuple[str, str],
) -> None:
    window = _window(tmp_path)
    i18n_service.set_language(language)
    window.resize(1500, 900)
    window.project_dock.show()
    window.operation_manager_dock.show()
    window.show()
    _application().processEvents()
    window._refresh_compact_dock_titles()
    assert (
        window.project_dock.windowTitle(),
        window.operation_manager_dock.windowTitle(),
    ) == compact
    assert (
        window.project_dock.accessibleName(),
        window.operation_manager_dock.accessibleName(),
    ) == full
    dock_tab_bars = [
        tab_bar
        for tab_bar in window.findChildren(QTabBar)
        if bool(tab_bar.property("hmsDockTabBar"))
    ]
    assert dock_tab_bars
    visible_dock_labels = {
        tab_bar.tabText(index)
        for tab_bar in dock_tab_bars
        for index in range(tab_bar.count())
    }
    assert set(compact) <= visible_dock_labels
    for tab_bar in dock_tab_bars:
        for index in range(tab_bar.count()):
            if tab_bar.tabText(index) not in compact:
                continue
            expected_full = full[compact.index(tab_bar.tabText(index))]
            assert tab_bar.tabToolTip(index) == expected_full
            assert expected_full in tab_bar.accessibleName()
    metrics = audit_widget(window)
    assert metrics.dock_tab_elision_count == 0
    _dispose(window)


@pytest.mark.parametrize("dpi_percent", (100, 125, 150))
def test_font_metric_audit_covers_supported_dpi_without_elision(
    i18n_service: TranslationService,
    tmp_path: Path,
    dpi_percent: int,
) -> None:
    i18n_service.set_language(UiLanguage.VI_VN)
    window = _window(tmp_path)
    font = window.font()
    font.setPointSizeF(max(8.0, 9.0 * dpi_percent / 100.0))
    for widget in (
        window,
        *window._ribbon.findChildren(QToolButton),
        *window.findChildren(QTabBar),
    ):
        widget.setFont(font)
    window._ribbon.retranslate_ui()
    window.resize(1500, 900)
    window.project_dock.show()
    window.operation_manager_dock.show()
    window.show()
    _application().processEvents()
    window._refresh_compact_dock_titles()
    metrics = audit_widget(window)
    assert metrics.ribbon_elision_count == 0
    assert metrics.dock_tab_elision_count == 0
    _dispose(window)
    dialog = QFileDialog(None, "Save", str(tmp_path), "HMS (*.HMS)")
    dialog.setFont(font)
    dialog._localize_controls()
    dialog.resize(900, 600)
    dialog.show()
    _application().processEvents()
    assert audit_widget(dialog).sidebar_elision_count == 0
    _dispose(dialog)


def test_rendered_audit_rejects_intentionally_elided_ribbon_label() -> None:
    root = QWidget()
    button = QToolButton(root)
    button.setObjectName("RibbonButton")
    button.setText("Nạp 3D...")
    button.setProperty("fullText", "Nạp 3D mới cho dự án CAM")
    button.setToolTip("Nạp 3D mới cho dự án CAM")
    button.setAccessibleName("Nạp 3D mới cho dự án CAM")
    button.resize(60, 40)
    root.resize(120, 80)
    root.show()
    button.show()
    _application().processEvents()
    metrics = audit_widget(root)
    assert metrics.unintended_elision_count >= 1
    assert metrics.ribbon_elision_count >= 1
    _dispose(root)


@pytest.mark.parametrize(
    ("language", "contaminated"),
    (
        (UiLanguage.EN_US, "Chưa có"),
        (UiLanguage.EN_US, "CAD Viewer 사용 불가"),
        (UiLanguage.VI_VN, "CAD VIEWER UNAVAILABLE"),
        (UiLanguage.VI_VN, "Stage 8A.4.3 review"),
        (UiLanguage.KO_KR, "strategy"),
        (UiLanguage.KO_KR, "Setup"),
        (UiLanguage.KO_KR, "Mesh"),
        (UiLanguage.KO_KR, "METRIC"),
    ),
)
def test_rendered_audit_fails_for_injected_cross_locale_text(
    i18n_service: TranslationService,
    language: UiLanguage,
    contaminated: str,
) -> None:
    root = QWidget()
    QLabel(contaminated, root)
    root.show()
    _application().processEvents()
    runtime = audit_widget(root)
    report = audit_locale(
        i18n_service,
        language,
        visible_keys=(),
        runtime=RuntimeAuditMetrics(texts=runtime.texts),
    )
    assert report.mixed_language_count >= 1
    _dispose(root)
