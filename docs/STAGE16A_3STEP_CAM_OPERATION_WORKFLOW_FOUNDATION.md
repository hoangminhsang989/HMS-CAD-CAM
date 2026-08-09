# Stage16A — Quy trình tạo nguyên công CAM 3 bước

## Trạng thái

- Stage13C: **100% COMPLETE**.
- Stage14A: **100% COMPLETE**.
- Stage15A: **100% COMPLETE**.
- Chương trình Mega-WP1: **3-STEP CAM OPERATION WORKFLOW + PRODUCTION TOOL
  LIBRARY FOUNDATION**.
- Mega-WP1: **100% DELIVERED**.
- Stage16A: `IN_PROGRESS`; tài liệu này không tuyên bố Stage16A hoàn tất.
- Accepted implementation candidate:
  `891da56f8813bdc91c87b049dbea8f8198d5e74d`; tree
  `7722013bd9a358c1e40b4b0e4480d9b6c269bb7c`.
- Direct review: **R176 FINAL DIRECT REVIEW PASS**.
- Overall HMS: **~87% provisional roadmap estimate**, không phải số đo hoàn tất
  được chứng nhận toán học.
- Mega-WP2 chưa bắt đầu; phạm vi Stage16A tiếp theo cần authority riêng
  `STAGE16A_MEGA_WP2_SCOPE_SELECTION_AND_IMPLEMENTATION_AUTHORITY`.

## Architecture map đã audit

Luồng ownership production thật hiện tại là:

`Project → CamJob → Setup → OperationTree → Operation`

Repository không có aggregate “CAM Program” riêng cho milling. Stage16A dùng cặp
`CamJobId + SetupId` làm program context thật, giữ `parent_node_id` để chèn đúng
group hiện hành. `ProgramAssembly` vẫn là bước downstream của Post, không trở
thành owner của operation. `ProjectSession` và `CamProjectSnapshot` là context
application/runtime, không phải một “CAM Program” persistent giả.

Các ranh giới được tái sử dụng:

- `OperationManagerActions` mở `OperationCreationWizard` qua `CamWorkspace`.
- `OperationCreationSession` giữ working copy typed, project generation, Job,
  Setup, strategy, Tool/Profile và provenance; session không chứa operation đã
  persist.
- `Stage16AToolSelectionService` đọc chính `ToolDefinition`, `ToolAssembly`,
  `HolderDefinition`, revision/fingerprint và `DEFAULT_TOOL_PROFILE_REGISTRY`.
- `Stage16AOperationCreationAdapter` tạo context tạm rồi gọi nguyên vẹn schema,
  resolver, `prepare_*_update` và generator validation hiện có của Drilling,
  Parallel và Z-Level.
- `ProjectService.execute_cam_creation()` là một application transaction: kiểm
  tra lại project generation, Job, Setup, group, strategy, Tool identity,
  compatibility và configuration revision trước khi thêm đúng một operation.

## UX ba bước

1. **Chọn nguyên công**: danh sách được lấy từ registry production hiện có;
   Mega-WP1 quảng bá đúng `drilling_v1`, `parallel_finishing_3d` và
   `z_level_finishing_3d`.
2. **Chọn Tool**: tìm kiếm theo tên/family/stable ID; Tool tương thích được ưu
   tiên; Tool không tương thích vẫn có thể nhìn thấy nhưng bị vô hiệu hóa cùng
   lý do. Giao diện quản lý Tool hiện có được mở bằng action rõ ràng.
3. **Thông số nguyên công**: nhúng `FunctionEditorPage` và schema production
   hiện có. Basic được hiển thị trước; Advanced giữ manual overrides. Footer
   Apply/Calculate của editor bị ẩn trong wizard để chỉ nút `Tạo nguyên công`
   được phép publish working copy.

Back giữ lựa chọn hợp lệ trước đó. Thay strategy chỉ giữ Tool nếu registry xác
nhận vẫn tương thích. Thay Tool hủy Step 3 working values và buộc resolver chạy
lại. Cancel/Escape chỉ hủy working copy, không gọi application service.

## Tool compatibility và provenance

Không có Tool library thứ hai. Compatibility kết hợp:

- trạng thái Tool Assembly/Tool/Holder bằng revision, fingerprint và unit;
- family allowlist của `ToolStrategyProfileSchema`;
- validation chuyên biệt trong `prepare_parallel_update()`,
  `prepare_z_level_update()` hoặc Drilling generator.

Thứ tự resolution không thay đổi:

`operation override → Tool program profile → Tool common default → automatic policy → safe default`

Step 3 hiển thị các summary provenance do editor/automatic contract hiện có tạo
ra. Manual override được lưu trong `AutomaticParameterContract` với mode
`MANUAL`; thay Tool tạo working editor mới nên override cũ không bị giữ ngầm.
Resolver hiện có là nguồn authoritative cho Tool/profile/default; effective
value và provenance được giữ nhất quán giữa editor, session và operation đã
commit.

## Transaction và fail-closed

Finish dựng và validate candidate hoàn chỉnh trước mutation. Ngay trong command,
service kiểm tra lại project, generation, Job, Setup, group, strategy, Tool,
Tool configuration revision và duplicate operation identity. Lỗi ở bất kỳ gate
nào tạo ra **0 partial operation** và không publish CAM 3D zone.

Double-click/repeated Finish bị chặn ở UI; OperationTree cũng từ chối duplicate
identity. Tool/project/Setup bị xóa hoặc thay đổi khi wizard đang mở sẽ làm lựa
chọn invalid và chặn Next/Finish.

Terminal `CREATED`/`CANCELLED` là monotonic. Callback cũ bị chặn bằng
lease/generation; Finish hợp lệ chỉ tạo đúng một Operation. Tool/profile/project/
CamJob/Setup stale đều fail closed, không cho callback cũ publish hoặc tạo lần
hai.

## Persistence và safety boundary

- SQLite hiện tại là schema **v5** (authority nhắc “schema v4”, nhưng audit
  checkout xác nhận v5). Mega-WP1 thay đổi schema: **0**.
- `.HMS` format migration: **0**.
- Operation/Tool payload-version change: **0**.
- Dependency change: **0**.
- CAM algorithm fork: **0**; collision/contact solver, Simulation và Post
  change: **0**.

Operation được tạo ở trạng thái cần Calculate (`DIRTY`/missing artifact). Wizard
không gọi Calculate, Simulation, Post, Program Assembly hay NC export và không
đánh dấu machine-ready. Retained execution truth: Calculate **NOT_TRIGGERED**,
Simulation **NOT_TRIGGERED**, Post **NOT_TRIGGERED**, machine-ready claim **0**.

## Responsive, localization và accessibility

Wizard dùng logical pixels và `CAM_POPUP_DENSITY`, clamp theo available work
area. Matrix test bao phủ 1280×720, 1366×768, 1500×900, 1920×1080 ở 100%,
125%, 150% và 200%. Step titles word-wrap, Step 3 có scroll của Function Editor,
và navigation luôn nằm ngoài scroll.

Catalog production có đủ VI_VN/EN_US/KO_KR. Back/Next/Cancel/Finish và trạng thái
compatibility có accessible name/description; tab order của Function Editor được
tái sử dụng. Enter chỉ dùng default button của step hiện tại; Escape thực hiện
Cancel an toàn.

## WP1 evidence và giới hạn

Focused tests mới kiểm tra session transitions, compatibility, stale/deleted
Tool, profile revision, strategy/Tool invalidation, keyboard, localization,
geometry matrix, Cancel, exactly-once và product integration thật. Probe thật
tạo Parallel operation trong sandbox `.HMS`, lưu/mở lại đúng stable identity,
đồng thời xác nhận Calculate/Simulation/Post đều không chạy.

Retained evidence được ghi riêng theo đúng scope:

- Stage16A focused: **79 PASS**.
- R176 final bounded: **431 PASS, failed 0, errors 0, exit 0**.
- Production UI matrix: **144/144 PASS**.
- Repository collection/full: **0/0**.

Không cộng các tập evidence này thành một test count toàn repository. Catalog
production và accessibility review bao phủ VI/EN/KO; Basic/Advanced tiếp tục tái
sử dụng Function Editor hiện có. AI Sync activity **0**; Stage17 activity **0**.

Giới hạn có chủ ý:

- chỉ ba strategy đã có Tool profile schema production tham gia wizard;
- không tạo Tool mới trong wizard;
- không tạo Program Template, feature recognition hoặc strategy AI;
- không chứng nhận CNC clearance hay machine-ready;
- các strategy khác tiếp tục dùng command/editor hiện hữu cho tới WP được phê
  duyệt riêng.
