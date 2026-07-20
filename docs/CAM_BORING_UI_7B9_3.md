# HMS CAM — Boring UI và Persistence 7B.9.3

## Phạm vi

Giai đoạn 7B.9.3 đưa strategy `boring_v1` vào CAM workspace và project lifecycle
hiện có. UI gọi domain/application service, không đọc ghi SQLite trực tiếp, không
sửa CAD/XCAF kernel và không tạo simulation, collision đầy đủ, Post Processor,
G-code hoặc canned cycle G85/G86/G87/G89.

## Operation và tài nguyên

Toolbar CAM có lệnh tạo bundle project-owned gồm BORING_BAR D15–D25 tay phải,
holder, `ToolAssembly` và máy MILL hỗ trợ capability Drilling, hai chiều spindle
cùng Flood/Mist/Through-spindle. `Thêm Boring` yêu cầu HolePattern resolve được,
BORING_BAR assembly hiện hành và máy MILL/MILL_TURN tương thích trước khi commit.

Tree hiển thị Job → Setup → Boring theo `CamJobId`, `SetupId`, `CamNodeId` và
`OperationId`; không dùng row index làm identity. Rename, delete, reorder, refresh,
project switch và close giữ hoặc dọn selection/draft/presentation theo domain ID.

## Editor và Apply policy

Editor cho nhập Top Z, final depth, clearance, retract, finished bore diameter,
pre-bore diameter bắt buộc, RPM, feed-per-revolution, dwell tùy chọn, spindle
direction, controlled-feed retract, coolant và tolerance. Không có trường
feed-per-minute hoặc radial stock có thể sửa độc lập.

Các giá trị read-only được tính deterministic từ draft:

```text
radial_stock = (finished_bore_diameter - pre_bore_diameter) / 2
feed_per_minute = feed_per_revolution × RPM
cutting_depth = top_z - final_depth
```

Draft được giữ theo `OperationId`, không nằm trong persistence và không làm project
dirty. Generate bị vô hiệu đến khi draft khớp operation đã Apply. Apply dựng một
candidate bất biến, resolve toàn bộ geometry, kiểm tra tool/holder/machine rồi mới
thay operation bằng một command atomic. Validation hoặc command failure giữ domain
cũ, phục hồi UI từ operation đã commit và không tự sửa pre-bore, chọn resource thay
thế hoặc chuyển artifact sang `VALID`.

## Tool, machine và geometry

Combo tool của Boring chỉ hiển thị assembly có `ToolFamily.BORING_BAR`. Nhãn
read-only hiển thị min/max bore, cutting/usable length, hand, unit, shank, stickout,
holder, revision/fingerprint và trạng thái snapshot current/stale. Validation
fail-closed bao phủ access/reach, hand/direction, cutting/usable length, shank,
holder-stickout, coolant và provenance tool/holder.

Machine phải là MILL hoặc MILL_TURN, có `OperationCapability.DRILLING`, spindle
direction/RPM phù hợp, không vượt maximum feed đã derive và hỗ trợ coolant yêu cầu.
Không yêu cầu tapping synchronization, spindle orient hoặc capability Boring riêng.

Boring tái sử dụng Drilling picker/resolver cho explicit point, BREP vertex, full
circular edge, repeated XCAF occurrence và multi-hole. Bind/Rebind chỉ cập nhật
draft sau khi toàn bộ references resolve; cancel, lỗi, ambiguous, stale, source
mismatch hoặc project/document switch giữ binding cũ. Clear là mutation rõ ràng.
Circular edge chỉ xác nhận finished diameter; pre-bore luôn do người dùng khai báo,
không suy đoán từ edge hoặc artifact Drilling/Reaming và không tạo dependency mới.

## Viewer và persistence

Generate/Recompute gọi `ProjectService.compute_boring()` với project generation
mong đợi. Chỉ artifact đúng provenance được display; failure hoặc stale callback
giữ artifact/presentation `VALID` cũ. Show/Hide, operation switch, remove, project
switch và close dùng registry theo `OperationId`; controlled retract giữ semantic
riêng, không bị phân loại thành rapid.

SQLite giữ schema v4. Codec hiện có round-trip strategy, HolePattern,
GeometryReference, finished/pre-bore, RPM/feed-per-revolution, Z heights, dwell,
spindle/coolant/retract policy, BORING_BAR geometry, assembly/holder/machine typed
references và artifact metadata/file qua Save/Open, Save As, Autosave và Recovery.
Radial stock, feed-per-minute, runtime token, viewer/native object và NC syntax
không được lưu như nguồn độc lập. Open normalize `COMPUTING` thành `DIRTY`; artifact
thiếu/hỏng và geometry unresolved được giữ fail-closed, không auto-rebind hoặc làm
project dirty. Payload future version bị từ chối.

## Kiểm tra và giới hạn

- `tests/unit/test_boring_ui.py`: MM/INCH, draft/Apply atomic, derived preview,
  Bind/Rebind/Clear, multi-hole/explicit pattern/edge mismatch, tool/holder/machine,
  Generate/Recompute/Show-Hide, stale callback, Save/Open/Save As,
  Autosave/Recovery, tampered artifact, normalization và future payload.
- `tests/manual_stage7b93_gui.py`: smoke Windows/PySide6/OCP thật cho toàn bộ luồng
  UI/persistence/lifecycle chính.
- Regression gate giữ Boring 7B.9.1/2, shared resolver, Drilling/Tapping/Reaming,
  CAM/project persistence và CAD/XCAF.

Boring v1 vẫn chỉ hỗ trợ controlled-feed retract và pre-bore khai báo tường minh;
chưa có simulation, collision đầy đủ, Post Processor hoặc G-code.
