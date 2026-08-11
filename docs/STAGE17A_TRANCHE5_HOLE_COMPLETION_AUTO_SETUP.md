# Stage17A Tranche5 — Hole Completion Auto Setup

Status: `STAGE17A_TRANCHE5_HOLE_COMPLETION_PRODUCT_CONTRACT_FROZEN`

Baseline: `b5866d6c2f660cb404afdcfa389b9543abc69a36` / tree
`f96cc5b968bc5fde9b266c2c3d3b81e41abfe8af`.

Canonical product identity:
`STAGE17A_TRANCHE5_HOLE_COMPLETION_AUTOMATIC_GEOMETRY_INTELLIGENCE`.

Covered families are exactly Tapping, Reaming and Boring. Lathe/turning,
controller policy, cutting-data policy, collision clearance and new toolpath
algorithms remain outside this tranche.

## HOLE_COMPLETION_EXISTING_PRODUCT_CONTRACT_MATRIX

### Shared geometry and setup

| Field/evidence | Existing representation | Ownership |
| --- | --- | --- |
| selected hole identity | persistent `HoleReference` or explicit `HolePattern` | `GEOMETRY_AUTHORITY` |
| centre/order | finite `HoleLocation.position`; `HolePattern` canonical ordering | `GEOMETRY_AUTHORITY` |
| axis | `HoleLocation.axis`; current generators require setup +Z | `GEOMETRY_AUTHORITY` |
| machining plane | `HoleLocation.plane_origin` | `GEOMETRY_AUTHORITY` |
| pattern fingerprint/count/bounds/spacing | deterministic location and pattern fingerprints | `GEOMETRY_AUTHORITY` |
| unqualified hole diameter | optional `HoleLocation.diameter` with no initial/finished/thread semantic tag | `UNSUPPORTED` as finished/thread authority; provenance only |
| independent feature depth | not represented by `HoleLocation`; operation `DrillDepthDefinition` is authored intent | `UNSUPPORTED` as geometry authority |
| top/reference Z | authored depth plus independently resolved common plane | conditional `GEOMETRY_AUTHORITY` from plane |
| target/bottom depth | authored `DrillDepthDefinition.bottom_z` | `USER_DESIGN_INTENT` until independent feature depth exists |
| clearance/retract | explicit operation numerics; no stock/fixture safe-plane source | `USER_PROCESS_INTENT` |
| Tool and machine selection | explicit current references | `USER_PROCESS_INTENT` |
| Tool geometry/fingerprint | typed current Tool/Assembly/Holder snapshots | `TOOL_AUTHORITY` for compatibility and physical bounds only |
| feed/spindle/dwell/coolant/retract behavior | explicit operation fields | `USER_PROCESS_INTENT` |
| canned cycle/Post semantics | generator/Post boundary | `CONTROLLER_SPECIFIC` |

### Tapping

| Field | Existing representation | Ownership |
| --- | --- | --- |
| nominal diameter | explicit strategy value; exact TAP Tool compatibility check | `USER_DESIGN_INTENT`; Tool validates only |
| pitch | explicit strategy value; exact `TapGeometry.pitch` compatibility check | `USER_DESIGN_INTENT`; no Tool-name inference |
| hand | explicit strategy value; exact Tool hand compatibility check | `USER_DESIGN_INTENT` |
| thread standard/class/TPI/major/minor/pitch diameter | no production semantic model | `UNSUPPORTED` |
| threaded feature depth | no independent feature-depth/thread-depth evidence | `UNSUPPORTED` for AUTO; authored depth remains manual |
| threaded length/usable length/stickout | typed TAP Tool and Assembly | `TOOL_AUTHORITY` as capacity bounds |
| rigid/floating synchronization | explicit `TappingSynchronizationPolicy`, validated against machine modes | `USER_PROCESS_INTENT` |
| spindle and synchronized feed | explicit RPM; generator mathematically maps pitch × RPM after validation | `USER_PROCESS_INTENT` and machine validation, not AUTO policy |

### Reaming

| Field | Existing representation | Ownership |
| --- | --- | --- |
| finished/nominal diameter | explicit `nominal_diameter`; exact REAMER diameter check | `USER_DESIGN_INTENT`; Tool validates only |
| pre-hole diameter | required explicit `pre_hole_diameter` | `USER_PROCESS_INTENT` |
| unqualified selected-hole diameter | no initial-versus-finished classification | `UNSUPPORTED` as finished target authority |
| stock per side | derived validation consequence of explicit nominal/pre-hole values | `USER_PROCESS_INTENT`, never generic AUTO allowance |
| cutting length | typed Reamer geometry | `TOOL_AUTHORITY` as depth capacity bound |
| feed/rev, RPM, direction, retract, coolant, dwell | explicit strategy values | `USER_PROCESS_INTENT` |

### Boring

| Field | Existing representation | Ownership |
| --- | --- | --- |
| finished bore diameter | explicit `finished_bore_diameter` | `USER_DESIGN_INTENT` |
| pre-bore diameter | required explicit `pre_bore_diameter` | `USER_PROCESS_INTENT` |
| radial stock | exact consequence of explicit pre/finished diameters | `USER_PROCESS_INTENT` |
| Tool min/max bore diameter | `BoringBarGeometry` capability range | `TOOL_AUTHORITY` as bounds, never exact target |
| adjustable-head/radial setting | no consumed production metadata | `UNSUPPORTED` |
| holder/shank/stickout/cutting length | typed Tool/Holder/Assembly feasibility checks | `TOOL_AUTHORITY` as bounds only |
| compensation/orientation/controller cycle | not represented as Tranche5 authority | `UNSUPPORTED` or `CONTROLLER_SPECIFIC` |
| feed/rev, RPM, direction, retract, coolant, dwell | explicit strategy values | `USER_PROCESS_INTENT` |

## Frozen automatic ownership

For an eligible current Tool and resolved hole pattern, shared hidden AUTO state
contains deterministic order, count, fingerprint, axis, common plane, bounding
box, minimum spacing, geometry/tool fingerprints, unit, depth evidence class,
diameter evidence class and explicit unavailable reasons.

`top_z` may be AUTO only from the resolved common machining plane. `final_depth`
may be AUTO only from a future explicit, finite, common feature-depth authority;
the current `HoleLocation` contract supplies none, so production correctly keeps
all three existing depths manual. Clearance/retract remain manual because no
safe-plane authority exists.

Tapping thread fields remain manual because current geometry has no authoritative
thread definition or threaded depth. Reaming nominal diameter and Boring finished
diameter remain manual because the current optional hole diameter is semantically
unqualified. Tool dimensions validate authored intent and never create design
intent.

The three diameter concepts remain distinct:

1. source/initial hole diameter;
2. authoritative finished-feature diameter;
3. Tool cutting/effective size.

## Eligibility and stale behavior

Eligibility requires a resolved non-empty finite pattern, unique centres and
identities, one unit, setup +Z axis, one plane, stable geometry fingerprint,
current exact Tool family and valid Tool geometry. Tapping requires `TAP`,
Reaming requires `REAMER`, and Boring requires `BORING_BAR` plus the existing
Holder boundary. Mixed grouped evidence never uses average/min/max/first-hole
fallback.

Persisted AUTO dependencies include operation family, policy version, geometry
and pattern fingerprints, hole count, axis, plane, Tool fingerprint/geometry,
depth source, diameter source and unit. Generators recompute and reject stale
AUTO state before emission even when an old numeric value remains plausible.

## Modes, persistence and UX

Reuse only `AUTO`, `MANUAL_OVERRIDE`, legacy `MANUAL` and `NOT_APPLICABLE` in
the additive `automatic_parameter_contract`. Legacy numerics remain manual,
manual overrides survive dependency changes, reset-to-AUTO is explicit, and
temporary evidence loss preserves AUTO intent without emission. SQLite remains
schema 5.

Basic shows a compact Auto Setup summary, hole/pattern state, reference plane,
depth authority and thread/diameter authority. Advanced exposes top/depth and
applicable target-field modes, provenance, unavailable reasons and reset. Process
fields remain ordinary manual controls. VI is default and VI/EN/KO, dark mode,
two-column layout, DPI scaling and lifecycle behavior remain unchanged.

## Acceptance and delivery boundary

Completion requires pure shared geometry and three separate policies, generator
revalidation, additive persistence, Basic/Advanced UI, VI/EN/KO, focused,
substantial bounded, lifecycle and full regression with candidate-induced and
indeterminate failures zero. R211 is local-only: no protected-main integration,
push or production AI Sync.
