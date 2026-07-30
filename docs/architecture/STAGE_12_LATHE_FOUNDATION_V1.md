# Stage 12 — Lathe Foundation V1

Status: owner-approved authoritative additive specification
Scope state on successful Stage 12 closure: `COMPLETE`
Stage 9A.9 state on successful Stage 12 closure: `UNBLOCKED_FOR_IMPLEMENTATION`

This specification is the canonical Stage 12 contract for the HMS CAD/CAM
Lathe foundation. It is additive to the existing CAM domain. If an earlier
planning document is less specific, this specification governs Lathe V1.

## 1. Scope and boundaries

Stage 12 provides a typed, immutable, deterministic, runtime-only foundation:

- exact strategy, family, geometry, parameter, and tool-capability registries;
- Lathe ownership, operation state, typed diagnostics, and readiness;
- typed mutation commands and an atomic in-memory application service;
- read-only, closed-session, setup/source/generation lifecycle handling;
- injected tool-capability resolution using canonical tool references;
- a Qt-free presenter-neutral query/command boundary for Stage 9A.9;
- typed workspace readiness that unlocks implementation but does not activate
  a Lathe presenter.

The following are explicitly excluded:

- Qt presenter, widget, panel, dock, action, icon, or viewport adapter;
- selection-to-Lathe automatic conversion;
- turning toolpath calculation or material-removal logic;
- Post Processor, G-code, simulation, or machine execution;
- project/database persistence, `.HMS` mutation, schema migration, or pickle;
- a second Tool/Holder/Profile/Assembly catalog;
- localized visible strings or translation catalogs.

## 2. Canonical reuse and units

The foundation reuses canonical `OperationId`, `SetupId`, `ToolDefinitionId`,
`ToolProgramProfileId`, `ToolAssemblyId`, `Revision`, non-nil project/source
UUIDs, and `CadDocumentId`. Existing `OperationFamily.TURNING`,
`SetupKind.TURN`, `CylinderStock`, machine TURNING/THREADING capabilities,
`TurningInsertGeometry`, Tool, Holder, Assembly, and Tool Profile contracts are
unchanged.

Units are fixed at this boundary:

| Quantity | Canonical V1 unit |
| --- | --- |
| length | millimetres (`mm`) |
| angle | degrees |
| spindle speed | revolutions/minute (`rpm`) |
| feed | millimetres/revolution (`mm/rev`) |
| time | seconds |

Numeric inputs accept exact `int` or `float` values except `bool`, normalize to
`float`, and must be finite. Integer inputs accept exact `int` only and reject
`bool`. There is no string coercion or localized decimal parsing.

There is no single canonical operation coolant enum in the current repository;
the existing tool and machine coolant enums have different roles. Therefore
`coolant_mode` is not part of Lathe V1.

## 3. Exact strategy and family registry

The registry contains exactly these entries in this order:

| Member | Stable strategy ID | Family |
| --- | --- | --- |
| `FACE` | `lathe.face.v1` | `TURNING` |
| `OD_ROUGH` | `lathe.od_rough.v1` | `TURNING` |
| `OD_FINISH` | `lathe.od_finish.v1` | `TURNING` |
| `ID_ROUGH` | `lathe.id_rough.v1` | `TURNING` |
| `ID_FINISH` | `lathe.id_finish.v1` | `TURNING` |
| `OD_GROOVE` | `lathe.od_groove.v1` | `GROOVING` |
| `ID_GROOVE` | `lathe.id_groove.v1` | `GROOVING` |
| `PART_OFF` | `lathe.part_off.v1` | `GROOVING` |
| `OD_THREAD` | `lathe.od_thread.v1` | `THREADING` |
| `ID_THREAD` | `lathe.id_thread.v1` | `THREADING` |
| `AXIAL_DRILL` | `lathe.axial_drill.v1` | `HOLE_MAKING` |

Family IDs are exactly:

- `lathe.family.turning.v1`;
- `lathe.family.grooving.v1`;
- `lathe.family.threading.v1`;
- `lathe.family.hole_making.v1`.

There is no twelfth, alias, deprecated, unknown, or custom strategy in V1. The
registry is metadata only and has no executable toolpath implementation.

## 4. Exact tool capabilities

The tool-capability enum contains exactly nine members:

`FACE_TURNING`, `OD_TURNING`, `ID_TURNING`, `OD_GROOVING`, `ID_GROOVING`,
`PARTING`, `OD_THREADING`, `ID_THREADING`, and `AXIAL_DRILLING`.

The required mapping is exact:

| Strategy | Required capability |
| --- | --- |
| `FACE` | `FACE_TURNING` |
| `OD_ROUGH`, `OD_FINISH` | `OD_TURNING` |
| `ID_ROUGH`, `ID_FINISH` | `ID_TURNING` |
| `OD_GROOVE` | `OD_GROOVING` |
| `ID_GROOVE` | `ID_GROOVING` |
| `PART_OFF` | `PARTING` |
| `OD_THREAD` | `OD_THREADING` |
| `ID_THREAD` | `ID_THREADING` |
| `AXIAL_DRILL` | `AXIAL_DRILLING` |

Capability resolution is injected. The production default resolver returns no
capability and therefore fails closed. Capability must be supplied by a typed
registry/profile adapter; user-facing display names are never inspected.

## 5. Ownership and operation aggregate

`LatheOwnershipKey` is immutable and contains:

- `project_id` (non-nil UUID);
- `document_id` (`CadDocumentId`, non-blank value);
- `source_id` (non-nil UUID);
- `generation` (non-negative exact integer; `bool` rejected);
- `setup_id` (`SetupId`);
- `operation_id` (`OperationId`).

Project, document, source, setup, operation, and generation comparisons are
exact. Any mismatch fails closed. Operations are isolated by operation ID and
session; stale ownership is never silently rebound.

`LatheOperationState` is immutable and contains exactly the foundation state:

- ownership;
- strategy ID;
- typed parameter state;
- optional geometry binding;
- optional tool binding;
- exact `bool` enabled flag;
- ordered typed diagnostics;
- canonical non-negative `Revision`.

Revision starts at zero. Each successful semantic mutation increments it once.
A failed mutation makes no state change and does not increment it. Create starts
at zero; validation is a query and does not increment; deletion returns the
final incremented tombstone outcome while removing the operation from service.

The aggregate contains no Qt/OCP object, connection, raw exception, localized
visible text, or mutable public list/dictionary.

## 6. Geometry binding

The exact geometry kinds and stable IDs are:

| Member | Stable ID |
| --- | --- |
| `AXIS` | `lathe.geometry.axis.v1` |
| `PROFILE` | `lathe.geometry.profile.v1` |
| `FACE` | `lathe.geometry.face.v1` |
| `EDGE` | `lathe.geometry.edge.v1` |
| `CYLINDER` | `lathe.geometry.cylinder.v1` |
| `POINT` | `lathe.geometry.point.v1` |

`LatheGeometryBinding` contains kind, a non-empty ordered tuple of unique,
non-blank stable entity-ID strings, source UUID, and non-negative generation.
It never contains an OCP object.

Allowed geometry kinds are exact:

| Strategy | Allowed kinds |
| --- | --- |
| `FACE` | `FACE`, `EDGE`, `PROFILE` |
| `OD_ROUGH`, `OD_FINISH` | `PROFILE`, `EDGE`, `CYLINDER` |
| `ID_ROUGH`, `ID_FINISH` | `PROFILE`, `EDGE`, `CYLINDER` |
| `OD_GROOVE`, `ID_GROOVE` | `PROFILE`, `EDGE`, `FACE` |
| `PART_OFF` | `EDGE`, `FACE`, `PROFILE` |
| `OD_THREAD`, `ID_THREAD` | `CYLINDER`, `EDGE`, `PROFILE` |
| `AXIAL_DRILL` | `POINT`, `AXIS`, `CYLINDER` |

The application service rejects stale or incompatible geometry atomically.

## 7. Parameter metadata and common envelope

Each immutable parameter descriptor provides parameter ID, value kind, unit
kind, BASIC/ADVANCED group, required flag, order, bounds and exclusivity, enum
values, and semantic label/help keys. Keys are always
`lathe.parameter.<id>.label` and `lathe.parameter.<id>.help`; no visible labels
are stored.

Every strategy begins with these common parameters in this order:

| Parameter | Type/unit | Constraint | Group |
| --- | --- | --- | --- |
| `spindle_speed_rpm` | float/rpm | `> 0` | BASIC |
| `feed_mm_per_rev` | float/mm/rev | `> 0` | BASIC |
| `clearance_mm` | float/mm | `> 0` | BASIC |
| `retract_mm` | float/mm | `>= 0` | ADVANCED |
| `spindle_direction` | enum `CW`, `CCW` | required | BASIC |

## 8. Exact strategy parameter schemas

The following fields appear after the common envelope and in listed order.
There are no hidden or additional fields.

### FACE

- `face_z_mm`: float, BASIC;
- `outer_diameter_mm`: float `> 0`, BASIC;
- `inner_diameter_mm`: float `>= 0`, BASIC;
- `max_depth_of_cut_mm`: float `> 0`, ADVANCED;
- `finish_allowance_mm`: float `>= 0`, ADVANCED;
- constraint: `inner_diameter_mm < outer_diameter_mm`.

### OD_ROUGH and ID_ROUGH

- `start_z_mm`: float, BASIC;
- `end_z_mm`: float, BASIC;
- `target_diameter_mm`: float `> 0`, BASIC;
- `max_depth_of_cut_mm`: float `> 0`, BASIC;
- `radial_stock_to_leave_mm`: float `>= 0`, ADVANCED;
- `axial_stock_to_leave_mm`: float `>= 0`, ADVANCED;
- constraint: `start_z_mm != end_z_mm`.

### OD_FINISH and ID_FINISH

- `start_z_mm`: float, BASIC;
- `end_z_mm`: float, BASIC;
- `target_diameter_mm`: float `> 0`, BASIC;
- `finish_passes`: integer `>= 1`, ADVANCED;
- `spring_passes`: integer `>= 0`, ADVANCED;
- constraint: `start_z_mm != end_z_mm`.

### OD_GROOVE and ID_GROOVE

- `center_z_mm`: float, BASIC;
- `groove_width_mm`: float `> 0`, BASIC;
- `target_diameter_mm`: float `> 0`, BASIC;
- `max_step_mm`: float `> 0`, ADVANCED;
- `side_allowance_mm`: float `>= 0`, ADVANCED.

### PART_OFF

- `cutoff_z_mm`: float, BASIC;
- `target_diameter_mm`: float `>= 0`, BASIC;
- `max_step_mm`: float `> 0`, ADVANCED;
- `side_clearance_mm`: float `>= 0`, ADVANCED.

### OD_THREAD and ID_THREAD

- `start_z_mm`: float, BASIC;
- `end_z_mm`: float, BASIC;
- `major_diameter_mm`: float `> 0`, BASIC;
- `minor_diameter_mm`: float `> 0`, BASIC;
- `pitch_mm`: float `> 0`, BASIC;
- `thread_hand`: enum `RIGHT`, `LEFT`, BASIC;
- `pass_count`: integer `>= 1`, ADVANCED;
- `spring_passes`: integer `>= 0`, ADVANCED;
- `infeed_angle_deg`: float `0 <= value < 90`, ADVANCED;
- constraints: `start_z_mm != end_z_mm` and
  `minor_diameter_mm < major_diameter_mm`.

### AXIAL_DRILL

- `depth_mm`: float `> 0`, BASIC;
- `retract_plane_z_mm`: float, BASIC;
- `peck_depth_mm`: optional float `> 0`, ADVANCED;
- `dwell_seconds`: float `>= 0`, ADVANCED.

Parameter state is immutable, canonically ordered, schema-aware, rejects
unknown or missing required fields, and supports only atomic updates.

## 9. Dedicated V1 default factory

Defaults are editor starting values only. They do not bypass ownership,
geometry, tool compatibility, lifecycle, read-only, or readiness.

Common defaults are `spindle_speed_rpm=1000.0`, `feed_mm_per_rev=0.2`,
`clearance_mm=2.0`, `retract_mm=1.0`, and `spindle_direction=CW`.

| Strategy | Exact strategy-specific defaults |
| --- | --- |
| `FACE` | `face_z_mm=0.0`, `outer_diameter_mm=50.0`, `inner_diameter_mm=0.0`, `max_depth_of_cut_mm=1.0`, `finish_allowance_mm=0.2` |
| `OD_ROUGH` | `start_z_mm=0.0`, `end_z_mm=-50.0`, `target_diameter_mm=40.0`, `max_depth_of_cut_mm=2.0`, `radial_stock_to_leave_mm=0.5`, `axial_stock_to_leave_mm=0.2` |
| `OD_FINISH` | `start_z_mm=0.0`, `end_z_mm=-50.0`, `target_diameter_mm=40.0`, `finish_passes=1`, `spring_passes=0` |
| `ID_ROUGH` | `start_z_mm=0.0`, `end_z_mm=-30.0`, `target_diameter_mm=20.0`, `max_depth_of_cut_mm=1.0`, `radial_stock_to_leave_mm=0.3`, `axial_stock_to_leave_mm=0.2` |
| `ID_FINISH` | `start_z_mm=0.0`, `end_z_mm=-30.0`, `target_diameter_mm=20.0`, `finish_passes=1`, `spring_passes=0` |
| `OD_GROOVE` | `center_z_mm=-20.0`, `groove_width_mm=3.0`, `target_diameter_mm=35.0`, `max_step_mm=1.0`, `side_allowance_mm=0.1` |
| `ID_GROOVE` | `center_z_mm=-20.0`, `groove_width_mm=3.0`, `target_diameter_mm=25.0`, `max_step_mm=1.0`, `side_allowance_mm=0.1` |
| `PART_OFF` | `cutoff_z_mm=-50.0`, `target_diameter_mm=0.0`, `max_step_mm=1.0`, `side_clearance_mm=0.2` |
| `OD_THREAD` | `start_z_mm=0.0`, `end_z_mm=-30.0`, `major_diameter_mm=20.0`, `minor_diameter_mm=18.0`, `pitch_mm=1.5`, `thread_hand=RIGHT`, `pass_count=8`, `spring_passes=1`, `infeed_angle_deg=29.0` |
| `ID_THREAD` | `start_z_mm=0.0`, `end_z_mm=-30.0`, `major_diameter_mm=20.0`, `minor_diameter_mm=18.0`, `pitch_mm=1.5`, `thread_hand=RIGHT`, `pass_count=8`, `spring_passes=1`, `infeed_angle_deg=29.0` |
| `AXIAL_DRILL` | `depth_mm=30.0`, `retract_plane_z_mm=2.0`, `peck_depth_mm=null`, `dwell_seconds=0.0` |

## 10. Tool binding

`LatheToolBinding` uses canonical tool/profile/assembly IDs, canonical revisions,
and an immutable resolved capability set. Profile identity/revision may be null
because the canonical Tool Program Profile architecture is optional; assembly
identity is required by the canonical tooling architecture.

Blank or wrong identity types, missing tools, stale references, and capability
mismatch are rejected. Assignment failure preserves the prior operation. The
aggregate never retains a raw mutable Tool object.

## 11. Readiness and diagnostics

Operation readiness is exactly `INVALID`, `INCOMPLETE`, or `READY`.

`READY` requires a structurally valid enabled aggregate, live ownership, known
strategy, valid complete parameters, compatible geometry, compatible tool
capability, an open service/session, and a mutable session. A disabled
operation is not ready.

Diagnostics distinguish at least `missing_setup`, `missing_geometry`,
`missing_tool`, `incompatible_tool`, `invalid_parameter`, `stale_ownership`,
`read_only`, `closed`, `unknown_strategy`, and `disabled_operation`. Ordering is
deterministic. READY never claims toolpath, Post, G-code, or simulation.

## 12. Commands, atomicity, and strategy-change policy

The exact immutable commands are:

1. `CreateLatheOperation`;
2. `UpdateLatheParameters`;
3. `ChangeLatheStrategy`;
4. `BindLatheGeometry`;
5. `ClearLatheGeometry`;
6. `BindLatheTool`;
7. `ClearLatheTool`;
8. `SetLatheOperationEnabled`;
9. `DeleteLatheOperation`;
10. `ValidateLatheOperation`.

Commands carry exact ownership and expected revision. There is no generic
dictionary command. Multi-parameter changes use immutable typed update entries.
The service builds a complete candidate first and publishes it only after all
validation succeeds.

Changing strategy resets all strategy-specific parameters to the approved V1
defaults, retains the five valid common-envelope values, and then validates the
complete new state. Existing geometry/tool bindings are retained but are
re-evaluated against the new strategy; an incompatible binding prevents READY
without silent data loss.

## 13. Application service and lifecycle

`LatheOperationService` owns operations for exactly one runtime session. It
creates, queries, lists in creation order, mutates only through typed commands,
validates, deletes, and closes. It has no persistence, callback, worker,
process-wide singleton, background thread, or filesystem/database write.

`LatheServiceSession` owns project, document, source, generation, optional
active setup, read-only, and closed state. Required transitions are open/create,
set read-only, switch setup, switch source, increment generation, and close.
Close is idempotent. Prior operations remain queryable but become stale after a
source/setup/generation change. Closed/read-only state rejects mutation. A
project/document mismatch is rejected, and service instances share no state.

## 14. Presenter-neutral and workspace boundaries

The Qt-free Stage 9A.9 boundary exposes immutable
`LatheStrategyDescriptor`, `LatheParameterDescriptor`,
`LatheOperationSnapshot`, `LathePresenterSnapshot`, `LatheCommandOutcome`, and
`LatheWorkspaceReadiness`. The facade lists/query metadata, creates/selects and
queries operations, applies typed parameter changes, changes strategy,
binds/clears geometry and tool, enables/disables, deletes, validates, reads
diagnostics, and reads workspace readiness by delegating to the application
service.

Workspace readiness states are exactly `FOUNDATION_UNAVAILABLE`,
`FOUNDATION_READY`, `PRESENTER_IMPLEMENTATION_ALLOWED`, and `PRESENTER_ACTIVE`.
After successful Stage 12 construction the state is
`PRESENTER_IMPLEMENTATION_ALLOWED`, never `PRESENTER_ACTIVE`, with reason
`presenter_not_implemented`. Before foundation availability the reason is
`foundation_not_ready`. The existing TIỆN workspace remains fail-closed and
disabled with no visible topology change.

## 15. Canonical in-memory snapshot

The optional deterministic mapping codec uses exact schema ID
`lathe.foundation.snapshot.v1`, rejects unknown/missing fields on decode, and
round-trips only in memory. It does not write files, databases, `.HMS` content,
or pickle payloads.

## 16. Acceptance matrix

| Area | Acceptance boundary |
| --- | --- |
| Identity/ownership | canonical types, blank/nil/bool/negative rejection, exact mismatch and isolation |
| Registry | exactly 11 ordered strategies, four families, six geometry kinds, nine capabilities; no duplicate/custom entry |
| Parameters | exact fields/order/groups/units/keys/defaults; finite strict numeric types; cross-field constraints; atomic update |
| Geometry/tool | exact compatibility matrices; stale/missing/incompatible fail closed; immutable bindings |
| Aggregate | immutable state, canonical revision, typed deterministic diagnostics |
| Commands/service | exact ten commands, expected-revision guards, no partial mutation, deterministic list/delete/close |
| Lifecycle | setup/source/generation invalidation, project/document rejection, read-only/closed rejection, no leakage |
| Presenter | immutable Qt-free/OCP-free DTOs and facade delegation; no localized visible text |
| Workspace | foundation available, Stage 9A.9 unlocked only for implementation, TIỆN remains disabled, no topology delta |
| Serialization | exact schema ID, deterministic in-memory mapping, strict decode, no persistence/pickle |
| Regression | existing turning/setup/stock/machine/tool/profile/workspace/project contracts unchanged |
| Exclusions | no Lathe UI, selection adapter, toolpath, Post/G-code, simulation, database/schema, package, or push |

## 17. Exact completion definition

Stage 12 is complete only when production code and mapped tests pass focused
`pytest -W error`, static/import/resource gates, a fresh-clone full repository
`pytest -W error` run, exact candidate staging, commit verification, and
post-commit light verification. Completion sets WP0–WP4 and
`LATHE_FOUNDATION_V1` to `COMPLETE`, sets Stage 9A.9 to
`UNBLOCKED_FOR_IMPLEMENTATION`, and leaves Lathe UI, toolpath, Post, and
simulation `NOT_STARTED`.
