"""Deterministic, non-mutating validation issue collection for WP1."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from pathlib import Path, PureWindowsPath
import re
from typing import Any

from .models import ValidationIssue, ValidationSeverity


_SENSITIVE_KEY_PARTS = ("secret", "token", "password", "credential", "authorization", "api_key")
_URL_USERINFO_RE = re.compile(r"(?P<scheme>[a-z][a-z0-9+.-]*://)[^/@\s]+@", re.IGNORECASE)
_QUERY_SECRET_RE = re.compile(
    r"(?i)(token|password|secret|api[_-]?key)=([^&\s]+)"
)


def _sanitize_string(value: str) -> str:
    sanitized = _URL_USERINFO_RE.sub(r"\g<scheme><redacted>@", value)
    return _QUERY_SECRET_RE.sub(r"\1=<redacted>", sanitized)


def _sanitize_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _sanitize_string(value)
    if isinstance(value, Path):
        return "<path>"
    if isinstance(value, Mapping):
        return {
            str(key): (
                "<redacted>"
                if any(part in str(key).casefold() for part in _SENSITIVE_KEY_PARTS)
                else _sanitize_value(item)
            )
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (tuple, list, set, frozenset)):
        return tuple(_sanitize_value(item) for item in value)
    return f"<{type(value).__name__}>"


def sanitize_details(details: Mapping[str, Any] | None) -> tuple[tuple[str, Any], ...]:
    """Convert details to deterministic, JSON-safe, secret-redacted pairs."""

    if details is None:
        return ()
    return tuple(
        (
            str(key),
            "<redacted>"
            if any(part in str(key).casefold() for part in _SENSITIVE_KEY_PARTS)
            else _sanitize_value(value),
        )
        for key, value in sorted(details.items(), key=lambda pair: str(pair[0]))
    )


def sanitize_issue_path(path: str | None) -> str | None:
    if path is None:
        return None
    candidate = PureWindowsPath(path)
    if candidate.drive or path.startswith(("/", "\\")):
        return "<redacted-path>"
    return path.replace("\\", "/")


def make_issue(
    *,
    code: str,
    severity: ValidationSeverity,
    component: str,
    message: str,
    field: str | None = None,
    path: str | None = None,
    details: Mapping[str, Any] | None = None,
) -> ValidationIssue:
    """Build one immutable issue without mutating validation inputs."""

    return ValidationIssue(
        code=code,
        severity=severity,
        component=component,
        field=field,
        path=sanitize_issue_path(path),
        message=_sanitize_string(message),
        details=sanitize_details(details),
    )


def sort_validation_issues(issues: Iterable[ValidationIssue]) -> tuple[ValidationIssue, ...]:
    collected = tuple(issues)
    if any(not isinstance(issue, ValidationIssue) for issue in collected):
        raise TypeError("issues must contain ValidationIssue")
    return tuple(sorted(collected, key=ValidationIssue.sort_key))


def has_blocking_issues(issues: Iterable[ValidationIssue]) -> bool:
    return any(issue.severity in {ValidationSeverity.ERROR, ValidationSeverity.FATAL} for issue in issues)


class ValidationCollector:
    """Collect issues and turn validator defects into deterministic fatal issues."""

    __slots__ = ("_issues",)

    def __init__(self) -> None:
        self._issues: list[ValidationIssue] = []

    def add(self, issue: ValidationIssue) -> None:
        if not isinstance(issue, ValidationIssue):
            raise TypeError("issue must be ValidationIssue")
        self._issues.append(issue)

    def extend(self, issues: Iterable[ValidationIssue]) -> None:
        for issue in issues:
            self.add(issue)

    def run(
        self,
        validator: Callable[[], Iterable[ValidationIssue] | ValidationIssue | None],
        *,
        component: str,
        field: str | None = None,
        path: str | None = None,
    ) -> None:
        """Run a validator and fail closed if its implementation raises."""

        try:
            result = validator()
            if result is None:
                return
            if isinstance(result, ValidationIssue):
                self.add(result)
                return
            self.extend(result)
        except Exception as error:
            self.add(
                make_issue(
                    code="VALIDATOR_INTERNAL_ERROR",
                    severity=ValidationSeverity.FATAL,
                    component=component,
                    field=field,
                    path=path,
                    message="Validator raised an internal exception",
                    details={"exception_type": type(error).__name__},
                )
            )

    @property
    def issues(self) -> tuple[ValidationIssue, ...]:
        return sort_validation_issues(self._issues)

    @property
    def has_blocking(self) -> bool:
        return has_blocking_issues(self._issues)
