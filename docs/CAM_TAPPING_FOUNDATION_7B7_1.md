# CAM Tapping Foundation 7B.7.1

## Phạm vi

Giai đoạn 7B.7.1 bổ sung domain và strategy Tapping thuần Python, dùng
`strategy_key` là `tapping_v1`. Tapping tiếp tục thuộc
`OperationFamily.DRILLING` và tái sử dụng nguyên trạng geometry/resolver của
Drilling cho điểm tường minh, BREP vertex, cạnh tròn đầy đủ và `HolePattern`.

Không có thay đổi SQLite schema, CAD/XCAF kernel hoặc UI trong giai đoạn này.

## Quy ước process

- Pitch luôn là độ dài dương; hand không được biểu diễn bằng pitch âm.
- Right-hand dùng spindle clockwise khi xuống và counterclockwise khi rút.
- Left-hand dùng spindle counterclockwise khi xuống và clockwise khi rút.
- Feed chuyển động đồng bộ dùng đơn vị độ dài trên vòng quay và bằng đúng pitch.
- Feed theo phút chỉ được dẫn xuất từ pitch nhân RPM để kiểm tra giới hạn máy.
- Rigid và floating là semantic policy độc lập với cú pháp controller.

Mỗi lỗ có chuỗi semantic xác định: rapid, approach, synchronization begin,
spindle cutting direction, synchronized descent, dwell tùy chọn, spindle
reversal, synchronized retract, hole complete, synchronization end và final
retract an toàn.

## Tool và machine

Chỉ `ToolFamily.TAP` được chấp nhận. Validation kiểm tra snapshot assembly/tool,
unit, đường kính danh nghĩa, pitch, hand, threaded length, usable length và
stickout.

Machine phải khai báo `OperationCapability.TAPPING`, tapping mode tương ứng,
hai chiều spindle và synchronized feed. Payload machine legacy vẫn đọc và tạo
lại fingerprint cũ khi không có capability mới; capability legacy trống không
được hiểu ngầm là hỗ trợ tapping.

## Derived artifact và recompute

Input fingerprint bao phủ strategy, geometry đã resolve, Setup/WCS,
tool/assembly và machine. Publish dùng computation token hiện hành; candidate
cũ hoặc provenance sai không thể ghi đè artifact. Recompute thất bại giữ
artifact `VALID` đã publish trước đó.

## Giới hạn

Chưa có Tapping UI, Viewer chuyên biệt, simulation, collision, controller
mapping hay chương trình máy. Floating policy hiện là capability semantic;
compliance vật lý của holder chưa được mô phỏng.
