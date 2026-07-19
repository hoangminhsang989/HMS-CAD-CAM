# CAM Tapping UI và Persistence 7B.7.3

## Phạm vi

Giai đoạn này đưa strategy `tapping_v1` vào CAM workspace và project lifecycle
đang có. UI chỉ làm việc với domain/application service; không đọc hoặc ghi
SQLite trực tiếp, không thay đổi CAD/XCAF kernel và không tạo Post Processor,
G-code, canned cycle hay dữ liệu mô phỏng/collision.

## Tạo operation và tài nguyên

Toolbar CAM có lệnh riêng để tạo bundle TAP cơ bản gồm dao M8 x 1.25 tay phải,
dao tương ứng tay trái, holder, hai `ToolAssembly` và máy phay hỗ trợ Tapping.
Việc tách lệnh này giữ nguyên contract của bundle Tool/Machine cơ bản cũ.

`Thêm Tapping` chỉ commit operation sau khi geometry, TAP assembly và machine
được resolve và `TappingGenerator` xác nhận toàn bộ input. Operation dùng typed
`OperationId`/`CamNodeId`, tham gia rename, reorder, delete và selection restore
giống các operation CAM hiện có.

Editor hỗ trợ:

- `RIGHT_HAND_TAP` và `LEFT_HAND_TAP`;
- `RIGID` và `FLOATING`;
- top Z, final depth, clearance, retract, nominal diameter, pitch dương, RPM,
  dwell tùy chọn và tolerance;
- chọn `ToolAssembly` và `MachineDefinition` thuộc project.

Không có trường linear feed. Feed-per-revolution được strategy giữ bằng pitch.
Hand được biểu diễn bằng spindle semantic: RH đi xuống CW/rút CCW; LH đi xuống
CCW/rút CW. Rigid yêu cầu synchronized feed; Floating chỉ kiểm tra capability
semantic của machine và không giả lập compliance cơ khí.

Draft được giữ theo `OperationId`. Draft chưa Apply hoặc không hợp lệ làm vô
hiệu Generate và không mutation domain. Apply dựng candidate bất biến, validate
đầy đủ rồi mới thay operation trong một command; lỗi command phục hồi editor từ
operation đã commit. Thay geometry, tool, machine, parameter hoặc enabled state
đánh dấu artifact dirty bằng lý do tương ứng; UI không tự đặt `VALID`.

## HolePattern và picking

Tapping tái sử dụng Drilling resolver cho explicit point, BREP vertex, full
circular edge và `HolePattern`. Nhiều selection hợp lệ được bind thành pattern
native-free; từng `GeometryReference` vẫn giữ source, occurrence path,
fingerprint và revision để repeated XCAF occurrence không bị nhập nhằng.

Bind/Rebind chỉ đổi draft sau khi resolve thành công. Cancel, exception, stale
project generation, source mismatch, stale hoặc ambiguous resolution đều giữ
binding cũ. Clear là thao tác rõ ràng. Project switch/close xóa draft, reference
runtime và presentation cũ; callback thuộc generation cũ không được bind hoặc
display.

Các lỗi validation dùng diagnostic ổn định `tap.*`, gồm parameter/depth/
clearance, geometry, tool family/revision/unit/diameter/pitch/hand, machine mode,
spindle direction, synchronized feed, generation và stale result. Tree hiển thị
disabled cùng trạng thái artifact `MISSING`, `DIRTY`, `COMPUTING`, `VALID` hoặc
`FAILED`, và trạng thái hole bound/pattern/missing.

## Viewer và persistence

Generate/Recompute gọi `ProjectService.compute_tapping()` với project generation
mong đợi. Chỉ artifact đã publish mới được hiển thị. Chọn operation nạp artifact
đúng `OperationId`; Show/Hide giữ riêng theo operation. Compute thất bại giữ
artifact/presentation `VALID` cũ, còn stale callback không thay viewer.

SQLite schema vẫn là v4. Serializer hiện có round-trip strategy, HolePattern,
geometry references, tool/machine references, hand/mode, artifact metadata/file
và typed IDs qua Save/Open, Save As, Autosave và Recovery. Runtime token, viewer
registry, QObject/OCP handle và cú pháp NC không được lưu. Khi Open, trạng thái
`COMPUTING` cũ được normalize thành `DIRTY`; artifact thiếu/hỏng được reconcile
thành `DIRTY`/`MISSING`, còn editable strategy/reference vẫn được giữ unresolved
cho đến khi CAD resolver sẵn sàng.

## Kiểm tra

Test bao phủ tạo/edit RH-LH và rigid-floating, draft/Apply atomic, Bind/Rebind/
Clear, multi-hole và explicit point pattern, mọi mismatch chính, machine thiếu
sync, Generate/Recompute/Show-Hide, stale/failure preservation, project switch,
Save/Open/Save As/Autosave/Recovery, normalization và artifact tampering. Các
suite Drilling, CAM, project lifecycle và CAD/XCAF tiếp tục là regression gate.
