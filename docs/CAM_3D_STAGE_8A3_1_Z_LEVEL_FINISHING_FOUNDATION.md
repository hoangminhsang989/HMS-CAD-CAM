# CAM 3D Stage 8A.3.1 — Z-Level Finishing Foundation

Trạng thái: **IN PROGRESS — foundation review, chưa production-safe**.

Stage này bổ sung thuật toán nền cho gia công tinh theo cao độ Z trên CAM 3D
fixed three-axis. Không có Production Function Editor, không sửa popup/UI/icon,
không viết Production Post và không nâng SQLite schema (vẫn v4).

## Scope và contract

- Strategy identity: `z_level_finishing_3d`.
- Algorithm version: `1`; strategy payload version: `1`.
- Tool axis cố định theo `W` của machining frame; `W` trùng Setup WCS Z.
- Tool duy nhất: ball-end; flat-end, bull-nose, tapered, 3+2, five-axis,
  undercut, tilting, rest machining và chiến lược hybrid đều fail closed.
- Input là selected trimmed BRep face references đã persist trong operation và
  calculation mesh có provenance từng triangle. Không lưu OCP object trong payload.

## Geometry definition

Với một contact point `p`, differential normal `n`, bán kính ball `r` và
surface allowance `a`, tool-center locus là:

`c(p) = p + (r + a) n`

Z-Level không cắt giao tuyến mặt gốc với mặt phẳng W. Mỗi level giải implicit
field:

`g(u,v) = frame_w(c(u,v)) - requested_level`

và trace `g = 0` trong miền trimmed. Vì vậy contact, normal, radius và
allowance đều có mặt trong validation; không dùng contact height đơn thuần.

## Machining frame, bounds và schedule

`ZLevelMachiningFrame` lưu origin cùng U/V/W trực giao, thuận tay phải. Bounds
được tính chỉ từ selected part-face triangles; W bounds đã bao gồm ball-center
offset và allowance. `top_level`/`bottom_level` là **tool-center levels**, đều
inclusive. Schedule tính theo index (`top - i * stepdown`), làm tròn ổn định,
thêm bottom một lần và chặn duplicate/level ngoài envelope.

Guardrail deterministic gồm maximum level count, face count, contour count,
point count và subdivision depth. Vượt guardrail trả diagnostic
`z_level.excessive_*`, không trả path một phần.

## Implicit field, root tracing và topology

Mỗi triangle có affine field với normal differential từ calculation mesh. Edge
bracketing và root interpolation tạo zero-set segments; coplanar triangle edges
được xử lý như boundary edges và shared internal edges bị loại bỏ. Endpoint
được snap theo quantization tolerance có kiểm soát.

Contour graph có stable ordering, duplicate-segment removal, disconnected
components, closed/open classification, region ID, loop type và predecessor.
Branch point, ambiguous trim, singular normal, boundary escape và bất thường
self-intersection đều fail closed. Outer boundary được classify
`interior/on_boundary/outside/ambiguous`; hole/disconnected topology không được
nối bằng khoảng cách thuần túy.

Discretization chia theo maximum segment length, giữ level deviation,
normal/face provenance, không có duplicate liên tiếp và không publish spike.
CW/CCW là orientation policy; `automatic` dùng CCW mặc định. Đây không phải
nhãn climb/conventional vì chưa có material-side contract.

## Contact validation

Mỗi `ZLevelPathPoint` lưu:

- face provenance và triangle source;
- contact point, normalized differential normal;
- tool center và requested level;
- level/contact/allowance deviation;
- boundary classification.

Validation loại NaN/inf, normal suy biến, contact ngoài selected trim,
tool-center level sai tolerance và allowance sai dấu. Preview thống kê rejected
sample và ambiguous sample.

## Ordering và linking

Contour được sắp xếp theo level top-to-bottom, region ID và contour index ổn
định. Trên cùng level, linking không thay đổi topology. Chuyển vùng dùng shared
Stage 8A.2.2 safety contract: retract → clearance → rapid → approach; không
direct-link chỉ vì endpoint gần. `machine_ready_clearance_verified` vẫn
`false`.

## Safety integration

Z-Level dùng lại cutter/shank/holder primitives, swept AABB broad phase, narrow
phase, gouge/collision diagnostics, cancellation cadence, diagnostic
aggregation và SAFE-only gate của Stage 8A.2.2. Motion provenance được mã hóa
theo level/contour/segment trong Toolpath IR. Holder thiếu geometry hoặc safety
UNKNOWN không thể trở thành READY.

Trong review evidence, `safe_zlevel` chứng minh Z-Level candidate đi qua shared
safety gate; các cutter/shank/Holder/rapid collision probe tái sử dụng fixture
của Stage 8A.2.2. Z-Level không tạo safety solver riêng. Các diagnostic
`parallel.safety.*` trong những probe đó là diagnostic của shared legacy
contract, không phải strategy production mới.

## Toolpath IR và lifecycle

IR chỉ chứa CUT, LINK approach, RETRACT và RAPID/CLEARANCE, không hard-code
G-code. Marker `z_level.safety.contract` gắn strategy, algorithm/payload version,
safety report fingerprint và `machine_ready_clearance_verified=false`.

Artifact đi qua Candidate → SAFE/READY hoặc Failed/Cancelled/Stale/Unknown.
`publish_toolpath` kiểm tra operation revision, computation token, effective input
fingerprint và latest-wins. Calculation mới không làm stale artifact của
Parallel operation khác; publish partial bị cấm; previous READY được giữ.

## Cancellation, persistence, Simulation và Post

Checkpoint có ở validation, bounds, schedule, triangle tracing, graph,
discretization, ordering/linking, safety và trước publish. Cancel không ghi
database dở dang và không publish partial.

Operation payload dùng primitive JSON hiện có; face/tool/assembly references,
frame, level parameters, revision, artifact metadata và safety hash giữ được qua
Save/Open. Cache có thể tạo lại; không lưu worker/cancellation/OCP/temporary UV
graph.

Simulation chỉ nhận artifact có marker SAFE hiện hành, đúng strategy/version và
artifact lifecycle VALID. Production Post cho Z-Level fail closed với capability
reason rõ ràng; chưa có NC generator trong stage này.

## Diagnostics và giới hạn

Namespace nội bộ `z_level.*` bao phủ invalid face/workplane/tool/stepdown,
excessive levels/contours/points, singular normal, unresolved root, ambiguous
trim, branch/open contour, duplicate/self-intersection, invalid contact,
level/allowance deviation, boundary escape, safety unknown, cancelled,
superseded và stale artifact. UI có thể dịch message sang tiếng Việt; không
đẩy exception thô ra production UI.

Đây là foundation dựa trên calculation mesh đã được CAD adapter xác minh.
Universal gouge-free/collision-free, fixture/stock avoidance khi thiếu geometry,
automatic holder optimization, cusp-height adaptive stepdown và machine-ready
clearance chưa được chứng nhận. Không tuyên bố production-ready.

## Review package và QA

Review package Git-ignored nằm tại
`reference_private/DERIVED/CAM_3D_8A3_1_Z_LEVEL_FOUNDATION/`, gồm 15 sơ đồ,
montage và JSON/Markdown reports. Ảnh được render trực tiếp từ
`calculation_records.json`; `evidence_manifest.json` liên kết từng ảnh/report
sample với fixture, calculation ID, revision/fingerprint và Toolpath/safety
hash. Các report level/contact/topology/determinism/cancellation/lifecycle,
safety/guardrail/unsupported lưu số liệu và diagnostic thực thay vì chỉ dùng
cờ boolean. Package không phải asset production.

Focused tests: `tests/unit/test_z_level_foundation.py` và
`tests/unit/test_z_level_review_package.py`. Full QA phải chạy tuần tự với
Python 3.14, `pip check`, compileall và pytest; Stage 8A.2.3/Parallel và Stage
9A.6 là regression baseline. Chưa commit ở stage này.
