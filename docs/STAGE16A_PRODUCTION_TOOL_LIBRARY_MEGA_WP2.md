# Stage16A Mega-WP2 — Thư viện Tool sản xuất và tái sử dụng an toàn

## Trạng thái

- Authority: **R178 MEGA-WP2 IMPLEMENTATION**.
- Stage16A: **IN_PROGRESS**.
- Mega-WP1: **100% DELIVERED**.
- Mega-WP2: **IMPLEMENTATION_COMPLETE / DIRECT_REVIEW_PENDING**.
- Remote base dùng để tạo candidate:
  `9d9cdf738d7dbdd6cbdf70e07159ba7b45446ee4`, tree
  `3f0fc70b720027610d4ff7e09b8dad3ed1b65073`.
- SQLite schema: **5, không đổi**.
- `.HMS` migration: **0**.
- Dependency mới: **0**.
- Không push, không AI Sync, không Stage17.

Tài liệu này mô tả implementation candidate chờ direct review. Nó không tuyên
bố Mega-WP2 đã delivered và không đóng Stage16A.

## Kết quả architecture audit

Repository production lưu CAM theo ownership thật:

`Project → CamJob → Setup → OperationTree → Operation`

Tooling hiện có được giữ nguyên:

- `ToolDefinition` là immutable snapshot có `ToolDefinitionId` ổn định,
  physical `revision`, `ToolCommonDefaults`, `ToolProgramProfile` và
  `configuration_revision`.
- `ToolAssembly` có stable `ToolAssemblyId`, snapshot expected Tool/Holder
  revision + fingerprint và là identity mà `Operation` tham chiếu qua
  `ToolAssemblyReference`.
- SQLite v5 đã lưu toàn bộ Tool/Holder/Assembly dưới JSON payload typed; defaults,
  profiles và configuration revision nằm trong Tool payload v2. Vì vậy WP2
  không cần bảng, cột hoặc migration mới.
- `CamApplicationService` + `ProjectService.execute_cam_command()` là transaction
  boundary hiện hữu. UI không đọc/ghi SQLite trực tiếp.
- `DEFAULT_TOOL_PROFILE_REGISTRY`, resolver trong `tool_profiles.py` và
  `Stage16AToolSelectionService` tiếp tục là nguồn duy nhất cho schema,
  effective value, provenance và compatibility.
- Domain không có persisted archive/inactive flag. WP2 không giả archive bằng
  rename, filter-only state hoặc hidden UI state.

## Application contract

`ToolDefinitionDraft` chỉ chứa dữ liệu authoring thật và cố ý không có
persistent ID. `CamApplicationService` sinh `ToolDefinitionId` và optional
`ToolAssemblyId` tại commit command.

### Create

- Hỗ trợ toàn bộ `ToolFamily` và concrete geometry hiện có: cylindrical,
  ball-end, bull-nose, drill, chamfer, tap, boring bar, turning insert và
  custom envelope.
- Validation đi qua constructor domain hiện hữu; combination sai fail closed.
- Có thể tạo Tool definition riêng hoặc tạo đồng thời một Tool Assembly khả
  dụng cho Step2. Holder chỉ được dùng nếu tồn tại và cùng unit.
- Cancel đóng dialog trước command nên mutation bằng 0. Save bị disable ngay
  khi accept, ngăn double-submit từ cùng edit surface.

### Edit và concurrency

- Stable Tool ID được giữ nguyên.
- Expected physical revision và expected configuration revision đều bắt buộc.
  Snapshot editor cũ không được overwrite state mới.
- Physical edit tăng cả physical revision và configuration revision, rồi cập
  nhật expected Tool snapshot của các assembly cùng transaction và tăng
  assembly revision. Unit persisted không được đổi ngầm.
- Existing Operation không bị rewrite parameters hoặc đổi Tool reference.
  Assembly revision mới khiến dependency reference cũ stale theo contract
  hiện hữu; calculation output không tự chạy lại.

### Duplicate

- Tool duplicate nhận Tool ID mới; mọi copied assembly nhận Assembly ID mới.
- Common defaults và concrete physical definition được deep-copy.
- Program profiles nhận profile ID mới và mặc định disabled để không tạo hai
  enabled profile mơ hồ. Original và duplicate chỉnh sửa độc lập.
- Không Operation reference nào được copy; Operation cũ vẫn trỏ assembly gốc.

### Archive và delete

- Archive/unarchive: **ARCHIVE_UNAVAILABLE_WITH_CURRENT_SCHEMA**. Action được
  hiển thị disabled cùng lý do localized; không có persistence giả.
- Hard delete chỉ được phép khi Tool thật sự không có Tool Assembly và không có
  Operation/protected reference qua assembly.
- Nếu còn assembly, delete fail closed; cascade count **0**, không null/rewrite
  Operation và không đổi CAM3D zone.
- Tool definition không tham chiếu có thể xóa atomic. ID khác không đổi và ID
  mới tiếp tục dùng UUID authority, không phụ thuộc row position.

## Dedicated Tool Library UX

`ToolLibraryDialog` là workflow riêng, không nằm trong wizard ba bước:

- main list có search, real-enum family filter, registry strategy filter và
  deterministic sort theo name/family/principal size/config revision/usage;
- row identity luôn là stable Tool ID; full ID vẫn searchable và detail surface
  giữ full identity dù list hiển thị compact ID;
- detail surface hiển thị physical/config revision, assembly/Holder,
  registry-driven compatibility, reference count/location, common defaults và
  program profiles;
- editor dùng ba tab compact `Basic / Geometry / Assembly`; defaults/profile
  dùng finite typed controls lấy trực tiếp từ model/schema hiện hữu;
- destructive confirmation dùng Cancel làm default; Delete không phải Enter
  default; Escape đóng/cancel an toàn.

Catalog production có đủ VI_VN, EN_US và KO_KR cho string WP2. Matrix tự động
bao phủ 3 locale × 4 scale × 4 screen × 4 surface = **192 geometry states** tại
1280×720, 1366×768, 1500×900 và 1920×1080, scale 100/125/150/200. Main/detail
chuyển compact column cục bộ; không đổi global UI scale.

## Giữ nguyên wizard ba bước

Contract Mega-WP1 không đổi:

1. chọn strategy;
2. chọn **Tool đã tồn tại** qua Tool Assembly;
3. dùng FunctionEditor/schema production hiện hữu.

Step2 chỉ có action mở dedicated Tool Library; không có form tạo Tool inline.
Sau khi manager trả về, Step2 đọc snapshot mới, loại duplicate row và
revalidate selection:

- Tool mới tương thích xuất hiện sau refresh;
- missing/deleted/incompatible Tool làm selection mất hiệu lực và chặn Next;
- config-only revision drift giữ UX Back hiện hữu nhưng Next lấy lại live choice
  và tạo binding mới; callback Step3 cũ vẫn bị lease/revision gate chặn;
- physical family/geometry change dùng registry hiện hữu để re-evaluate
  compatibility; không có matrix thứ hai.

Resolver precedence vẫn là:

`operation override → Tool program profile → Tool common default → automatic policy → safe default`

## Safety và persistence probes

Sandbox product probe thực hiện create, duplicate, defaults/profile edit,
Step2 live refresh, stale Step3 callback, fresh binding, exactly-one Parallel
Operation, referenced delete rejection, unreferenced delete, incompatible-after-
edit và save/reopen.

Kết quả contract:

- stale callback: Operation delta **0**, CAM3D zone delta **0**, project.db byte
  delta **0**;
- fresh binding dùng configuration revision hiện tại và provenance từ resolver;
- managed Tool tạo đúng **1 Operation**;
- existing Operation parameters/Tool reference giữ nguyên sau Tool defaults
  thay đổi;
- Tool/Assembly/profile/default/revision/Operation identity round-trip qua
  project.db schema v5;
- Calculate **NOT_TRIGGERED**, Simulation **NOT_TRIGGERED**, Post
  **NOT_TRIGGERED**, machine-ready **0**.

Focused implementation evidence trước final gate:

- Tool Library application/query cases: **47 PASS**;
- Tool Library UI file: **196 PASS**, trong đó geometry matrix **192/192 PASS**;
- sandbox product integration: **1 PASS**;
- new/focused R178 cases trong ba file: **244 PASS**.

Final bounded regression và static certification được thực hiện sau khi source,
docs và exact file set được đóng băng; kết quả cuối thuộc R178 handoff report.

## Ngoài phạm vi / giới hạn thật

- Persisted archive/inactive semantics chưa có và không được giả lập.
- Hard delete không cascade Tool Assembly; Tool có assembly phải giữ nguyên.
- Không thêm CAM algorithm, Tool geometry kernel, feature recognition, strategy
  AI, Program Template, cloud/vendor catalog, Simulation/Post/G-code, installer
  hoặc updater.
- Không chạy repository-wide suite và không thu thập Stage13C.
- Next authority sau candidate này:
  **STAGE16A_MEGA_WP2_PRODUCTION_TOOL_LIBRARY_DIRECT_REVIEW_AUTHORITY**.
