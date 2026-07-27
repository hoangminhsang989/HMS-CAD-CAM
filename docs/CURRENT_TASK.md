# Nhiệm vụ hiện tại — Stage 9A.7

## Trạng thái ưu tiên

- **Stage 9A.7 — Post/Program Assembly UI Cleanup — IN PROGRESS.**
- Đặc tả được duyệt: **R4** (`STAGE_9A7_SPECIFICATION_REVIEW_R4.zip`).
- Baseline bắt buộc: branch `main`, HEAD
  `7dd26867f27e67baef0ca2e7dc04d95663e8d27a`.
- **WP1 Review R7 đã được duyệt** làm baseline source của Stage 9A.7, gồm test
  isolation, final full-QA gate, total state projection, attempt identity và
  feature flag foundation. WP2 chưa được áp dụng; C3.1 chưa tồn tại tại revision
  này; WP3–WP6 chưa bắt đầu.
- R4 tách accepted generation result khỏi incoming callback audit: callback
  stale/incomplete/đến sau terminal không đổi state/headline/fingerprint/result,
  còn cancel replacement attempt giữ previous result, managed artifact và
  Preview evidence.
- Readiness source identity không phụ thuộc generated payload byte/SHA; initial
  no-result workflow phải đạt `READY_TO_GENERATE + IDLE`. Accepted result phải
  nhất quán với attempt provenance; managed so với accepted payload hiện hành.
- Callback counters phải cân bằng/không âm; external active/failed bắt buộc
  identity complete và source identity khóa content SHA/checksum/bytes/provenance.
- `dirty_state` và external confirmation rejection được project thành audit
  output; dead core input field count = 0.
- Feature flag chỉ nhận exact bool; external attempt chỉ có canonical dispatch
  ID; nested evidence được validate sớm. Managed explicit negative state và
  external `explicit_stale` fail-closed; core external không còn
  `explicit_current`. `presentation_readiness_blocked` không phải WP2 action
  authority.
- R6 khóa `current_request_fingerprint` là source authority duy nhất; attempt
  phải khớp project/request/order source hiện tại; stale active reject, stale
  terminal ignore; callback publish bắt buộc accepted result exact identity và
  provenance. Generation callback artifact-write/selection-mutation luôn bằng 0
  và automatic downstream count được derive.
- R7 cô lập review-package test Stage 8A.4.1 bằng optional output root và
  `tmp_path`; production default/behavior, permission và WP1 semantics không đổi.
  Extracted R7 review tree đã được xác minh đủ **19/19 manifest hashes**; tài
  liệu không khẳng định ZIP R7 hiện còn tồn tại. Shared DERIVED manifest được
  kiểm tra bất biến; exact test đạt 3/3. Legacy artifact owner
  `BUILTIN\Administrators` không rename/xóa được và được ghi cleanup failed,
  không dùng Administrator hoặc reset ACL.
- QA R7: **280 passed** focused, **166 passed** regression; full lặp lại
  **2150 passed, 2 deselected**, failure/error = 0. `pip check`, `compileall src
  tests tools`, `git diff --check` PASS.
- **Stage 8A.4.4 — Kiến trúc cài đặt và dữ liệu — COMPLETED.**
- **Stage 8A.4.3 — Hệ thống đa ngôn ngữ — COMPLETED.**
- **Stage 8A.4.2 — Kiến trúc hai chế độ tài liệu — COMPLETED.**
- **Stage 8A.4.1 — Nền tảng cấu hình Tool theo chương trình — COMPLETED.**
- **Stage 9A.I1 — DEFERRED.**

Stage 8A.4.4 tách typed scope cho install read-only, dữ liệu machine-wide,
preference/cache người dùng và document/CAM project do người dùng chọn. Root
production đã chốt là `C:\HMS-CADCAM\`, `C:\ProgramData\HMS-CADCAM\`,
`%APPDATA%\HMS-CADCAM\` và `%LOCALAPPDATA%\HMS-CADCAM\`; resolver không dùng
CWD và test/review chỉ dùng injection sandbox rõ nguồn.

- `ApplicationPathsService`, `StorageBootstrapService`, manifest layout v1,
  path security fail-closed, atomic writer và resource lock là contract typed.
- ProgramData có đúng 8 thư mục; AppData roaming/local phân trách nhiệm rõ,
  thêm `Profiles` dưới Roaming và
  không nhận dữ liệu project hoặc shared library sai scope.
- Config precedence là user hợp lệ → machine-wide → built-in read-only → code
  fallback; user không override policy machine bị khóa.
- Backup machine-wide có checksum/retention; migration chỉ scan/preview/copy/
  verify/atomic publish, giữ source cũ và loại trừ `.HMS`/project data.
- `.BAKUPHMS` production hỗ trợ 14 category, user/machine scope, selective
  backup/restore, strict validation, conflict action, backup-before-restore và
  rollback; executable/project/secret/cache/log/temp luôn bị loại trừ.
- Profile giao diện theo người dùng lưu atomic dưới Roaming bằng UUID, hỗ trợ
  create/copy/rename/default/delete, backup/import-as-copy và chuyển runtime
  locale/shortcut/Quick Access/layout mà giữ workspace/project/worker.
- UI modeless `Cài đặt → Hệ thống → Vị trí dữ liệu` hỗ trợ VI/EN/KO, hiển thị
  production target nhưng không cho đổi root; startup dùng cảnh báo không modal.
- Review package R3 native Windows 40 PNG + 16 JSON + 1 Markdown đã được người
  dùng duyệt; PNG/source mismatch và `visual_evidence_missing_count` đều bằng 0.
- QA cuối đạt **122 passed** focused (baseline 116), **194 passed** regression
  liên quan và **1870 passed, 2 deselected** toàn dự án; `pip check`,
  `compileall src tests tools` và `git diff --check` đều PASS.

Stage 8A.4.4 đã `COMPLETED`; Stage 9A.7 đang `IN PROGRESS` theo phê duyệt R4.
WP1 Review R7 là ranh giới source hiện hành; WP2 và C3.1 chưa được áp dụng.

Không thay đổi SQLite schema v4, `.HMS`, CAM project-root, Tool payload,
geometry transfer, CAM algorithms, Simulation/Post safety hoặc machine-ready.
Chưa triển khai MSI/EXE installer, code signing, updater, UAC workflow, registry
association, Program Templates behavior hay Production Post mới.

Tài liệu contract: `docs/HMS_STORAGE_ARCHITECTURE_8A4_4.md`,
`docs/HMS_BACKUP_RESTORE_8A4_4.md`, `docs/HMS_USER_PROFILES_8A4_4.md`.

# Lịch sử — Stage 8A.4.3

Stage 8A.4.3 bổ sung ngôn ngữ giao diện typed `VI_VN`, `EN_US` và `KO_KR`.
Tiếng Việt luôn là mặc định và fallback ưu tiên; locale là preference người dùng,
không thuộc manifest, SQLite, Tool payload hoặc semantics dự án. Package GUI
native Windows 28 file đã được người dùng duyệt; không có stage kế tiếp nào
được bắt đầu.

- Runtime switch VI→EN→KO→VI→EN→KO→VI hiện retranslate
  property/model/delegate, viewport/status/diagnostic, notification và
  accessibility từ source ngữ nghĩa; không giữ chuỗi locale trước và không tạo
  lại dock/tab.
- Model phát `dataChanged`/`headerDataChanged`, delegate resolve theo locale hiện
  hành; file dialog, dirty lifecycle và message formatter 0/1/n dùng ngôn ngữ HMS.
- Catalog UTF-8, QSettings persistence, fallback tiếng Việt và glossary kỹ thuật
  được kiểm tra duplicate/missing/placeholder, raw key và fallback hit.
- Rendered audit đọc QAction, widget, model/header/cell, delegate, log/status,
  ribbon/sidebar/QTabBar/dock tab, font metrics, tooltip/accessibility và
  notification formatter; regression cố tình đưa chuỗi nhiễm hoặc label elide
  phải bị phát hiện.
- QA cuối: **58 passed** focused Stage 8A.4.3 mới, **140 passed** regression
  liên quan, **198 passed** nhóm Stage và **1748 passed, 2 deselected** toàn
  repository; `pip check`, `compileall src tests tools` và `git diff --check`
  đạt.
- Package đã duyệt giữ đúng 18 PNG, 9 JSON và 1 Markdown; package Git-ignored
  không thuộc source commit.

Ngoài phạm vi và chưa triển khai: ProgramData/install layout, importer đa định
dạng đầy đủ, Export 3D, cấu hình phiên bản Export, CAM workflow ba bước, Tool đa
họ, màu đường chạy dao, Program Templates và Production Post mới.

Tài liệu contract: `docs/HMS_MULTILINGUAL_UI_8A4_3.md`.

# Lịch sử — Stage 8A.4.2

## Trạng thái ưu tiên

- **Stage 8A.4.2 — Kiến trúc hai chế độ tài liệu — COMPLETED.**
- **Stage 8A.4.1 — Nền tảng cấu hình Tool theo chương trình — COMPLETED.**
- **Stage 8A.3.3 — Z-Level Finishing Production Function Editor — COMPLETED.**
- **Stage 9A.I1 — DEFERRED.**

Stage hiện tại tách rõ tài liệu CAD đơn lẻ lưu trong một container `.HMS` và dự
án CAM làm việc trực tiếp trong một thư mục workspace. `ProjectService` tiếp tục
là ranh giới application/persistence; SQLite project schema giữ v4 và loader
phải tương thích dự án thư mục `.HMS` cũ.

Stage 8A.4.2 đã hoàn thành trên baseline
`88aff7829244c610d68d099de3147c8bb17f2443`, với baseline QA **1601 passed,
2 deselected**. Không stage kế tiếp hoặc stage đa ngôn ngữ nào được bắt đầu.

Phần mở rộng hiện có production command `Nạp 3D mới cho dự án CAM`, inbox
atomic, scan sau project-open/recovery, notification không modal, preview và ba
lựa chọn Add/Replace/Update, scoped stale cùng rollback/crash recovery.

Bộ review native Windows Git-ignored đã được người dùng duyệt, có đúng 43 file:
30 PNG hash riêng, 12 JSON và 1 Markdown. QA khóa cuối đạt **136 passed** cho
focused Stage, **113 passed** cho regression liên quan và **1690 passed,
2 deselected** cho toàn repository. `pip check`, `compileall src tests tools`,
audit GUI tiếng Việt với toàn bộ nhóm lỗi bằng 0 và `git diff --check` đều đạt.

SQLite giữ schema **v4** và loader tiếp tục tương thích dự án thư mục `.HMS`
legacy. File `.HMS` đơn lẻ không phải container dự án CAM; geometry transfer
không phải chứng nhận an toàn và không tự Calculate, Simulation hoặc Post.
Machine-ready clearance chưa được xác minh hay chứng nhận.

Ngoài phạm vi: hệ thống đa ngôn ngữ, ProgramData/install layout, importer đa
định dạng đầy đủ, Export 3D, cài đặt phiên bản xuất, three-step CAM workflow,
thuật toán Tool đa họ, màu toolpath, Program Templates và Production Post mới.

Tài liệu contract: `docs/HMS_DOCUMENT_AND_CAM_WORKSPACE_8A4_2.md`.

## Kết quả hoàn thành Stage 8A.3.3

- Z-Level Finishing Production Function Editor đã hoàn tất trên hợp đồng
  algorithm v2, payload v1 và SQLite schema v4; không tăng phiên bản.
- Basic/Advanced editor, tham số tự động và tùy chỉnh thủ công, lifecycle
  Apply/Calculate/Cancel, worker nền, persistence, Operation Manager,
  accessibility, minh họa và responsive/DPI đã có mã chạy được và QA.
- Simulation chỉ mở cho artifact hiện hành **READY + SAFE v2**. Production Post
  tiếp tục fail-closed; không tạo claim G-code production.
- Machine-ready clearance chưa được xác minh hoặc chứng nhận; chưa kiểm chứng
  trên máy CNC thật.
- Dependency không đổi; icon không đổi và Stage 9A.I1 tiếp tục `DEFERRED`.
- Package review native Windows gồm 27 ảnh kỹ thuật, 1 montage, 9 JSON và
  1 Markdown (38 file, 28/28 PNG có hash riêng) đã được người dùng duyệt.
- Localization audit bao phủ 115.103 record; mọi leak, raw namespace/model
  token, chuỗi lặp và acronym không được phép đều bằng 0.
- QA khóa cuối: **302 passed** focused; **1559 passed, 2 deselected** toàn
  repository; `pip check`, `compileall` và `git diff --check` đều đạt.

# Lịch sử — Stage 8A.3.2

## Trạng thái ưu tiên

- Functional UI baseline: `24d2a42` — hoàn thành đánh giá đồng bộ giao diện
  phay 2D Stage 9A.5.4.
- **Stage 8A.3.2 — Z-Level Finishing Hardening and Collision Safety — COMPLETED.**
- **Stage 8A.3.1 — Z-Level Finishing Foundation — COMPLETED.**
- **Stage 8A.2.3 — Parallel Finishing Production Function Editor — COMPLETED.**
- **Stage 9A.I1 — HMS Isometric CAD/CAM Icon Pack: DEFERRED.**
- **Stage 9A.6 — Drilling Family Production Function Editors — COMPLETED.**
- **Stage 8A.2.1 — Parallel Finishing Foundation — COMPLETED.**
- **Stage 8A.2.2 — Parallel Finishing Hardening and Collision Safety — COMPLETED.**

## Kết quả hoàn thành Stage 8A.3.2

- Z-Level hardening/collision safety đã hoàn tất với algorithm v2, payload v1 và
  SQLite schema v4.
- Dùng shared Stage 8A.2.2 safety contract qua adapter Z-Level; không fork safety
  solver, không fallback âm thầm sang Parallel.
- Contact/root 3D validation, pathological topology fail-closed, swept cutter/
  shank/Holder, scope/hash/invalidation, conservative direct-link fallback,
  cancellation/latest-wins và SAFE-only READY gate đã có mã chạy được và QA.
- Persistence, Simulation gate, Production Post fail-closed, diagnostic
  aggregation và evidence consistency đã được kiểm tra.
- Review package Git-ignored nằm tại
  `reference_private/DERIVED/CAM_3D_8A3_2_Z_LEVEL_HARDENING_SAFETY/`.
- Package hardening hiện có 18 ảnh kỹ thuật, một montage, 17 report JSON chuyên
  biệt và evidence manifest liên kết PNG/report/calculation/hash; tổng 39 file.
- Chưa production-safe, chưa machine-ready, chưa Production Function Editor,
  chưa Production Post cho Z-Level; machine-ready clearance vẫn
  `false`; Stage 8A.3.3 chưa bắt đầu.

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
# Ghi chú triển khai — Stage 8A.3.3

## Trạng thái ưu tiên

- **Stage 8A.3.3 — Z-Level Finishing Production Function Editor — xem mục hiện tại ở đầu tài liệu.**
- **Stage 8A.3.2 — COMPLETED.**
- **Stage 8A.3.1 — COMPLETED.**
- **Stage 8A.2.3 — COMPLETED.**
- **Stage 8A.2.2 — COMPLETED.**
- **Stage 9A.6 — COMPLETED.**
- **Stage 9A.I1 — HMS Isometric CAD/CAM Icon Pack — DEFERRED.**

Phần này được giữ lại để truy vết; không stage nào khác đang `IN PROGRESS`.
