"""Strict, conservative TEST_RESULTS.json parsing for AI Sync WP3."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path, PureWindowsPath
import re
from typing import Any

from .models import (
    EvidenceSource,
    TestEvidence,
    TestStatus,
    VerificationStatus,
    normalize_relative_posix_path,
    require_non_negative_int,
    validate_utc_datetime,
)


TEST_RESULTS_SCHEMA_VERSION = 1
DURATION_TOLERANCE_SECONDS = 0.01
_SECRET_RE = re.compile(r"(?i)(?:token|password|secret|credential|api[_-]?key)(?:=|:)")


class TestEvidenceError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class TestResultsDocument:
    schema_version: int
    project: str
    generated_at: datetime | None
    runs: tuple[TestEvidence, ...]
    evidence_present: bool


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise TestEvidenceError("TEST_RESULTS_DUPLICATE_KEY", "Evidence contains duplicate JSON key")
        result[key] = value
    return result


def _object(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TestEvidenceError("TEST_RESULTS_TYPE_INVALID", f"{field} must be an object")
    return value


def _keys(value: dict[str, Any], field: str, required: set[str], optional: set[str] = frozenset()) -> None:
    if required - value.keys():
        raise TestEvidenceError("TEST_RESULTS_REQUIRED_FIELD_MISSING", f"{field} is missing a required field")
    if value.keys() - required - optional:
        raise TestEvidenceError("TEST_RESULTS_UNKNOWN_FIELD", f"{field} contains an unsupported field")


def _timestamp(value: object, field: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise TestEvidenceError("TEST_RESULTS_TIME_INVALID", f"{field} must be RFC3339 UTC")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
        return validate_utc_datetime(parsed, field)
    except (TypeError, ValueError) as error:
        raise TestEvidenceError("TEST_RESULTS_TIME_INVALID", f"{field} must be RFC3339 UTC") from error


def _count(value: object, field: str) -> int | None:
    if value is None:
        return None
    try:
        return require_non_negative_int(value, field)
    except (TypeError, ValueError) as error:
        raise TestEvidenceError("TEST_RESULTS_COUNT_INVALID", f"{field} must be a non-negative integer or null") from error


def _safe_command(argv: object) -> tuple[str, ...]:
    if not isinstance(argv, list) or not argv or any(not isinstance(item, str) or not item for item in argv):
        raise TestEvidenceError("TEST_RESULTS_COMMAND_INVALID", "command.argv must be a non-empty string array")
    for argument in argv:
        if _SECRET_RE.search(argument) or PureWindowsPath(argument).drive or argument.startswith(("/", "\\")):
            raise TestEvidenceError("TEST_RESULTS_SECRET_OR_PATH", "command.argv contains unsafe private data")
    return tuple(argv)


def _resolve_log(repository_root: Path, value: object) -> tuple[str, Path]:
    try:
        relative = normalize_relative_posix_path(value, "log_path")
        resolved = repository_root.joinpath(*relative.split("/")).resolve(strict=False)
        resolved.relative_to(repository_root)
    except (TypeError, ValueError, OSError) as error:
        raise TestEvidenceError("TEST_RESULTS_LOG_PATH_UNSAFE", "log_path is unsafe") from error
    return relative, resolved


def parse_test_results_bytes(
    data: bytes,
    *,
    repository_root: Path,
    expected_project: str | None = None,
) -> TestResultsDocument:
    if data.startswith(b"\xef\xbb\xbf"):
        raise TestEvidenceError("TEST_RESULTS_BOM_FORBIDDEN", "Evidence must be UTF-8 without BOM")
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise TestEvidenceError("TEST_RESULTS_UTF8_INVALID", "Evidence is not valid UTF-8") from error
    try:
        decoded = json.loads(text, object_pairs_hook=_pairs)
    except TestEvidenceError:
        raise
    except json.JSONDecodeError as error:
        raise TestEvidenceError("TEST_RESULTS_JSON_INVALID", "Evidence JSON is malformed") from error
    try:
        root = Path(repository_root).resolve(strict=True)
    except OSError as error:
        raise TestEvidenceError("TEST_RESULTS_ROOT_INVALID", "Evidence root is unavailable") from error
    document = _object(decoded, "document")
    _keys(document, "document", {"schema_version", "project", "generated_at", "runs"})
    schema = document["schema_version"]
    if isinstance(schema, bool) or schema != TEST_RESULTS_SCHEMA_VERSION:
        raise TestEvidenceError("TEST_RESULTS_SCHEMA_UNSUPPORTED", "Evidence schema is unsupported")
    project = document["project"]
    if not isinstance(project, str) or not project:
        raise TestEvidenceError("TEST_RESULTS_PROJECT_INVALID", "Evidence project is invalid")
    if expected_project is not None and project != expected_project:
        raise TestEvidenceError("TEST_RESULTS_PROJECT_MISMATCH", "Evidence project does not match configuration")
    generated_at = _timestamp(document["generated_at"], "generated_at")
    raw_runs = document["runs"]
    if not isinstance(raw_runs, list):
        raise TestEvidenceError("TEST_RESULTS_RUNS_INVALID", "runs must be an array")
    runs: list[TestEvidence] = []
    seen: set[str] = set()
    for raw in raw_runs:
        run = _object(raw, "run")
        required = {
            "run_id", "command", "exit_code", "started_at", "completed_at", "duration_seconds",
            "counts", "status", "evidence_source", "verification", "verification_notes",
        }
        _keys(run, "run", required, {"log_path", "log_sha256"})
        run_id = run["run_id"]
        if not isinstance(run_id, str) or not run_id or run_id in seen:
            raise TestEvidenceError("TEST_RESULTS_RUN_ID_INVALID", "run_id is invalid or duplicated")
        seen.add(run_id)
        command = _object(run["command"], "command")
        _keys(command, "command", {"argv"}, {"display"})
        argv = _safe_command(command["argv"])
        exit_code = run["exit_code"]
        if exit_code is not None and (isinstance(exit_code, bool) or not isinstance(exit_code, int)):
            raise TestEvidenceError("TEST_RESULTS_EXIT_CODE_INVALID", "exit_code must be integer or null")
        started = _timestamp(run["started_at"], "started_at")
        completed = _timestamp(run["completed_at"], "completed_at")
        if completed < started:
            raise TestEvidenceError("TEST_RESULTS_TIME_ORDER_INVALID", "completed_at precedes started_at")
        duration = run["duration_seconds"]
        if isinstance(duration, bool) or not isinstance(duration, (int, float)) or duration < 0:
            raise TestEvidenceError("TEST_RESULTS_DURATION_INVALID", "duration_seconds is invalid")
        if abs(float(duration) - (completed - started).total_seconds()) > DURATION_TOLERANCE_SECONDS:
            raise TestEvidenceError("TEST_RESULTS_DURATION_MISMATCH", "duration_seconds does not match timestamps")
        counts = _object(run["counts"], "counts")
        count_names = {"passed", "failed", "skipped", "deselected", "xfailed", "xpassed", "warnings"}
        _keys(counts, "counts", count_names)
        parsed_counts = {name: _count(counts[name], name) for name in count_names}
        try:
            status = TestStatus(run["status"])
            source = EvidenceSource(run["evidence_source"])
            requested_verification = VerificationStatus(run["verification"])
        except (TypeError, ValueError) as error:
            raise TestEvidenceError("TEST_RESULTS_ENUM_INVALID", "Evidence status/source/verification is invalid") from error
        if status is TestStatus.PASSED:
            if exit_code != 0 or parsed_counts["failed"] not in {0, None}:
                raise TestEvidenceError("TEST_RESULTS_FALSE_PASS", "Passed evidence conflicts with exit code or failures")
        notes = run["verification_notes"]
        if not isinstance(notes, list) or any(not isinstance(item, str) or not item for item in notes):
            raise TestEvidenceError("TEST_RESULTS_NOTES_INVALID", "verification_notes is invalid")
        log_path: str | None = None
        log_sha = run.get("log_sha256")
        if log_sha is not None and (not isinstance(log_sha, str) or re.fullmatch(r"[0-9a-f]{64}", log_sha) is None):
            raise TestEvidenceError("TEST_RESULTS_HASH_INVALID", "log_sha256 is invalid")
        resolved_log: Path | None = None
        if run.get("log_path") is not None:
            log_path, resolved_log = _resolve_log(root, run["log_path"])
        if log_sha is not None:
            if resolved_log is None or not resolved_log.is_file():
                raise TestEvidenceError("TEST_RESULTS_LOG_MISSING", "Hashed evidence log is missing")
            actual = hashlib.sha256(resolved_log.read_bytes()).hexdigest()
            if actual != log_sha:
                raise TestEvidenceError("TEST_RESULTS_HASH_MISMATCH", "Evidence log hash does not match")
        verification = requested_verification
        normalized_notes = list(notes)
        if source in {EvidenceSource.MANUAL, EvidenceSource.IMPORTED_LOG}:
            verification = VerificationStatus.UNVERIFIED
            normalized_notes.append(f"{source.value}_is_unverified_in_v1_1")
        if status is TestStatus.PASSED and verification is VerificationStatus.VERIFIED:
            if source is not EvidenceSource.RUNNER_JSON or resolved_log is None or log_sha is None:
                raise TestEvidenceError("TEST_RESULTS_VERIFIED_EVIDENCE_MISSING", "Verified PASS lacks required evidence")
        runs.append(
            TestEvidence(
                run_id=run_id, command=argv, exit_code=exit_code, started_at=started,
                completed_at=completed, duration_seconds=float(duration), status=status,
                evidence_source=source, log_path=log_path, log_sha256=log_sha,
                verification=verification, verification_issues=tuple(sorted(set(normalized_notes))),
                **parsed_counts,
            )
        )
    ordered = tuple(sorted(runs, key=lambda item: (item.started_at, item.run_id)))
    return TestResultsDocument(TEST_RESULTS_SCHEMA_VERSION, project, generated_at, ordered, True)


def load_test_results(
    repository_root: Path,
    evidence_path: Path | str = Path(".ai/TEST_RESULTS.json"),
    *,
    expected_project: str | None = None,
    required: bool = False,
) -> TestResultsDocument:
    root = Path(repository_root).resolve(strict=True)
    candidate = Path(evidence_path)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        resolved = candidate.resolve(strict=False)
        resolved.relative_to(root)
    except (OSError, ValueError) as error:
        raise TestEvidenceError("TEST_RESULTS_PATH_UNSAFE", "Evidence path is outside repository") from error
    if not resolved.exists():
        if required:
            raise TestEvidenceError("TEST_RESULTS_REQUIRED_MISSING", "Required test evidence is missing")
        return TestResultsDocument(TEST_RESULTS_SCHEMA_VERSION, expected_project or "unknown", None, (), False)
    try:
        data = resolved.read_bytes()
    except OSError as error:
        raise TestEvidenceError("TEST_RESULTS_READ_FAILED", "Evidence could not be read") from error
    return parse_test_results_bytes(data, repository_root=root, expected_project=expected_project)
