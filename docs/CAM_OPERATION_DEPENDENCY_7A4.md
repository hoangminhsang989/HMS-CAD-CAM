# CAM Operation Tree and Dependency 7A.4

## Aggregate and tree invariants

Each `Setup` owns one immutable `OperationTree` snapshot with a deterministic root
ID derived from the setup ID. A node is either a group or an operation. Groups own
ordered child IDs; operation nodes own exactly one `Operation` and never have
children. Parent/child links are checked in both directions, every node is reachable
from the root, and node/operation IDs are unique within the setup. Public collections
are tuples. Mutation returns a fully validated replacement and increments the tree
revision only after success. Removing a group recursively removes its subtree,
operation records and incident dependency edges. The root cannot be removed or moved.

`CamJob.update_operation_tree` replaces the complete setup snapshot. A successful
change increments the setup revision and job revision once; failure changes neither.
Setup codec v2 stores the tree and revision. Setup v1 payloads from 7A.2 remain
accepted and receive an empty deterministic tree at revision zero. The outer CamJob
v1 envelope remains compatible because its nested setup codec is version-aware.

## Strategy, parameters and geometry inputs

`Operation` uses family plus a versioned strategy key instead of an algorithm class
hierarchy. `OperationParameterSet` accepts only finite, bounded JSON primitives in a
validated tuple of uniquely named values. Names are normalized into canonical order;
schema and strategy version 1 are the only versions supported in 7A.4. Arbitrary
dictionaries, nested/native objects, NaN and infinity are rejected. Parameters are
controller-neutral and contain no G-code command model or syntax contract.

Geometry inputs are ordered records with their own `GeometryInputId`, semantic role,
required flag, expected reference kind and optional selection order. Repeated uses of
the same persistent geometry remain distinct input occurrences. The domain never
resolves or rebinds CAD data. Adapter-supplied resolution results drive diagnostics;
missing, ambiguous, stale, changed-topology and source-mismatch results cannot make a
required input valid.

Tool assemblies and optional machines are referenced by ID plus expected revision,
content fingerprint and known unit. Machine requirements also carry semantic
capabilities. Native-free assessment contracts distinguish missing, stale,
capability and unit mismatch without coupling operations to a library adapter.

## Dependency DAG and dirty propagation

`DependencyGraph` is separate from visual tree structure. Typed edges cover geometry,
WCS, stock, fixture, tool, machine, parameters and operation output. Every target and
every operation-output source must exist. Duplicate edges are rejected. Cycles among
operation-output edges are rejected and topological ordering uses canonical operation
ID order when several nodes are ready.

An external dependency change dirties direct consumers with its specific reason and
then dirties operation-output descendants with `UPSTREAM_CHANGED`. Unrelated
operations are unchanged. Disabled operations keep their dirty/stale information;
group status is derived later and is not authoritative state.

## Recompute and stale-result policy

Artifact states are `MISSING`, `DIRTY`, `COMPUTING`, `VALID` and `FAILED`. Compute may
start only from missing, dirty or failed state and creates one generation token while
capturing the canonical input fingerprint. Only that token may publish or fail. A
changed fingerprint or disabled operation rejects the result and leaves the state
dirty; an old token cannot overwrite newer state. Valid artifacts become dirty before
replacement. Input fingerprints use canonical SHA-256 data from strategy/parameters,
ordered geometry and setup facts, tool/machine snapshots and upstream artifacts;
Python `hash()`, time and random values are excluded from content identity.

Validation diagnostics contain serializable severity, code, message and sorted text
context. Codecs for nodes, trees, operations, inputs, parameters, DAG, diagnostics and
artifact state are strict, deterministic and reject future versions atomically.

## Current limits

7A.4 adds no machining algorithm, Toolpath IR, worker, simulation, collision checking,
Post Processor, G-code, UI, SQLite/project lifecycle integration or CAD resolver.
External dependency keys are stable adapter-provided identities; persistence adapters
and artifact storage belong to later stages.
