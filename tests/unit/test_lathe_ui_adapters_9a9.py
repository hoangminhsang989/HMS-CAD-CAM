"""Stage 9A.9 Tool and geometry bridge acceptance tests."""

from __future__ import annotations

from dataclasses import replace

import pytest

from hms_cadcam.cam.lathe.capabilities import LatheToolReference
from hms_cadcam.cam.lathe.types import (
    LatheGeometryKind,
    LatheStrategyId,
    LatheToolCapability,
)
from hms_cadcam.ui.lathe_adapters import (
    LatheGeometrySelectionError,
    LatheSelectionContext,
    ProjectLatheToolCatalog,
    lathe_geometry_from_selection,
)
from hms_cadcam.viewer.models import SelectionMode

from _lathe_ui_fixtures import selection_context, tool_catalog_for


@pytest.mark.parametrize(
    ("mode", "strategy", "expected"),
    (
        (SelectionMode.FACE, LatheStrategyId.FACE, LatheGeometryKind.FACE),
        (SelectionMode.EDGE, LatheStrategyId.OD_ROUGH, LatheGeometryKind.EDGE),
        (SelectionMode.WIRE, LatheStrategyId.OD_THREAD, LatheGeometryKind.PROFILE),
        (SelectionMode.VERTEX, LatheStrategyId.AXIAL_DRILL, LatheGeometryKind.POINT),
    ),
)
def test_exact_native_free_selection_mappings(mode, strategy, expected) -> None:
    context = selection_context(mode)
    binding = lathe_geometry_from_selection(
        context,
        strategy,
        expected_document_id=context.document_id,
        expected_source_id=context.source_id,
        expected_generation=context.generation,
    )
    assert binding.kind is expected
    assert binding.entity_ids == tuple(
        item.selection_id for item in context.selections
    )
    assert "OCP" not in repr(binding)


@pytest.mark.parametrize(
    ("context_factory", "strategy", "code"),
    (
        (
            lambda: selection_context(SelectionMode.SOLID),
            LatheStrategyId.FACE,
            "lathe.geometry.selection_kind_unavailable",
        ),
        (
            lambda: selection_context(SelectionMode.VERTEX),
            LatheStrategyId.FACE,
            "lathe.geometry.selection_incompatible",
        ),
        (
            lambda: selection_context(
                SelectionMode.FACE,
                selection_ids=("same", "same"),
            ),
            LatheStrategyId.FACE,
            "lathe.geometry.selection_duplicate",
        ),
    ),
)
def test_selection_bridge_rejects_unproved_or_invalid_geometry(
    context_factory, strategy, code
) -> None:
    context = context_factory()
    with pytest.raises(LatheGeometrySelectionError, match=code):
        lathe_geometry_from_selection(
            context,
            strategy,
            expected_document_id=context.document_id,
            expected_source_id=context.source_id,
            expected_generation=context.generation,
        )


def test_selection_bridge_rejects_empty_stale_and_mixed_inputs() -> None:
    context = selection_context()
    empty = LatheSelectionContext(
        context.document_id, context.source_id, context.generation, ()
    )
    with pytest.raises(LatheGeometrySelectionError, match="selection_empty"):
        lathe_geometry_from_selection(
            empty,
            LatheStrategyId.FACE,
            expected_document_id=empty.document_id,
            expected_source_id=empty.source_id,
            expected_generation=empty.generation,
        )
    with pytest.raises(LatheGeometrySelectionError, match="selection_stale"):
        lathe_geometry_from_selection(
            context,
            LatheStrategyId.FACE,
            expected_document_id=context.document_id,
            expected_source_id=context.source_id,
            expected_generation=context.generation + 1,
        )
    mixed_parts = (
        selection_context(SelectionMode.FACE).selections[0],
        selection_context(SelectionMode.EDGE, selection_ids=("edge-2",)).selections[0],
    )
    mixed = LatheSelectionContext(
        context.document_id,
        context.source_id,
        context.generation,
        mixed_parts,
    )
    with pytest.raises(LatheGeometrySelectionError, match="selection_mixed"):
        lathe_geometry_from_selection(
            mixed,
            LatheStrategyId.FACE,
            expected_document_id=mixed.document_id,
            expected_source_id=mixed.source_id,
            expected_generation=mixed.generation,
        )


@pytest.mark.parametrize("capability", tuple(LatheToolCapability))
def test_explicit_typed_capability_registry_binds_canonical_references(
    capability: LatheToolCapability,
) -> None:
    catalog, reference = tool_catalog_for(capability)
    choices = catalog.choices()
    assert len(choices) == 1
    assert choices[0].reference == reference
    assert choices[0].supports(capability)
    resolution = catalog.resolve(reference)
    assert resolution.exists and resolution.current
    assert capability in resolution.capabilities
    assert resolution.tool_revision is not None
    assert resolution.assembly_revision is not None


def test_tool_catalog_never_infers_turning_capability_from_display_name() -> None:
    catalog, reference = tool_catalog_for(LatheToolCapability.OD_TURNING)
    tool = catalog.tools[0]
    assembly = catalog.assemblies[0]
    no_evidence = ProjectLatheToolCatalog((tool,), (), (assembly,))
    resolution = no_evidence.resolve(reference)
    assert resolution.exists and resolution.current
    assert not resolution.capabilities
    assert not no_evidence.choices()[0].supports(LatheToolCapability.OD_TURNING)


def test_drill_family_is_exact_typed_axial_capability_and_stale_is_closed() -> None:
    catalog, reference = tool_catalog_for(LatheToolCapability.AXIAL_DRILLING)
    assert catalog.resolve(reference).capabilities == frozenset(
        {LatheToolCapability.AXIAL_DRILLING}
    )
    tool = catalog.tools[0]
    assembly = catalog.assemblies[0]
    stale = replace(
        assembly,
        expected_tool_revision=assembly.expected_tool_revision.next(),
    )
    stale_catalog = ProjectLatheToolCatalog((tool,), (), (stale,))
    stale_reference = LatheToolReference(tool.tool_id, None, stale.assembly_id)
    resolution = stale_catalog.resolve(stale_reference)
    assert resolution.exists
    assert not resolution.current
    assert not stale_catalog.choices()[0].supports(
        LatheToolCapability.AXIAL_DRILLING
    )


def test_missing_tool_or_assembly_fails_closed() -> None:
    catalog, reference = tool_catalog_for(LatheToolCapability.FACE_TURNING)
    empty = ProjectLatheToolCatalog()
    assert not empty.resolve(reference).exists
    assert empty.choices() == ()
    wrong = LatheToolReference(
        reference.tool_id,
        reference.profile_id,
        type(reference.assembly_id).new(),
    )
    assert not catalog.resolve(wrong).exists
