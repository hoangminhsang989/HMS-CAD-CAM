# Stage 9A.9 — Lathe UI Contract V1

Status after Stage 12 closure: `UNBLOCKED_FOR_IMPLEMENTATION`
Presenter status after Stage 12 closure: `NOT_STARTED`

This is the authoritative presenter-neutral acceptance contract for the later
Stage 9A.9 Lathe UI. It does not authorize UI implementation in Stage 12.

## 1. Input snapshot

The UI presenter must consume only the immutable
`LathePresenterSnapshot`. The snapshot contains:

- exactly 11 ordered `LatheStrategyDescriptor` values;
- immutable operation snapshots in deterministic service creation order;
- optional active operation identity;
- the live read-only/closed lifecycle projection;
- deterministic typed diagnostics;
- typed `LatheWorkspaceReadiness`.

An operation snapshot exposes canonical ownership, strategy ID, canonical
parameter values, optional geometry/tool bindings, enabled state, readiness,
diagnostics, and revision. It contains no QWidget, signal, icon, OCP object,
database object, localized visible text, or raw exception.

## 2. Presenter commands

The presenter facade must delegate to the Stage 12 application service for:

- list/query exact strategy metadata;
- create operation;
- select active operation;
- read active or identified operation snapshot;
- apply one typed parameter change or an atomic typed update set;
- change strategy;
- bind/clear geometry;
- bind/clear tool;
- enable/disable;
- delete;
- validate;
- query diagnostics and workspace readiness.

All mutations carry current ownership and expected revision. The UI must render
typed `LatheCommandOutcome`; it must not directly mutate operation state or use
a generic dictionary command.

## 3. Strategy and parameter metadata

The strategy list and ordering are fixed by
`STAGE_12_LATHE_FOUNDATION_V1.md`. The UI must not add a custom/unknown row,
alias, or inferred strategy. Each strategy descriptor supplies its stable ID,
family ID, allowed geometry kinds, required tool capability, and exact ordered
parameter descriptors.

Each parameter descriptor supplies stable parameter ID, value and unit kinds,
BASIC/ADVANCED disclosure group, required state, order, bounds, exclusivity,
enum values, semantic label key, and help key. The UI uses this metadata; it
must not maintain a parallel schema or hidden parameter.

## 4. BASIC and ADVANCED groups

The five common fields follow the Stage 12 group assignments. Every
strategy-specific group assignment is also exact. BASIC fields are visible in
the normal editor flow. ADVANCED fields are discoverable through an explicit,
keyboard-accessible expansion. Hiding ADVANCED presentation must not remove or
mutate values.

The UI may seed an editor only through the dedicated V1 default factory.
Defaults are not readiness evidence and never bypass ownership, geometry, tool,
read-only, or lifecycle checks.

## 5. Feature-off/on topology

Feature-off behavior:

- no Lathe service or presenter instance is created;
- no Lathe dock, panel, action, widget, or dynamic workspace page is added;
- existing workspace topology is unchanged;
- readiness is `FOUNDATION_UNAVAILABLE` with reason
  `foundation_not_ready` where queried.

Foundation-on but presenter-not-implemented behavior (the Stage 12 result):

- foundation registry/service construction is permitted;
- readiness is `PRESENTER_IMPLEMENTATION_ALLOWED` with reason
  `presenter_not_implemented`;
- the existing TIỆN workspace remains disabled and fail-closed;
- there is still no Lathe presenter, widget, panel, dock, or new action;
- visible topology is unchanged.

Only a separately owner-approved Stage 9A.9 implementation may transition to
`PRESENTER_ACTIVE`. Stage 12 must never report that state.

## 6. Lifecycle and read-only

The presenter must discard or fail closed on project/document/source/setup
mismatch and stale generation. A source or setup switch must not silently
rebind an operation. Project close is idempotent and makes mutations unavailable.
Read-only state permits deterministic inspection but rejects every mutation.
Operation A commands cannot affect operation B, and a new service session cannot
observe a prior session's operation state.

The presenter must refresh from a new immutable snapshot after every accepted
command or lifecycle event. It must not cache mutable domain objects.

## 7. Diagnostics

The UI consumes stable diagnostic codes and semantic parameters. It localizes
them at the presentation boundary. At minimum it must distinguish missing
setup/geometry/tool, incompatible tool, invalid parameter, stale ownership,
read-only, closed, unknown strategy, disabled operation, revision mismatch,
and operation-not-found outcomes.

Diagnostic order from the foundation is authoritative and deterministic. The
UI may group or filter diagnostics visually but may not rewrite their meaning or
promote readiness to success.

## 8. I18N and accessibility requirements

Stage 9A.9 must add Vietnamese, English, and Korean catalogs for the semantic
label/help keys. Stage 12 intentionally adds no catalogs. Visible labels must
come from localization, not enum values or hard-coded domain strings.

Every interactive control must have a stable `objectName`, accessible name,
accessible description where needed, keyboard navigation, visible focus, and a
clear disabled reason. Enum choices and validation errors must remain usable
without color alone. BASIC/ADVANCED expansion and diagnostic navigation must be
keyboard accessible.

## 9. Explicit UI exclusions

Stage 9A.9 V1 must not manufacture:

- viewport selection conversion not covered by a separately approved adapter;
- toolpath preview, generation, recalculation worker, or progress claims;
- Post, G-code, simulation, stock removal, or machine execution;
- persistence or `.HMS`/database schema changes;
- a second tool catalog;
- a readiness label implying calculated or production-ready output.

## 10. Acceptance matrix

| Area | Required evidence for later Stage 9A.9 |
| --- | --- |
| Snapshot | immutable projection only; exact 11 descriptors and deterministic operation order |
| Commands | typed facade delegation; revision/ownership guards; atomic outcomes |
| Metadata | exact strategy and parameter metadata; no parallel/hidden schema |
| Disclosure | BASIC normal flow; ADVANCED explicit and accessible; values preserved |
| Feature off | no service/presenter/UI/topology delta; `foundation_not_ready` |
| Foundation on | no presenter/UI; TIỆN disabled; `presenter_not_implemented` |
| Lifecycle | close/switch/stale/read-only/isolation fail closed |
| Diagnostics | stable typed codes localized only in UI; no fake success |
| I18N | VI/EN/KO catalogs for all semantic keys |
| Accessibility | object names, keyboard/focus, accessible names/descriptions, non-color-only status |
| Regression | existing Home/CAD/Mill/Simulation/Post topology and Stage 9A.8 semantics unchanged |
| Exclusions | no toolpath/Post/G-code/simulation/persistence/selection adapter unless separately approved |

## 11. Implementation allowlist guidance

A later owner-approved Stage 9A.9 work package must lock an exact path allowlist
after auditing current UI feature flags, workspace shell, Function Editor,
Operation Manager, localization catalogs, and accessibility tests. It should
reuse the Stage 12 facade and DTOs rather than modify Lathe domain/application
contracts. Any required change to persistence, toolpath, Post, simulation, or
shared operation semantics is a separate architecture decision and must stop
the UI work package before implementation.
