# CAM Persistence and Project Lifecycle 7A.6

## Ownership and SQLite schema v4

Editable CAM state is authoritative: a project owns zero or more ordered `CamJob`
aggregates, one optional active job, their setup/tree/operation/dependency state, and
project snapshots of tools, holders, tool assemblies and machines. Domain UUIDs are
primary identities; SQLite row IDs are never substituted. Project identity remains
separate and Save As preserves every CAM domain ID.

Migration v4 adds `cam_project_state`, `cam_jobs`, `cam_setups`, `cam_nodes`,
`cam_operations`, `cam_dependencies`, four project-definition snapshot tables and
`toolpath_artifacts`. Jobs, setups, nodes, operations and edges are normalized by
aggregate boundary and deterministic position. Strict versioned JSON payloads retain
the existing domain codecs without duplicating a full tree in another column.
Migration is performed by the existing ordered `ProjectDatabase` transaction; v3
projects gain empty CAM tables and no CAM rows. Future schemas remain rejected, no
downgrade occurs, and manifest/source files are outside migration scope.

Project-owned tooling/machine definitions are offline snapshots. Operations still
retain their expected ID/revision/fingerprint references. A future external library
comparison may mark a snapshot stale, but opening never silently replaces it and no
global tool-library database is introduced.

## Derived artifact store

Toolpath IR remains derived and is stored as canonical UTF-8 JSON below:

```text
toolpaths/<artifact-uuid-hex>.toolpath.json
```

SQLite stores only artifact/operation IDs, canonical relative path, SHA-256 checksum,
content/input fingerprints, byte size, schema, expected operation revision,
computation generation and completion status. File loading verifies size, checksum,
IR fingerprint and metadata/content agreement. Missing or corrupt files do not block
editable CAM loading; the operation is retained, changed to DIRTY with
`ARTIFACT_MISSING`, and receives a serializable diagnostic. Invalid metadata is not
written back until a later explicit Save.

Candidate publication uses an exclusive staging file, flush/fsync, atomic replace and
read-back verification. Only after the 7A.4 publish contract accepts the token,
fingerprint, operation identity/revision and enabled state is metadata staged in the
application snapshot. Save commits metadata with editable state in the project SQLite
transaction. A DB failure may leave an unreferenced candidate file, never a DB row
pointing to invalid content; bounded orphan cleanup removes it after a later successful
Save. An older referenced artifact is not deleted before replacement succeeds.

## Save, Open and Save As

`ProjectService` remains the only lifecycle gateway. `CamApplicationService` owns one
native-free snapshot under an `RLock`; ProjectService stages mutations and mirrors
project dirty state. Save writes CAD and complete CAM state through the existing one
SQLite transaction. It does not make an artifact VALID. A runtime COMPUTING state is
normalized to DIRTY, its token is discarded, and an interruption diagnostic is
persisted. Failed Save keeps the pending in-memory snapshot and project dirty.

Open migrates and validates manifest/database, reconstructs every complete CAM
aggregate, then checks artifact files before ProjectService activation. A malformed
editable payload aborts activation and preserves the previously open project. A
derived-file failure degrades only its operation. Geometry references are deserialized
unchanged and are neither resolved nor rebound; CAD resolver outcomes remain the
native-free validation contract from 7A.1/7A.4.

Save As uses the existing staging-directory transaction and SQLite online backup,
rewrites pending CAM state, copies only referenced artifacts through verified load and
atomic publish, and then publishes an independent project directory. Source IDs and
all CAM IDs stay unchanged; candidate, staging and orphan artifact files are excluded.

## Autosave and recovery

Policy v1 is editable-state-first. Autosave always writes pending CAM into the
checksummed SQLite snapshot without cleaning the main session or changing its path.
Large artifact files are not duplicated into each autosave directory; their metadata
is retained and the main project's published file is revalidated after recovery. If
that file is missing or corrupt, the recovered operation becomes DIRTY rather than
being lost. Recovery never reuses a COMPUTING token and does not resolve geometry or
touch CAD/XCAF source appearance.

## Path and cleanup security

Artifact paths from SQLite must be canonical two-part POSIX relative paths. Absolute,
UNC, drive-qualified, `..`, colon and backslash forms are rejected. The artifact root
and files may not be symlinks or Windows junctions/reparse escapes. Filenames derive
only from a validated `ToolpathArtifactId`. Cleanup is non-recursive, skips links and
directories, deletes only known staging names or unreferenced canonical toolpath files,
and can never delete outside `toolpaths/`. JSON and artifact byte limits are explicit.

## Current limits

7A.6 adds no CAM UI, global tool library, background compute worker, machining
algorithm, renderer, simulation, collision, kinematics/IK, Post Processor or G-code.
External CAD/tool/machine resolver adapters and binary/chunked artifact persistence
remain later-stage work.
