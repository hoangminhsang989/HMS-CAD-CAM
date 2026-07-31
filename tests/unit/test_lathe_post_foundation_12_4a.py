"""Focused Stage 12.4A contract tests for the neutral Lathe Program IR."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
import hashlib

import pytest

from hms_cadcam.cam.lathe.lathe_post import (
    LatheProgramAssemblerV1,
    LatheProgramBlockKind,
    LatheProgramIdentity,
    LatheProgramService,
    LatheProgramReadiness,
    LathePostUnavailableError,
    LatheUnits,
    NEUTRAL_PROFILE_ID,
    render_neutral_listing,
)
from hms_cadcam.cam.lathe.types import LatheStrategyId
from tests.unit._lathe_toolpath_fixtures import generate, ready_request, stock_snapshot


def _assembled(strategy: LatheStrategyId = LatheStrategyId.OD_ROUGH):
    stock = stock_snapshot(inner_diameter_mm=10) if strategy is LatheStrategyId.ID_THREAD else None
    _, operation, request = ready_request(strategy, stock=stock)
    result = generate(request)
    owner = operation.ownership
    identity = LatheProgramIdentity(
        owner.project_id,
        owner.document_id,
        owner.source_id,
        owner.generation,
        owner.setup_id,
        "program-stage12-4a",
        0,
    )
    outcome = LatheProgramAssemblerV1().assemble(
        identity,
        (operation,),
        accepted_results={str(owner.operation_id): result},
    )
    assert outcome.accepted, outcome.diagnostics
    assert outcome.program is not None
    return identity, operation, result, outcome.program


def test_identity_is_frozen_and_rejects_blank_or_bool_revision() -> None:
    with pytest.raises(ValueError):
        LatheProgramIdentity("", "document", "source", 0, "setup", "program", 0)
    with pytest.raises(ValueError):
        LatheProgramIdentity("project", "document", "source", True, "setup", "program", 0)
    identity, *_ = _assembled()
    with pytest.raises(AttributeError):
        identity.program_id = "changed"  # type: ignore[misc]


def test_exact_program_lifecycle_and_neutral_profile() -> None:
    _, _, _, program = _assembled()
    assert program.profile_id == NEUTRAL_PROFILE_ID
    assert program.blocks[0].kind is LatheProgramBlockKind.PROGRAM_BEGIN
    assert program.blocks[1].kind is LatheProgramBlockKind.SET_UNITS
    assert program.blocks[1].payload.units is LatheUnits.MILLIMETRES
    assert program.blocks[2].kind is LatheProgramBlockKind.SET_PLANE
    assert program.blocks[-1].kind is LatheProgramBlockKind.PROGRAM_END
    assert tuple(block.sequence_index for block in program.blocks) == tuple(range(len(program.blocks)))


def test_listing_is_deterministic_and_not_controller_output() -> None:
    _, _, _, program = _assembled()
    first = render_neutral_listing(program)
    second = render_neutral_listing(program)
    assert first == second
    for token in ("G00", "G01", "G18", "G32", "G76", "M03", "M05", "T0101"):
        assert token not in first
    assert "PREVIEW ONLY" in first
    assert "NOT MACHINE-READY" in first
    assert hashlib.sha256(first.encode()).hexdigest() == hashlib.sha256(second.encode()).hexdigest()


def test_thread_strategies_preserve_semantic_thread_intent() -> None:
    for strategy in (LatheStrategyId.OD_THREAD, LatheStrategyId.ID_THREAD):
        _, _, _, program = _assembled(strategy)
        thread_blocks = tuple(block for block in program.blocks if block.kind is LatheProgramBlockKind.THREAD_CUT_INTENT)
        assert thread_blocks
        assert all(block.payload.phase_neutral for block in thread_blocks)
        assert all(block.payload.feed_mm_per_rev == block.payload.pitch_mm for block in thread_blocks)


def test_failed_toolpath_never_returns_partial_success() -> None:
    identity, operation, result, _ = _assembled()
    failed = SimpleNamespace(identity=result.identity, state=type(result.state).CANCELLED, succeeded=False)
    outcome = LatheProgramAssemblerV1().assemble(identity, (operation,), {str(operation.ownership.operation_id): failed})
    assert not outcome.accepted
    assert outcome.program is None


def test_service_readiness_and_exact_owner_invalidation() -> None:
    identity, operation, result, _ = _assembled()
    service = LatheProgramService()
    outcome = service.assemble(identity, (operation,), {str(operation.ownership.operation_id): result})
    assert outcome.accepted
    assert service.readiness().readiness is LatheProgramReadiness.NEUTRAL_PREVIEW_READY
    assert service.invalidate(project_id="different") is False
    assert service.latest is not None
    assert service.invalidate(project_id=identity.project_id) is True
    assert service.latest is None
    with pytest.raises(LathePostUnavailableError):
        service.request_production_post()
