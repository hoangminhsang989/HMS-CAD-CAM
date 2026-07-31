"""Stage 12.3 architecture, scope, API and I18N contract tests."""

from __future__ import annotations

import ast
import json
from pathlib import Path

from hms_cadcam.cam.lathe.toolpath import (
    EXECUTABLE_LATHE_TOOLPATH_STRATEGIES,
    LATHE_ID_THREAD_ALGORITHM_VERSION,
    LATHE_OD_THREAD_ALGORITHM_VERSION,
    LATHE_THREAD_TOOLPATH_PREVIEW_CAPABILITY,
    UNSUPPORTED_LATHE_TOOLPATH_STRATEGIES,
    strategy_algorithm_version,
)
from hms_cadcam.cam.lathe.types import LatheStrategyId
from hms_cadcam.ui.feature_flags import UiFeatureFlag


ROOT = Path(__file__).resolve().parents[2]
TOOLPATH = ROOT / "src" / "hms_cadcam" / "cam" / "lathe" / "toolpath"
UI = ROOT / "src" / "hms_cadcam" / "ui"
SPEC = (
    ROOT
    / "docs"
    / "architecture"
    / "STAGE_12_3_LATHE_THREAD_TOOLPATH_PREVIEW_V3.md"
)
THREAD_DIAGNOSTICS = (
    "phase_neutral_synchronized_centerline_preview",
    "thread_feed_derived_from_pitch",
    "nominal_infeed_angle_metadata_only",
    "not_machine_ready",
    "missing_internal_bore",
    "invalid_thread_diameter_order",
    "thread_major_exceeds_stock",
    "thread_minor_below_bore",
    "invalid_pitch",
    "invalid_pass_count",
    "invalid_spring_passes",
    "invalid_infeed_angle",
    "thread_range_outside_stock",
    "incompatible_thread_tool",
    "incompatible_thread_geometry",
)


def _imports(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    values: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            values.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            values.append(node.module)
    return tuple(values)


def test_authoritative_spec_covers_exact_scope_and_exclusions() -> None:
    text = SPEC.read_text(encoding="utf-8")
    required = (
        "PHASE_NEUTRAL_SYNCHRONIZED_CENTERLINE_PREVIEW",
        "lathe.od_thread.toolpath.v3",
        "lathe.id_thread.toolpath.v3",
        "lathe.thread.toolpath.preview.v3",
        "total_radial_depth_mm",
        "spring_pass_index",
        "THREAD_FEED_DERIVED_FROM_PITCH",
        "NOMINAL_INFEED_ANGLE_METADATA_ONLY",
        "NOT_STARTED",
    )
    assert all(token in text for token in required)
    assert "G32/G33/G76" in text
    assert "machine-ready claims are excluded" in text


def test_pure_v3_layers_have_no_qt_ocp_post_simulation_or_persistence_imports() -> None:
    forbidden = (
        "PySide6",
        "shiboken6",
        "OCP",
        "hms_cadcam.viewer",
        "hms_cadcam.ui",
        "hms_cadcam.cam.post",
        "hms_cadcam.cam.simulation",
        "hms_cadcam.project",
        "sqlite",
        "pickle",
        "multiprocessing",
    )
    for path in TOOLPATH.glob("*.py"):
        assert not any(
            value == token or value.startswith(token + ".")
            for value in _imports(path)
            for token in forbidden
        )


def test_registry_versions_and_existing_feature_topology_are_exact() -> None:
    assert EXECUTABLE_LATHE_TOOLPATH_STRATEGIES == tuple(LatheStrategyId)
    assert UNSUPPORTED_LATHE_TOOLPATH_STRATEGIES == ()
    assert strategy_algorithm_version(LatheStrategyId.OD_THREAD) == (
        LATHE_OD_THREAD_ALGORITHM_VERSION
    )
    assert strategy_algorithm_version(LatheStrategyId.ID_THREAD) == (
        LATHE_ID_THREAD_ALGORITHM_VERSION
    )
    assert (
        LATHE_THREAD_TOOLPATH_PREVIEW_CAPABILITY
        == "lathe.thread.toolpath.preview.v3"
    )
    assert UiFeatureFlag.LATHE_TOOLPATH_12_1.value == "lathe_toolpath_12_1"
    feature_source = (UI / "feature_flags.py").read_text(encoding="utf-8")
    assert "LATHE_TOOLPATH_12_3" not in feature_source
    main_source = (UI / "main_window.py").read_text(encoding="utf-8")
    assert main_source.count("LatheViewportPreviewSink(self.viewport)") == 1


def test_vi_en_ko_catalogs_have_exact_new_thread_keys_and_legacy_values() -> None:
    catalogs = {
        name: json.loads((UI / "catalogs" / name).read_text(encoding="utf-8"))
        for name in ("vi_VN.json", "en_US.json", "ko_KR.json")
    }
    keys = tuple(catalogs["vi_VN.json"])
    assert all(tuple(catalog) == keys for catalog in catalogs.values())
    for code in THREAD_DIAGNOSTICS:
        key = f"lathe.diagnostic.{code}"
        values = tuple(catalog[key] for catalog in catalogs.values())
        assert all(value.strip() and value != key for value in values)
    assert catalogs["en_US.json"][
        "lathe.toolpath.status.stage12_2_thread_unsupported"
    ] == "Thread toolpath preview is not implemented in V2."
    assert catalogs["vi_VN.json"][
        "lathe.toolpath.status.unsupported_strategy"
    ] == "Chiến lược này chưa hỗ trợ xem trước đường chạy dao trong V1."


def test_no_second_request_executor_cache_or_viewport_actor_system() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            TOOLPATH / "request.py",
            TOOLPATH / "runtime.py",
            TOOLPATH / "generators.py",
        )
    )
    assert "class LatheToolpathRequestV2" not in source
    assert "class LatheToolpathRequestV3" not in source
    assert source.count("class LatheToolpathCoordinator") == 1
    assert source.count("class LatheInMemoryToolpathCache") == 1
    viewer_source = (ROOT / "src/hms_cadcam/viewer/lathe.py").read_text(
        encoding="utf-8"
    )
    assert "Helix" not in viewer_source
