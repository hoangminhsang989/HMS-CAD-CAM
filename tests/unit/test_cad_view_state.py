"""Persistence-key and SQLite CAD view-state tests."""

from __future__ import annotations

from contextlib import closing
import sqlite3
from dataclasses import fields, is_dataclass
from uuid import uuid4

from hms_cadcam.cad.models import (
    BoundingBox,
    CadDocumentId,
    CadDocumentTree,
    CadGeometryKind,
    CadObjectId,
    CadObjectKind,
    CadObjectNode,
)
from hms_cadcam.cad.persistent_keys import (
    PersistentCadObjectKey,
    TopologyPath,
    TopologyPathVersion,
    build_persistent_object_map,
)
from hms_cadcam.project.cad_state import CadViewState, PersistentObjectAppearance
from hms_cadcam.project.cad_state_store import CadViewStateStore
from hms_cadcam.project.database import ProjectDatabase
from hms_cadcam.viewer.models import ObjectAppearance, ObjectColor


def _tree(
    document_value: str,
    *,
    reverse: bool = False,
    first_max: float = 2,
) -> CadDocumentTree:
    document_id = CadDocumentId(document_value)
    first = CadObjectNode(
        document_id,
        CadObjectId(f"{document_value}:runtime-a"),
        CadObjectKind.SOLID,
        "Runtime A",
        BoundingBox(0, 0, 0, first_max, first_max, first_max),
        has_presentation=True,
    )
    second = CadObjectNode(
        document_id,
        CadObjectId(f"{document_value}:runtime-b"),
        CadObjectKind.SOLID,
        "Runtime B",
        BoundingBox(5, 5, 5, 7, 7, 7),
        has_presentation=True,
    )
    children = (second, first) if reverse else (first, second)
    root = CadObjectNode(
        document_id,
        CadObjectId(f"{document_value}:document"),
        CadObjectKind.DOCUMENT,
        "Document",
        BoundingBox(0, 0, 0, 7, 7, 7),
        children,
    )
    return CadDocumentTree(document_id, root)


def test_topology_paths_are_deterministic_without_runtime_or_traversal_identity() -> None:
    source_id = uuid4()
    first = build_persistent_object_map(source_id, CadGeometryKind.BREP, _tree("doc:one"))
    second = build_persistent_object_map(
        source_id,
        CadGeometryKind.BREP,
        _tree("doc:two", reverse=True),
    )

    assert set(first.by_persistent) == set(second.by_persistent)
    assert all("doc:" not in key.topology_path.value for key in first.by_persistent)


def test_changed_topology_signature_does_not_resolve_to_the_old_object() -> None:
    source_id = uuid4()
    original = build_persistent_object_map(
        source_id, CadGeometryKind.BREP, _tree("doc:original")
    )
    changed = build_persistent_object_map(
        source_id,
        CadGeometryKind.BREP,
        _tree("doc:changed", first_max=3),
    )
    old_key = original.by_runtime[CadObjectId("doc:original:runtime-a")]

    assert old_key not in changed.by_persistent


def test_same_topology_from_another_project_has_disjoint_persistent_keys() -> None:
    first = build_persistent_object_map(
        uuid4(), CadGeometryKind.BREP, _tree("doc:first-project")
    )
    second = build_persistent_object_map(
        uuid4(), CadGeometryKind.BREP, _tree("doc:second-project")
    )

    assert set(first.by_persistent).isdisjoint(second.by_persistent)
    assert {
        key.topology_path for key in first.by_persistent
    } == {key.topology_path for key in second.by_persistent}


def test_ambiguous_sibling_topology_is_not_mapped() -> None:
    document_id = CadDocumentId("doc:ambiguous")
    bounds = BoundingBox(0, 0, 0, 1, 1, 1)
    children = tuple(
        CadObjectNode(
            document_id,
            CadObjectId(f"runtime:{index}"),
            CadObjectKind.SOLID,
            f"Solid {index}",
            bounds,
            has_presentation=True,
        )
        for index in range(2)
    )
    root = CadObjectNode(
        document_id,
        CadObjectId("runtime:root"),
        CadObjectKind.DOCUMENT,
        "Document",
        bounds,
        children,
    )

    mapping = build_persistent_object_map(
        uuid4(), CadGeometryKind.BREP, CadDocumentTree(document_id, root)
    )

    assert mapping.by_runtime == {}
    assert mapping.ambiguous_nodes == 2


def test_default_rows_are_omitted_and_reset_deletes_existing_rows(tmp_path) -> None:
    database_path = tmp_path / "project.db"
    ProjectDatabase().initialize(database_path)
    source_id = uuid4()
    key = PersistentCadObjectKey(
        source_id,
        CadGeometryKind.BREP,
        TopologyPathVersion.V1,
        TopologyPath("solid:" + "a" * 32),
    )
    store = CadViewStateStore()

    with store.transaction(database_path) as connection:
        store.replace_all(connection, [CadViewState(source_id)], [source_id])
    with closing(sqlite3.connect(database_path)) as connection, connection:
        assert connection.execute("SELECT COUNT(*) FROM cad_view_state").fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM cad_object_appearance"
        ).fetchone()[0] == 0

    changed = CadViewState(
        source_id,
        object_appearances=(
            PersistentObjectAppearance(
                key,
                ObjectAppearance(color=ObjectColor(0.1, 0.2, 0.3)),
            ),
        ),
    )
    with store.transaction(database_path) as connection:
        store.replace_all(connection, [changed], [source_id])
    with closing(sqlite3.connect(database_path)) as connection, connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM cad_object_appearance"
        ).fetchone()[0] == 1

    with store.transaction(database_path) as connection:
        store.replace_all(connection, [CadViewState(source_id)], [source_id])
    with closing(sqlite3.connect(database_path)) as connection, connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM cad_object_appearance"
        ).fetchone()[0] == 0


def test_future_view_state_blocks_appearance_rows_for_the_same_source(tmp_path) -> None:
    database_path = tmp_path / "future-state.db"
    ProjectDatabase().initialize(database_path)
    source_id = uuid4()
    timestamp = "2026-01-01T00:00:00Z"
    with closing(sqlite3.connect(database_path)) as connection, connection:
        connection.execute(
            "INSERT INTO cad_view_state VALUES (?, ?, ?, ?, ?)",
            (str(source_id), 99, "shaded", "top", timestamp),
        )
        connection.execute(
            "INSERT INTO cad_object_appearance VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(source_id),
                1,
                "solid:" + "8" * 32,
                "brep",
                0,
                0.1,
                0.2,
                0.3,
                0.4,
                timestamp,
            ),
        )

    loaded = CadViewStateStore().load(database_path, [source_id])

    assert loaded == {}


def test_public_persistence_models_contain_no_native_cad_objects() -> None:
    source_id = uuid4()
    key = PersistentCadObjectKey(
        source_id,
        CadGeometryKind.BREP,
        TopologyPathVersion.V1,
        TopologyPath("solid:" + "b" * 32),
    )
    values = (
        key,
        PersistentObjectAppearance(key, ObjectAppearance()),
        CadViewState(source_id),
    )
    for value in values:
        assert is_dataclass(value)
        for field in fields(value):
            field_value = getattr(value, field.name)
            candidates = field_value if isinstance(field_value, tuple) else (field_value,)
            assert all(
                not type(candidate).__module__.startswith(("OCP", "PySide6"))
                for candidate in candidates
            )
