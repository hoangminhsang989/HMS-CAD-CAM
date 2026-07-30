"""Ownership, binding, aggregate, readiness, and snapshot tests for Stage 12."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from uuid import UUID

import pytest

from hms_cadcam.cad.models import CadDocumentId
from hms_cadcam.cam.domain.ids import OperationId, SetupId
from hms_cadcam.cam.domain.operation import DiagnosticSeverity
from hms_cadcam.cam.domain.revision import Revision
from hms_cadcam.cam.lathe.domain import (
    LATHE_SNAPSHOT_SCHEMA_VERSION,
    LatheGeometryBinding,
    LatheOperationState,
    LatheOwnershipKey,
    evaluate_lathe_operation,
    lathe_operation_from_canonical_mapping,
    lathe_operation_to_canonical_mapping,
)
from hms_cadcam.cam.lathe.parameters import build_lathe_v1_defaults
from hms_cadcam.cam.lathe.types import (
    LatheDiagnostic,
    LatheDiagnosticCode,
    LatheGeometryKind,
    LatheOperationReadiness,
    LatheStrategyId,
)
from tests.unit._lathe_fixtures import (
    complete_operation,
    operation_id,
    ownership,
    service_for,
    session,
    setup_id,
    stable_uuid,
)


def _evaluate(state: LatheOperationState, **changes: object):
    live = session()
    values = {
        "project_id": live.project_id,
        "document_id": live.document_id,
        "source_id": live.source_id,
        "generation": live.generation,
        "setup_id": live.setup_id,
        "read_only": False,
        "closed": False,
    }
    values.update(changes)
    return evaluate_lathe_operation(state, **values)  # type: ignore[arg-type]


def test_ownership_uses_canonical_identities_and_is_immutable() -> None:
    key = ownership()
    assert isinstance(key.project_id, UUID)
    assert isinstance(key.document_id, CadDocumentId)
    assert isinstance(key.setup_id, SetupId)
    assert isinstance(key.operation_id, OperationId)
    with pytest.raises(FrozenInstanceError):
        key.generation = 9  # type: ignore[misc]


@pytest.mark.parametrize("generation", [True, -1])
def test_ownership_rejects_bool_and_negative_generation(generation: object) -> None:
    live = session()
    with pytest.raises(ValueError, match="non-negative"):
        LatheOwnershipKey(
            live.project_id,
            live.document_id,
            live.source_id,
            generation,  # type: ignore[arg-type]
            setup_id(),
            operation_id(),
        )


def test_ownership_rejects_blank_and_nil_identities() -> None:
    live = session()
    with pytest.raises(ValueError, match="non-blank"):
        LatheOwnershipKey(
            live.project_id,
            CadDocumentId(" "),
            live.source_id,
            live.generation,
            setup_id(),
            operation_id(),
        )
    with pytest.raises(ValueError, match="non-nil"):
        LatheOwnershipKey(
            UUID(int=0),
            live.document_id,
            live.source_id,
            live.generation,
            setup_id(),
            operation_id(),
        )


def test_geometry_binding_is_ordered_immutable_and_strict() -> None:
    live = session()
    binding = LatheGeometryBinding(
        LatheGeometryKind.PROFILE,
        ("entity-b", "entity-a"),
        live.source_id,
        live.generation,
    )
    assert binding.entity_ids == ("entity-b", "entity-a")
    with pytest.raises(FrozenInstanceError):
        binding.entity_ids = ()  # type: ignore[misc]
    with pytest.raises(ValueError, match="non-empty"):
        LatheGeometryBinding(
            LatheGeometryKind.PROFILE, (), live.source_id, live.generation
        )
    with pytest.raises(ValueError, match="unique"):
        LatheGeometryBinding(
            LatheGeometryKind.PROFILE,
            ("same", "same"),
            live.source_id,
            live.generation,
        )
    with pytest.raises(ValueError, match="non-blank"):
        LatheGeometryBinding(
            LatheGeometryKind.PROFILE, (" ",), live.source_id, live.generation
        )
    with pytest.raises(ValueError, match="non-blank"):
        LatheGeometryBinding(
            LatheGeometryKind.PROFILE,
            (object(),),  # type: ignore[arg-type]
            live.source_id,
            live.generation,
        )


def test_aggregate_is_immutable_and_revision_rejects_bool() -> None:
    state = LatheOperationState(
        ownership(),
        LatheStrategyId.FACE,
        build_lathe_v1_defaults(LatheStrategyId.FACE),
    )
    assert state.revision == Revision(0)
    with pytest.raises(FrozenInstanceError):
        state.enabled = False  # type: ignore[misc]
    with pytest.raises(ValueError):
        Revision(True)  # type: ignore[arg-type]


def test_readiness_distinguishes_incomplete_invalid_disabled_and_ready() -> None:
    service, reference = service_for()
    state = LatheOperationState(
        ownership(live_session=service.session),
        LatheStrategyId.FACE,
        build_lathe_v1_defaults(LatheStrategyId.FACE),
    )
    missing = _evaluate(state)
    assert missing.readiness is LatheOperationReadiness.INCOMPLETE
    assert {item.code for item in missing.diagnostics} == {
        LatheDiagnosticCode.MISSING_GEOMETRY,
        LatheDiagnosticCode.MISSING_TOOL,
    }
    disabled = _evaluate(replace(state, enabled=False))
    assert disabled.readiness is LatheOperationReadiness.INCOMPLETE
    assert LatheDiagnosticCode.DISABLED_OPERATION in {
        item.code for item in disabled.diagnostics
    }
    invalid = _evaluate(
        replace(
            state,
            diagnostics=(
                LatheDiagnostic(
                    LatheDiagnosticCode.INVALID_PARAMETER,
                    DiagnosticSeverity.ERROR,
                    "feed_mm_per_rev",
                ),
            ),
        )
    )
    assert invalid.readiness is LatheOperationReadiness.INVALID
    complete = complete_operation(service, reference)
    ready = service.evaluate(complete.ownership.operation_id)
    assert ready.readiness is LatheOperationReadiness.READY
    assert ready.diagnostics == ()


@pytest.mark.parametrize(
    "change",
    [
        {"project_id": stable_uuid("foreign-project")},
        {"document_id": CadDocumentId("foreign-document")},
        {"source_id": stable_uuid("foreign-source")},
        {"generation": 99},
        {"setup_id": setup_id(2)},
    ],
)
def test_project_document_source_generation_and_setup_mismatch_fail_closed(
    change: dict[str, object]
) -> None:
    state = LatheOperationState(
        ownership(),
        LatheStrategyId.FACE,
        build_lathe_v1_defaults(LatheStrategyId.FACE),
    )
    result = _evaluate(state, **change)
    assert result.readiness is LatheOperationReadiness.INVALID
    assert LatheDiagnosticCode.STALE_OWNERSHIP in {
        item.code for item in result.diagnostics
    }


def test_missing_setup_read_only_and_closed_have_distinct_diagnostics() -> None:
    state = LatheOperationState(
        ownership(),
        LatheStrategyId.FACE,
        build_lathe_v1_defaults(LatheStrategyId.FACE),
    )
    missing_setup = _evaluate(state, setup_id=None)
    assert LatheDiagnosticCode.MISSING_SETUP in {
        item.code for item in missing_setup.diagnostics
    }
    read_only = _evaluate(state, read_only=True)
    assert read_only.readiness is LatheOperationReadiness.INVALID
    assert LatheDiagnosticCode.READ_ONLY in {item.code for item in read_only.diagnostics}
    closed = _evaluate(state, closed=True)
    assert closed.readiness is LatheOperationReadiness.INVALID
    assert LatheDiagnosticCode.CLOSED in {item.code for item in closed.diagnostics}


def test_canonical_snapshot_is_deterministic_strict_and_round_trips_in_memory() -> None:
    service, reference = service_for()
    state = complete_operation(service, reference)
    first = lathe_operation_to_canonical_mapping(state)
    second = lathe_operation_to_canonical_mapping(state)
    assert first == second
    assert first["schema_version"] == LATHE_SNAPSHOT_SCHEMA_VERSION
    assert lathe_operation_from_canonical_mapping(first) == state
    unknown = dict(first)
    unknown["unknown"] = True
    with pytest.raises(ValueError, match="fields"):
        lathe_operation_from_canonical_mapping(unknown)
    missing = dict(first)
    del missing["revision"]
    with pytest.raises(ValueError, match="fields"):
        lathe_operation_from_canonical_mapping(missing)
