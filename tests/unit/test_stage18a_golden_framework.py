"""Engineering-only deterministic NC sample framework tests."""

from dataclasses import replace

import pytest

from hms_cadcam.cam.domain import CamInvariantError
from hms_cadcam.cam.post import ProgramAssemblyService
from hms_cadcam.cam.qualification import (
    GoldenSampleFixture,
    SampleAuthority,
    SampleStatus,
    engineering_sample_fixtures,
    run_deterministic_sample,
)
from tests.unit.test_fanuc_robodrill_21i_runtime import _runtime_source
from tests.unit.test_program_assembly import _request


def _deterministic_generator():
    source = _runtime_source()
    request = _request([source])

    def generate():
        result = ProgramAssemblyService().assemble(request).result
        assert result is not None
        return result

    return generate


def test_engineering_fixture_set_is_complete_and_never_owner_approved():
    fixtures = engineering_sample_fixtures()

    assert len(fixtures) == 8
    assert len({item.sample_id for item in fixtures}) == 8
    assert all(
        item.authority is SampleAuthority.ENGINEERING_REGRESSION_SAMPLE
        and item.expected_sha256 is None
        for item in fixtures
    )
    assert fixtures[-1].strategy_keys == ("tapping_v1",)


def test_run_a_b_bytes_and_sha_are_deterministic_but_owner_approval_pending():
    fixture = engineering_sample_fixtures()[0]

    result = run_deterministic_sample(fixture, _deterministic_generator())

    assert result.status is SampleStatus.GOLDEN_SAMPLE_OWNER_APPROVAL_PENDING
    assert result.run_count == 2
    assert result.byte_length > 0


def test_expected_hash_mismatch_is_not_relabelled_pass():
    fixture = replace(engineering_sample_fixtures()[0], expected_sha256="0" * 64)

    result = run_deterministic_sample(fixture, _deterministic_generator())

    assert result.status is SampleStatus.EXPECTED_OUTPUT_MISMATCH


def test_owner_approved_label_requires_exact_expected_bytes():
    with pytest.raises(CamInvariantError):
        GoldenSampleFixture(
            "stage18a.owner.sample",
            ("facing_2_5d",),
            SampleAuthority.OWNER_APPROVED_MACHINE_SAMPLE,
        )
