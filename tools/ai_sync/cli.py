"""Command-line interface for the four AI Sync Engine V1.1 commands."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence, TextIO

from .engine import CLI_ERROR, COMMANDS, EngineDependencies, execute


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValueError(message)


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="update_ai_sync.py", description="HMS AI Sync Engine V1.1")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in COMMANDS:
        child = subparsers.add_parser(command, help=f"{command} AI Sync state")
        child.add_argument("--repo", required=True, type=Path)
        child.add_argument("--config", default=Path(".ai/config.json"), type=Path)
        child.add_argument("--format", choices=("human", "json"), default="human")
        child.add_argument("--expected-head")
        child.add_argument("--verbose", action="store_true")
        if command != "inspect":
            child.add_argument("--stage")
            child.add_argument("--task")
            child.add_argument("--metadata", type=Path)
            child.add_argument("--expected-metadata-sha256")
    return parser


def _human(payload: dict[str, object]) -> str:
    if not payload.get("ok"):
        error = payload.get("error", {})
        if isinstance(error, dict):
            return f"ERROR {error.get('code', 'UNKNOWN')}: {error.get('message', 'operation failed')}"
        return "ERROR: operation failed"
    lines = [
        f"Command: {payload.get('command')}",
        f"Engine: {payload.get('engine_version')}",
        f"Run ID: {payload.get('run_id')}",
        f"Writes performed: {str(payload.get('writes_performed', False)).lower()}",
    ]
    git = payload.get("git")
    if isinstance(git, dict):
        lines.extend((f"Branch: {git.get('branch')}", f"HEAD: {git.get('head_oid')}", f"Dirty: {git.get('dirty')}"))
    return "\n".join(lines)


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
    dependencies: EngineDependencies | None = None,
) -> int:
    parser = build_parser()
    try:
        arguments = parser.parse_args(argv)
    except ValueError as error:
        stderr.write(f"CLI_ERROR: {error}\n")
        return CLI_ERROR
    sink = (lambda line: stderr.write(line + "\n")) if arguments.verbose else None
    deps = dependencies or EngineDependencies(log_sink=sink)
    if dependencies is not None and arguments.verbose and dependencies.log_sink is None:
        deps = EngineDependencies(
            clock=dependencies.clock, run_id=dependencies.run_id,
            log_sink=sink, publisher=dependencies.publisher, recovery=dependencies.recovery,
        )
    result = execute(
        arguments.command, arguments.repo, config_path=arguments.config,
        metadata_path=getattr(arguments, "metadata", None),
        stage=getattr(arguments, "stage", None), task=getattr(arguments, "task", None),
        expected_metadata_sha256=getattr(arguments, "expected_metadata_sha256", None),
        expected_head=arguments.expected_head, dependencies=deps,
    )
    if arguments.format == "json":
        stdout.write(json.dumps(result.payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
    else:
        stream = stdout if result.exit_code == 0 else stderr
        stream.write(_human(result.payload) + "\n")
    return result.exit_code
