# Nhiệm vụ hiện tại — Stage 8A.3.1

## Trạng thái ưu tiên

- Functional UI baseline: `24d2a42` — hoàn thành đánh giá đồng bộ giao diện
  phay 2D Stage 9A.5.4.
- **Stage 8A.3.1 — Z-Level Finishing Foundation — COMPLETED.**
- **Stage 8A.2.3 — Parallel Finishing Production Function Editor — COMPLETED.**
- **Stage 9A.I1 — HMS Isometric CAD/CAM Icon Pack: DEFERRED.**
- **Stage 9A.6 — Drilling Family Production Function Editors — COMPLETED.**
- **Stage 8A.2.1 — Parallel Finishing Foundation — COMPLETED.**
- **Stage 8A.2.2 — Parallel Finishing Hardening and Collision Safety — COMPLETED.**

## Kết quả hoàn thành Stage 8A.2.3

- Production Function Editor native-free cho Parallel Finishing, dùng trực tiếp
  algorithm v3/payload v1 và safety pipeline Stage 8A.2.2.
- Geometry, ball-end tool/holder, direction, cut parameters, clearance/retract,
  conservative linking, capability và structured safety diagnostics.
- Preview/Apply/Calculate/Cancel, worker progress, latest-wins, Operation Manager,
  persistence schema v4 và Simulation/Post fail-closed gates.
- Native Windows review cuối gồm 19 trạng thái và một montage; người dùng đã
  duyệt GUI để hoàn tất Stage 8A.2.3.
- UX CAM tự động-first đã được bổ sung cho Parallel: Basic chỉ giữ Hình học,
  Dao, Chất lượng và Tham số tự động; Advanced có manual override riêng và giữ
  giá trị nhập sai để sửa.
- Shared automatic-parameter contract được lưu trong payload primitive hiện có,
  tham gia effective/artifact fingerprint; algorithm v3, strategy v1 và SQLite
  schema v4 không đổi.
- Gói duyệt bổ sung gồm 15 ảnh (14 trạng thái + một montage) tại
  `reference_private/DERIVED/UI_STAGE_8A2_3_AUTO/`; kết quả đã được hợp nhất vào
  gói duyệt cuối được người dùng chấp thuận.
- Kiến trúc thao tác CAM tập trung đang chuyển sang cột trái + một popup modeless
  duy nhất: single-click chỉ chọn, double-click/Enter mở, dirty-switch có ba lựa
  chọn rõ ràng và tối đa một popup con. Cả 9 production editor dùng chung host.
- Registry minh họa HMS vector đã bao phủ 9 editor; Parallel có scene động theo
  One-way/Zigzag, hướng, linking, quality và Auto/Tùy chỉnh bằng debounce nhẹ.
- Tạo nguyên công thành công phát identity đã persist để MainWindow mở ngay
  đúng operation trong popup singleton; command lỗi/hủy không mở nhầm editor.
- Gói duyệt popup gồm 24 trạng thái và một montage tại
  `reference_private/DERIVED/UI_STAGE_8A2_3_POPUP/` đã được tạo bằng QPA Windows
  native, Segoe UI, DPI 150% và kiểm tra trực quan; kết quả đã được hợp nhất vào
  gói duyệt cuối được người dùng chấp thuận.
- Compact density policy dùng chung đã thay popup 920×700/min 720×600 bằng target
  responsive 587×630, 624×702 và 672×778; footer cố định, cuộn dọc, minh họa
  thu gọn, child limits, geometry preference/clamp và DPI logical metrics áp dụng
  cho đủ chín editor. Gói review mới gồm 24 trạng thái và một montage tại
  `reference_private/DERIVED/UI_STAGE_8A2_3_POPUP_COMPACT/`, gồm bằng chứng
  native Qt scale 125%/150%; kết quả đã được hợp nhất vào gói duyệt cuối được
  người dùng chấp thuận.
- One-screen Basic dùng responsive grid hai cột ở ba work-area target; Parallel
  gom Hình học/Tool, Chất lượng/Minh họa và tóm tắt tự động sáu ô, không còn
  scrollbar dọc ở trạng thái thường. `IllustrationViewport` dùng fit-inside,
  aspect ratio theo registry và semantic riêng cho đủ chín operation/Parallel
  state/Boring. Review mới nằm tại
  `reference_private/DERIVED/UI_STAGE_8A2_3_GRID_ILLUSTRATIONS/`; kết quả đã
  được hợp nhất vào gói duyệt cuối được người dùng chấp thuận.
- Giao diện production dùng catalog tiếng Việt tập trung; audit tĩnh tại
  `reference_private/DERIVED/UI_VIETNAMESE_AUDIT/` đạt 1046 chuỗi tĩnh và 11150
  chuỗi runtime qua 21 trạng thái, số chuỗi chưa dịch bằng 0.
- QA khóa cuối đạt 235/235 focused liên chức năng và 1461 passed, 2 deselected
  toàn repository; compileall, `pip check` và harness Windows native 25 artifact
  đều đạt. Static/runtime untranslated cùng bằng 0: 1046 chuỗi tĩnh và 11150
  chuỗi runtime qua 21 trạng thái.
- Machine-ready clearance vẫn `false`, production Post chưa hỗ trợ và icon tiếp
  tục DEFERRED.
- Final review đã khóa mapper chín tên chức năng tiếng Việt, Operation
  Manager hai dòng compact, bốn renderer/semantic Parallel tách biệt, legend và
  child `Đóng minh họa` có focus restore. Harness cuối ghi 24 ảnh + montage tại
  `reference_private/DERIVED/UI_STAGE_8A2_3_FINAL_GUI/` và kết thúc sạch sau khi
  đóng dự án thử trước cửa sổ. Kết quả đã được hợp nhất vào gói duyệt cuối được
  người dùng chấp thuận.
- Sửa clipping/DPI đã bỏ height cap của scroll content/section, reset stretch
  cũ khi reflow, tính chiều cao theo size hint/font native và dùng scrollbar
  `AsNeeded`. Basic fit không cuộn dọc ở native 100%/125% khi đủ work area;
  DPI 150% hoặc logical height thấp dùng một cột và cuộn dọc thật, footer vẫn
  cố định, không cuộn ngang. Summary giữ đủ sáu nhãn/giá trị bằng layout hai cột
  hoặc label-trên/value-dưới; Operation Manager hẹp dùng indentation 6 px/status
  tối đa 64 px và selected row có nền tương phản.
- Review clipping gồm 19 ảnh + montage tại
  `reference_private/DERIVED/UI_STAGE_8A2_3_DPI_CLIPPING_FIX/`. Bounds matrix
  bao phủ 100/125/150% và DPR 2.0; QA đạt 235/235 focused và 1470 passed,
  2 deselected toàn repository. Audit đạt 1046 chuỗi tĩnh, 11145 chuỗi runtime
  qua 21 trạng thái, untranslated bằng 0; `pip check` và compileall đều đạt.
  Người dùng đã duyệt package; Stage 8A.2.3 được đánh dấu **COMPLETED**.

## Phạm vi Stage 8A.2.2 đã hoàn thành

- Giữ nguyên foundation 8A.2.1 và thêm validation stage riêng trước khi publish.
- Kiểm tra gouge cutter, shank, holder và toàn bộ swept motion với protected
  geometry đã khai báo.
- Dùng contract `SAFE/UNSAFE/UNKNOWN/CANCELLED/FAILED`; chỉ `SAFE` được publish
  artifact READY/VALID.
- Làm cứng boundary, sharp edge, curvature, retract/link/rapid, cancellation,
  latest-wins, Simulation và Post fail-closed.
- Algorithm version đã chuyển `2 -> 3`; strategy payload vẫn version 1 và
  SQLite vẫn schema v4.
- Chưa xây Production Function Editor, chưa sửa icon và chưa tuyên bố
  production-safe.

## Kết quả hoàn thành Stage 8A.2.2

- Candidate Parallel phải qua tool-assembly collision scene, broad/narrow phase,
  expected-contact classification và swept-motion validation trước publish.
- Chỉ report `SAFE` với algorithm version 3, safety hash và checked/unverified
  scope hợp lệ mới tạo artifact READY; v2, stale, unsafe và unknown bị từ chối.
- Cutter gouge, shank/holder collision, rapid/approach/retract/link, boundary,
  sharp edge, concave access và linking vùng rời đều có test fail-closed.
- Diagnostic collision được aggregate deterministic theo calculation/motion/
  component/geometry/code, giữ deepest penetration, minimum clearance và
  `occurrence_count`.
- Holder `declared_absent` chỉ SAFE trong declared assembly scope và vẫn nằm
  trong unverified components; Post yêu cầu holder verification phải từ chối.
- Clearance 0,001 mm là internal detection minimum;
  `machine_ready_clearance_verified = false` và stage không tuyên bố universal
  collision-free, gouge-free hoặc production-safe.
- Review package 30 file đã được người dùng duyệt. QA cuối trước commit đạt
  98 focused, 302 regression chọn lọc và 1382 passed, 2 deselected toàn dự án.

## Phạm vi Stage 8A.2.1 đã hoàn thành

- Xây foundation có kiểm chứng cho pass song song trên các mặt CAM 3D đã chọn.
- Tái sử dụng CAM 3D Foundation 8A.1, Toolpath IR, lifecycle artifact và
  persistence hiện có.
- Phạm vi dao ban đầu là ball-end, fixed three-axis trong Setup WCS.
- Linking dùng retract/clearance bảo thủ; chưa có collision-aware production
  linking hoặc bảo đảm gouge-free cho mọi topology.
- Không xây production Function Editor, không sửa icon và không bắt đầu stage
  tiếp theo.

## Kết quả hiện tại Stage 8A.2.1

- Algorithm version 2 đã có parameter contract native-free, structured diagnostics và persistence
  qua `OperationParameterSet` hiện có.
- Đã có frame U/V/W, selected-region bounds, pass planning, mesh-plane
  intersection, clipping, discretization và one-way/zigzag ordering deterministic.
- Ball-end tool-center path dùng part-normal allowance; tool/allowance/boundary
  và protective geometry chưa hỗ trợ đều fail-closed.
- Linking dùng retract/clearance bảo thủ và chuyển sang Toolpath IR hiện có.
- Đã có progress/cancellation worker, latest-wins check, atomic artifact publish,
  Simulation compatibility và Save/Close/Open trên SQLite schema v4.
- Review JSON nằm trong `reference_private/DERIVED/CAM_3D_8A2_1/` và được Git
  ignore.
- Focused suite đạt 62 passed; full suite tuần tự đạt 1346 passed,
  2 deselected sau khi khóa algorithm version 2.
- Review package đã được người dùng duyệt. Coarse mesh chỉ còn là regression cho
  approximation/fail-closed; bằng chứng tolerance dùng BRep contact projection,
  differential normal, mesh-quality metrics, zigzag, cancellation, unsupported
  diagnostics và Toolpath IR.
- Phạm vi hoàn thành gồm clipping, one-way/zigzag ordering, conservative linking,
  Toolpath IR/Simulation compatibility, progress/cancellation, persistence,
  deterministic review, atomic latest-wins publish và fail-closed capability gate.
- Đây vẫn là foundation: chưa production-safe cho mọi topology, chưa có bảo đảm
  holder/shank collision hoặc universal gouge-free, production editor/Post/linking,
  tool ngoài ball-end hay multi-axis orientation.

## Lý do tạm gác Stage 9A.I1

- Bộ icon chưa đạt chuẩn hình học và độ nét cần thiết.
- Icon chưa phải ưu tiên trong giai đoạn hoàn thiện giao diện chức năng chính.
- Production UI tiếp tục dùng icon hoặc placeholder hiện có.
- Stage 9A.I1 sẽ được đánh giá lại sau khi giao diện chức năng chính hoàn thiện.

## Phạm vi Stage 9A.6 đã hoàn tất

- Drilling.
- Tapping.
- Reaming.
- Boring.
- Tích hợp trong Unified Function Editor.
- Tối giản chế độ Basic.
- Thu gọn Advanced/Expert theo progressive disclosure.
- Giữ tương đương domain, toolpath và post hiện có.

## Kết quả hoàn thành

- Shared Drilling Family editor foundation dùng chung cho Drilling, Tapping,
  Reaming và Boring đã hoàn tất.
- Unified Function Editor đã có progressive disclosure và lifecycle
  Preview/Apply/Calculate đúng applied-state contract.
- Operation Manager đã tích hợp editor và Duplicate lifecycle với identity,
  geometry input và artifact state mới.
- Save/Open round-trip và exact-equivalence đã được kiểm tra trên cả bốn editor.
- Native Windows GUI review đã được người dùng duyệt; font harness đã được sửa để
  chặn ảnh thiếu glyph, panel 460 px và footer polish đã đạt.
- SQLite giữ nguyên schema v4; dependency và icon không đổi.
- Stage 9A.I1 icon pack tiếp tục deferred.
- Full regression đạt 1284 passed, 2 deselected.

## Ràng buộc chuyển giai đoạn

- Giữ algorithm v3, payload v1, Toolpath IR và SQLite schema v4; không mở rộng
  sang strategy CAM 3D khác hoặc production Post.
- Stage 9A.5.4 và các production editor Facing, Planar Facing, Contour, Pocket
  tiếp tục là baseline giao diện cần bảo toàn.
- Stage 9A.I1 icon pack tiếp tục deferred; không sửa icon trong Stage 9A.6.
- Không tự động bắt đầu stage tiếp theo sau khi hoàn tất Stage 9A.6.
