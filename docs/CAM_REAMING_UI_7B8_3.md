# HMS CAM — Reaming UI và Persistence 7B.8.3

## Phạm vi

Giai đoạn 7B.8.3 đưa strategy `reaming_v1` vào CAM workspace và project
lifecycle hiện có. UI chỉ gọi domain/application service, không đọc ghi SQLite
trực tiếp, không sửa CAD/XCAF kernel và không tạo Post Processor, G-code, canned
cycle, simulation hoặc collision.

## Tạo operation và tài nguyên

Toolbar CAM có lệnh tạo bundle project-owned gồm dao doa D8, holder,
`ToolAssembly` và máy phay hỗ trợ Drilling, hai chiều spindle cùng coolant Flood,
Mist và Through-spindle. `Thêm Reaming` yêu cầu một HolePattern resolve được,
REAMER assembly và máy tương thích trước khi commit operation.

Operation dùng `OperationId`/`CamNodeId` typed. Rename, delete, reorder và refresh
tree tiếp tục giữ identity theo domain ID, không dùng row index. Project switch
hoặc close xóa draft, selection runtime và presentation cũ.

## Editor và draft policy

Editor cho phép nhập Top Z, final depth, clearance, retract, finished nominal
diameter, pre-hole diameter bắt buộc, RPM, feed-per-revolution, dwell tùy chọn,
spindle direction, controlled-feed retract, coolant và tolerance. Không có trường
allowance hoặc feed-per-minute có thể chỉnh sửa.

Stock mỗi phía, feed-per-minute và cutting depth được xem trước read-only:

```text
stock_per_side = (nominal_diameter - pre_hole_diameter) / 2
feed_per_minute = feed_per_revolution × RPM
cutting_depth = top_z - final_depth
```

Draft được giữ theo `OperationId` và không nằm trong persistence. Draft chưa Apply
hoặc không hợp lệ vô hiệu Generate. Apply dựng candidate bất biến, resolve
geometry, kiểm tra tool/machine rồi mới thay operation bằng một command. Validation
hoặc command failure giữ domain cũ, phục hồi editor từ operation đã commit và hiển
thị diagnostic ổn định. UI không tự sửa pre-hole, tự chọn resource thay thế hoặc
tự đặt artifact thành `VALID`.

## Geometry và validation

Reaming tái sử dụng Drilling picker/resolver cho explicit point, BREP vertex, full
circular edge, repeated XCAF occurrence và multi-hole. Bind/Rebind chỉ cập nhật
draft sau khi resolve thành công; cancel, exception, stale generation, ambiguous,
source mismatch hoặc resolution failure giữ binding cũ. Clear là mutation rõ ràng
và làm artifact dirty.

Circular-edge diameter chỉ xác nhận finished nominal diameter. Nó không cung cấp
pre-hole và UI không suy đoán dữ liệu từ Drilling artifact. Pre-hole phải dương,
nhỏ hơn nominal và tạo stock mỗi phía lớn hơn tolerance nhưng nhỏ hơn giới hạn
hình học. Các lỗi được chuyển qua nhóm `ream.*`, gồm geometry, pre-hole/stock,
depth/clearance, tool snapshot/family/diameter/length/stickout, machine
RPM/feed/coolant/spindle, generation và stale result.

## Viewer và persistence

Generate/Recompute gọi `ProjectService.compute_reaming()` với project generation
mong đợi. Chỉ artifact đã publish được display; failure hoặc stale callback giữ
artifact/presentation `VALID` cũ. Show/Hide, operation switch, remove, project
switch và close dùng registry theo `OperationId`. Controlled retract vẫn là nhóm
presentation riêng, không bị hiển thị như rapid.

SQLite giữ schema v4. Codec operation hiện có round-trip strategy, HolePattern,
GeometryReference, tool/machine snapshot, typed IDs và artifact metadata/file qua
Save/Open, Save As, Autosave và Recovery. Allowance, feed-per-minute, computation
token, viewer/native object và NC syntax không được lưu như dữ liệu nguồn. Open
normalize `COMPUTING` thành `DIRTY`; artifact thiếu/hỏng được reconcile fail-closed
mà không tự rebind geometry hoặc làm project dirty.

## Kiểm tra

- `tests/unit/test_reaming_ui.py`: MM/INCH, draft/Apply atomic, derived preview,
  Bind/Rebind/Clear, edge/tool/machine mismatch, Generate/Show-Hide, stale/failure,
  project switch và persistence/recovery/tamper/normalization.
- Các suite Reaming domain/strategy/viewer/recompute, Drilling/Tapping, CAM,
  project lifecycle và CAD/XCAF là regression gate.
- `tests/manual_stage7b83_gui.py`: smoke Windows/PySide6/OCP thật cho multi-hole,
  editor, validation rollback, Generate, controlled retract, Show/Hide,
  Save/Open/Save As, Autosave/Recovery, project switch, resize và close.

## Giới hạn còn lại

Reaming v1 chỉ có controlled-feed retract và pre-hole do người dùng khai báo.
Chưa có dependency Drilling, Boring, simulation, collision, Post Processor,
G-code hoặc cú pháp G85/G86.
