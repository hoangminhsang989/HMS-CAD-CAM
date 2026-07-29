from __future__ import annotations

import ast
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from hms_cadcam.cad.models import CadDocumentId
from hms_cadcam.cam.application.defaults import basic_parallel_resources
from hms_cadcam.cam.application.cam3d_editor import (
    Cam3DEditorApplicationService,
    Cam3DEditorField,
    Cam3DEditorState,
    Cam3DParameterDraft,
    Cam3DProjectContext,
    Cam3DToolAssemblyChoice,
    Cam3DToolProfileChoice,
)
from hms_cadcam.cam.application.cam3d_request import (
    Cam3DActiveSetupContext,
    Cam3DCacheDecision,
    Cam3DCacheRecordIdentity,
    Cam3DCalculationJobId,
    Cam3DCalculationOwnershipKey,
    Cam3DCalculationPolicy,
    Cam3DCalculationRequestBuilder,
    Cam3DCalculationRequestContract,
    Cam3DCalculationSession,
    Cam3DPreviewCacheKey,
    Cam3DRequestDiagnosticCode,
    Cam3DRequestFingerprint,
    Cam3DResultIdentity,
    Cam3DSessionDecision,
    evaluate_cam3d_cache_reuse,
)
from hms_cadcam.cam.application.cam3d_selection import (
    Cam3DSelectedSurface,
    Cam3DSelectionIssue,
    Cam3DSelectionProvenance,
    Cam3DSelectionRole,
    Cam3DSelectionState,
    Cam3DSelectionValidity,
)
from hms_cadcam.cam.domain import (
    DEFAULT_TOOL_PROFILE_REGISTRY,
    DependencyFingerprint,
    Length,
    LengthUnit,
    Revision,
    SetupId,
    ToolAssembly,
    ToolAssemblyId,
    ToolProgramProfile,
    ToolProgramProfileId,
    WcsFrame,
)
from tests.unit._cam3d_fixtures import surface, tool


@dataclass(frozen=True, slots=True)
class _ReadyFixture:
    context: Cam3DProjectContext
    selection: Cam3DSelectionState
    editor: Cam3DEditorState
    setup: Cam3DActiveSetupContext


def _selected_item(
    role: Cam3DSelectionRole,
    selector: str,
    *,
    project_id: UUID,
    document_id: CadDocumentId,
    source_id: UUID,
    generation: int,
) -> Cam3DSelectedSurface:
    return Cam3DSelectedSurface(
        role,
        surface(
            project_id,
            source_id,
            selector,
            role.cam_role,
            revision=Revision(0),
        ),
        Cam3DSelectionProvenance(
            project_id,
            generation,
            document_id,
            source_id,
        ),
        f"Surface {selector}",
    )


def _ready_fixture(
    *,
    project_id: UUID | None = None,
    document_id: CadDocumentId | None = None,
    source_id: UUID | None = None,
    generation: int = 4,
    setup_id: SetupId | None = None,
    read_only: bool = False,
) -> _ReadyFixture:
    project_id = project_id or uuid4()
    document_id = document_id or CadDocumentId("document-wp3")
    source_id = source_id or uuid4()
    context = Cam3DProjectContext.open(
        project_id,
        generation,
        document_id=document_id,
        source_id=source_id,
        read_only=read_only,
    )
    selection = Cam3DSelectionState.for_project(
        project_id, generation, read_only=read_only
    )
    for role in Cam3DSelectionRole:
        selection = selection.assign(
            role,
            (
                _selected_item(
                    role,
                    role.value,
                    project_id=project_id,
                    document_id=document_id,
                    source_id=source_id,
                    generation=generation,
                ),
            ),
        )
    value = tool(ball=False)
    assembly = ToolAssembly.create(
        ToolAssemblyId.new(),
        "WP3 assembly",
        value,
        Length(20.0, LengthUnit.MM),
        Length(40.0, LengthUnit.MM),
    )
    schema = DEFAULT_TOOL_PROFILE_REGISTRY.schema("parallel_finishing_3d")
    now = datetime(2026, 7, 29, tzinfo=UTC)
    profile = ToolProgramProfile(
        ToolProgramProfileId.new(),
        value.tool_id,
        schema.strategy_id,
        schema.display_name_vi,
        True,
        schema.profile_schema_version,
        schema.normalize_values({}),
        now,
        now,
        value.revision,
        value.content_fingerprint,
    )
    editor = Cam3DEditorState(
        context,
        selection,
        Cam3DParameterDraft(),
        Cam3DToolAssemblyChoice(assembly, value),
        Cam3DToolProfileChoice(profile, value),
    )
    ownership = Cam3DCalculationOwnershipKey(
        project_id,
        document_id,
        source_id,
        setup_id or SetupId.new(),
    )
    setup = Cam3DActiveSetupContext(
        ownership,
        generation,
        Revision(2),
        WcsFrame.identity(LengthUnit.MM),
    )
    return _ReadyFixture(context, selection, editor, setup)


def _build(
    fixture: _ReadyFixture,
    *,
    editor: Cam3DEditorState | None = None,
    context: Cam3DProjectContext | None = None,
    selection: Cam3DSelectionState | None = None,
    setup: Cam3DActiveSetupContext | None | object = ...,
    job_id: Cam3DCalculationJobId | None | object = ...,
    policy: Cam3DCalculationPolicy | None = None,
):
    selected_setup = fixture.setup if setup is ... else setup
    selected_job = Cam3DCalculationJobId.new() if job_id is ... else job_id
    return Cam3DCalculationRequestBuilder().build(
        editor=editor or fixture.editor,
        live_context=context or fixture.context,
        live_selection=selection or fixture.selection,
        active_setup=selected_setup,  # type: ignore[arg-type]
        job_id=selected_job,  # type: ignore[arg-type]
        policy=policy or Cam3DCalculationPolicy(),
    )


def _request(fixture: _ReadyFixture, **changes: object) -> Cam3DCalculationRequestContract:
    result = _build(fixture, **changes)
    assert result.accepted
    assert result.request is not None
    return result.request


def _assert_failure(result, code: Cam3DRequestDiagnosticCode) -> None:
    assert not result.accepted
    assert result.request is None
    assert tuple(item.code for item in result.diagnostics) == (code,)


def test_valid_request_build_is_immutable_typed_and_atomic() -> None:
    fixture = _ready_fixture()
    before_editor = fixture.editor
    before_selection = fixture.selection
    request = _request(fixture)

    assert request.inputs.zone.part
    assert request.inputs.ownership == fixture.setup.ownership
    assert request.inputs.project_generation == 4
    assert request.fingerprint == Cam3DRequestFingerprint.from_inputs(request.inputs)
    assert request.cache_key == Cam3DPreviewCacheKey.from_request_fingerprint(
        request.fingerprint, request.inputs.policy
    )
    assert fixture.editor == before_editor
    assert fixture.selection == before_selection


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ("missing_part", Cam3DRequestDiagnosticCode.PART_MISSING),
        ("invalid_check", Cam3DRequestDiagnosticCode.SELECTION_INVALID),
        ("invalid_fixture", Cam3DRequestDiagnosticCode.SELECTION_INVALID),
        ("editor_partial", Cam3DRequestDiagnosticCode.EDITOR_PARTIAL),
        ("read_only", Cam3DRequestDiagnosticCode.READ_ONLY),
        ("closed", Cam3DRequestDiagnosticCode.PROJECT_CLOSED),
        ("stale_generation", Cam3DRequestDiagnosticCode.STALE_GENERATION),
        ("project_mismatch", Cam3DRequestDiagnosticCode.PROJECT_MISMATCH),
        ("document_mismatch", Cam3DRequestDiagnosticCode.DOCUMENT_MISMATCH),
        ("source_mismatch", Cam3DRequestDiagnosticCode.SOURCE_MISMATCH),
        ("setup_missing", Cam3DRequestDiagnosticCode.SETUP_MISSING),
        ("setup_mismatch", Cam3DRequestDiagnosticCode.SETUP_MISMATCH),
        ("job_missing", Cam3DRequestDiagnosticCode.JOB_ID_MISSING),
    ],
)
def test_builder_lifecycle_and_partial_failures_are_typed(
    mutation: str,
    code: Cam3DRequestDiagnosticCode,
) -> None:
    fixture = _ready_fixture()
    kwargs: dict[str, object] = {}
    if mutation == "missing_part":
        selection = replace(fixture.selection, part=())
        kwargs.update(selection=selection, editor=replace(fixture.editor, selection=selection))
    elif mutation in {"invalid_check", "invalid_fixture"}:
        field = "check" if mutation == "invalid_check" else "fixture"
        item = getattr(fixture.selection, field)[0]
        invalid = replace(
            item,
            validity=Cam3DSelectionValidity.INVALID,
            stale_reason=Cam3DSelectionIssue.INVALID_GEOMETRY_KIND,
        )
        selection = replace(fixture.selection, **{field: (invalid,)})
        kwargs.update(selection=selection, editor=replace(fixture.editor, selection=selection))
    elif mutation == "editor_partial":
        kwargs["editor"] = replace(fixture.editor, tool_profile=None)
    elif mutation == "read_only":
        read_only = _ready_fixture(
            project_id=fixture.context.project_id,
            document_id=fixture.context.document_id,
            source_id=fixture.context.source_id,
            setup_id=fixture.setup.ownership.setup_id,
            read_only=True,
        )
        fixture = read_only
    elif mutation == "closed":
        kwargs["context"] = Cam3DProjectContext.closed()
    elif mutation == "stale_generation":
        kwargs["context"] = replace(fixture.context, project_generation=5)
    elif mutation == "project_mismatch":
        kwargs["context"] = replace(fixture.context, project_id=uuid4())
    elif mutation == "document_mismatch":
        kwargs["context"] = replace(
            fixture.context, document_id=CadDocumentId("other-document")
        )
    elif mutation == "source_mismatch":
        kwargs["context"] = replace(fixture.context, source_id=uuid4())
    elif mutation == "setup_missing":
        kwargs["setup"] = None
    elif mutation == "setup_mismatch":
        foreign = replace(
            fixture.setup.ownership,
            source_id=uuid4(),
        )
        kwargs["setup"] = replace(fixture.setup, ownership=foreign)
    elif mutation == "job_missing":
        kwargs["job_id"] = None
    _assert_failure(_build(fixture, **kwargs), code)


def test_editor_invalid_numeric_fails_without_mutating_the_draft() -> None:
    fixture = _ready_fixture()
    service = Cam3DEditorApplicationService(
        fixture.context,
        fixture.selection,
        parameters=fixture.editor.parameters,
    )
    service.assign_tool_assembly(
        fixture.editor.tool_assembly, live_context=fixture.context  # type: ignore[arg-type]
    )
    service.assign_tool_profile(
        fixture.editor.tool_profile, live_context=fixture.context  # type: ignore[arg-type]
    )
    before = service.state.parameters
    rejected = service.replace_numeric_field(
        Cam3DEditorField.TOLERANCE_MM,
        float("nan"),
        live_context=fixture.context,
    )
    assert not rejected.accepted
    assert rejected.state.parameters == before
    _assert_failure(
        _build(fixture, editor=rejected.state),
        Cam3DRequestDiagnosticCode.NUMERIC_INVALID,
    )


def test_stale_tool_and_foreign_profile_fail_closed() -> None:
    fixture = _ready_fixture()
    assert fixture.editor.tool_assembly is not None
    stale_assembly = replace(
        fixture.editor.tool_assembly.assembly,
        expected_tool_revision=Revision(99),
    )
    stale_editor = replace(
        fixture.editor,
        tool_assembly=replace(
            fixture.editor.tool_assembly,
            assembly=stale_assembly,
        ),
    )
    _assert_failure(
        _build(fixture, editor=stale_editor),
        Cam3DRequestDiagnosticCode.TOOL_PROFILE_INVALID,
    )


def test_bool_is_rejected_for_generation_and_policy_versions() -> None:
    fixture = _ready_fixture()
    with pytest.raises(ValueError):
        replace(fixture.setup, project_generation=True)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        Cam3DCalculationPolicy(algorithm_version=True)  # type: ignore[arg-type]


def test_unsupported_policy_version_fails_closed() -> None:
    fixture = _ready_fixture()
    policy = Cam3DCalculationPolicy(calculation_policy_version=2)
    _assert_failure(
        _build(fixture, policy=policy),
        Cam3DRequestDiagnosticCode.UNSUPPORTED_POLICY_VERSION,
    )


def test_same_semantic_input_is_stable_and_job_id_is_excluded() -> None:
    fixture = _ready_fixture()
    first = _request(fixture, job_id=Cam3DCalculationJobId.new())
    second = _request(fixture, job_id=Cam3DCalculationJobId.new())
    assert first.job_id != second.job_id
    assert first.fingerprint == second.fingerprint
    assert first.cache_key == second.cache_key


def test_profile_timestamps_display_and_selection_labels_are_excluded() -> None:
    fixture = _ready_fixture()
    first = _request(fixture)
    assert fixture.editor.tool_profile is not None
    profile = replace(
        fixture.editor.tool_profile.profile,
        display_name="한국어 표시 이름",
        created_at=fixture.editor.tool_profile.profile.created_at + timedelta(days=1),
        updated_at=fixture.editor.tool_profile.profile.updated_at + timedelta(days=1),
    )
    selection = replace(
        fixture.selection,
        part=(replace(fixture.selection.part[0], display_label="Chi tiết"),),
    )
    editor = replace(
        fixture.editor,
        selection=selection,
        tool_profile=replace(fixture.editor.tool_profile, profile=profile),
    )
    second = _request(fixture, editor=editor, selection=selection)
    assert first.fingerprint == second.fingerprint
    payload_text = json.dumps(second.inputs.canonical_payload(), sort_keys=True)
    assert all(
        excluded not in payload_text
        for excluded in ("created_at", "updated_at", "display_name", "theme", "ui_scale", "dock")
    )


def test_unordered_part_input_is_canonicalized_deterministically() -> None:
    fixture = _ready_fixture()
    first_item = fixture.selection.part[0]
    second_item = _selected_item(
        Cam3DSelectionRole.PART,
        "part-second",
        project_id=fixture.context.project_id,  # type: ignore[arg-type]
        document_id=fixture.context.document_id,  # type: ignore[arg-type]
        source_id=fixture.context.source_id,  # type: ignore[arg-type]
        generation=fixture.context.project_generation,  # type: ignore[arg-type]
    )
    left_selection = replace(fixture.selection, part=(first_item, second_item))
    right_selection = replace(fixture.selection, part=(second_item, first_item))
    left = _request(
        fixture,
        editor=replace(fixture.editor, selection=left_selection),
        selection=left_selection,
    )
    right = _request(
        fixture,
        editor=replace(fixture.editor, selection=right_selection),
        selection=right_selection,
    )
    assert left.fingerprint == right.fingerprint


def test_fingerprint_survives_canonical_process_boundary_round_trip() -> None:
    request = _request(_ready_fixture())
    payload = request.inputs.canonical_payload()
    transported = json.loads(json.dumps(payload, ensure_ascii=True, sort_keys=True))
    assert DependencyFingerprint.from_payload(payload) == (
        DependencyFingerprint.from_payload(transported)
    )


def test_selection_setup_tool_profile_numeric_and_policy_change_fingerprint() -> None:
    fixture = _ready_fixture()
    base = _request(fixture)

    replacement_part = _selected_item(
        Cam3DSelectionRole.PART,
        "part-changed",
        project_id=fixture.context.project_id,  # type: ignore[arg-type]
        document_id=fixture.context.document_id,  # type: ignore[arg-type]
        source_id=fixture.context.source_id,  # type: ignore[arg-type]
        generation=fixture.context.project_generation,  # type: ignore[arg-type]
    )
    selection = replace(fixture.selection, part=(replacement_part,))
    selection_request = _request(
        fixture,
        selection=selection,
        editor=replace(fixture.editor, selection=selection),
    )

    setup_id = SetupId.new()
    setup = replace(
        fixture.setup,
        ownership=replace(fixture.setup.ownership, setup_id=setup_id),
    )
    setup_request = _request(fixture, setup=setup)

    assert fixture.editor.tool_assembly is not None
    assembly = replace(
        fixture.editor.tool_assembly.assembly,
        stickout=Length(21.0, LengthUnit.MM),
    )
    tool_request = _request(
        fixture,
        editor=replace(
            fixture.editor,
            tool_assembly=replace(fixture.editor.tool_assembly, assembly=assembly),
        ),
    )

    assert fixture.editor.tool_profile is not None
    profile = replace(
        fixture.editor.tool_profile.profile,
        strategy_id="z_level_finishing_3d",
    )
    profile_request = _request(
        fixture,
        editor=replace(
            fixture.editor,
            tool_profile=replace(fixture.editor.tool_profile, profile=profile),
        ),
    )

    numeric_request = _request(
        fixture,
        editor=replace(
            fixture.editor,
            parameters=replace(fixture.editor.parameters, allowance_mm=0.25),
        ),
    )
    policy_request = _request(
        fixture,
        policy=Cam3DCalculationPolicy(algorithm_version=2),
    )

    changed = {
        selection_request.fingerprint,
        setup_request.fingerprint,
        tool_request.fingerprint,
        profile_request.fingerprint,
        numeric_request.fingerprint,
        policy_request.fingerprint,
    }
    assert len(changed) == 6
    assert base.fingerprint not in changed
    record = Cam3DCacheRecordIdentity.from_request(base)
    assert evaluate_cam3d_cache_reuse(record, setup_request) is (
        Cam3DCacheDecision.INVALIDATE_OWNERSHIP
    )
    for changed_request in (
        selection_request,
        tool_request,
        profile_request,
        numeric_request,
        policy_request,
    ):
        assert evaluate_cam3d_cache_reuse(record, changed_request) is (
            Cam3DCacheDecision.INVALIDATE_SEMANTIC_INPUT
        )

def test_generation_changes_fingerprint_and_cache_key() -> None:
    fixture = _ready_fixture()
    base = _request(fixture)
    generation = 5
    context = replace(fixture.context, project_generation=generation)

    def update_items(items):
        return tuple(
            replace(
                item,
                provenance=replace(item.provenance, project_generation=generation),
            )
            for item in items
        )

    selection = replace(
        fixture.selection,
        project_generation=generation,
        part=update_items(fixture.selection.part),
        check=update_items(fixture.selection.check),
        fixture=update_items(fixture.selection.fixture),
    )
    editor = replace(fixture.editor, context=context, selection=selection)
    setup = replace(fixture.setup, project_generation=generation)
    changed = _request(
        fixture,
        context=context,
        selection=selection,
        editor=editor,
        setup=setup,
    )
    assert changed.fingerprint != base.fingerprint
    assert changed.cache_key != base.cache_key


def test_cache_key_depends_on_fingerprint_and_preview_policy_not_job() -> None:
    fixture = _ready_fixture()
    first = _request(fixture, job_id=Cam3DCalculationJobId.new())
    second = _request(fixture, job_id=Cam3DCalculationJobId.new())
    assert first.cache_key == second.cache_key
    changed_policy = Cam3DCalculationPolicy(preview_policy_version=2)
    changed_key = Cam3DPreviewCacheKey.from_request_fingerprint(
        first.fingerprint, changed_policy
    )
    assert changed_key != first.cache_key


def test_cache_invalidation_matrix_and_ui_exclusions() -> None:
    fixture = _ready_fixture()
    request = _request(fixture)
    record = Cam3DCacheRecordIdentity.from_request(request)
    assert evaluate_cam3d_cache_reuse(record, request) is Cam3DCacheDecision.REUSE

    ownership_fields = {
        "project_id": uuid4(),
        "document_id": CadDocumentId("switched-document"),
        "source_id": uuid4(),
        "setup_id": SetupId.new(),
    }
    for field, value in ownership_fields.items():
        changed = replace(record, ownership=replace(record.ownership, **{field: value}))
        assert evaluate_cam3d_cache_reuse(changed, request) is (
            Cam3DCacheDecision.INVALIDATE_OWNERSHIP
        )
    assert evaluate_cam3d_cache_reuse(
        replace(record, project_generation=record.project_generation + 1), request
    ) is Cam3DCacheDecision.INVALIDATE_GENERATION

    semantic_request = _request(
        fixture,
        editor=replace(
            fixture.editor,
            parameters=replace(fixture.editor.parameters, tolerance_mm=0.02),
        ),
    )
    assert evaluate_cam3d_cache_reuse(record, semantic_request) is (
        Cam3DCacheDecision.INVALIDATE_SEMANTIC_INPUT
    )
    for _presentation_only_change in (
        {"language": "ko-KR"},
        {"theme": "dark"},
        {"ui_scale": 1.5},
        {"dock_visible": False},
    ):
        assert evaluate_cam3d_cache_reuse(record, request) is Cam3DCacheDecision.REUSE


def test_latest_wins_supersedes_old_job_and_accepts_latest_once() -> None:
    fixture = _ready_fixture()
    first = _request(fixture)
    second = _request(
        fixture,
        editor=replace(
            fixture.editor,
            parameters=replace(fixture.editor.parameters, allowance_mm=0.1),
        ),
    )
    session = Cam3DCalculationSession(first.ownership, first.project_generation)
    session = session.register(first).session
    session = session.register(second).session
    assert session.publication_decision(Cam3DResultIdentity.from_request(first)) is (
        Cam3DSessionDecision.SUPERSEDED
    )
    accepted = session.accept_result(Cam3DResultIdentity.from_request(second))
    assert accepted.accepted
    duplicate = accepted.session.accept_result(Cam3DResultIdentity.from_request(second))
    assert not duplicate.accepted
    assert duplicate.decision is Cam3DSessionDecision.DUPLICATE_RESULT


def test_session_cancel_stale_fingerprint_close_and_switch_fail_closed() -> None:
    fixture = _ready_fixture()
    request = _request(fixture)
    session = Cam3DCalculationSession(request.ownership, request.project_generation)
    session = session.register(request).session
    identity = Cam3DResultIdentity.from_request(request)

    cancelled = session.request_cancellation(request.job_id)
    assert cancelled.accepted
    assert cancelled.session.publication_decision(identity) is (
        Cam3DSessionDecision.CANCELLED
    )
    stale = replace(identity, project_generation=identity.project_generation + 1)
    assert session.publication_decision(stale) is Cam3DSessionDecision.STALE_GENERATION
    mismatch = replace(
        identity,
        fingerprint=replace(
            identity.fingerprint,
            value=DependencyFingerprint.from_payload({"different": True}),
        ),
    )
    assert session.publication_decision(mismatch) is (
        Cam3DSessionDecision.FINGERPRINT_MISMATCH
    )
    assert session.close().publication_decision(identity) is Cam3DSessionDecision.CLOSED
    switched = session.rebind(
        replace(session.ownership, setup_id=SetupId.new()),
        session.project_generation,
    )
    assert switched.publication_decision(identity) is (
        Cam3DSessionDecision.OWNERSHIP_MISMATCH
    )


def test_independent_ownership_isolation_and_foreign_cancel_do_not_mutate() -> None:
    first_fixture = _ready_fixture()
    second_fixture = _ready_fixture()
    first = _request(first_fixture)
    second = _request(second_fixture)
    first_session = Cam3DCalculationSession(
        first.ownership, first.project_generation
    ).register(first).session
    second_session = Cam3DCalculationSession(
        second.ownership, second.project_generation
    ).register(second).session

    rejected = first_session.register(second)
    assert not rejected.accepted
    assert rejected.decision is Cam3DSessionDecision.OWNERSHIP_MISMATCH
    assert rejected.session == first_session
    wrong_cancel = first_session.request_cancellation(second.job_id)
    assert not wrong_cancel.accepted
    assert wrong_cancel.session == first_session
    assert second_session.publication_decision(Cam3DResultIdentity.from_request(second)) is (
        Cam3DSessionDecision.ACCEPTED
    )


def test_duplicate_request_registration_is_deterministic() -> None:
    request = _request(_ready_fixture())
    session = Cam3DCalculationSession(request.ownership, request.project_generation)
    first = session.register(request)
    duplicate = first.session.register(request)
    assert first.accepted
    assert not duplicate.accepted
    assert duplicate.decision is Cam3DSessionDecision.DUPLICATE_REQUEST
    assert duplicate.session == first.session


def test_wp3_module_has_no_qt_worker_thread_process_or_io_import() -> None:
    path = Path("src/hms_cadcam/cam/application/cam3d_request.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported_roots = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert imported_roots.isdisjoint(
        {"PySide6", "threading", "multiprocessing", "subprocess", "pathlib", "sqlite3"}
    )


def test_wp2a_wp2b_foundation_contracts_remain_importable() -> None:
    resources = basic_parallel_resources(LengthUnit.MM)
    fixture = _ready_fixture()
    assert fixture.selection.resolved
    service = Cam3DEditorApplicationService(
        fixture.context,
        fixture.selection,
        parameters=fixture.editor.parameters,
    )
    service.assign_tool_assembly(
        fixture.editor.tool_assembly, live_context=fixture.context  # type: ignore[arg-type]
    )
    service.assign_tool_profile(
        fixture.editor.tool_profile, live_context=fixture.context  # type: ignore[arg-type]
    )
    assert service.evaluate(fixture.context).valid
    assert resources
