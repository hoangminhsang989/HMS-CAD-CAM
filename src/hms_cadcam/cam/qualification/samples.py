"""Deterministic golden/engineering sample framework without owner-byte claims."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from hms_cadcam.cam.domain.errors import CamInvariantError, CamValidationError
from hms_cadcam.cam.post.assembly_model import ProgramAssemblyResult
from hms_cadcam.cam.qualification.model import SampleAuthority, sha256_bytes


_KEY = re.compile(r"[a-z][a-z0-9_.-]{1,127}")
_SHA256 = re.compile(r"[0-9a-f]{64}")


class SampleStatus(StrEnum):
    ENGINEERING_REGRESSION_PASS = "engineering_regression_pass"
    OWNER_APPROVED_PASS = "owner_approved_pass"
    GOLDEN_SAMPLE_OWNER_APPROVAL_PENDING = "golden_sample_owner_approval_pending"
    DETERMINISM_FAILURE = "determinism_failure"
    EXPECTED_OUTPUT_MISMATCH = "expected_output_mismatch"


@dataclass(frozen=True, slots=True)
class GoldenSampleFixture:
    sample_id: str
    strategy_keys: tuple[str, ...]
    authority: SampleAuthority
    expected_sha256: str | None = None
    format_version: int = 1

    def __post_init__(self) -> None:
        if self.format_version != 1:
            raise CamValidationError("Unsupported golden sample fixture version")
        if not isinstance(self.sample_id, str) or _KEY.fullmatch(self.sample_id) is None:
            raise CamValidationError("Golden sample ID is invalid")
        if not isinstance(self.strategy_keys, tuple) or not self.strategy_keys or any(
            not isinstance(item, str) or _KEY.fullmatch(item) is None
            for item in self.strategy_keys
        ):
            raise CamValidationError("Golden sample strategies are invalid")
        if not isinstance(self.authority, SampleAuthority):
            raise CamValidationError("Golden sample authority is invalid")
        if self.expected_sha256 is not None and _SHA256.fullmatch(self.expected_sha256) is None:
            raise CamValidationError("Golden sample expected SHA-256 is invalid")
        if (
            self.authority is SampleAuthority.OWNER_APPROVED_MACHINE_SAMPLE
            and self.expected_sha256 is None
        ):
            raise CamInvariantError("Owner-approved sample requires exact expected bytes")

    def to_dict(self) -> dict[str, object]:
        return {
            "format": "HMS_STAGE18A_GOLDEN_SAMPLE_FIXTURE",
            "format_version": self.format_version,
            "sample_id": self.sample_id,
            "strategy_keys": list(self.strategy_keys),
            "authority": self.authority.value,
            "expected_sha256": self.expected_sha256,
        }


@dataclass(frozen=True, slots=True)
class GoldenSampleResult:
    sample_id: str
    status: SampleStatus
    sha256: str
    byte_length: int
    run_count: int = 2

    def __post_init__(self) -> None:
        if not isinstance(self.sample_id, str) or _KEY.fullmatch(self.sample_id) is None:
            raise CamValidationError("Golden sample result ID is invalid")
        if not isinstance(self.status, SampleStatus):
            raise CamValidationError("Golden sample status is invalid")
        if _SHA256.fullmatch(self.sha256) is None:
            raise CamValidationError("Golden sample result SHA-256 is invalid")
        if type(self.byte_length) is not int or self.byte_length <= 0 or self.run_count != 2:
            raise CamValidationError("Golden sample result statistics are invalid")


def engineering_sample_fixtures() -> tuple[GoldenSampleFixture, ...]:
    """Return the frozen R218 engineering set; no item is owner-approved."""

    rows = (
        ("stage18a.facing", ("facing_2_5d",)),
        ("stage18a.contour", ("contour_2d",)),
        ("stage18a.pocket", ("pocket_2_5d",)),
        ("stage18a.drilling.standard", ("drilling_v1",)),
        ("stage18a.drilling.spot", ("drilling_v1",)),
        ("stage18a.drilling.peck", ("drilling_v1",)),
        ("stage18a.multi_tool", ("facing_2_5d", "contour_2d")),
        ("stage18a.tapping.negative", ("tapping_v1",)),
    )
    return tuple(
        GoldenSampleFixture(
            sample_id,
            strategies,
            SampleAuthority.ENGINEERING_REGRESSION_SAMPLE,
        )
        for sample_id, strategies in rows
    )


def run_deterministic_sample(
    fixture: GoldenSampleFixture,
    generate: Callable[[], ProgramAssemblyResult],
) -> GoldenSampleResult:
    """Run A/B generation and compare exact UTF-8 bytes and optional owner hash."""

    if not isinstance(fixture, GoldenSampleFixture) or not callable(generate):
        raise TypeError("Golden sample fixture/generator is invalid")
    first = generate()
    second = generate()
    if not isinstance(first, ProgramAssemblyResult) or not isinstance(second, ProgramAssemblyResult):
        raise TypeError("Golden sample generator must return ProgramAssemblyResult")
    first_bytes = first.canonical_text.encode("utf-8")
    second_bytes = second.canonical_text.encode("utf-8")
    digest = sha256_bytes(first_bytes)
    if first_bytes != second_bytes or digest != sha256_bytes(second_bytes):
        return GoldenSampleResult(
            fixture.sample_id,
            SampleStatus.DETERMINISM_FAILURE,
            digest,
            len(first_bytes),
        )
    if fixture.expected_sha256 is not None and fixture.expected_sha256 != digest:
        return GoldenSampleResult(
            fixture.sample_id,
            SampleStatus.EXPECTED_OUTPUT_MISMATCH,
            digest,
            len(first_bytes),
        )
    status = (
        SampleStatus.OWNER_APPROVED_PASS
        if fixture.authority is SampleAuthority.OWNER_APPROVED_MACHINE_SAMPLE
        else SampleStatus.GOLDEN_SAMPLE_OWNER_APPROVAL_PENDING
    )
    return GoldenSampleResult(fixture.sample_id, status, digest, len(first_bytes))


__all__ = [
    "GoldenSampleFixture", "GoldenSampleResult", "SampleStatus",
    "engineering_sample_fixtures",
    "run_deterministic_sample",
]
