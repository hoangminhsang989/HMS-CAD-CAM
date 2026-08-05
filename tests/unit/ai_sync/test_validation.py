"""WP1 tests for deterministic fail-closed validation."""

from __future__ import annotations

from tools.ai_sync.models import ValidationIssue, ValidationSeverity
from tools.ai_sync.validation import (
    ValidationCollector,
    has_blocking_issues,
    make_issue,
    sanitize_details,
    sort_validation_issues,
)


def _issue(code: str, severity: ValidationSeverity) -> ValidationIssue:
    return make_issue(code=code, severity=severity, component="wp1", message=code)


def test_issue_ordering_is_deterministic_and_severity_first() -> None:
    issues = (
        _issue("Z_INFO", ValidationSeverity.INFO),
        _issue("B_ERROR", ValidationSeverity.ERROR),
        _issue("A_FATAL", ValidationSeverity.FATAL),
        _issue("A_ERROR", ValidationSeverity.ERROR),
        _issue("A_WARNING", ValidationSeverity.WARNING),
    )
    expected = ("A_FATAL", "A_ERROR", "B_ERROR", "A_WARNING", "Z_INFO")
    assert tuple(issue.code for issue in sort_validation_issues(issues)) == expected
    assert tuple(issue.code for issue in sort_validation_issues(reversed(issues))) == expected


def test_blocking_helper_only_flags_error_and_fatal() -> None:
    assert not has_blocking_issues((_issue("I", ValidationSeverity.INFO), _issue("W", ValidationSeverity.WARNING)))
    assert has_blocking_issues((_issue("E", ValidationSeverity.ERROR),))
    assert has_blocking_issues((_issue("F", ValidationSeverity.FATAL),))


def test_validator_internal_exception_becomes_sanitized_fatal_issue() -> None:
    collector = ValidationCollector()

    def broken_validator():
        raise RuntimeError("secret token=do-not-log")

    collector.run(broken_validator, component="models", field="version", path="C:\\private\\config.json")
    assert collector.has_blocking
    assert len(collector.issues) == 1
    issue = collector.issues[0]
    assert issue.code == "VALIDATOR_INTERNAL_ERROR"
    assert issue.severity is ValidationSeverity.FATAL
    assert issue.path == "<redacted-path>"
    assert "do-not-log" not in issue.message
    assert dict(issue.details) == {"exception_type": "RuntimeError"}


def test_collector_does_not_mutate_candidate_or_issue_input() -> None:
    candidate = {"progress": 50}
    issue = _issue("VALID", ValidationSeverity.INFO)
    collector = ValidationCollector()
    collector.run(lambda: (issue,), component="models")
    assert candidate == {"progress": 50}
    assert collector.issues == (issue,)


def test_details_are_deterministic_json_safe_and_secret_redacted() -> None:
    details = sanitize_details(
        {
            "z": {"token": "abc", "nested": [2, 1]},
            "password": "hidden",
            "url": "https://user:pass@example.invalid/path?token=abc",
        }
    )
    values = dict(details)
    assert tuple(key for key, _value in details) == ("password", "url", "z")
    assert values["password"] == "<redacted>"
    assert "pass" not in values["url"] and "abc" not in values["url"]
    assert values["z"]["token"] == "<redacted>"
