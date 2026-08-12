"""General Settings shell and Interface/UI-scale page for C3.1."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QPoint, QRect, QSize, QTimer, Qt
from PySide6.QtGui import QColor, QCloseEvent, QKeyEvent, QMoveEvent, QShowEvent
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QColorDialog,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSlider,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from hms_cadcam.ui.i18n import TranslationService, UiLanguage, translation_service
from hms_cadcam.ui.localization import localize_widget_tree, ui_text
from hms_cadcam.ui.ai_assist_settings import AiAssistSettingsPage
from hms_cadcam.ai_assist.controller import AiAssistController
from hms_cadcam.ai_assist.stage13b_settings import AdvisorSettingsService
from hms_cadcam.ui.settings.ui_scale import (
    DEFAULT_PERCENT,
    MAX_PERCENT,
    MIN_PERCENT,
    UI_SCALE_PRESETS,
    UiScaleManager,
)
from hms_cadcam.ui.settings.export_3d_settings import Export3dSettingsPage
from hms_cadcam.ui.settings.export_defaults import ExportDefaultsSettingsService
from hms_cadcam.ui.settings.viewport_background import (
    VIEWPORT_BACKGROUND_PRESETS,
    ViewportBackgroundManager,
)
from hms_cadcam.viewer.models import ObjectColor


_DIALOG_BASE_SIZE = QSize(820, 600)
_DIALOG_BASE_MINIMUM = QSize(600, 420)
_DIALOG_MINIMUM_FLOOR = QSize(520, 360)
_DIALOG_SCREEN_MARGIN = 12
_NAVIGATION_BASE_WIDTH = 190
_UI_SCALE_RESET_SCOPE = "ui_scale"
_EXPORT_DEFAULTS_RESET_SCOPE = "export_defaults_current"
_VIEWPORT_BACKGROUND_RESET_SCOPE = "viewport_background"
_SHELL_EMPTY_MESSAGE = (
    "This settings category has no available options in the current version."
)


@dataclass(frozen=True, slots=True)
class SettingsCategory:
    """Stable settings-page metadata used by navigation and page actions."""

    key: str
    source: str
    reset_scope: str | None = None


_SETTINGS_CATEGORIES: tuple[SettingsCategory, ...] = (
    SettingsCategory("Interface", "Interface", _UI_SCALE_RESET_SCOPE),
    SettingsCategory("Keyboard shortcuts", "Keyboard shortcuts"),
    SettingsCategory("Language", "Language"),
    SettingsCategory("Storage & projects", "Storage & projects"),
    SettingsCategory("CAD/Viewer", "CAD/Viewer", _VIEWPORT_BACKGROUND_RESET_SCOPE),
    SettingsCategory("3D Export", "3D Export", _EXPORT_DEFAULTS_RESET_SCOPE),
    SettingsCategory("CAM", "CAM"),
    SettingsCategory("Performance", "Performance"),
    SettingsCategory("Advanced", "Advanced"),
)
_SETTINGS_CATEGORY_BY_KEY = {
    category.key: category for category in _SETTINGS_CATEGORIES
}
_AI_ASSIST_CATEGORY = SettingsCategory("AI and Automation", "AI and Automation")


@dataclass(frozen=True, slots=True)
class SettingsDialogGeometry:
    """Screen-aware settings geometry contract for runtime and focused tests."""

    available_geometry: QRect
    frame_allowance: QSize
    desired_size: QSize
    effective_minimum_size: QSize
    maximum_dialog_size: QSize
    target_dialog_size: QSize
    actual_frame_geometry: QRect
    contained_in_available_geometry: bool
    content_scroll_required: bool
    footer_accessible: bool


def settings_dialog_geometry(
    available_geometry: QRect,
    frame_allowance: QSize,
    desired_size: QSize,
    requested_minimum_size: QSize,
    *,
    content_minimum_size: QSize = QSize(),
    actual_frame_geometry: QRect | None = None,
    footer_geometry: QRect | None = None,
    footer_visible: bool = True,
    content_scroll_required: bool | None = None,
    safety_margin: int = _DIALOG_SCREEN_MARGIN,
) -> SettingsDialogGeometry:
    """Return deterministic client/frame constraints inside one screen work area."""

    for name, value, expected in (
        ("available_geometry", available_geometry, QRect),
        ("frame_allowance", frame_allowance, QSize),
        ("desired_size", desired_size, QSize),
        ("requested_minimum_size", requested_minimum_size, QSize),
        ("content_minimum_size", content_minimum_size, QSize),
    ):
        if not isinstance(value, expected):
            raise TypeError(f"{name} must be {expected.__name__}")
    if available_geometry.isNull() or available_geometry.width() <= 0 or available_geometry.height() <= 0:
        raise ValueError("available_geometry must have positive dimensions")
    margin = max(
        0,
        min(
            int(safety_margin),
            max(0, (available_geometry.width() - 1) // 2),
            max(0, (available_geometry.height() - 1) // 2),
        ),
    )
    safe_geometry = available_geometry.adjusted(margin, margin, -margin, -margin)
    frame = QSize(max(0, frame_allowance.width()), max(0, frame_allowance.height()))
    maximum = QSize(
        max(1, safe_geometry.width() - frame.width()),
        max(1, safe_geometry.height() - frame.height()),
    )
    effective_minimum = QSize(
        min(maximum.width(), max(1, requested_minimum_size.width())),
        min(maximum.height(), max(1, requested_minimum_size.height())),
    )
    target = QSize(
        min(maximum.width(), max(effective_minimum.width(), max(1, desired_size.width()))),
        min(maximum.height(), max(effective_minimum.height(), max(1, desired_size.height()))),
    )
    planned_frame_size = QSize(target.width() + frame.width(), target.height() + frame.height())
    planned_frame = QRect(
        safe_geometry.x() + max(0, (safe_geometry.width() - planned_frame_size.width()) // 2),
        safe_geometry.y() + max(0, (safe_geometry.height() - planned_frame_size.height()) // 2),
        planned_frame_size.width(),
        planned_frame_size.height(),
    )
    actual_frame = QRect(actual_frame_geometry) if actual_frame_geometry is not None else planned_frame
    contained = available_geometry.contains(actual_frame)
    required_scroll = (
        bool(content_scroll_required)
        if content_scroll_required is not None
        else (
            content_minimum_size.width() > target.width()
            or content_minimum_size.height() > target.height()
            or desired_size.width() > target.width()
            or desired_size.height() > target.height()
        )
    )
    footer_is_accessible = bool(footer_visible) and (
        contained if footer_geometry is None else available_geometry.contains(footer_geometry)
    )
    return SettingsDialogGeometry(
        available_geometry=QRect(available_geometry),
        frame_allowance=frame,
        desired_size=QSize(desired_size),
        effective_minimum_size=effective_minimum,
        maximum_dialog_size=maximum,
        target_dialog_size=target,
        actual_frame_geometry=actual_frame,
        contained_in_available_geometry=contained,
        content_scroll_required=required_scroll,
        footer_accessible=footer_is_accessible,
    )


class GeneralSettingsDialog(QDialog):
    """One non-modal, idempotent settings shell with a live UI-scale page."""

    def __init__(
        self,
        scale_manager: UiScaleManager,
        *,
        service: TranslationService | None = None,
        ai_assist_controller: AiAssistController | None = None,
        advisor_settings_service: AdvisorSettingsService | None = None,
        export_defaults_service: ExportDefaultsSettingsService | None = None,
        viewport_background_manager: ViewportBackgroundManager | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._scale_manager = scale_manager
        self._service = service or translation_service()
        self._ai_assist_controller = ai_assist_controller
        self._advisor_settings_service = advisor_settings_service
        self._export_defaults_service = (
            export_defaults_service
            or ExportDefaultsSettingsService(scale_manager.settings)
        )
        self._viewport_background_manager = viewport_background_manager
        self._categories = (
            (*_SETTINGS_CATEGORIES, _AI_ASSIST_CATEGORY)
            if ai_assist_controller is not None
            else _SETTINGS_CATEGORIES
        )
        self._category_by_key = {category.key: category for category in self._categories}
        self._preview_dirty = False
        self._setting_controls = False
        self._screen_fit_guard = False
        self._fit_timer = QTimer(self)
        self._fit_timer.setSingleShot(True)
        self._fit_timer.timeout.connect(self._fit_to_screen)
        self._category_items: list[QListWidgetItem] = []
        self._category_pages: list[QWidget] = []
        self._last_geometry: SettingsDialogGeometry | None = None
        self.setObjectName("GeneralSettingsDialog")
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setMinimumSize(QSize(0, 0))
        self.resize(_DIALOG_BASE_SIZE)
        self._build_ui()
        self._scale_manager.preview_changed.connect(self._preview_scale_changed)
        self._scale_manager.scale_changed.connect(self._applied_scale_changed)
        self._language_changed_slot = self.retranslate_ui
        self._service.language_changed.connect(self._language_changed_slot)
        self.retranslate_ui(self._service.language)
        self._preview_scale_changed(self._scale_manager.current_percent)

    @property
    def scale_manager(self) -> UiScaleManager:
        """Return the injected scale service used by this dialog."""

        return self._scale_manager

    @property
    def selected_category(self) -> str:
        """Return the stable category key selected in navigation."""

        item = self.category_list.currentItem()
        return str(item.data(Qt.ItemDataRole.UserRole)) if item is not None else "Interface"

    def _build_ui(self) -> None:
        self.title_label = QLabel()
        self.title_label.setObjectName("GeneralSettingsTitle")
        self.title_label.setStyleSheet("font-weight: 600;")
        self.breadcrumb_label = QLabel()
        self.breadcrumb_label.setObjectName("GeneralSettingsBreadcrumb")
        self.breadcrumb_label.setWordWrap(True)

        self.category_list = QListWidget()
        self.category_list.setObjectName("SettingsCategoryList")
        self.category_list.setMinimumWidth(0)
        self.category_list.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding
        )
        self.category_list.currentRowChanged.connect(self._category_changed)

        self.page_stack = QStackedWidget()
        self.page_stack.setObjectName("SettingsPageStack")
        self.page_stack.setMinimumSize(QSize(0, 0))
        self.page_stack.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored
        )
        self._interface_page = self._build_interface_page()
        self.page_stack.addWidget(self._interface_page)
        self._category_pages.append(self._interface_page)
        for category in self._categories[1:]:
            page = (
                AiAssistSettingsPage(self._ai_assist_controller, advisor_settings_service=self._advisor_settings_service)
                if category.key == _AI_ASSIST_CATEGORY.key and self._ai_assist_controller is not None
                else Export3dSettingsPage(
                    self._export_defaults_service,
                    translation=self._service,
                )
                if category.key == "3D Export"
                else self._build_viewport_page()
                if category.key == "CAD/Viewer" and self._viewport_background_manager is not None
                else self._build_placeholder_page(category.key)
            )
            self.page_stack.addWidget(page)
            self._category_pages.append(page)
            if isinstance(page, Export3dSettingsPage):
                self._export_3d_page = page
                page.dirty_changed.connect(self._export_defaults_dirty_changed)

        self.reset_button = QPushButton()
        self.reset_button.setObjectName("ResetUiScaleButton")
        self.reset_button.clicked.connect(self._reset_default)
        self.cancel_button = QPushButton()
        self.cancel_button.setObjectName("CancelSettingsButton")
        self.cancel_button.clicked.connect(self._cancel)
        self.apply_button = QPushButton()
        self.apply_button.setObjectName("ApplySettingsButton")
        self.apply_button.setDefault(True)
        self.apply_button.clicked.connect(self._apply)
        self.ok_button = QPushButton()
        self.ok_button.setObjectName("OkSettingsButton")
        self.ok_button.clicked.connect(self._ok)
        self._footer_buttons = (
            self.reset_button,
            self.cancel_button,
            self.apply_button,
            self.ok_button,
        )

        self.page_scroll = QScrollArea()
        self.page_scroll.setObjectName("SettingsPageScrollArea")
        self.page_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.page_scroll.setWidgetResizable(True)
        self.page_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.page_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.page_scroll.setMinimumSize(QSize(0, 0))
        self.page_scroll.setWidget(self.page_stack)

        navigation = QHBoxLayout()
        navigation.addWidget(self.category_list)
        navigation.addWidget(self.page_scroll, 1)

        footer = QHBoxLayout()
        footer.addWidget(self.reset_button)
        footer.addStretch(1)
        footer.addWidget(self.cancel_button)
        footer.addWidget(self.apply_button)
        footer.addWidget(self.ok_button)

        self._root_layout = QVBoxLayout(self)
        self._root_layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self._root_layout.addWidget(self.title_label)
        self._root_layout.addWidget(self.breadcrumb_label)
        self._root_layout.addLayout(navigation, 1)
        self._root_layout.addLayout(footer)

        for category in self._categories:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, category.key)
            item.setData(Qt.ItemDataRole.UserRole + 1, category.source)
            self.category_list.addItem(item)
            self._category_items.append(item)
        self.category_list.setCurrentRow(0)

    def _build_interface_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("SettingsInterfacePage")
        layout = QVBoxLayout(page)
        self._interface_layout = layout
        self.interface_heading = QLabel()
        self.interface_heading.setObjectName("SettingsInterfaceHeading")
        self.interface_heading.setStyleSheet("font-weight: 600;")
        self.interface_description = QLabel()
        self.interface_description.setWordWrap(True)

        scale_row = QHBoxLayout()
        self.scale_label = QLabel()
        self.scale_slider = QSlider(Qt.Orientation.Horizontal)
        self.scale_slider.setObjectName("UiScaleSlider")
        self.scale_slider.setRange(MIN_PERCENT, MAX_PERCENT)
        self.scale_slider.setSingleStep(5)
        self.scale_slider.setPageStep(25)
        self.scale_slider.setTickInterval(25)
        self.scale_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.scale_spin = QSpinBox()
        self.scale_spin.setObjectName("UiScaleSpinBox")
        self.scale_spin.setRange(MIN_PERCENT, MAX_PERCENT)
        self.scale_spin.setSingleStep(1)
        self.scale_spin.setSuffix("%")
        self.scale_spin.valueChanged.connect(self._spin_scale_changed)
        self.scale_slider.valueChanged.connect(self._slider_scale_changed)
        scale_row.addWidget(self.scale_label)
        scale_row.addWidget(self.scale_slider, 1)
        scale_row.addWidget(self.scale_spin)

        self.presets_label = QLabel()
        self.preset_layout = QGridLayout()
        self.preset_layout.setContentsMargins(0, 0, 0, 0)
        self.preset_buttons: dict[int, QPushButton] = {}
        for value in UI_SCALE_PRESETS:
            button = QPushButton(f"{value}%")
            button.setObjectName(f"UiScalePreset{value}")
            button.clicked.connect(self._preset_handler(value))
            self.preset_buttons[value] = button
        self._relayout_preset_buttons()
        self.preview_heading = QLabel()
        self.preview_heading.setObjectName("UiScalePreviewHeading")
        self.preview_status = QLabel()
        self.preview_status.setObjectName("UiScalePreviewStatus")
        self.preview_status.setWordWrap(True)
        self.preview_frame = QFrame()
        self.preview_frame.setObjectName("UiScalePreviewFrame")
        preview_layout = QVBoxLayout(self.preview_frame)
        self.sample_title = QLabel()
        self.sample_title.setObjectName("UiScaleSampleTitle")
        self.sample_button = QPushButton()
        self.sample_button.setObjectName("UiScaleSampleButton")
        self.sample_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DialogApplyButton)
        )
        self.sample_input = QLineEdit()
        self.sample_input.setObjectName("UiScaleSampleInput")
        self.sample_combo = QComboBox()
        self.sample_combo.setObjectName("UiScaleSampleCombo")
        self.sample_combo.addItems(["", "", ""])
        self.sample_table = QTableWidget(2, 3)
        self.sample_table.setObjectName("UiScaleSampleTable")
        self.sample_table.setHorizontalHeaderLabels(["A", "B", "C"])
        for row in range(2):
            for column in range(3):
                self.sample_table.setItem(row, column, QTableWidgetItem(f"{row + 1}:{column + 1}"))
        self.sample_tree = QTreeWidget()
        self.sample_tree.setObjectName("UiScaleSampleTree")
        self.sample_tree.setHeaderLabels([""])
        root = QTreeWidgetItem([""])
        root.addChild(QTreeWidgetItem([""]))
        self.sample_tree.addTopLevelItem(root)
        preview_layout.addWidget(self.sample_title)
        preview_layout.addWidget(self.sample_button)
        preview_layout.addWidget(self.sample_input)
        preview_layout.addWidget(self.sample_combo)
        preview_layout.addWidget(self.sample_table)
        preview_layout.addWidget(self.sample_tree)

        layout.addWidget(self.interface_heading)
        layout.addWidget(self.interface_description)
        layout.addLayout(scale_row)
        layout.addWidget(self.presets_label)
        layout.addLayout(self.preset_layout)
        layout.addWidget(self.preview_heading)
        layout.addWidget(self.preview_status)
        layout.addWidget(self.preview_frame, 1)
        return page

    def _build_placeholder_page(self, category: str) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        heading = QLabel()
        heading.setObjectName(f"Settings{category.replace('/', '')}Heading")
        heading.setProperty("settingsCategory", category)
        heading.setStyleSheet("font-weight: 600;")
        message = QLabel()
        message.setObjectName(f"Settings{category.replace('/', '')}Message")
        message.setProperty("settingsCategory", category)
        message.setWordWrap(True)
        layout.addWidget(heading)
        layout.addWidget(message)
        layout.addStretch(1)
        return page

    def _build_viewport_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("SettingsCadViewerPage")
        layout = QVBoxLayout(page)
        self.viewport_heading = QLabel()
        self.viewport_heading.setStyleSheet("font-weight: 600;")
        self.viewport_description = QLabel()
        self.viewport_description.setWordWrap(True)
        self.background_label = QLabel()
        controls = QHBoxLayout()
        self.background_preview = QFrame()
        self.background_preview.setObjectName("ViewportBackgroundPreview")
        self.background_preview.setFixedSize(72, 32)
        self.background_picker = QPushButton()
        self.background_picker.setObjectName("ViewportBackgroundPicker")
        self.background_picker.clicked.connect(self._pick_background)
        controls.addWidget(self.background_preview)
        controls.addWidget(self.background_picker)
        controls.addStretch(1)
        self.background_presets = QHBoxLayout()
        self.background_preset_buttons: list[QPushButton] = []
        for index, color in enumerate(VIEWPORT_BACKGROUND_PRESETS):
            button = QPushButton(color.to_hex())
            button.setObjectName(f"ViewportBackgroundPreset{index}")
            button.clicked.connect(
                lambda _checked=False, value=color: self._set_background_preview(value)
            )
            self.background_presets.addWidget(button)
            self.background_preset_buttons.append(button)
        self.background_presets.addStretch(1)
        layout.addWidget(self.viewport_heading)
        layout.addWidget(self.viewport_description)
        layout.addWidget(self.background_label)
        layout.addLayout(controls)
        layout.addLayout(self.background_presets)
        layout.addStretch(1)
        manager = self._viewport_background_manager
        assert manager is not None
        manager.preview_changed.connect(self._background_preview_changed)
        self._background_preview_changed(manager.current_color)
        return page

    def _pick_background(self) -> None:
        manager = self._viewport_background_manager
        if manager is None:
            return
        selected = QColorDialog.getColor(
            QColor(manager.current_color.to_hex()),
            self,
            ui_text("Viewport background color"),
        )
        if selected.isValid():
            self._set_background_preview(
                ObjectColor(selected.redF(), selected.greenF(), selected.blueF())
            )

    def _set_background_preview(self, color: ObjectColor) -> None:
        manager = self._viewport_background_manager
        if manager is not None:
            manager.set_preview_color(color)

    def _background_preview_changed(self, color: ObjectColor) -> None:
        self.background_preview.setStyleSheet(
            f"background:{color.to_hex()};border:1px solid #6f7780"
        )
        self.background_preview.setToolTip(color.to_hex())
        self._update_apply_button()

    def _preset_handler(self, value: int) -> Callable[[], None]:
        return lambda: self._scale_manager.set_preview_percent(value)

    def _slider_scale_changed(self, value: int) -> None:
        if not self._setting_controls:
            self._scale_manager.set_preview_percent(value)

    def _spin_scale_changed(self, value: int) -> None:
        if not self._setting_controls:
            self._scale_manager.set_preview_percent(value)

    def _preview_scale_changed(self, value: int) -> None:
        self._preview_dirty = value != self._scale_manager.persisted_percent
        self._setting_controls = True
        try:
            self.scale_slider.setValue(value)
            self.scale_spin.setValue(value)
        finally:
            self._setting_controls = False
        self._scale_manager.apply_widget_tree(self)
        self._apply_preview_metrics()
        self.preview_status.setText(
            "\n".join(
                (
                    ui_text("Preview: {percent}%").format(percent=value),
                    ui_text("Applied: {percent}%").format(
                        percent=self._scale_manager.persisted_percent
                    ),
                )
            )
        )
        screen = self.screen() or QApplication.primaryScreen()
        if screen is not None and (value >= 150 and (screen.availableGeometry().width() < 1280 or screen.availableGeometry().height() < 720)):
            self.preview_status.setText(
                self.preview_status.text()
                + "\n"
                + ui_text("This scale may require a compact layout on the current screen.")
            )
        self._update_apply_button()
        self._schedule_fit()

    def _applied_scale_changed(self, value: int) -> None:
        self._preview_scale_changed(value)

    def _apply_preview_metrics(self) -> None:
        metrics = self._scale_manager.metrics()
        self._root_layout.setContentsMargins(
            self._scale_manager.scaled_margins((18, 16, 18, 16), minimum=6)
        )
        self._root_layout.setSpacing(self._scale_manager.scaled_int(10, minimum=4))
        self._interface_layout.setSpacing(metrics.spacing)
        self._relayout_preset_buttons()
        self._apply_navigation_width()
        self.sample_table.verticalHeader().setDefaultSectionSize(metrics.row_height)
        self.sample_table.horizontalHeader().setDefaultSectionSize(metrics.header_height)
        self.sample_tree.setIconSize(metrics.icon_size)

    def _relayout_preset_buttons(self) -> None:
        while self.preset_layout.count():
            self.preset_layout.takeAt(0)
        page_scroll = getattr(self, "page_scroll", None)
        available_width = max(
            1,
            page_scroll.viewport().width() if page_scroll is not None else self.width(),
        )
        button_width = max(48, self._scale_manager.scaled_int(72, minimum=48))
        columns = max(1, min(len(self.preset_buttons), available_width // button_width))
        for index, value in enumerate(UI_SCALE_PRESETS):
            button = self.preset_buttons[value]
            self.preset_layout.addWidget(button, index // columns, index % columns)

    def _apply_navigation_width(self) -> None:
        available_width = max(self.width(), self._scale_manager.scaled_int(520, minimum=260))
        baseline = self._scale_manager.scaled_int(_NAVIGATION_BASE_WIDTH, minimum=120)
        cap = max(120, int(available_width * 0.30))
        width = min(baseline, cap)
        self.category_list.setMinimumWidth(width)
        self.category_list.setMaximumWidth(max(width, int(available_width * 0.36)))

    def _category_changed(self, row: int) -> None:
        if 0 <= row < self.page_stack.count():
            self.page_stack.setCurrentIndex(row)
            category = str(self._category_items[row].data(Qt.ItemDataRole.UserRole))
            title = ui_text(category)
            self.breadcrumb_label.setText(f"{ui_text('General settings')} / {title}")
            self._update_reset_button(category)

    def _update_reset_button(self, category_key: str | None = None) -> None:
        category = self._category_by_key.get(category_key or self.selected_category)
        enabled = category is not None and category.reset_scope in {
            _UI_SCALE_RESET_SCOPE,
            _EXPORT_DEFAULTS_RESET_SCOPE,
            _VIEWPORT_BACKGROUND_RESET_SCOPE,
        }
        self.reset_button.setEnabled(enabled)
        self.reset_button.setText(
            ui_text(
                "Reset to 100%"
                if category is not None and category.reset_scope == _UI_SCALE_RESET_SCOPE
                else "Reset current format"
                if category is not None and category.reset_scope == _EXPORT_DEFAULTS_RESET_SCOPE
                else "Reset Default"
                if category is not None and category.reset_scope == _VIEWPORT_BACKGROUND_RESET_SCOPE
                else "Reset"
            )
        )

    def _export_defaults_dirty_changed(self, _dirty: bool) -> None:
        self._update_apply_button()

    def _update_apply_button(self) -> None:
        if not hasattr(self, "apply_button"):
            return
        export_page = getattr(self, "_export_3d_page", None)
        self.apply_button.setEnabled(
            self._preview_dirty
            or (export_page is not None and export_page.dirty)
            or (
                self._viewport_background_manager is not None
                and self._viewport_background_manager.current_color
                != self._viewport_background_manager.persisted_color
            )
        )

    def _apply(self) -> bool:
        export_page = getattr(self, "_export_3d_page", None)
        if export_page is not None and export_page.dirty and not export_page.apply():
            self._update_apply_button()
            return False
        if self._preview_dirty and not self._scale_manager.apply_percent():
            self.preview_status.setText(ui_text("The UI scale could not be saved."))
            self._update_apply_button()
            return False
        manager = self._viewport_background_manager
        if manager is not None and manager.current_color != manager.persisted_color:
            if not manager.apply_color():
                self._update_apply_button()
                return False
        self._preview_dirty = False
        self._update_apply_button()
        return True

    def _ok(self) -> None:
        if self._apply():
            self.close()

    def _cancel(self) -> None:
        self._scale_manager.cancel_preview()
        if self._viewport_background_manager is not None:
            self._viewport_background_manager.cancel_preview()
        self.close()

    def _reset_default(self) -> None:
        category = self._category_by_key.get(self.selected_category)
        if category is None:
            return
        if category.reset_scope == _UI_SCALE_RESET_SCOPE:
            self._scale_manager.reset_default()
        elif category.reset_scope == _EXPORT_DEFAULTS_RESET_SCOPE:
            self._export_3d_page.reset_current()
        elif category.reset_scope == _VIEWPORT_BACKGROUND_RESET_SCOPE:
            assert self._viewport_background_manager is not None
            self._viewport_background_manager.reset_default()

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        self._fit_to_screen()
        self._schedule_fit()
        self._preview_scale_changed(self._scale_manager.current_percent)

    def _schedule_fit(self) -> None:
        if not self._fit_timer.isActive() and not self._screen_fit_guard:
            self._fit_timer.start(100)

    def moveEvent(self, event: QMoveEvent) -> None:  # noqa: N802
        super().moveEvent(event)
        if self._screen_fit_guard or not self.isVisible():
            return
        screen = self.screen() or QApplication.primaryScreen()
        if screen is not None and not screen.availableGeometry().contains(
            self.frameGeometry()
        ):
            self._schedule_fit()

    def _fit_to_screen(self) -> None:
        if self._screen_fit_guard:
            return
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            return
        self._screen_fit_guard = True
        try:
            available = screen.availableGeometry()
            frame_before = self.frameGeometry()
            frame_delta = QSize(
                max(0, frame_before.width() - self.width()),
                max(0, frame_before.height() - self.height()),
            )
            desired = self._scale_manager.scaled_size(
                _DIALOG_BASE_SIZE, minimum=_DIALOG_MINIMUM_FLOOR
            )
            requested_minimum = self._scale_manager.scaled_size(
                _DIALOG_BASE_MINIMUM, minimum=_DIALOG_MINIMUM_FLOOR
            )
            current_page = self.page_stack.currentWidget()
            content_minimum = current_page.sizeHint() if current_page is not None else QSize()
            constraints = settings_dialog_geometry(
                available,
                frame_delta,
                desired,
                requested_minimum,
                content_minimum_size=content_minimum,
            )
            self._last_geometry = constraints
            self.setMinimumSize(constraints.effective_minimum_size)
            self.setMaximumSize(constraints.maximum_dialog_size)
            self.resize(constraints.target_dialog_size)
            frame = self.frameGeometry()
            geometry = self.geometry()
            client_offset_x = geometry.x() - frame.x()
            client_offset_y = geometry.y() - frame.y()
            target_frame = constraints.actual_frame_geometry
            client_position = QPoint(
                target_frame.x() + client_offset_x,
                target_frame.y() + client_offset_y,
            )
            handle = self.windowHandle()
            if handle is not None:
                handle.setPosition(client_position)
            else:
                self.move(target_frame.x(), target_frame.y())
        finally:
            self._screen_fit_guard = False

    def _footer_geometry(self) -> QRect:
        geometry = QRect()
        for button in self._footer_buttons:
            button_geometry = QRect(button.mapToGlobal(button.rect().topLeft()), button.size())
            geometry = button_geometry if geometry.isNull() else geometry.united(button_geometry)
        return geometry

    def geometry_evidence(self) -> SettingsDialogGeometry:
        screen = self.screen() or QApplication.primaryScreen()
        available = screen.availableGeometry() if screen is not None else QRect()
        frame = self.frameGeometry()
        client = self.geometry()
        frame_delta = QSize(
            max(0, frame.width() - client.width()),
            max(0, frame.height() - client.height()),
        )
        desired = self._scale_manager.scaled_size(
            _DIALOG_BASE_SIZE, minimum=_DIALOG_MINIMUM_FLOOR
        )
        requested_minimum = self._scale_manager.scaled_size(
            _DIALOG_BASE_MINIMUM, minimum=_DIALOG_MINIMUM_FLOOR
        )
        current_page = self.page_stack.currentWidget()
        content_minimum = current_page.sizeHint() if current_page is not None else QSize()
        return settings_dialog_geometry(
            available,
            frame_delta,
            desired,
            requested_minimum,
            content_minimum_size=content_minimum,
            actual_frame_geometry=frame,
            footer_geometry=self._footer_geometry(),
            footer_visible=all(button.isVisible() for button in self._footer_buttons),
            content_scroll_required=self.page_scroll.verticalScrollBar().maximum() > 0,
        )

    def retranslate_ui(self, language: object = None) -> None:
        if language is not None:
            UiLanguage.coerce(language)
        self.setWindowTitle(ui_text("General settings"))
        self.setAccessibleName(ui_text("General settings"))
        self.setAccessibleDescription(ui_text("Configure interface and display preferences."))
        self.title_label.setText(ui_text("General settings"))
        self._category_changed(self.category_list.currentRow())
        self.interface_heading.setText(ui_text("Scale and density"))
        self.interface_description.setText(ui_text("UI scale changes the logical presentation metrics without changing Windows DPI."))
        self.scale_label.setText(ui_text("UI scale"))
        self.scale_label.setAccessibleName(ui_text("UI scale"))
        self.scale_spin.setToolTip(ui_text("Enter any whole-number percentage from 50% to 200%."))
        self.presets_label.setText(ui_text("Quick presets"))
        self.preview_heading.setText(ui_text("Preview"))
        self.sample_title.setText(ui_text("Sample title"))
        self.sample_button.setText(ui_text("Sample button"))
        self.sample_input.setPlaceholderText(ui_text("Sample input"))
        self.sample_combo.setItemText(0, ui_text("Sample option A"))
        self.sample_combo.setItemText(1, ui_text("Sample option B"))
        self.sample_combo.setItemText(2, ui_text("Sample option C"))
        self.sample_tree.headerItem().setText(0, ui_text("Sample tree"))
        self.sample_tree.topLevelItem(0).setText(0, ui_text("Root"))
        self.sample_tree.topLevelItem(0).child(0).setText(0, ui_text("Child"))
        for index, category in enumerate(self._categories):
            self._category_items[index].setText(ui_text(category.key))
            if index > 0:
                page = self._category_pages[index]
                if isinstance(page, AiAssistSettingsPage):
                    page.retranslate_ui()
                    continue
                if isinstance(page, Export3dSettingsPage):
                    page.retranslate_ui(language)
                    continue
                if category.key == "CAD/Viewer" and self._viewport_background_manager is not None:
                    self.viewport_heading.setText(ui_text("3D display"))
                    self.viewport_description.setText(
                        ui_text("Choose one stable solid background for CAD and Machining Simulation viewports.")
                    )
                    self.background_label.setText(ui_text("Viewport background color"))
                    self.background_picker.setText(ui_text("Choose color…"))
                    continue
                heading = page.findChildren(QLabel)[0]
                message = page.findChildren(QLabel)[1]
                heading.setText(ui_text(category.key))
                message.setText(ui_text(_SHELL_EMPTY_MESSAGE))
        self._update_reset_button()
        self.cancel_button.setText(ui_text("Cancel"))
        self.apply_button.setText(ui_text("Apply"))
        self.ok_button.setText(ui_text("OK"))
        localize_widget_tree(self)
        self._preview_scale_changed(self._scale_manager.current_percent)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if getattr(self, "_language_changed_slot", None) is not None:
            self._service.language_changed.disconnect(self._language_changed_slot)
            self._language_changed_slot = None
        self._scale_manager.cancel_preview()
        if self._viewport_background_manager is not None:
            self._viewport_background_manager.cancel_preview()
        event.accept()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self._cancel()
            return
        super().keyPressEvent(event)


__all__ = ["GeneralSettingsDialog", "SettingsDialogGeometry", "settings_dialog_geometry"]
