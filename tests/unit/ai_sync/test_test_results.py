"""WP3 tests for conservative test evidence parsing."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path

import pytest

from tools.ai_sync.models import VerificationStatus
from tools.ai_sync.test_results import TestEvidenceError as EvidenceParseError, load_test_results, parse_test_results_bytes


def _run() -> dict[str, object]:
    return {
        "run_id": "focused-1",
        "command": {"argv": ["python", "-m", "pytest", "tests/unit/ai_sync"], "display": "ignored"},
        "exit_code": 0,
        "started_at": "2026-08-04T00:00:00Z",
        "completed_at": "2026-08-04T00:00:02Z",
        "duration_seconds": 2.0,
        "counts": {"passed": 2, "failed": 0, "skipped": 0, "deselected": 0, "xfailed": 0, "xpassed": 0, "warnings": 0},
        "status": "passed", "evidence_source": "runner_json", "verification": "unverified", "verification_notes": [],
    }


def _doc(runs=None) -> dict[str, object]:
    return {"schema_version": 1, "project": "HMS CAD/CAM", "generated_at": "2026-08-04T00:00:03Z", "runs": [_run()] if runs is None else runs}


def _bytes(value) -> bytes:
    return json.dumps(value, ensure_ascii=False).encode()


def test_missing_is_no_evidence_unless_required(tmp_path: Path) -> None:
    result = load_test_results(tmp_path, expected_project="HMS CAD/CAM")
    assert not result.evidence_present and result.runs == ()
    with pytest.raises(EvidenceParseError, match="missing"):
        load_test_results(tmp_path, required=True)


def test_empty_and_multiple_runs_are_deterministically_ordered(tmp_path: Path) -> None:
    assert parse_test_results_bytes(_bytes(_doc([])), repository_root=tmp_path).runs == ()
    later = _run(); later["run_id"] = "z"; later["started_at"] = "2026-08-04T00:00:10Z"; later["completed_at"] = "2026-08-04T00:00:12Z"
    earlier = _run(); earlier["run_id"] = "a"
    parsed = parse_test_results_bytes(_bytes(_doc([later, earlier])), repository_root=tmp_path)
    assert [item.run_id for item in parsed.runs] == ["a", "z"]


@pytest.mark.parametrize("data", (b"{", b"\xff", b"\xef\xbb\xbf{}"))
def test_malformed_utf8_and_bom_fail(data: bytes, tmp_path: Path) -> None:
    with pytest.raises(EvidenceParseError): parse_test_results_bytes(data, repository_root=tmp_path)


def test_duplicate_key_and_run_id_fail(tmp_path: Path) -> None:
    with pytest.raises(EvidenceParseError): parse_test_results_bytes(b'{"schema_version":1,"schema_version":1}', repository_root=tmp_path)
    run = _run()
    with pytest.raises(EvidenceParseError): parse_test_results_bytes(_bytes(_doc([run, run])), repository_root=tmp_path)


@pytest.mark.parametrize(
    ("mutate", "value"),
    (("duration_seconds", 9), ("exit_code", 1), ("status", "bogus")),
)
def test_duration_exit_and_enum_invariants_fail(tmp_path: Path, mutate: str, value: object) -> None:
    run = _run(); run[mutate] = value
    with pytest.raises(EvidenceParseError): parse_test_results_bytes(_bytes(_doc([run])), repository_root=tmp_path)


@pytest.mark.parametrize("value", (-1, True))
def test_negative_and_bool_counts_fail(tmp_path: Path, value: object) -> None:
    run = _run(); run["counts"]["passed"] = value
    with pytest.raises(EvidenceParseError): parse_test_results_bytes(_bytes(_doc([run])), repository_root=tmp_path)


def test_failed_count_cannot_be_passed(tmp_path: Path) -> None:
    run = _run(); run["counts"]["failed"] = 1
    with pytest.raises(EvidenceParseError): parse_test_results_bytes(_bytes(_doc([run])), repository_root=tmp_path)


@pytest.mark.parametrize("status", ("timed_out", "cancelled", "error", "unknown"))
def test_nonpass_statuses_remain_unverified(tmp_path: Path, status: str) -> None:
    run = _run(); run["status"] = status; run["exit_code"] = None
    result = parse_test_results_bytes(_bytes(_doc([run])), repository_root=tmp_path)
    assert result.runs[0].verification is VerificationStatus.UNVERIFIED


@pytest.mark.parametrize("source", ("manual", "imported_log"))
def test_manual_and_imported_log_are_forced_unverified(tmp_path: Path, source: str) -> None:
    run = _run(); run["evidence_source"] = source; run["verification"] = "verified"
    result = parse_test_results_bytes(_bytes(_doc([run])), repository_root=tmp_path)
    assert result.runs[0].verification is VerificationStatus.UNVERIFIED


def test_verified_runner_json_requires_and_verifies_log_hash(tmp_path: Path) -> None:
    log = tmp_path / "evidence.log"; log.write_bytes(b"verified")
    run = _run(); run.update({"verification": "verified", "log_path": "evidence.log", "log_sha256": hashlib.sha256(b"verified").hexdigest()})
    assert parse_test_results_bytes(_bytes(_doc([run])), repository_root=tmp_path).runs[0].verification is VerificationStatus.VERIFIED
    run["log_sha256"] = "0" * 64
    with pytest.raises(EvidenceParseError): parse_test_results_bytes(_bytes(_doc([run])), repository_root=tmp_path)


def test_verified_pass_missing_log_and_unsafe_paths_or_secrets_fail(tmp_path: Path) -> None:
    run = _run(); run["verification"] = "verified"
    with pytest.raises(EvidenceParseError): parse_test_results_bytes(_bytes(_doc([run])), repository_root=tmp_path)
    run = _run(); run["log_path"] = "../secret.log"
    with pytest.raises(EvidenceParseError): parse_test_results_bytes(_bytes(_doc([run])), repository_root=tmp_path)
    run = _run(); run["command"]["argv"] = ["pytest", "--token=secret"]
    with pytest.raises(EvidenceParseError): parse_test_results_bytes(_bytes(_doc([run])), repository_root=tmp_path)


def test_project_must_match_when_requested(tmp_path: Path) -> None:
    with pytest.raises(EvidenceParseError): parse_test_results_bytes(_bytes(_doc()), repository_root=tmp_path, expected_project="Other")
