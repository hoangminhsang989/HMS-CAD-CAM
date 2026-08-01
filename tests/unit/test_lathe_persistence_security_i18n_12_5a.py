"""Stage 12.5A restore atomicity, pure-layer, security, and I18N parity."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from hms_cadcam.cam.lathe.types import LatheStrategyId
from hms_cadcam.ui.feature_flags import UiFeatureFlag, UiFeatureFlags
from tests.unit._lathe_fixtures import complete_operation, service_for, stable_uuid


_DIAGNOSTIC_KEYS = {
    "lathe.persistence.derived_corrupt",
    "lathe.persistence.derived_stale",
    "lathe.persistence.derived_ownership_mismatch",
    "lathe.persistence.derived_version_mismatch",
    "lathe.persistence.authoring_incompatible",
}


def test_public_restore_validates_complete_tuple_before_one_atomic_hydration() -> None:
    source, reference = service_for(LatheStrategyId.FACE)
    operation = complete_operation(source, reference)
    target, _ = service_for(
        LatheStrategyId.FACE,
        live_session=source.session,
        reference=reference,
    )
    assert target.restore_operations((operation,)) == (operation,)
    assert target.list_operations() == (operation,)
    with pytest.raises(ValueError, match="already"):
        target.restore_operations((operation,))

    fresh, _ = service_for(
        LatheStrategyId.FACE,
        live_session=source.session,
        reference=reference,
    )
    stale = replace(
        operation,
        ownership=replace(operation.ownership, project_id=stable_uuid("foreign")),
    )
    with pytest.raises(ValueError, match="stale"):
        fresh.restore_operations((stale,))
    assert fresh.list_operations() == ()


def test_feature_flag_is_explicit_fail_closed_and_depends_on_lathe_ui() -> None:
    disabled_parent = UiFeatureFlags(
        {UiFeatureFlag.LATHE_PERSISTENCE_12_5A: True}
    )
    assert not disabled_parent.is_enabled(UiFeatureFlag.LATHE_PERSISTENCE_12_5A)
    enabled = UiFeatureFlags(
        {
            UiFeatureFlag.LATHE_9A9: True,
            UiFeatureFlag.LATHE_PERSISTENCE_12_5A: True,
        }
    )
    assert enabled.is_enabled(UiFeatureFlag.LATHE_PERSISTENCE_12_5A)
    assert not UiFeatureFlags.for_production().is_enabled(
        UiFeatureFlag.LATHE_PERSISTENCE_12_5A
    )


def test_persistence_layer_is_qt_free_and_has_no_executable_deserialization() -> None:
    root = Path("src/hms_cadcam/cam/lathe/persistence")
    sources = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(root.glob("*.py"))
    )
    assert "PySide6" not in sources
    assert "pickle" not in sources
    assert "marshal" not in sources
    assert "eval(" not in sources
    assert "exec(" not in sources
    assert "subprocess" not in sources
    assert "simulation" not in sources.casefold()
    assert "machine_output_ready" not in sources.casefold()
    assert ".NC" not in sources


def test_vi_en_ko_diagnostic_catalogs_have_exact_parity_and_placeholders() -> None:
    root = Path("src/hms_cadcam/ui/catalogs")
    catalogs = {
        locale: json.loads((root / f"{locale}.json").read_text(encoding="utf-8"))
        for locale in ("vi_VN", "en_US", "ko_KR")
    }
    for catalog in catalogs.values():
        assert _DIAGNOSTIC_KEYS.issubset(catalog)
        assert all("{subject}" in catalog[key] for key in _DIAGNOSTIC_KEYS)
    assert {
        key for key in catalogs["vi_VN"] if key.startswith("lathe.persistence.")
    } == {
        key for key in catalogs["en_US"] if key.startswith("lathe.persistence.")
    } == {
        key for key in catalogs["ko_KR"] if key.startswith("lathe.persistence.")
    }
