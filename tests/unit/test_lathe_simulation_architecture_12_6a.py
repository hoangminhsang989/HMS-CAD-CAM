"""Static purity, catalog, schema and resource contracts for Stage 12.6A."""

from __future__ import annotations

import ast
import json
from pathlib import Path

from hms_cadcam.project.constants import DATABASE_SCHEMA_VERSION, PROJECT_FORMAT_VERSION


ROOT = Path(__file__).resolve().parents[2]
DOMAIN = ROOT / "src" / "hms_cadcam" / "cam" / "lathe" / "simulation"


def test_domain_has_zero_pyside_imports_and_no_executable_deserialization() -> None:
    forbidden = {"PySide6", "pickle", "marshal", "subprocess"}
    for path in DOMAIN.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in (node.names if isinstance(node, ast.Import) else [ast.alias(node.module or "")])
        }
        assert not imports.intersection(forbidden), path
        assert "eval(" not in source
        assert "exec(" not in source


def test_vi_en_ko_catalog_coverage_is_complete_and_nonempty() -> None:
    catalogs = []
    for locale in ("vi_VN", "en_US", "ko_KR"):
        payload = json.loads((ROOT / "src" / "hms_cadcam" / "ui" / "catalogs" / f"{locale}.json").read_text(encoding="utf-8"))
        subset = {key: value for key, value in payload.items() if key.startswith("lathe.simulation.")}
        assert subset and all(isinstance(value, str) and value.strip() for value in subset.values())
        catalogs.append(set(subset))
    assert catalogs[0] == catalogs[1] == catalogs[2]


def test_stage12_6a_keeps_project_schema_v5_and_has_no_persistence_module() -> None:
    assert DATABASE_SCHEMA_VERSION == 5
    assert PROJECT_FORMAT_VERSION == 1
    assert not (DOMAIN / "persistence.py").exists()
    assert not (DOMAIN / "codec.py").exists()
