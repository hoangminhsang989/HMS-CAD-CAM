"""Persistent XCAF occurrence keys and SQLite v3 appearance rows."""

from __future__ import annotations

import sqlite3
from dataclasses import fields, is_dataclass, replace
from pathlib import Path
from uuid import uuid4

from hms_cadcam.cad.models import CadDocumentTree, CadGeometryKind, CadObjectId
from hms_cadcam.cad.ocp import OcpCadKernel
from hms_cadcam.cad.persistent_keys import (
    PersistentKeyScheme,
    PersistentXcafOccurrenceKey,
    build_persistent_object_map,
)
from hms_cadcam.project.cad_state import (
    CadViewState,
    ObjectAppearanceOverride,
    PersistentObjectAppearance,
)
from hms_cadcam.project.cad_state_store import CadViewStateStore
from hms_cadcam.project.database import ProjectDatabase
from hms_cadcam.viewer.models import ObjectColor
from spikes.xcaf_step.fixture import write_xcaf_step_fixture


def _import_tree(source: Path) -> tuple[OcpCadKernel, CadDocumentTree]:
    kernel = OcpCadKernel()
    result = kernel.import_step(source)
    assert result.document_id is not None
    return kernel, kernel.get_document_tree(result.document_id)


def test_occurrence_keys_repeat_after_reimport_and_distinguish_instances(
    tmp_path: Path,
) -> None:
    source = tmp_path / "assembly.step"
    write_xcaf_step_fixture(source)
    source_id = uuid4()
    _first_kernel, first_tree = _import_tree(source)
    _second_kernel, second_tree = _import_tree(source)
    first = build_persistent_object_map(
        source_id, CadGeometryKind.BREP, first_tree
    )
    second = build_persistent_object_map(
        source_id, CadGeometryKind.BREP, second_tree
    )

    assert set(first.by_persistent) == set(second.by_persistent)
    repeated_nodes = [
        node
        for node in first_tree.presentation_nodes
        if node.product_name == "Repeated Product"
    ]
    repeated_keys = [first.by_runtime[node.object_id] for node in repeated_nodes]
    assert len(repeated_keys) == 2
    assert all(isinstance(key, PersistentXcafOccurrenceKey) for key in repeated_keys)
    assert repeated_keys[0].occurrence_path != repeated_keys[1].occurrence_path
    assert repeated_keys[0].product_identity == repeated_keys[1].product_identity
    assert all(
        key.key_scheme is PersistentKeyScheme.XCAF_OCCURRENCE
        for key in repeated_keys
    )
    serialized = repr(tuple(first.by_persistent))
    assert "ocp:" not in serialized
    assert "xcaf-object" not in serialized
    assert "0:1:" not in serialized


def test_ambiguous_xcaf_occurrence_path_is_not_mapped(tmp_path: Path) -> None:
    source = tmp_path / "ambiguous.step"
    write_xcaf_step_fixture(source)
    _kernel, tree = _import_tree(source)
    assembly = tree.root.children[0]
    leaf = next(node for node in assembly.children if node.has_presentation)
    duplicate = replace(
        leaf,
        object_id=CadObjectId(f"{tree.document_id}:duplicate"),
        occurrence_id=replace(leaf.occurrence_id, value="duplicate-occurrence"),
    )
    changed_assembly = replace(assembly, children=(*assembly.children, duplicate))
    changed_tree = CadDocumentTree(
        tree.document_id,
        replace(tree.root, children=(changed_assembly,)),
    )

    mapping = build_persistent_object_map(
        uuid4(), CadGeometryKind.BREP, changed_tree
    )

    assert leaf.object_id not in mapping.by_runtime
    assert duplicate.object_id not in mapping.by_runtime
    assert mapping.ambiguous_nodes >= 2


def test_xcaf_override_round_trip_uses_nullable_v3_columns(tmp_path: Path) -> None:
    source = tmp_path / "round-trip.step"
    write_xcaf_step_fixture(source)
    _kernel, tree = _import_tree(source)
    source_id = uuid4()
    mapping = build_persistent_object_map(
        source_id, CadGeometryKind.BREP, tree
    )
    key = next(iter(mapping.by_persistent))
    assert isinstance(key, PersistentXcafOccurrenceKey)
    override = ObjectAppearanceOverride(
        visible=False,
        transparency=0.35,
    )
    state = CadViewState(
        source_id,
        object_appearances=(PersistentObjectAppearance(key, override),),
    )
    database_path = tmp_path / "project.db"
    ProjectDatabase().initialize(database_path)
    store = CadViewStateStore()

    with store.transaction(database_path) as connection:
        store.replace_all(connection, (state,), (source_id,))

    with sqlite3.connect(database_path) as connection:
        row = connection.execute(
            """
            SELECT visible, color_r, color_g, color_b, transparency
            FROM cad_xcaf_occurrence_appearance
            """
        ).fetchone()
        assert row == (0, None, None, None, 0.35)
        assert connection.execute(
            "SELECT COUNT(*) FROM cad_object_appearance"
        ).fetchone()[0] == 0
    assert store.load(database_path, (source_id,))[source_id] == state

    with store.transaction(database_path) as connection:
        store.replace_all(connection, (CadViewState(source_id),), (source_id,))
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM cad_xcaf_occurrence_appearance"
        ).fetchone()[0] == 0


def test_source_color_is_not_part_of_empty_or_visibility_override() -> None:
    override = ObjectAppearanceOverride(visible=False)
    assert override.color is None
    assert override.transparency is None
    assert ObjectAppearanceOverride().is_empty
    color = ObjectColor(0.1, 0.2, 0.3)
    assert ObjectAppearanceOverride(color=color).color == color


def test_cad_state_load_releases_database_file(tmp_path: Path) -> None:
    database_path = tmp_path / "project.db"
    moved_path = tmp_path / "project-loaded.db"
    ProjectDatabase().initialize(database_path)

    assert CadViewStateStore().load(database_path, ()) == {}
    database_path.replace(moved_path)

    assert moved_path.is_file()
    assert not database_path.exists()


def test_public_xcaf_persistence_models_are_native_free(tmp_path: Path) -> None:
    source = tmp_path / "public.step"
    write_xcaf_step_fixture(source)
    _kernel, tree = _import_tree(source)
    mapping = build_persistent_object_map(uuid4(), CadGeometryKind.BREP, tree)
    key = next(iter(mapping.by_persistent))
    values = (
        key,
        ObjectAppearanceOverride(color=ObjectColor(0.2, 0.3, 0.4)),
    )
    for value in values:
        assert is_dataclass(value)
        for field in fields(value):
            candidate = getattr(value, field.name)
            assert not type(candidate).__module__.startswith(
                ("OCP", "PySide6")
            )
