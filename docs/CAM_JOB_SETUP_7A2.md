# CAM Job and Setup 7A.2

## Aggregate boundary

`CamJob` is the aggregate root and owns an ordered tuple of immutable `Setup`
snapshots. Callers cannot mutate setup or fixture collections directly. Successful
state-changing methods replace a complete validated setup and increment the job
revision once. Failed changes leave the aggregate untouched. Removing the active
setup selects the first remaining setup; an empty job has no active setup.

## Source-scope policy

Each setup declares one primary machining-model source and zero or more auxiliary
source UUIDs. The main model reference must use the primary source. Reference-backed
stock and fixtures may use another project source only when that source is explicitly
listed as auxiliary. A source mismatch is rejected; the domain never aliases or
resolves foreign geometry implicitly.

## WCS invariants

The WCS origin has a known length unit. Its finite X, Y and Z axes must be unit
length and mutually orthogonal within the dimensionless tolerance `1e-9`.
`X cross Y` must equal Z, so left-handed frames are unsupported. No OCP coordinate
or transform object crosses the domain boundary.

## Stock variants

Box stock uses three positive dimensions and a frame. Cylinder stock uses positive
diameter and length plus a frame; radius is not a second convention. Dimensions and
frame share one known unit. From-model and custom stock carry only a persistent
`GeometryReference`; they do not resolve or own CAD geometry. Variant codecs reject
mixed or unknown payload fields.

## Fixture instances

Each fixture has its own `FixtureInstanceId`, affine 4x4 placement, role and enabled
flag. Multiple instances may share one geometry reference while retaining distinct
IDs and placements. Translation carries a known length unit matching the setup WCS.
Perspective matrices, duplicate IDs and undeclared sources are rejected.

## Not implemented

This stage does not implement tools, holders, machines, operation trees, toolpaths,
recompute dependencies, SQLite/project lifecycle integration, UI, CAD adapters,
simulation, Post Processor, G-code or milling/turning algorithms.
