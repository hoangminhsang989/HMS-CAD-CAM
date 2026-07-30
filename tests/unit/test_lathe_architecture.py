"""Static, import, resource, and exclusion gates for Lathe Foundation V1."""

from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import subprocess
import sys

from hms_cadcam.cam.domain.revision import Revision
from hms_cadcam.cam.lathe.commands import (
    DeleteLatheOperation,
    SetLatheOperationEnabled,
)
from hms_cadcam.cam.lathe.types import LatheStrategyId
from tests.unit._lathe_fixtures import create_operation, operation_id, service_for, setup_id, stable_uuid


ROOT = Path(__file__).resolve().parents[2]
LATHE_ROOT = ROOT / "src" / "hms_cadcam" / "cam" / "lathe"
PURE_MODULES = tuple(sorted(LATHE_ROOT.glob("*.py")))


def _imports(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(item.name for item in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.append(node.module)
    return tuple(modules)


def _run_probe(code: str) -> str:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.stderr == ""
    return completed.stdout.strip()


def test_pure_lathe_modules_have_no_qt_ocp_persistence_post_or_toolpath_imports() -> None:
    assert {path.name for path in PURE_MODULES} == {
        "__init__.py",
        "application.py",
        "capabilities.py",
        "commands.py",
        "domain.py",
        "parameters.py",
        "presenter.py",
        "readiness.py",
        "strategies.py",
        "types.py",
    }
    forbidden = (
        "PySide6",
        "OCP",
        "sqlite3",
        "hms_cadcam.cam.post",
        "hms_cadcam.cam.simulation",
        "hms_cadcam.cam.toolpath",
        "hms_cadcam.ui",
        "hms_cadcam.project.database",
    )
    for path in PURE_MODULES:
        imports = _imports(path)
        assert not any(
            item == prefix or item.startswith(f"{prefix}.")
            for item in imports
            for prefix in forbidden
        ), (path, imports)


def test_pure_modules_define_no_ui_worker_lock_callback_or_file_write_boundary() -> None:
    forbidden_names = {
        "QObject",
        "QWidget",
        "QThread",
        "Thread",
        "Process",
        "Lock",
        "RLock",
    }
    forbidden_calls = {"open", "write_text", "write_bytes", "dump", "dumps"}
    for path in PURE_MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        assert names.isdisjoint(forbidden_names), path
        calls = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        attributes = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        assert (calls | attributes).isdisjoint(forbidden_calls), path
        assert "callback" not in path.read_text(encoding="utf-8").casefold()


def test_direct_import_is_cycle_free_and_loads_zero_pyside_modules() -> None:
    output = _run_probe(
        "import importlib,sys; "
        "mods=['types','parameters','strategies','capabilities','domain','commands',"
        "'application','readiness','presenter']; "
        "[importlib.import_module('hms_cadcam.cam.lathe.'+m) for m in mods]; "
        "print(sum(1 for n in sys.modules if n.startswith('PySide6')))"
    )
    assert output == "0"


def test_registry_schema_and_defaults_are_deterministic_across_processes() -> None:
    code = (
        "import json; from hms_cadcam.cam.lathe import *; "
        "print(json.dumps({"
        "'strategies':[x.strategy_id.value for x in LATHE_STRATEGY_REGISTRY],"
        "'schemas':[[d.parameter_id for d in s.descriptors] for s in LATHE_PARAMETER_SCHEMAS],"
        "'defaults':[[[k,(v.value if hasattr(v,'value') else v)] for k,v in build_lathe_v1_defaults(s).values] for s in LatheStrategyId]"
        "},sort_keys=True,separators=(',',':')))"
    )
    first = _run_probe(code)
    second = _run_probe(code)
    assert first == second
    decoded = json.loads(first)
    assert len(decoded["strategies"]) == 11
    assert len(decoded["schemas"]) == 11
    assert len(decoded["defaults"]) == 11


def test_one_hundred_create_update_delete_cycles_are_deterministic() -> None:
    service, _reference = service_for()
    for index in range(1, 101):
        state = create_operation(service, index=index)
        changed = service.execute(
            SetLatheOperationEnabled(
                state.ownership, index % 2 == 0, state.revision
            )
        )
        assert changed.accepted and changed.operation is not None
        deleted = service.execute(
            DeleteLatheOperation(
                changed.operation.ownership, changed.operation.revision
            )
        )
        assert deleted.accepted and deleted.deleted
    assert service.list_operations() == ()


def test_fifty_session_and_lifecycle_transition_cycles_leak_no_state() -> None:
    for index in range(1, 51):
        service, _reference = service_for()
        state = create_operation(service)
        service.switch_setup(setup_id(index + 1))
        service.switch_source(stable_uuid(f"source-cycle/{index}"), index)
        service.increment_generation()
        assert service.close().closed
        assert service.close().closed
        assert service.query(operation_id()) == state
        reopened, _reference = service_for()
        assert reopened.list_operations() == ()


def test_lathe_ui_exists_only_in_authorized_stage9a9_modules() -> None:
    ui_root = ROOT / "src" / "hms_cadcam" / "ui"
    lathe_ui_paths = tuple(sorted(ui_root.glob("lathe_*.py")))
    assert {path.name for path in lathe_ui_paths} == {
        "lathe_adapters.py",
        "lathe_presenter.py",
        "lathe_session.py",
        "lathe_workspace.py",
    }
    forbidden_imports = (
        "sqlite3",
        "hms_cadcam.cam.post",
        "hms_cadcam.cam.simulation",
        "hms_cadcam.cam.toolpath",
        "hms_cadcam.project.database",
    )
    forbidden_runtime_names = {"QThread", "Thread", "Process"}
    for path in lathe_ui_paths:
        imports = _imports(path)
        assert not any(
            item == prefix or item.startswith(f"{prefix}.")
            for item in imports
            for prefix in forbidden_imports
        ), (path, imports)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        assert names.isdisjoint(forbidden_runtime_names), path

    assert not (LATHE_ROOT / "toolpath.py").exists()
    assert not (LATHE_ROOT / "post.py").exists()
    assert not (LATHE_ROOT / "simulation.py").exists()
