# Stage17A — CAM Automatic Parameters & Operation Intelligence V1

## Canonical freeze

- Product increment: `STAGE17A_CAM_AUTOMATIC_PARAMETERS_AND_OPERATION_INTELLIGENCE`.
- Program container: `MEGA_WP3_AUTOMATIC_CAM_SETUP_MODERNIZATION`.
- Base: `aaeca0bb7e0ef64f299c5c719f5e5d1b67fd127e` / tree `b6e09a204e3dc5c27244e2e50312c5f4d73dad1e`.
- Stage16A remains closed. This tranche is local-only and must not mutate protected main.

## Goal

Reduce CAM numeric input by deriving safe, deterministic setup values from validated
Tool/cutter geometry, stock or planar-face evidence, operation context, unit and
quality profile. Basic shows useful derived values; Advanced exposes intentional
manual overrides and the reason for each value.

## Tranche 1 scope

1. Extend the Qt-free automatic contract with `AUTO`, `MANUAL_OVERRIDE` (while
   loading legacy `manual`) and `NOT_APPLICABLE`, typed provenance inputs, bounds,
   clamp state and fail-closed diagnostics.
2. Add one shared Facing policy engine with distinct `STOCK_BOX` and
   `PLANAR_FACE` policies.
3. Derive only values whose evidence is present: Facing/Planar stepover and
   stepdown; Stock BOX overtravel. Feed, spindle, allowance and linking heights
   remain `NOT_APPLICABLE` unless an existing validated process contract supplies
   them. Planar target height remains geometry-owned.
4. Integrate derived values into the existing Facing and Planar Function Editor;
   Basic displays compact read-only derived values and Advanced preserves editable
   override fields plus reset-to-auto.
5. Preserve existing Parallel and Z-Level automatic policies as
   `ALREADY_AUTOMATIC`; do not alter their toolpath algorithms.

## Explicitly deferred operations

- Contour/Pocket: different profile/region geometry policies require a separate
  operation-specific evidence contract before automatic derivation.
- Drilling/Reaming/Boring/Tapping: feed/cycle/material/controller semantics are
  not safely inferable from the current contract.
- Lathe, Post, machine kinematics, stock-removal simulation and schema redesign:
  out of scope.

## Derivation policy V1

Quality factors are explicit and deterministic: `FAST=0.65`, `BALANCED=0.50`,
`HIGH=0.35`.

- Stepover: `diameter * quality_factor`; Bull Nose is additionally bounded by
  `diameter - corner_radius`; unsupported cutter geometry is `NOT_APPLICABLE`.
- Stepdown: `min(depth_span, axial_cutting_length * quality_factor)` when both
  depth span and axial cutting length are positive; otherwise `NOT_APPLICABLE`.
- Stock BOX overtravel: `max(tolerance * 2, diameter * 0.25)` when stock and
  cutter evidence exist; otherwise `NOT_APPLICABLE`.
- Every positive result is clamped to `[max(tolerance, 1e-9), physical_upper]`.
  A clamp is recorded in provenance. No display-unit value is used as an internal
  unit, and no machine/material property is invented.

## AUTO / override behavior

- AUTO recomputes when Tool, holder/geometry evidence, unit, quality profile or
  operation context changes.
- Advanced edits create an explicit manual override contract entry; manual values
  are not silently replaced by later AUTO recomputation.
- Reset-to-auto discards only the selected override and restores the current
  validated derivation.
- Missing/invalid evidence produces `NOT_APPLICABLE` or `UNRESOLVED`, an explicit
  prerequisite explanation and no fabricated numeric value.

## Compatibility and persistence

The existing `automatic_parameter_contract` remains additive and bounded. Existing
contracts with `mode=manual` and missing new provenance fields continue to load.
Facing operation payload format/schema remain version 1; the contract is metadata
inside the existing parameter set and does not reinterpret legacy numeric values.
Malformed/new unsupported payloads fail closed.

## Acceptance criteria

1. Shared engine is Qt-free, typed, deterministic and does not import translated UI strings.
2. AUTO/MANUAL_OVERRIDE/NOT_APPLICABLE round-trip and legacy `manual` loading pass.
3. Facing Stock BOX and Planar FACE policies use real End Mill, Ball End, Bull Nose
   and supported custom cutter geometry; unsupported families fail closed.
4. Provenance includes value, mode, reason, inputs, bounds and clamp state.
5. Tool/geometry/unit/profile changes recompute AUTO fields; overrides survive;
   reset-to-auto works without mutating unrelated values.
6. Basic/Advanced editor schemas expose derived values, override controls and
   localized explanations without breaking existing field identity or lifecycle.
7. VI/EN/KO key parity, placeholders, UTF-8 and untranslated production-string
   audit pass for all new labels.
8. Legacy Facing projects load/save without numeric reinterpretation; new contract
   round-trips and malformed contracts fail closed.
9. Focused, bounded and broad regression show candidate-induced and indeterminate
   failures `0`; `NEW_FAILURE_DELTA_R193=0`.
10. Protected main retains exact HEAD/tree, five hashes and eight R191 outputs.

## Production/test manifests

Expected production paths: `cam/automatic_parameters.py`, new
`cam/automatic_facing.py`, `ui/function_editor/model.py`,
`ui/function_editor/strategies/common_milling.py`, and necessary catalog/UI host
integration only. Expected tests: shared automatic contract tests, new Facing
policy tests, existing Facing/Planar editor tests, persistence and localization
parity tests, plus bounded callers determined after implementation.

## Rollback boundary

Reject the candidate if any legacy numeric payload changes meaning, an unsupported
tool receives a fabricated value, an override is lost, schema compatibility fails,
or any candidate-induced/indeterminate regression remains. Revert only candidate
commits in the clean worktree; protected main is never reset, stashed or edited.

