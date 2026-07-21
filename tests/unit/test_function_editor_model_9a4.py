"""Stage 9A.4 typed schema, draft/apply and preference-state tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QSettings

from hms_cadcam.ui.function_editor import (
    ApplicabilityOperator,
    FunctionEditorApplicability,
    FunctionEditorDiagnosticSeverity,
    FunctionEditorDraftState,
    FunctionEditorDraftStatus,
    FunctionEditorField,
    FunctionEditorFieldKind,
    FunctionEditorRegistry,
    FunctionEditorSchema,
    FunctionEditorSection,
    FunctionEditorStateStore,
    FunctionEditorStrategyKey,
    FunctionEditorSummary,
    FunctionEditorUserState,
    FunctionEditorValueSource,
    ParameterDisclosureLevel,
    build_contour_reference_schema,
)


def test_reference_schema_has_stable_ids_and_deterministic_order() -> None:
    schema = build_contour_reference_schema()

    assert [item.section_id for item in schema.ordered_sections] == [
        "basic",
        "geometry",
        "tool",
        "cutting",
        "levels",
        "linking",
        "advanced",
        "expert",
    ]
    assert len({item.field_id for item in schema.fields}) == len(schema.fields)
    assert schema.field("safe_z").source is FunctionEditorValueSource.SETUP
    assert schema.field("stepdown").default_label == "HMS reference v1"
    assert schema.strategy == FunctionEditorStrategyKey("contour_reference_9a4")


@pytest.mark.parametrize("bad_id", ["", "Has Space", "9starts-with-number", "x/y"])
def test_invalid_stable_field_id_is_rejected(bad_id: str) -> None:
    with pytest.raises(ValueError):
        FunctionEditorField(field_id=bad_id, label="Bad")


def test_duplicate_section_and_field_ids_are_rejected() -> None:
    field = FunctionEditorField("same", "Same")
    duplicate_section = FunctionEditorSection("section", "Section", (field, field))
    with pytest.raises(ValueError, match="Duplicate field ID"):
        FunctionEditorSchema(
            "duplicate_fields",
            FunctionEditorStrategyKey("duplicate_fields"),
            FunctionEditorSummary("Duplicate", "test"),
            (duplicate_section,),
        )

    section = FunctionEditorSection("same_section", "Section", (field,))
    with pytest.raises(ValueError, match="Duplicate section ID"):
        FunctionEditorSchema(
            "duplicate_sections",
            FunctionEditorStrategyKey("duplicate_sections"),
            FunctionEditorSummary("Duplicate", "test"),
            (section, section),
        )


def test_unknown_applicability_dependency_is_rejected() -> None:
    field = FunctionEditorField(
        "dependent",
        "Dependent",
        applicable_when=FunctionEditorApplicability(
            "missing", ApplicabilityOperator.TRUTHY
        ),
    )
    with pytest.raises(ValueError, match="Unknown field applicability"):
        FunctionEditorSchema(
            "unknown_dependency",
            FunctionEditorStrategyKey("unknown_dependency"),
            FunctionEditorSummary("Unknown", "test"),
            (FunctionEditorSection("basic", "Basic", (field,)),),
        )


def test_typed_registry_never_falls_back_by_raw_text() -> None:
    registry = FunctionEditorRegistry()
    schema = build_contour_reference_schema()
    registry.register(schema)

    assert registry.resolve(schema.strategy) is schema
    assert registry.resolve(FunctionEditorStrategyKey("not_migrated")) is None
    assert registry.strategies == (schema.strategy,)
    with pytest.raises(ValueError, match="already registered"):
        registry.register(schema)


def test_disclosure_is_a_maximum_and_applicability_hides_stale_values() -> None:
    schema = build_contour_reference_schema()
    state = FunctionEditorDraftState(schema)
    basic_sections = schema.visible_sections(
        dict(state.values), ParameterDisclosureLevel.BASIC
    )

    assert "advanced" not in {item.section_id for item in basic_sections}
    assert "expert" not in {item.section_id for item in basic_sections}
    assert "lead_length" in state.applicable_field_ids()
    state.edit("use_lead", False)
    assert "lead_length" not in state.applicable_field_ids()
    assert "lead_length" not in state.applicable_snapshot()
    state.edit("use_lead", True)
    assert state.values["lead_length"] == "1.0"


def test_draft_initial_edit_invalid_and_reset_contract() -> None:
    state = FunctionEditorDraftState(build_contour_reference_schema())
    applied = dict(state.applied_values)

    assert state.status is FunctionEditorDraftStatus.NO_CHANGES
    assert not state.is_dirty
    state.edit("stepdown", "0")
    assert state.status is FunctionEditorDraftStatus.MODIFIED
    assert state.is_dirty
    diagnostics = state.validate()
    assert state.status is FunctionEditorDraftStatus.INVALID
    assert diagnostics[0].field_id == "stepdown"
    assert diagnostics[0].severity is FunctionEditorDiagnosticSeverity.ERROR
    assert dict(state.applied_values) == applied

    state.reset_field("stepdown")
    assert state.values["stepdown"] == applied["stepdown"]
    assert not state.is_dirty

    state.edit("feed_rate", "700")
    state.edit("spindle_rpm", "5000")
    state.reset_section("cutting")
    assert not state.is_dirty


def test_restore_recommended_defaults_never_auto_applies() -> None:
    schema = build_contour_reference_schema()
    state = FunctionEditorDraftState(schema, {"stepdown": "3.0"})
    state.edit("stepdown", "4.0")

    state.restore_recommended_defaults("cutting")

    assert state.values["stepdown"] == "2.0"
    assert state.applied_values["stepdown"] == "3.0"
    assert state.status is FunctionEditorDraftStatus.MODIFIED


def test_apply_success_passes_one_immutable_applicable_snapshot() -> None:
    state = FunctionEditorDraftState(build_contour_reference_schema())
    state.edit("feed_rate", "650")
    state.edit("use_lead", False)
    received: list[dict[str, object]] = []

    def apply(values) -> bool:
        received.append(dict(values))
        with pytest.raises(TypeError):
            values["feed_rate"] = "mutate"
        return True

    assert state.apply(apply)
    assert state.status is FunctionEditorDraftStatus.APPLIED
    assert state.applied_values["feed_rate"] == "650"
    assert "lead_length" not in received[0]
    assert not state.is_dirty


def test_apply_failure_rolls_back_presentation_snapshot() -> None:
    state = FunctionEditorDraftState(build_contour_reference_schema())
    before = dict(state.applied_values)
    state.edit("feed_rate", "650")

    def fail(_values) -> None:
        raise RuntimeError("service transaction rolled back")

    assert not state.apply(fail)
    assert dict(state.applied_values) == before
    assert state.values["feed_rate"] == "650"
    assert state.is_dirty
    assert state.diagnostics[-1].code == "apply.failed"


def test_invalid_apply_never_calls_domain_callback() -> None:
    state = FunctionEditorDraftState(build_contour_reference_schema())
    state.edit("final_depth", "5")
    calls = 0

    def apply(_values) -> bool:
        nonlocal calls
        calls += 1
        return True

    assert not state.apply(apply)
    assert calls == 0
    assert state.status is FunctionEditorDraftStatus.INVALID


def test_calculation_uses_applied_state_only_and_never_unapplied_draft() -> None:
    state = FunctionEditorDraftState(build_contour_reference_schema())
    assert state.can_calculate
    assert state.calculation_snapshot()["feed_rate"] == "500"

    state.edit("feed_rate", "900")
    assert not state.can_calculate
    with pytest.raises(RuntimeError, match="applied state"):
        state.calculation_snapshot()
    assert state.applied_values["feed_rate"] == "500"

    assert state.apply()
    assert state.calculation_snapshot()["feed_rate"] == "900"
    state.mark_stale()
    assert not state.can_calculate


def test_preview_token_is_rejected_after_edit_operation_or_project_switch() -> None:
    schema = build_contour_reference_schema()
    first = FunctionEditorDraftState(
        schema, project_key="project-a", operation_key="operation-a", generation=4
    )
    request = first.preview_request()
    assert first.accepts_preview(request)
    first.edit("feed_rate", "501")
    assert not first.accepts_preview(request)

    other_operation = FunctionEditorDraftState(
        schema, project_key="project-a", operation_key="operation-b", generation=4
    )
    other_project = FunctionEditorDraftState(
        schema, project_key="project-b", operation_key="operation-a", generation=4
    )
    assert not other_operation.accepts_preview(request)
    assert not other_project.accepts_preview(request)


def test_non_primitive_qt_or_callback_state_is_rejected() -> None:
    state = FunctionEditorDraftState(build_contour_reference_schema())
    with pytest.raises(TypeError, match="callbacks"):
        state.edit("operation_name", lambda: None)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="non-serializable"):
        state.edit("operation_name", object())  # type: ignore[arg-type]


def test_user_state_is_versioned_and_contains_no_draft_values(tmp_path: Path) -> None:
    settings = QSettings(str(tmp_path / "ui.ini"), QSettings.Format.IniFormat)
    store = FunctionEditorStateStore(settings)
    schema = build_contour_reference_schema()
    store.save(
        schema,
        FunctionEditorUserState(
            disclosure_level=ParameterDisclosureLevel.EXPERT,
            expanded_sections=("basic", "expert"),
            has_expansion_state=True,
            help_visible=True,
        ),
    )

    loaded = store.load(schema)
    assert loaded.disclosure_level is ParameterDisclosureLevel.EXPERT
    assert loaded.expanded_sections == ("basic", "expert")
    assert loaded.help_visible
    settings.sync()
    text = (tmp_path / "ui.ini").read_text(encoding="utf-8")
    assert "feed_rate" not in text
    assert "operation_name" not in text


def test_state_version_mismatch_removes_only_function_editor_group(
    tmp_path: Path,
) -> None:
    settings = QSettings(str(tmp_path / "version.ini"), QSettings.Format.IniFormat)
    settings.setValue("unrelated/value", "keep")
    settings.setValue("function_editor_9a4/version", 999)
    settings.setValue("function_editor_9a4/old/draft", "must disappear")
    FunctionEditorStateStore(settings)

    assert settings.value("unrelated/value") == "keep"
    assert settings.value("function_editor_9a4/old/draft") is None
    assert settings.value("function_editor_9a4/version", type=int) == 1
