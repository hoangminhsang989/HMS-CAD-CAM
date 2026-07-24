# Stage 8A.3.3 — Z-Level Finishing Production Function Editor

Trạng thái: **COMPLETED**. Package review cuối đã được người dùng duyệt. Phạm
vi của stage này kết thúc ở editor, lifecycle và review UI; chưa mở sang CAM
stage kế tiếp.

## Hợp đồng không thay đổi

- Algorithm Z-Level: **v2** (`hms_z_level_implicit_ball_center`).
- Payload strategy: **v1** (`z_level_finishing_3d`).
- Project/database schema: **SQLite v4**, không migration.
- Safety: hợp đồng Stage 8A.3.2; **CHƯA XÁC ĐỊNH** không được nâng thành
  **AN TOÀN**, sẵn sàng chạy máy hay Post-ready.
- Simulation chỉ được mở khi kết quả hiện hành là **SẴN SÀNG + AN TOÀN** với
  marker an toàn v2. Post sản xuất Z-Level tiếp tục chặn an toàn.

## Editor và vòng đời

Editor có một popup singleton dùng chung với các CAM Function Editor hiện có và
được mở từ Operation Manager. Basic chỉ hiển thị Hình học, Tool cầu, Hồ sơ
chất lượng và tóm tắt tham số tự động. Advanced chứa level range,
stepdown, tolerance/allowance, contour/boundary/order, linking, clearance,
protected geometry và safety/capability. Không có Expert tab.

Tham số tự động dùng policy `z_level.finishing.automatic` v1. Policy tạo
machining frame U/V/W, top/bottom, stepdown, tolerance, allowance, orientation,
boundary/order, linking, approach/retract, segment length, normal variation,
safety sampling/scope và protected-geometry scope. Các enum nội bộ
`fast`/`balanced`/`high` được mapper tập trung hiển thị thành
Nhanh/Cân bằng/Chất lượng cao; combo production cập nhật trực tiếp bản nháp,
effective hash, số lớp và minh họa. Ba policy dùng stepdown
4.5/3.0/1.8 mm và tolerance 0.01/0.01/0.005 mm; không hạ thấp phạm vi an toàn.
Tùy chỉnh thủ công được lưu cùng contract, giữ nguyên ý định khi
đóng/mở lại.

Geometry evidence được giữ native-free theo extents U/V/W của vùng mặt đã
chọn. Apply tạo operation + machining zone nguyên tử; Calculate đi qua trạng
thái `MISSING/DIRTY → COMPUTING → VALID/FAILED`, có generation guard,
cancellation và stale-result rejection. Duplicate cấp operation/zone/selection
identity mới và không sao chép artifact cũ.

## Safety, Simulation, Post

Operation Manager hiển thị `DRAFT`, `CALCULATING`, `STALE`, `FAILED`,
`AN TOÀN trong phạm vi` hoặc trạng thái tương ứng; không hiển thị claim sẵn
sàng chạy máy.
Dialog safety là bảng có cột thành phần, trạng thái, bằng chứng và diagnostic.
Các trạng thái holder thiếu, geometry thiếu, report stale, collision,
direct-link fallback đều fail-closed.

## Minh họa và hiển thị

Minh họa Z-Level là vector QPainter, không dùng bitmap hoặc text-only
placeholder. Có đủ 12 state: overview, quality_fast/balanced/high, inner-hole,
disconnected regions, direct/fallback linking, allowance, level range và
unknown/collision. Registry
dùng cùng descriptor cho compact/expanded/child dialog, có accessibility text
và kiểm thử high-DPI.

Review native Windows được tạo tại
`reference_private/DERIVED/UI_STAGE_8A3_3_Z_LEVEL_PRODUCTION_EDITOR/`: đúng 27
ảnh kỹ thuật, 1 montage, 9 report JSON và `REVIEW_INDEX.md` (38 file). Package
dùng `OperationManagerPanel` production, child illustration dialog thật, combo
quality thật và subprocess Qt scale 100%/125%/150% cùng DPR 2,0; audit
static/runtime/rendered và raw internal enum đều bằng 0; responsive report tách
horizontal/vertical scroll và ghi work-area thực tế.

Package được chấp thuận với 28/28 PNG có hash riêng, 27 hash ảnh kỹ thuật khớp
`summary.json`, 115.103 record localization và tất cả bộ đếm leak, raw
namespace/model token, chuỗi lặp, acronym không được phép đều bằng 0.

## Dữ liệu và giới hạn

Contract tự động lớn được lưu trong cùng operation parameter key dưới dạng
JSON UTF-8 nén `zlib-base64-v1` (vẫn contract v1, không đổi schema) để giữ giới
hạn primitive 4096 ký tự của `OperationParameterSet`; `from_json` vẫn đọc cả
JSON đầy đủ cũ. Cần có Holder hiện hành và Tool cầu hệ mm để Apply/Calculate.
Khoảng hở máy hiện chỉ là bằng chứng chưa xác minh; Z-Level chưa phải Post sản
xuất sẵn sàng chạy máy.

## QA khóa cuối

- Focused Stage 8A.3.3 và regression liên quan: **302 passed**.
- Toàn repository: **1559 passed, 2 deselected**.
- `pip check`, `compileall src tests tools` và `git diff --check`: đạt.
- Algorithm v2, payload v1, SQLite schema v4, dependency và icon không đổi.
- Simulation chỉ mở cho artifact hiện hành **READY + SAFE v2**; Production Post
  vẫn fail-closed và machine-ready clearance chưa được chứng nhận.
