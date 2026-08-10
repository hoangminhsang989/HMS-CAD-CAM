# Stage17A Tranche2 — Contour 2D Auto Setup

Status: `OWNER_CONTRACT_FROZEN`

Canonical identity:

- tranche: `STAGE17A_TRANCHE2_CONTOUR_2D_CLOSED_PROFILE_AUTOMATIC_PARAMETERS_AND_LEAD_INTELLIGENCE`;
- product name: `Contour 2D Auto Setup`;
- baseline: `84983fad0a8b8687c98e575a8f687e6d85ec3f58`;
- Tranche1 ancestor: `0f1836777c2c8553474503955abba381e6a1c46e`;
- R202 is local-only: no push and no protected-main integration.

## Existing production audit

Contour v1 already owns a single planar, explicitly closed LINE/ARC loop, a
known Setup-WCS plane, explicit top/final depth, explicit side and cutting
direction, typed Tool Assembly/Tool geometry, deterministic offsetting,
self-intersection rejection, exact depth-level closure, and a fail-closed
linear lead check. Its editor currently persists numeric `stepdown` and one
shared numeric `lead_length`; both are manual. The canonical start is a fixed
lowest-midpoint rule. There is no Contour automatic contract, provenance,
dependency recomputation, or Basic/Advanced AUTO control.

The resolved `ContourProfileDescriptor` is OCP-free and already exposes the
closed loop, segment type/endpoints, arc centre/radius, plane, orientation and
geometry fingerprint. This is sufficient for a minimal typed automatic
geometry view and deterministic entry ranking. It is not a stock or machine
collision model.

## Frozen eligibility

AUTO is eligible only when all of the following are validated:

- one planar, closed, continuous, non-degenerate Contour loop;
- the existing kernel/profile resolver accepts the loop and its machining
  offset does not collapse or self-intersect;
- known unit, machining side, positive depth span and Setup plane;
- a selected, current Tool with a supported Contour cutter family, positive
  diameter and explicit axial cutting length;
- the complete depth span is within validated cutter/assembly axial capacity.

Open, ambiguous, stale, unclosed, self-intersecting, non-planar, true-3D,
turning, missing-tool and missing-depth inputs fail closed as
`NOT_APPLICABLE`/`UNRESOLVED`. R202 never closes an open chain automatically.
Multiple independent loops remain outside Contour v1.

## Parameter ownership

| Field | R202 ownership | Authoritative inputs |
| --- | --- | --- |
| Stepdown | `AUTO` or `MANUAL_OVERRIDE` | depth span, explicit axial cutting length, assembly stickout, cutter family, quality profile, unit |
| Lead-in length | `AUTO`, `MANUAL_OVERRIDE`, or unavailable | cutter diameter/radius, actual offset loop, source boundary, local tangent/curvature, side, quality profile |
| Lead-out length | independent mode and provenance | exit tangent/curvature and validated local clearance; it is not blindly copied from entry |
| Entry placement | hidden deterministic AUTO decision when feasible | ranked non-corner segment midpoint, local length/curvature, lead feasibility, geometry fingerprint |
| Lead form | current linear representation only | smooth tangent linear form is preferred; validated repository-compatible normal linear form is the bounded fallback |

Feed, spindle, Tool choice, side, climb/conventional direction, stock
allowances, compensation semantics, target depths and final geometry remain
deliberate user/product intent. R202 does not infer them.

## Bounds and policy

Quality factors and geometric caps belong to the Qt-free Contour policy module,
not widgets. Automatic stepdown must be finite and satisfy
`0 < stepdown <= min(depth_span, usable_axial_capacity)`. The depth sequence
continues to use the existing final-level snap so the requested bottom is
reached without cumulative drift.

Automatic lead dimensions are cutter-scaled and capped by validated local
segment/curvature/clearance evidence. Candidate paths are checked against the
source boundary. A tangent linear lead is chosen only when it does not overlap
or cross the profile. Otherwise the existing normal linear form may be used
only after the same side and boundary checks. If neither is demonstrated,
AUTO lead is unavailable and manual intent is preserved.

Entry ranking is deterministic and limited to the existing closed-loop
geometry. It prefers tangent-continuous arc candidates, then longer
non-degenerate segments, then stable geometric/index tie-breakers. Stock,
fixture, holder and machine collision avoidance are not claimed.

## Provenance and persistence

R202 reuses `AutomaticParameterContract`, `AutomaticParameterValue`,
`AUTO`, `MANUAL_OVERRIDE`, legacy `MANUAL`, and `NOT_APPLICABLE`. Provenance
records Tool/cutter identity, contour fingerprint, depth span, unit, quality,
side, geometric bound, entry choice, lead form, reason and clamp/fallback
state. AUTO dependencies recompute; manual overrides survive unrelated changes
and can be reset to AUTO.

Persistence remains additive under `automatic_parameter_contract`. Legacy
numeric stepdown/lead values load as manual intent and are not silently
converted. AUTO policy and manual overrides round-trip through the existing
operation parameter payload. No SQLite schema migration is authorized or
required.

## UX and exclusions

Basic mode keeps Tool, profile, depth and user-owned machining intent prominent
and shows an automatic summary. Advanced mode exposes quality, independent
stepdown/lead modes, values, provenance, fallback/clamp state and reset-to-AUTO.
The existing VI/EN/KO catalog mechanism, compact two-column Function Editor,
dark theme, DPI scaling and Qt lifecycle rules remain authoritative.

R202 does not implement Pocket, drilling, Lathe, 3D finishing, material/feed/
speed databases, automatic side/direction/compensation intent, a collision
engine, a new lead curve family, or an unrelated toolpath algorithm.
