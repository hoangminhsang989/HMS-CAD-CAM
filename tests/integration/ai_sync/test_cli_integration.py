"""WP6 executable CLI integration tests."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys

from tests.unit.ai_sync.test_engine import make_engine_repo


def test_executable_inspect_json_is_read_only(tmp_path: Path) -> None:
    root = make_engine_repo(tmp_path)
    script = Path(__file__).parents[3] / "tools/update_ai_sync.py"
    result = subprocess.run(
        [sys.executable, str(script), "inspect", "--repo", str(root), "--format", "json"],
        cwd=Path(__file__).parents[3], check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", shell=False,
    )
    payload = json.loads(result.stdout)
    assert result.returncode == 0 and payload["writes_performed"] is False and result.stderr == ""

def test_executable_dry_commands_do_not_create_bytecode(tmp_path: Path) -> None:
    root = make_engine_repo(tmp_path)
    source_root = Path(__file__).parents[3]
    runner = tmp_path / "runner" / "tools"
    shutil.copytree(source_root / "tools/ai_sync", runner / "ai_sync", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copyfile(source_root / "tools/update_ai_sync.py", runner / "update_ai_sync.py")
    for command in ("inspect", "validate", "show-plan"):
        result = subprocess.run(
            [sys.executable, str(runner / "update_ai_sync.py"), command, "--repo", str(root), "--format", "json"],
            cwd=runner.parent, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", shell=False,
        )
        assert result.returncode == 0 and json.loads(result.stdout)["writes_performed"] is False
    assert not list(runner.rglob("__pycache__")) and not list(runner.rglob("*.pyc"))


def test_executable_sync_existing_lock_exits_nine_without_breaking_lock(tmp_path: Path) -> None:
    root = make_engine_repo(tmp_path)
    lock = root / ".ai/.sync-tmp/LOCK"
    lock.parent.mkdir(parents=True)
    lock.write_bytes(b"operator-owned-lock\n")
    script = Path(__file__).parents[3] / "tools/update_ai_sync.py"
    result = subprocess.run(
        [sys.executable, str(script), "sync", "--repo", str(root), "--format", "json"],
        cwd=Path(__file__).parents[3], check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", shell=False,
    )
    payload = json.loads(result.stdout)
    assert result.returncode == 9
    assert payload["error"]["code"] == "PUBLICATION_LOCKED"
    assert payload["writes_performed"] is False
    assert lock.read_bytes() == b"operator-owned-lock\n"
    assert result.stderr == ""
