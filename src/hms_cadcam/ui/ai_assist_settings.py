"""Presentation-only General Settings page for Stage 13A offline CAM AI assist."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from hms_cadcam.ai_assist.controller import AiAssistController
from hms_cadcam.ai_assist.lifecycle import AiRuntimeState
from hms_cadcam.ai_assist.policy import AiMode, MIB
from hms_cadcam.ai_assist.settings import AiAssistSettings
from hms_cadcam.ai_assist.cutting_advisor import RecommendationProfile
from hms_cadcam.ai_assist.stage13b_settings import AdvisorSettings, AdvisorSettingsService
from hms_cadcam.ui.localization import ui_text


def _display_bytes(value: int | None) -> str:
    if value is None:
        return ui_text("Unknown")
    if value >= 1024 * MIB:
        return f"{value / (1024 * MIB):.1f} GiB"
    return f"{value / MIB:.0f} MiB"


class AiAssistSettingsPage(QWidget):
    """Render and persist application-only AI resource preferences."""

    def __init__(self, controller: AiAssistController, parent: QWidget | None = None, *, advisor_settings_service: AdvisorSettingsService | None = None) -> None:
        super().__init__(parent)
        self._controller = controller
        self._advisor_settings_service = advisor_settings_service
        self._updating = False
        self.setObjectName("AiAssistSettingsPage")
        self._build_ui()
        self._load_settings()
        self.retranslate_ui()
        self._show_status(probe=False)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        self.heading_label = QLabel()
        self.heading_label.setObjectName("AiAssistSettingsHeading")
        self.heading_label.setStyleSheet("font-weight: 600;")
        root.addWidget(self.heading_label)

        self.master_checkbox = QCheckBox()
        self.master_checkbox.setObjectName("AiAssistMasterToggle")
        root.addWidget(self.master_checkbox)

        preference_group = QGroupBox()
        self.preference_group = preference_group
        form = QFormLayout(preference_group)
        self.mode_combo = QComboBox()
        self.mode_combo.setObjectName("AiAssistModeCombo")
        for mode in AiMode:
            self.mode_combo.addItem("", mode)
        self.ram_spin = QSpinBox()
        self.ram_spin.setObjectName("AiAssistRamRatioSpin")
        self.ram_spin.setRange(1, 100)
        self.ram_spin.setSuffix(" %")
        self.vram_spin = QSpinBox()
        self.vram_spin.setObjectName("AiAssistVramRatioSpin")
        self.vram_spin.setRange(1, 100)
        self.vram_spin.setSuffix(" %")
        self.waiting_label = QLabel()
        self.waiting_label.setObjectName("AiAssistResourceShortageLabel")
        self.mode_form_label = QLabel()
        self.ram_form_label = QLabel()
        self.vram_form_label = QLabel()
        form.addRow(self.mode_form_label, self.mode_combo)
        form.addRow(self.ram_form_label, self.ram_spin)
        form.addRow(self.vram_form_label, self.vram_spin)
        form.addRow(self.waiting_label)
        root.addWidget(preference_group)
        if self._advisor_settings_service is not None:
            advisor_group = QGroupBox()
            advisor_group.setObjectName("Stage13BAdvisorSettingsGroup")
            advisor_form = QFormLayout(advisor_group)
            self.advisor_checkbox = QCheckBox()
            self.advisor_checkbox.setObjectName("Stage13BAdvisorToggle")
            self.advisor_profile_combo = QComboBox()
            self.advisor_profile_combo.setObjectName("Stage13BAdvisorProfile")
            for profile in RecommendationProfile: self.advisor_profile_combo.addItem(profile.value, profile)
            self.advisor_timeout_spin = QSpinBox(); self.advisor_timeout_spin.setRange(1,30); self.advisor_timeout_spin.setObjectName("Stage13BAdvisorTimeout")
            advisor_form.addRow(self.advisor_checkbox)
            advisor_form.addRow("Profile", self.advisor_profile_combo)
            advisor_form.addRow("Worker timeout (seconds)", self.advisor_timeout_spin)
            root.addWidget(advisor_group)

        status_group = QGroupBox()
        self.status_group = status_group
        status_layout = QFormLayout(status_group)
        self._status_values: dict[str, QLabel] = {}
        self._status_labels: dict[str, QLabel] = {}
        for key in (
            "ram_available",
            "ram_reserve",
            "ai_budget",
            "vram_status",
            "compute",
            "tier",
            "state",
            "reason",
            "worker",
        ):
            label = QLabel()
            value = QLabel()
            value.setObjectName(f"AiAssistStatus{key.title().replace('_', '')}")
            value.setWordWrap(True)
            self._status_labels[key] = label
            self._status_values[key] = value
            status_layout.addRow(label, value)
        refresh_row = QHBoxLayout()
        refresh_row.addStretch(1)
        self.refresh_button = QPushButton()
        self.refresh_button.setObjectName("AiAssistRefreshResourceStatus")
        refresh_row.addWidget(self.refresh_button)
        status_layout.addRow(refresh_row)
        self.cam_continues_label = QLabel()
        self.cam_continues_label.setObjectName("AiAssistCamContinuesNormally")
        self.cam_continues_label.setWordWrap(True)
        root.addWidget(self.cam_continues_label)
        root.addWidget(status_group)
        root.addStretch(1)

        self.master_checkbox.toggled.connect(self._save_from_controls)
        self.mode_combo.currentIndexChanged.connect(self._save_from_controls)
        self.ram_spin.valueChanged.connect(self._save_from_controls)
        self.vram_spin.valueChanged.connect(self._save_from_controls)
        self.refresh_button.clicked.connect(lambda: self._show_status(probe=True))
        if self._advisor_settings_service is not None:
            self.advisor_checkbox.toggled.connect(self._save_advisor_settings); self.advisor_profile_combo.currentIndexChanged.connect(self._save_advisor_settings); self.advisor_timeout_spin.valueChanged.connect(self._save_advisor_settings)

    def _load_settings(self) -> None:
        self._updating = True
        values = self._controller.settings
        self.master_checkbox.setChecked(values.enabled)
        self.mode_combo.setCurrentIndex(self.mode_combo.findData(values.mode))
        self.ram_spin.setValue(values.ram_ratio_percent)
        self.vram_spin.setValue(values.vram_ratio_percent)
        self._updating = False
        if self._advisor_settings_service is not None:
            advisor=self._advisor_settings_service.load(); self._updating=True; self.advisor_checkbox.setChecked(advisor.enabled); self.advisor_profile_combo.setCurrentIndex(self.advisor_profile_combo.findData(advisor.profile)); self.advisor_timeout_spin.setValue(int(advisor.timeout_seconds)); self.advisor_checkbox.setEnabled(values.enabled); self._updating=False

    def _save_advisor_settings(self) -> None:
        if self._updating or self._advisor_settings_service is None: return
        profile=self.advisor_profile_combo.currentData()
        if not isinstance(profile, RecommendationProfile): return
        self._advisor_settings_service.save(AdvisorSettings(self.advisor_checkbox.isChecked(),profile,float(self.advisor_timeout_spin.value())))

    def _save_from_controls(self) -> None:
        if self._updating:
            return
        mode = self.mode_combo.currentData()
        if not isinstance(mode, AiMode):
            return
        values = AiAssistSettings(
            enabled=self.master_checkbox.isChecked(),
            mode=mode,
            ram_ratio_percent=self.ram_spin.value(),
            vram_ratio_percent=self.vram_spin.value(),
            user_cap_bytes=self._controller.settings.user_cap_bytes,
        )
        if not self._controller.save_settings(values):
            self._load_settings()
        self._show_status(probe=False)

    def _show_status(self, *, probe: bool) -> None:
        status = self._controller.refresh_resource_status() if probe else self._controller.status
        disabled = status.state is AiRuntimeState.OFF
        budget = status.budget
        self._status_values["ram_available"].setText(
            ui_text("Disabled") if disabled else _display_bytes(budget.effective_available_bytes if budget else None)
        )
        self._status_values["ram_reserve"].setText(
            ui_text("Disabled") if disabled else _display_bytes(budget.dynamic_reserve_bytes if budget else None)
        )
        self._status_values["ai_budget"].setText(
            ui_text("Disabled") if disabled else _display_bytes(budget.ai_ram_budget_bytes if budget else None)
        )
        self._status_values["vram_status"].setText(
            ui_text("Disabled") if disabled else ui_text("GPU unavailable or unknown")
            if budget is None or budget.vram_budget_bytes is None
            else _display_bytes(budget.vram_budget_bytes)
        )
        self._status_values["compute"].setText(
            ui_text("Disabled") if disabled else ui_text(
                f"ai_assist.compute.{budget.compute_selection.value}" if budget else "ai_assist.compute.CPU_ONLY"
            )
        )
        self._status_values["tier"].setText(
            ui_text("Disabled") if disabled else ui_text(
                f"ai_assist.tier.{status.selected_tier.value}" if status.selected_tier else "Unknown"
            )
        )
        self._status_values["state"].setText(ui_text(f"ai_assist.state.{status.state.value}"))
        self._status_values["reason"].setText(ui_text(f"ai_assist.reason.{status.reason_code}"))
        self._status_values["worker"].setText(
            ui_text("Worker started") if status.worker_started else ui_text("No worker or model loaded")
        )
        self.refresh_button.setEnabled(self.master_checkbox.isChecked())

    def retranslate_ui(self, _language: object = None) -> None:
        """Refresh every managed string after a central locale change."""

        self.heading_label.setText(ui_text("AI and Automation"))
        self.master_checkbox.setText(ui_text("AI CAM assist"))
        self.preference_group.setTitle(ui_text("AI and Automation"))
        self.mode_form_label.setText(ui_text("Mode"))
        self.ram_form_label.setText(ui_text("Maximum RAM ratio"))
        self.vram_form_label.setText(ui_text("Maximum VRAM ratio"))
        self.waiting_label.setText(ui_text("When resources are insufficient: wait for resources"))
        self.status_group.setTitle(ui_text("Resource status"))
        for mode in AiMode:
            self.mode_combo.setItemText(
                self.mode_combo.findData(mode),
                ui_text(
                    {
                        AiMode.AUTO: "Automatic according to machine resources",
                        AiMode.LITE: "AI Lite",
                        AiMode.STANDARD: "AI Standard",
                        AiMode.ENHANCED: "AI Enhanced",
                    }[mode]
                ),
            )
        labels = {
            "ram_available": "RAM available",
            "ram_reserve": "RAM reserve",
            "ai_budget": "AI RAM budget",
            "vram_status": "VRAM status",
            "compute": "CPU/GPU selection",
            "tier": "Selected tier",
            "state": "State",
            "reason": "Reason",
            "worker": "Worker status",
        }
        for key, text in labels.items():
            self._status_labels[key].setText(ui_text(text))
        self.refresh_button.setText(ui_text("Refresh resource status"))
        self.cam_continues_label.setText(ui_text("Traditional CAM continues normally"))
        self._show_status(probe=False)


__all__ = ["AiAssistSettingsPage"]
