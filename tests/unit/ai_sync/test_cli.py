"""WP6 CLI parsing, output, and forbidden-command tests."""

from __future__ import annotations

from io import StringIO
import json
from pathlib import Path

import pytest

from tools.ai_sync.cli import main
from tools.ai_sync.engine import CLI_ERROR, SUCCESS
from tests.unit.ai_sync.test_engine import _write_metadata, deps, make_engine_repo


def test_cli_help_lists_only_four_commands() -> None:
    stdout = StringIO()
    with pytest.raises(SystemExit) as caught:
        main(["--help"], stdout=stdout, stderr=StringIO(), dependencies=deps())
    assert caught.value.code == 0


@pytest.mark.parametrize("command", ("run-tests", "stage", "commit", "push", "fetch", "reset", "watch"))
def test_forbidden_or_unknown_commands_exit_cli_error(command: str) -> None:
    stderr = StringIO()
    assert main([command], stdout=StringIO(), stderr=stderr, dependencies=deps()) == CLI_ERROR
    assert "CLI_ERROR" in stderr.getvalue()


def test_json_stdout_is_one_parseable_document(tmp_path: Path) -> None:
    root = make_engine_repo(tmp_path); stdout = StringIO(); stderr = StringIO()
    code = main(["inspect", "--repo", str(root), "--format", "json"], stdout=stdout, stderr=stderr, dependencies=deps())
    payload = json.loads(stdout.getvalue())
    assert code == SUCCESS and payload["ok"] is True and payload["writes_performed"] is False
    assert stderr.getvalue() == ""


def test_human_success_goes_to_stdout_and_error_to_stderr(tmp_path: Path) -> None:
    root = make_engine_repo(tmp_path); stdout = StringIO(); stderr = StringIO()
    assert main(["validate", "--repo", str(root)], stdout=stdout, stderr=stderr, dependencies=deps()) == SUCCESS
    assert "Writes performed: false" in stdout.getvalue() and stderr.getvalue() == ""
    stdout = StringIO(); stderr = StringIO()
    assert main(["inspect", "--repo", str(root), "--expected-head", "0" * 40], stdout=stdout, stderr=stderr, dependencies=deps()) == 7
    assert stdout.getvalue() == "" and "ERROR" in stderr.getvalue()


def test_metadata_file_conflicts_with_inline_stage(tmp_path: Path) -> None:
    root = make_engine_repo(tmp_path); metadata = root / "metadata.json"; metadata.write_text("{}\n", encoding="utf-8")
    code = main(
        ["sync", "--repo", str(root), "--metadata", "metadata.json", "--stage", "WP6"],
        stdout=StringIO(), stderr=StringIO(), dependencies=deps(),
    )
    assert code == 7


def test_verbose_structured_log_uses_stderr(tmp_path: Path) -> None:
    root = make_engine_repo(tmp_path); stdout = StringIO(); stderr = StringIO()
    assert main(
        ["show-plan", "--repo", str(root), "--format", "json", "--verbose"],
        stdout=stdout, stderr=stderr, dependencies=deps(),
    ) == SUCCESS
    assert json.loads(stdout.getvalue())["ok"] is True
    assert json.loads(stderr.getvalue())["event"] == "engine_prepared"



def test_validate_and_show_plan_accept_external_metadata_but_inspect_rejects_it(tmp_path: Path) -> None:
    root = make_engine_repo(tmp_path)
    external = tmp_path / "authority.json"
    _write_metadata(external)

    for command in ("validate", "show-plan"):
        stdout = StringIO(); stderr = StringIO()
        code = main(
            [command, "--repo", str(root), "--metadata", str(external), "--format", "json"],
            stdout=stdout, stderr=stderr, dependencies=deps(),
        )
        payload = json.loads(stdout.getvalue())
        assert code == SUCCESS and stderr.getvalue() == ""
        assert payload["metadata_mode"] == "external_file"
        assert payload["writes_performed"] is False

    stderr = StringIO()
    code = main(
        ["inspect", "--repo", str(root), "--metadata", str(external)],
        stdout=StringIO(), stderr=stderr, dependencies=deps(),
    )
    assert code == CLI_ERROR and "CLI_ERROR" in stderr.getvalue()
