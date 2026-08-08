"""Stage15A WP2 strict QSettings persistence certification."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings

from hms_cadcam.cad.export_models import (
    ExportFormatId,
    ExportOverwritePolicy,
    ExportProfile,
    StlEncoding,
    StlMeshOptions,
)
from hms_cadcam.ui.settings.export_defaults import (
    PERSISTED_EXPORT_FORMATS,
    ExportDefaultsSettingsService,
    export_default_key,
    factory_export_profiles,
)


def _settings(path: Path) -> QSettings:
    return QSettings(str(path), QSettings.Format.IniFormat)


def _non_default_profiles() -> dict[ExportFormatId, ExportProfile]:
    profiles = factory_export_profiles()
    profiles[ExportFormatId.STEP] = ExportProfile(
        ExportFormatId.STEP,
        standard="AP203",
    )
    profiles[ExportFormatId.BREP] = ExportProfile(
        ExportFormatId.BREP,
        standard="2",
    )
    mesh = StlMeshOptions(0.037, 0.23, True)
    profiles[ExportFormatId.STL] = ExportProfile(
        ExportFormatId.STL,
        tolerance=mesh.linear_deflection,
        stl_encoding=StlEncoding.ASCII,
        mesh_options=mesh,
    )
    return profiles


def test_absent_values_load_exact_factory_defaults_without_writing(tmp_path: Path) -> None:
    settings = _settings(tmp_path / "defaults.ini")
    snapshot = ExportDefaultsSettingsService(settings).load()
    assert snapshot.profiles == factory_export_profiles()
    assert snapshot.issues == ()
    assert settings.allKeys() == []
    assert tuple(snapshot.profiles) == PERSISTED_EXPORT_FORMATS


def test_explicit_apply_is_restart_safe_and_exactly_typed(tmp_path: Path) -> None:
    path = tmp_path / "restart.ini"
    instance_a = ExportDefaultsSettingsService(_settings(path))
    expected = _non_default_profiles()
    expected[ExportFormatId.STEP] = ExportProfile(
        ExportFormatId.STEP,
        standard="AP242",
    )
    expected[ExportFormatId.BREP] = ExportProfile(
        ExportFormatId.BREP,
        standard="3",
    )
    instance_a.apply(expected)
    del instance_a

    instance_b = ExportDefaultsSettingsService(_settings(path))
    restored = instance_b.load()
    assert restored.issues == ()
    assert restored.profiles == expected
    assert restored.profiles[ExportFormatId.STEP].standard == "AP242"
    assert restored.profiles[ExportFormatId.BREP].standard == "3"
    stl = restored.profiles[ExportFormatId.STL]
    assert stl.stl_encoding is StlEncoding.ASCII
    assert stl.mesh_options == StlMeshOptions(0.037, 0.23, True)


def _payload(format_id: ExportFormatId) -> dict[str, object]:
    return ExportProfile.default_for(format_id).to_dict()


CORRUPT_VALUES: tuple[tuple[ExportFormatId, str], ...] = (
    (ExportFormatId.STEP, "{"),
    (ExportFormatId.STEP, "[]"),
    (
        ExportFormatId.STEP,
        json.dumps({**_payload(ExportFormatId.STEP), "format_version": 2}),
    ),
    (ExportFormatId.STEP, ExportProfile.default_for(ExportFormatId.BREP).to_json()),
    (
        ExportFormatId.STEP,
        json.dumps({**_payload(ExportFormatId.STEP), "unit_policy": "inch"}),
    ),
    (
        ExportFormatId.STL,
        json.dumps(
            {
                **_payload(ExportFormatId.STL),
                "mesh_options": {
                    "linear_deflection": True,
                    "angular_deflection": 0.5,
                    "relative": False,
                },
            }
        ),
    ),
    (
        ExportFormatId.STL,
        json.dumps(
            {
                **_payload(ExportFormatId.STL),
                "mesh_options": {
                    "linear_deflection": float("nan"),
                    "angular_deflection": 0.5,
                    "relative": False,
                },
            }
        ),
    ),
    (
        ExportFormatId.STL,
        json.dumps(
            {
                **_payload(ExportFormatId.STL),
                "mesh_options": {
                    "linear_deflection": 0.1,
                    "angular_deflection": float("inf"),
                    "relative": False,
                },
            }
        ),
    ),
    (
        ExportFormatId.STEP,
        json.dumps(
            {
                key: value
                for key, value in _payload(ExportFormatId.STEP).items()
                if key != "standard"
            }
        ),
    ),
    (
        ExportFormatId.STEP,
        json.dumps({**_payload(ExportFormatId.STEP), "unknown": "forbidden"}),
    ),
)


@pytest.mark.parametrize(("format_id", "raw"), CORRUPT_VALUES)
def test_corrupt_value_is_reported_falls_back_and_is_not_self_healed(
    tmp_path: Path,
    format_id: ExportFormatId,
    raw: str,
) -> None:
    settings = _settings(tmp_path / f"corrupt-{format_id.value}.ini")
    key = export_default_key(format_id)
    settings.setValue(key, raw)
    settings.sync()

    snapshot = ExportDefaultsSettingsService(settings).load()
    assert snapshot.profiles[format_id] == factory_export_profiles()[format_id]
    assert len(snapshot.issues) == 1
    assert snapshot.issues[0].format_id is format_id
    assert settings.value(key) == raw


def test_valid_but_unsafe_persistent_overwrite_policy_fails_closed(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path / "overwrite.ini")
    unsafe = ExportProfile.default_for(
        ExportFormatId.STEP,
        overwrite_policy=ExportOverwritePolicy.REPLACE_EXISTING,
    )
    key = export_default_key(ExportFormatId.STEP)
    settings.setValue(key, unsafe.to_json())
    settings.sync()
    snapshot = ExportDefaultsSettingsService(settings).load()
    assert snapshot.profiles[ExportFormatId.STEP].overwrite_policy is (
        ExportOverwritePolicy.FAIL_IF_EXISTS
    )
    assert snapshot.issues[0].format_id is ExportFormatId.STEP


def test_apply_rejects_missing_fake_or_mismatched_profiles(tmp_path: Path) -> None:
    service = ExportDefaultsSettingsService(_settings(tmp_path / "invalid.ini"))
    profiles = factory_export_profiles()
    profiles.pop(ExportFormatId.IGES)
    with pytest.raises(ValueError):
        service.apply(profiles)

    profiles = factory_export_profiles()
    profiles[ExportFormatId.STEP] = ExportProfile.default_for(ExportFormatId.BREP)
    with pytest.raises(ValueError):
        service.apply(profiles)
