# CAM 3D Stage 8A.2.2 — Parallel Hardening and Collision Safety

## Trạng thái và phạm vi

Stage 8A.2.2 làm cứng Parallel Finishing Foundation 8A.2.1 mà không xây lại
generator, không tạo Toolpath IR thứ hai và không thêm Production Function
Editor. Phạm vi vẫn là ball-end mill, fixed three-axis, Setup WCS và các mặt
BRep đã chọn. Stage này không phải chứng chỉ production-safe hoặc bảo đảm
gouge-free cho mọi topology.

Pipeline hiện tại:

1. resolve snapshot operation, CAM 3D context và tool assembly;
2. generate contact path, ball-center path và candidate Toolpath IR;
3. dựng tool assembly envelope và collision scene native-free;
4. kiểm tra topology, clearance và toàn bộ swept motion;
5. tạo safety report;
6. chỉ rebuild artifact với SAFE marker và publish khi report là `SAFE`;
7. stale, unsafe, unknown, cancelled hoặc failed result không được publish.

Generator, safety validator, collision math và artifact publisher nằm ở các
module riêng. UI không đọc database hoặc thực hiện collision query.

## Safety contract

Năm trạng thái được dùng thay cho boolean:

- `SAFE`: toàn bộ scene và motion trong capability hiện tại đã được chứng minh
  an toàn; đây là trạng thái duy nhất được publish.
- `UNSAFE`: có collision/gouge hoặc clearance chắc chắn không đạt.
- `UNKNOWN`: thiếu geometry/evidence hoặc chạm guardrail nên không thể chứng
  minh an toàn.
- `CANCELLED`: cancellation được quan sát ở checkpoint có giới hạn.
- `FAILED`: lỗi nội bộ tại application boundary; exception thô không đi ra UI.

Mỗi finding chứa code `parallel.safety.*`, severity, operation/pass/segment/
motion index, cutter/shank/holder, geometry source, face ID, closest distance,
penetration, tolerance, contact/tool point và debug metadata khi có. Report gắn
calculation ID, algorithm version, policy, statistics và deterministic hash.

## Phân loại geometry

- `MACHINING_FACE`: part surfaces đã chọn; chỉ cutter contact đúng provenance
  và đúng contact zone mới được miễn collision.
- `PROTECTED_PART`: triangle trong safety mesh nhưng không thuộc selection đã
  chọn.
- `CHECK_SURFACE`: keep-out/check geometry chính thức.
- `FIXTURE`: fixture surface chính thức trong MachiningZone3D.
- `STOCK`: unavailable nếu project không cung cấp stock geometry chính thức;
  validator không tạo stock giả.

Mọi surface đã khai báo phải xuất hiện trong calculation mesh. Surface thiếu
trả `UNKNOWN` và không publish. Non-selected part face không được đưa vào zone/
safety mesh không được ngầm coi là đã kiểm tra; giới hạn này được ghi trong
capability report.

## Tool assembly model

Tool envelope tái sử dụng `cam.simulation.envelope` và chuyển sang primitives
relative với ball-center pose:

- sphere cho ball tip;
- cylinder cho cutting section;
- cylinder cho exposed shank;
- sequence frustum/cylinder cho holder profile.

Flute length, shank diameter/length, stickout, gauge length, holder gauge line
và sections đều đến từ immutable domain snapshots. Frustum narrow phase dùng
maximum section radius bảo thủ và ghi approximation. Assembly có holder
reference nhưng thiếu/mismatch snapshot trả
`parallel.safety.missing_holder_geometry`. Assembly khai báo không có holder
được ghi `declared_absent`, không bị biến thành holder giả.

Mỗi safety report ghi tường minh `checked_components`,
`unverified_components`, `holder_state`, fingerprint của tool assembly safety
model và `safety_scope`. Holder hợp lệ thuộc checked scope; holder có reference
nhưng thiếu/sai snapshot làm report `UNKNOWN`. `declared_absent` chỉ chứng minh
an toàn trong assembly đã khai báo: cutter/shank được kiểm tra, holder nằm trong
unverified scope. Marker artifact mang cả scope fingerprint và fingerprint
assembly của Toolpath IR. Simulation có thể dùng declared assembly scope, nhưng
Post capability yêu cầu holder verification phải từ chối artifact
`declared_absent`; kết quả này không được mô tả là holder-safe.

## Broad phase và narrow phase

Broad phase dùng swept AABB bao toàn primitive, margin và cả hai đầu motion.
Triangle giữ index/source order canonical từ calculation mesh; candidate order
không phụ thuộc hash/set iteration. Broad phase cho phép false-positive nhưng
không được bỏ sót overlap.

Narrow phase dùng:

- exact center-segment/triangle distance cho sphere sweep;
- exact swept axis parallelogram/triangle distance cho cylinder axis;
- conservative maximum-radius axis sweep cho frustum/holder;
- point/segment/triangle và triangle/triangle distance native-free.

Sphere tangent expected contact không bị coi là gouge. Protected contact dùng
clearance margin và fail ngay cả khi cutter contact với face không được chọn.

Finding collision được aggregate theo calculation, operation, pass, segment,
motion, tool component, geometry source/ID và diagnostic code. Nhiều triangle
hoặc subdivision cùng key tạo một item giữ mẫu có penetration lớn nhất (sau đó
minimum clearance), cùng `occurrence_count`, sample range, minimum clearance,
maximum penetration và swept interval. Khác motion/component/geometry/pass vẫn
là finding riêng; report limit đếm unique key thay vì raw sample.

## Numerical tolerance và operational clearance

- `numeric_epsilon_mm`: ổn định phép tính/canonicalization; không thay thế
  khoảng hở vận hành.
- `contact_tolerance_mm`: phân loại expected cutter contact đúng provenance.
- `gouge_tolerance_mm`: penetration được dung sai trước khi kết luận gouge.
- `shank_clearance_mm`, `holder_clearance_mm`, `rapid_clearance_mm`: khoảng hở
  tối thiểu bắt buộc theo component/motion.
- `boundary_clearance_mm`: exclusion margin của boundary policy.

Các giá trị clearance Stage 8A.2.2 hiện có nguồn
`stage_8a2_2_internal_minimum`, đơn vị mm. Chúng là ngưỡng phát hiện nội bộ,
không phải machine-ready clearance và không được gọi là conservative production
clearance. Boundary semantics là clearance thực tế phải lớn hơn required
clearance; bằng đúng boundary vẫn fail-closed. NaN, infinity và required
clearance âm bị từ chối. Policy và toàn bộ giá trị trên nằm trong safety-report
hash, nên thay đổi operational clearance tạo hash mới.

Review contract khóa rõ `machine_ready_clearance_verified = false`. Giá trị
0,001 mm hiện tại là internal detection minimum, không phải khoảng hở vận hành
được chứng nhận cho máy, holder, fixture hoặc điều kiện gia công thực tế.

## Expected contact và gouge semantics

Expected contact cần đồng thời:

- component thuộc cutter;
- motion là cut/approach/retract dự kiến;
- triangle thuộc selected machining face;
- source face nằm trong provenance của contact point;
- triangle gần contact segment dự kiến theo contact/gouge tolerance;
- penetration không vượt gouge tolerance.

Selected-face penetration, contact ngoài zone, protected-face contact, offset
sai phía hoặc secondary contact đều không được miễn chỉ vì distance nhỏ.
Surface allowance, numeric epsilon, contact tolerance, gouge tolerance, shank/
holder/rapid/boundary clearance là các giá trị riêng.

## Swept-path validation

Mọi `RapidMove` và `LinearMove` được biến đổi từ Setup WCS về model/world và
kiểm tra toàn đoạn. Motion được subdivide deterministic theo length và maximum
validation step; mỗi submotion vẫn dùng swept primitive query, không chỉ kiểm
tra sample endpoints. Vì vậy rapid với hai endpoint an toàn nhưng cắt qua wall
ở giữa vẫn bị phát hiện.

Guardrails gồm protected triangles, candidates, broad/narrow/total checks,
subdivisions, checks per motion, report items và cancellation cadence. Vượt
limit trả `parallel.safety.limit_exceeded`/`UNKNOWN`, không publish.

## Boundary, sharp edge và curvature

Foundation giữ source-face provenance trong contact paths; không bridge giữa
disconnected segments. Closed planar INSIDE boundary tiếp tục được clip trước
discretization. Calculation epsilon chỉ canonicalize coincident mesh nodes;
contact tolerance không được dùng để nối khoảng trống tùy tiện.

Normal discontinuity/sharp edge không được average qua C0 edge. Generator hoặc
safety validator split/retract hay fail-closed bằng structured diagnostic.
Concave local offset contraction được kiểm tra; concave channel nhỏ hơn ball có
secondary contact/gouge và không được làm mượt để che lỗi. Convex cylindrical
BRep fixture dùng differential normal vẫn SAFE. Inner multi-loop hole/island
không có payload chính thức trong boundary v1 được ghi unsupported; protected
island geometry vẫn được collision engine kiểm tra khi có trong safety mesh.

## Linking, approach, retract và rapid

Stage này tiếp tục ưu tiên linking bảo thủ:

- mỗi disconnected segment retract lên retract/clearance;
- rapid positioning chỉ chạy ở clearance;
- approach/contact và retract được kiểm tra toàn sweep;
- không phát sinh low direct link giữa disconnected regions;
- clearance/retract phải nằm trên protected mesh bounds cộng margin;
- validator không tự tăng clearance âm thầm.

Nếu về sau có local direct link, nó chỉ được phép khi cùng swept validator trả
SAFE; nếu không phải fallback retract hoặc fail-closed.

## Algorithm version và compatibility

- trước stage: algorithm version 2;
- sau hardening: algorithm version 3;
- lý do: pass acceptance, safety filtering, deterministic input hash, artifact
  acceptance và publish semantics thay đổi;
- strategy payload vẫn version 1 vì field persistence không đổi;
- SQLite project database vẫn schema v4;
- artifact v2 cũ không có current SAFE marker nên Simulation/Post từ chối;
- OperationParameterSet v1 vẫn backward round-trip.

Published artifact v3 có đúng một `parallel.safety.contract` marker gồm
algorithm version 3, status SAFE, safety-report fingerprint, checked/unverified
components, holder state, safety scope và tool-assembly fingerprints. Candidate
trước validation chỉ mang status `candidate`, không được coi là safe.

## Cancellation, latest-wins và atomicity

Cancellation được kiểm tra trước safety, trong triangle broad scan, narrow
phase, giữa motion/subdivision và ngay trước publish. Report cancelled không
trở thành latest artifact. Final current-operation/token/input checks bảo vệ
latest-wins; stale result không ghi đè calculation mới.

Artifact file vẫn được ghi qua staging + fsync + atomic replace. Unsafe/unknown/
cancelled/failed pipeline không gọi store. READY artifact trước đó không bị xóa
hoặc thay bằng candidate lỗi. Project close dùng worker cancellation/lifecycle
hiện có; OCP shape không được serialize hay giữ trong report.

## Simulation và Post

Simulation preflight đã yêu cầu operation artifact state `VALID`, artifact
fingerprint hiện hành và, riêng Parallel, current SAFE contract marker. Unsafe,
unknown, candidate hoặc stale-algorithm artifact không được tạo Simulation
request.

Post mặc định vẫn không advertise Parallel capability. Nếu một Post definition
tương lai advertise strategy này, lowering vẫn yêu cầu SAFE marker trước khi
tiếp tục. Stage này không tuyên bố production Post support và không thay golden
NC hiện có.

## Fixtures, tests và review

Automated fixtures bao gồm planar expected contact, protected wall, shank,
holder, full swept rapid, disconnected retract linking, missing holder,
safety-limit, convex cylindrical BRep và concave cylindrical channel. Regression
8A.2.1 tiếp tục kiểm tra trim boundary, disconnected region, source provenance,
sharp edge, differential normals, persistence, worker, Simulation sampling và
Post capability gate.

Review package Git-ignored:

`reference_private/DERIVED/CAM_3D_8A2_2/`

Package có numeric reports, 19 ảnh kỹ thuật 1200×800, ba run cho bảy case,
collision order/hash đầy đủ, tám safety-report sample có số liệu,
cancellation/atomic publish reports và danh sách unsupported cases.

## Giới hạn còn lại

- Không bảo đảm universal collision/gouge-free ngoài declared scene.
- Stock/fixture chỉ được kiểm tra khi có geometry chính thức.
- Frustum holder dùng conservative maximum-radius narrow phase.
- Surface singularity, invalid/non-manifold topology và guardrail overflow
  fail-closed.
- Chưa hỗ trợ non-ball cutter, 5-axis, multi-axis holder orientation hoặc
  production Post.
- Chưa có Production Function Editor; đây phải là stage riêng sau khi review
  hardening được duyệt.
