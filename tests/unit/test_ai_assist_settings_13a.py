"""Application-settings, feature-capability, UI, and import-isolation tests."""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QCheckBox

from hms_cadcam.ai_assist.controller import AiAssistController
from hms_cadcam.ai_assist.resources import ResourceSnapshot
from hms_cadcam.ai_assist.settings import AiAssistSettings, AiAssistSettingsService
from hms_cadcam.ui.feature_flags import UiFeatureFlag, UiFeatureFlags
from hms_cadcam.ui.settings import GeneralSettingsDialog, UiScaleManager


class RecordingProvider:
    def __init__(self) -> None:
        self.calls = 0

    def sample(self, sampled_at_monotonic_ns: int | None = None) -> ResourceSnapshot:
        self.calls += 1
        raise AssertionError("AI OFF must not sample resources")


def test_default_qsettings_value_is_off_and_does_not_probe_or_create_runtime(tmp_path: Path) -> None:
    settings = QSettings(str(tmp_path / "ai.ini"), QSettings.Format.IniFormat)
    provider = RecordingProvider()
    controller = AiAssistController(AiAssistSettingsService(settings), provider, capability_enabled=True)

    assert not controller.settings.enabled
    assert controller.status.state.value == "OFF"
    controller.refresh_resource_status()
    assert provider.calls == 0


def test_capability_is_production_enabled_but_explicit_false_remains_fail_closed() -> None:
    assert UiFeatureFlags.for_production().is_enabled(UiFeatureFlag.OFFLINE_CAM_AI_ASSIST_13A)
    assert not UiFeatureFlags({UiFeatureFlag.OFFLINE_CAM_AI_ASSIST_13A: False}).is_enabled(
        UiFeatureFlag.OFFLINE_CAM_AI_ASSIST_13A
    )


def test_settings_page_is_integrated_only_when_capability_controller_exists(qtbot, tmp_path: Path) -> None:
    settings = QSettings(str(tmp_path / "ui.ini"), QSettings.Format.IniFormat)
    controller = AiAssistController(AiAssistSettingsService(settings), RecordingProvider(), capability_enabled=True)
    scale = UiScaleManager(settings)
    dialog = GeneralSettingsDialog(scale, ai_assist_controller=controller)
    qtbot.addWidget(dialog)

    assert "AI and Automation" in [
        dialog.category_list.item(index).data(Qt.ItemDataRole.UserRole)
        for index in range(dialog.category_list.count())
    ]
    assert dialog.findChild(QCheckBox, "AiAssistMasterToggle") is not None

    hidden = GeneralSettingsDialog(scale)
    qtbot.addWidget(hidden)
    assert "AI and Automation" not in [
        hidden.category_list.item(index).data(Qt.ItemDataRole.UserRole)
        for index in range(hidden.category_list.count())
    ]


def test_ai_settings_catalog_keys_have_vi_en_ko_parity() -> None:
    root = Path(__file__).parents[2] / "src" / "hms_cadcam" / "ui" / "catalogs"
    catalogs = {
        path.stem: json.loads(path.read_text(encoding="utf-8"))
        for path in (root / "vi_VN.json", root / "en_US.json", root / "ko_KR.json")
    }
    required = {
        "AI and Automation",
        "AI CAM assist",
        "ai_assist.state.WAITING_FOR_RESOURCES",
        "Traditional CAM continues normally",
        "No worker or model loaded",
    }
    for key in required:
        assert all(catalog[key].strip() for catalog in catalogs.values())


def test_settings_round_trip_uses_application_settings_only(tmp_path: Path) -> None:
    settings = QSettings(str(tmp_path / "persist.ini"), QSettings.Format.IniFormat)
    service = AiAssistSettingsService(settings)
    expected = AiAssistSettings(enabled=True)
    assert service.save(expected)
    assert service.load() == expected
