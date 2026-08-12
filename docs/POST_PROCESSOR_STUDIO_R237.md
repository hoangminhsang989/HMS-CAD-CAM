# R237 — Post Processor Studio, Tranche 1

## Boundary

Post Processor Studio manages immutable Post source revisions. It is separate
from CNC control, physical qualification, and the normal CAM `Post -> Export`
path. It never declares `MACHINE_READY`, Level2, or Level3.

## Lifecycle

`PostDefinition` binds a Post family to an explicit machine/controller/tool
interface. `PostRevision` stores exact immutable source bytes, parent lineage,
encoding and line-ending metadata. Current lifecycle is projected from separate
validation, regression and approval evidence, so approving a revision never
rewrites its committed identity.

The safe default is:

`DRAFT/CANDIDATE -> VALIDATED -> REVIEW_REQUIRED -> APPROVED`

Activation produces only a `PostActivationPlan`. The plan requires an approved
candidate and exact expected parent SHA, but remains `NOT_ACTIVE_GLOBALLY` in
R237. Global replacement and rollback execution require separate authority.

## Storage and packages

`PostStudioStore` writes append-only source/revision metadata under
`post/studio/` of an HMS project. Every object is verified after atomic write;
the manifest stores each file size and SHA-256. Export packages are deterministic
ZIPs with fixed entry timestamps. Imports verify all manifest hashes before
registering immutable bytes and reject conflicts.

No SQLite migration is required in Tranche 1: Post Studio persistence is an
additive project-owned artifact store and schema remains v5.

## WorkNC provider and R233

`WorkNC2021PostProvider` exposes the recovered chain as a provider capability.
It only prepares a unique sandbox workspace and audit manifest. A missing chain
returns `UNAVAILABLE`; it never falls back to global Post writes.

The R237 acceptance tests import the exact active FANUC-SHL bytes and R233
isolated candidate bytes, preserve their SHA-256 lineage, classify the G40
source delta, consume R233 generated NC evidence, and retain deployment state
`NOT_ACTIVE_GLOBALLY`.

## Visual rules and UI

The visual-rule projection maps known G40/G41/G42/G28/G53/G54 forms. Unknown
legacy directives are marked `RAW_SOURCE_REQUIRED`, preserving the raw source
without a lossy serializer. The Vietnamese-first Post Studio is a modeless CAM
menu window so it does not alter the existing compact Post dock group or block
normal CAM export. Validation exposes immediate state; sandbox work is isolated
from normal NC generation.
