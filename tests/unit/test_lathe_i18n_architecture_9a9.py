"""I18N parity and architectural exclusion gates for the Stage 9A.9 UI."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from string import Formatter
import ast
import json

from hms_cadcam.cam.lathe.parameters import LATHE_PARAMETER_SCHEMAS
from hms_cadcam.cam.lathe.types import (
    LatheDiagnosticCode,
    LatheGeometryKind,
    LatheStrategyFamily,
    LatheStrategyId,
    LatheToolCapability,
)
from hms_cadcam.ui.feature_flags import UiFeatureFlag, UiFeatureFlags
from hms_cadcam.ui.i18n import UiLanguage, build_default_catalogs


ROOT = Path(__file__).resolve().parents[2]
UI_ROOT = ROOT / "src" / "hms_cadcam" / "ui"
LATHE_UI_PATHS = (
    UI_ROOT / "lathe_adapters.py",
    UI_ROOT / "lathe_presenter.py",
    UI_ROOT / "lathe_session.py",
    UI_ROOT / "lathe_workspace.py",
)


def _lathe_keys() -> set[str]:
    keys = {
        "lathe.workspace.title",
        "lathe.workspace.available",
        "lathe.presenter.unavailable",
        "lathe.presenter.context_replaced",
        "lathe.presenter.project_context_unavailable",
        "lathe.strategy.browser.title",
        "lathe.strategy.apply",
        "lathe.operation.list.title",
        "lathe.operation.create",
        "lathe.operation.delete",
        "lathe.operation.delete.confirm",
        "lathe.operation.delete.confirm.help",
        "lathe.operation.enabled",
        "lathe.operation.validate",
        "lathe.operation.item",
        "lathe.parameters.title",
        "lathe.parameters.basic",
        "lathe.parameters.advanced",
        "lathe.parameters.advanced.toggle",
        "lathe.parameters.advanced.help",
        "lathe.parameters.apply",
        "lathe.parameters.no_changes",
        "lathe.parameter.optional.enable",
        "lathe.tool.title",
        "lathe.tool.selector",
        "lathe.tool.required_capability",
        "lathe.tool.bind",
        "lathe.tool.clear",
        "lathe.tool.compatible",
        "lathe.tool.incompatible",
        "lathe.tool.incompatible_or_unavailable",
        "lathe.tool.compatibility.help",
        "lathe.tool.empty",
        "lathe.tool.no_active_operation",
        "lathe.geometry.title",
        "lathe.geometry.selector",
        "lathe.geometry.bind",
        "lathe.geometry.clear",
        "lathe.geometry.current_selection.help",
        "lathe.geometry.current_selection.ready",
        "lathe.geometry.no_active_operation",
        "lathe.geometry.not_bound",
        "lathe.geometry.bound_summary",
        "lathe.geometry.selection_empty",
        "lathe.geometry.selection_duplicate",
        "lathe.geometry.selection_stale",
        "lathe.geometry.selection_kind_unavailable",
        "lathe.geometry.selection_mixed",
        "lathe.geometry.selection_incompatible",
        "lathe.geometry.selection_unavailable",
        "lathe.diagnostics.title",
        "lathe.diagnostics.no_active",
        "lathe.diagnostics.none",
        "lathe.readiness.title",
        "lathe.readiness.unavailable",
        "lathe.readiness.no_operation",
        "lathe.readiness.invalid",
        "lathe.readiness.incomplete",
        "lathe.readiness.ready",
        "lathe.readiness.not_calculated",
        "lathe.read_only",
        "lathe.command.accepted",
        "lathe.command.rejected",
        *(f"lathe.strategy.{item.name.casefold()}.label" for item in LatheStrategyId),
        *(f"lathe.family.{item.name.casefold()}.label" for item in LatheStrategyFamily),
        *(f"lathe.capability.{item.value.casefold()}.label" for item in LatheToolCapability),
        *(f"lathe.geometry.{item.name.casefold()}.label" for item in LatheGeometryKind),
        *(f"lathe.diagnostic.{item.value}" for item in LatheDiagnosticCode),
        *(f"lathe.enum.{item}" for item in ("cw", "ccw", "right", "left")),
        *(f"lathe.unit.{item}" for item in ("mm", "rpm", "mm/rev", "degree", "second")),
    }
    for schema in LATHE_PARAMETER_SCHEMAS:
        for descriptor in schema.descriptors:
            keys.add(descriptor.label_key)
            keys.add(descriptor.help_key)
    return keys


def _fields(value: str) -> Counter[str]:
    return Counter(
        field_name
        for _literal, field_name, _format_spec, _conversion in Formatter().parse(value)
        if field_name is not None
    )


def test_vi_en_ko_catalogs_have_exact_lathe_key_parity_and_placeholders() -> None:
    catalogs = build_default_catalogs()
    required = _lathe_keys()
    reference = catalogs[UiLanguage.EN_US].entries
    for language in UiLanguage:
        entries = catalogs[language].entries
        assert required.issubset(entries)
        assert all(entries[key].strip() for key in required)
        assert all(_fields(entries[key]) == _fields(reference[key]) for key in required)
    assert len({catalogs[item].entries["lathe.workspace.title"] for item in UiLanguage}) == 3


def test_catalog_json_has_no_duplicate_key_or_replacement_character() -> None:
    for name in ("vi_VN.json", "en_US.json", "ko_KR.json"):
        path = UI_ROOT / "catalogs" / name
        pairs = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=list)
        keys = [key for key, _value in pairs]
        assert len(keys) == len(set(keys))
        assert all("\ufffd" not in value and "\u25a1" not in value for _key, value in pairs)


def test_feature_flag_is_typed_fail_closed_and_does_not_enable_prior_features() -> None:
    flag = UiFeatureFlag.LATHE_9A9
    assert not UiFeatureFlags.for_development_and_tests().is_enabled(flag)
    assert not UiFeatureFlags.for_review_harness().is_enabled(flag)
    assert not UiFeatureFlags.for_production().is_enabled(flag)
    enabled = UiFeatureFlags({flag: True})
    assert enabled.is_enabled(flag)
    assert not enabled.is_enabled(UiFeatureFlag.CAM_3D_9A8)
    assert not enabled.is_enabled(UiFeatureFlag.POST_ASSEMBLY_9A7)


def test_foundation_modules_remain_qt_free_and_ui_imports_only_presenter_boundary() -> None:
    foundation = ROOT / "src" / "hms_cadcam" / "cam" / "lathe"
    for path in foundation.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "PySide6" not in source
        ast.parse(source, filename=str(path))
    for path in LATHE_UI_PATHS:
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_lathe_ui_has_no_persistence_toolpath_post_simulation_worker_or_ocp_import() -> None:
    forbidden_imports = (
        "sqlite",
        "persistence",
        "toolpath",
        "hms_cadcam.cam.post",
        "hms_cadcam.cam.simulation",
        "hms_cadcam.cam.adapters",
        "OCP.",
    )
    forbidden_runtime = (
        "QThread",
        "multiprocessing",
        "subprocess",
        "project.db",
        "calculate_toolpath",
        "generate_gcode",
    )
    for path in LATHE_UI_PATHS:
        source = path.read_text(encoding="utf-8")
        imports = "\n".join(
            line for line in source.splitlines() if line.startswith(("import ", "from "))
        )
        assert not any(token in imports for token in forbidden_imports)
        assert not any(token in source for token in forbidden_runtime)


def test_visible_catalog_text_does_not_claim_calculation_capability() -> None:
    catalogs = build_default_catalogs()
    for language in UiLanguage:
        values = " ".join(
            value
            for key, value in catalogs[language].entries.items()
            if key.startswith("lathe.")
        ).casefold()
        assert "toolpath generated" not in values
        assert "g-code available" not in values
        assert "simulation ready" not in values
