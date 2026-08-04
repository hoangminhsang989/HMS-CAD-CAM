# Stage 13C WP1 — Turning Advisor Scope and Contract Lock

## Status

Approved title: **Stage 13C — Offline CAM Cutting Parameter Advisor V1:
Turning Strategy Coverage Certification**.

WP1 locked the four strategies as `CONTRACT_LOCKED`, not `SUPPORTED`. R38 WP3
subsequently certified their production UI, Apply and lifecycle routes and
promoted only the four exact registry IDs to `SUPPORTED`.

| Strategy | Canonical ID | Diameter authority | Tool capability | Feed | Depth of cut |
| --- | --- | --- | --- | --- | --- |
| OD rough | `lathe.od_rough.v1` | `LatheStockSnapshotV1.outer_diameter_mm` | `OD_TURNING` | `mm/rev` | `max_depth_of_cut_mm`, radial |
| OD finish | `lathe.od_finish.v1` | `LatheParameterState.target_diameter_mm` | `OD_TURNING` | `mm/rev` | excluded |
| ID rough | `lathe.id_rough.v1` | `LatheStockSnapshotV1.inner_diameter_mm` | `ID_TURNING` | `mm/rev` | `max_depth_of_cut_mm`, radial |
| ID finish | `lathe.id_finish.v1` | `LatheParameterState.target_diameter_mm` | `ID_TURNING` | `mm/rev` | excluded |

## Fail-closed rules

Only the four typed IDs resolve. FACE, threading and unknown strategies reject.
Parameter state must match the strategy. OD target must be below stock OD. ID
requires an explicit positive bore, target above bore and below stock OD. Tool
evidence must be current and carry the exact typed capability. No FACE mapping,
tool shank diameter, display-name inference, zero fallback, cross-strategy
lookup, or guessed depth field is used. Unsupported outputs are retained and
warned, never applied.

The current Lathe tool resolver proves tool/assembly/profile identity, revision
and capability, but does not expose authoritative HSS/CARBIDE material or insert
diameter. WP1 does not infer either. Later runtime work must supply explicit
material provenance before constructing a `CuttingRequest`; model V1 is
unchanged.

WP2 added a feature-flag-gated, non-final runtime bridge for these four IDs. It
provides explicit Analyze, production draft-only Selective Apply, stale
ownership/digest rejection and one compatible draft Undo. The registry status
is `RUNTIME_BRIDGE_INTEGRATED_NOT_CERTIFIED`; the four IDs remain outside
`SUPPORTED`. Normal Apply, final UI workflow/lifecycle certification, schema,
package, network, Post and NC remained excluded from WP2.

## WP3 production certification

`OD_ROUGH`, `OD_FINISH`, `ID_ROUGH` and `ID_FINISH` now use the existing
`CuttingAdvisorPanel` embedded in the actual `LatheWorkspace`. Explicit
session-only workpiece/tool material selectors feed one owner-local
`TurningAdvisorUiSession`. Analyze uses the Stage 13A broker lease and Stage
13B JSONL worker; Selective Apply remains draft-only; the ordinary
`LatheParametersApplyButton` is still the sole production mutation route.
Workspace/presenter/operation invalidation releases the editor and worker.

Stage 13B authority remains exactly `facing_2_5d`, `drilling_v1`, `FACE`.
Threading, generic LATHE fallback, material inference, schema persistence,
automatic Analyze/Apply, Post, NC, simulation and toolpath generation remain
excluded.
