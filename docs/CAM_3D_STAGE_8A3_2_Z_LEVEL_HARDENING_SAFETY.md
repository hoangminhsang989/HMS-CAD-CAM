# CAM 3D Stage 8A.3.2 — Z-Level Finishing Hardening and Collision Safety

Trạng thái: **COMPLETED — hardening review đã hoàn tất; chưa production-safe**.

Stage này đã nâng thuật toán `z_level_finishing_3d` từ algorithm v1 lên **v2**.
Strategy payload vẫn **v1** và SQLite vẫn **schema v4**. Artifact Z-Level v1
không được xem là READY; phải calculate lại bằng v2. Parallel algorithm v3 và
payload v1 không đổi. Mọi nội dung trong stage đã được kiểm tra bằng focused
tests, regression tests và review-package consistency checks.

## Phạm vi

Phạm vi được kiểm chứng là fixed three-axis, Tool axis theo W, ball-end Tool,
selected trimmed BRep faces, nhiều vùng rời, Z-Level tool-center contour,
outer/inner trim, periodic seam, shared edge, cutter/shank/Holder assembly,
conservative linking, Toolpath IR, Simulation gate, persistence và artifact
lifecycle. Flat-end, bull-nose, tapered, lollipop, five-axis/3+2, undercut,
rest machining, hybrid steep/shallow, automatic tilting, stock-aware rest,
fixture avoidance khi thiếu geometry, machine-ready certification và
Production Post đều **fail closed** hoặc chưa hỗ trợ.

## Shared safety contract

Z-Level không tạo safety solver riêng. Adapter
`cam3d.zlevel.safety` gọi shared Stage 8A.2.2 contract cho tool assembly,
swept AABB broad phase, exact/conservative narrow phase, cutter gouge,
shank/Holder collision, swept CUT/LINK/APPROACH/RETRACT/RAPID, cancellation,
deterministic aggregation, safety-report hash và SAFE-only gate. Diagnostic
legacy `parallel.safety.*` được giữ trong shared report; adapter gắn thêm
provenance `z_level.*` và thông báo tiếng Việt.

## Contact, topology và swept validation

Mỗi contact được kiểm tra provenance selected face, finite point, differential
normal hữu hạn và normalize, orientation nhất quán, tool-center W đúng level,
allowance đúng dấu và đúng một lần, trim classification và phía tiếp xúc.
Reversed face, periodic seam, pole, degenerate edge, near-tangent, gần song
song Tool axis/normal, curvature lớn và chuyển tiếp concave/convex không được
chấp nhận chỉ vì root UV hội tụ; BRep resolver phải xác minh lại root trong
3D. Zero/repeated edge, branch/non-manifold graph, tiny/sliver loop,
self-intersection và contour collapse sau quantization được xử lý xác định
hoặc reject fail-closed.

Shared swept validation kiểm tra cả envelope cầu dọc segment, không chỉ
endpoint. Broad-phase candidates được giới hạn; narrow phase và subdivision
được kiểm tra cancellation định kỳ. Tangential contact trên chính machining
face được phân biệt với gouge; cutter chạm protected/neighbor face là collision.
Boundary escape và direct link qua inner hole/trim topology là UNSAFE hoặc
UNKNOWN; ambiguous không được READY.

## Safety scope và linking

Safety report khai báo trạng thái từng scope item: selected machining faces,
neighboring selected faces, protected model, stock, fixture, cutter, shank,
Holder, CUT, direct link, approach, retract và rapid. `NOT_PRESENT` (ví dụ
assembly không khai báo Holder) không bị đổi thành `CHECKED_SAFE`; Holder
reference thiếu/invalid là UNKNOWN. Nếu protected geometry được đánh dấu
required mà không có dữ liệu, gate trả UNKNOWN.

Direct link chỉ là candidate optimisation. Nếu swept assembly, boundary/hole
hoặc protected scope không chứng minh SAFE, Z-Level rebuild bằng
retract → clearance → rapid → approach. `machine_ready_clearance_verified`
luôn `false`; không suy diễn production clearance từ một Z height mặc định.

## Hash, lifecycle và READY gate

Contract hash v2 bao gồm strategy/algorithm/payload, operation revision,
selected-face fingerprints, machining frame, effective parameters, Tool và
shank fingerprints, Holder state/fingerprint, assembly, safety scope,
protected/fixture fingerprints, Toolpath IR hash, safety-report hash và
machine-ready state. Đổi Tool/Holder/geometry/scope/protected geometry,
algorithm, tolerance/allowance/stepdown hoặc linking policy làm artifact
không còn hợp lệ.

READY chỉ khi artifact là complete, strategy/v2/payload đúng, revision và
geometry hiện hành, scope hợp lệ, safety status SAFE, safety hash và contract
hash hợp lệ, calculation không superseded/cancelled/stale và không partial
publish. Previous READY được giữ theo lifecycle contract khi calculation mới
cancel/fail; artifact cũ chỉ dùng nếu vẫn đúng revision/version hiện hành.

## Cancellation, determinism và guardrails

Checkpoint có mặt ở scope preparation, protected indexing, cutter/shank/Holder
broad/narrow phase, per-level/per-contour/per-motion subdivision, aggregation,
safety hash, artifact hash và trước publish. Cancel không publish safety report
hoặc Toolpath một phần; latest-wins chặn callback/result cũ.

Guardrail là giới hạn deterministic cho face/candidate/narrow check,
subdivision/motion/record/group/work unit và tần suất cancellation; vượt giới
hạn trả diagnostic rõ, UNKNOWN/FAILED và không READY. Report giữ counters,
first/last occurrence, occurrence count, minimum clearance/maximum penetration,
representative provenance và exact/conservative classification theo thứ tự ổn
định. Không dùng benchmark thời gian làm hard gate.

## Simulation, Post và diagnostics

Simulation chỉ nhận Z-Level v2 artifact READY/SAFE có hash hợp lệ và phân biệt
CUT, LINK, RETRACT, RAPID, APPROACH cùng collision marker. Production Post cho
Z-Level tiếp tục fail-closed với capability reason tiếng Việt, không sinh
G-code. Diagnostic mới dùng namespace `z_level.safety.*`,
`z_level.geometry.*`, `z_level.linking.*`, `z_level.artifact.*`; raw exception
không đi vào production UI.

## Evidence package và giới hạn

Evidence Git-ignored nằm tại
`reference_private/DERIVED/CAM_3D_8A3_2_Z_LEVEL_HARDENING_SAFETY/`, gồm 18 ảnh
render từ calculation/safety result thực, một montage, 17 report JSON,
REVIEW_INDEX, summary và evidence manifest; tổng cộng 39 file. Chỉ
`calculation_records.json` giữ master records. Mỗi report scope/gouge/
shank-holder/swept/linking/boundary-hole/topology/aggregation/READY/hash/
invalidation/determinism/cancellation/guardrail/unsupported có projection,
invariant và content hash riêng. Manifest trỏ từng PNG tới record trong report
chuyên biệt và giữ PNG, calculation, safety cùng render-data hash. Ảnh không
phải sơ đồ độc lập và không được dùng để tuyên bố universal collision-free
hoặc universal gouge-free.

Stage này chưa có Production Function Editor, chưa có Production Post, chưa
machine-ready clearance certification và chưa production-safe cho mọi
topology/tool/fixture. `machine_ready_clearance_verified` vẫn `false`.
Stage 8A.3.3 chưa bắt đầu.
