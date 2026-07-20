# CAM Boring Foundation 7B.9.1

## Phạm vi

`boring_v1` biểu diễn single-point axial boring bằng boring bar/head trên Setup
`MILL` hoặc `MILL_TURN`. Operation tiếp tục thuộc `OperationFamily.DRILLING` và
chỉ yêu cầu `OperationCapability.DRILLING`.

Foundation này không biểu diễn fine boring với retract offset, back boring,
interpolation boring, taper boring, spindle orient, probing, bù đường kính tự
động, Post Processor hoặc bất kỳ cú pháp G/M-code nào.

## Strategy và pre-bore

`BoringStrategy` là model immutable, versioned và thuần Python. Dữ liệu nguồn
gồm unit, `DrillGeometryInput`, `DrillDepthDefinition`, finished/pre-bore
diameter, spindle RPM/direction, feed-per-revolution, clearance/retract height,
dwell, coolant, controlled-retract policy và tolerance.

`pre_bore_diameter` là bắt buộc. Circular EDGE chỉ xác nhận finished bore
diameter; nó không phải bằng chứng cho pre-bore. Strategy không đọc artifact
Drilling/Reaming và không tạo upstream operation dependency.

Các giá trị dẫn xuất không được lưu độc lập:

- `radial_stock = (finished_bore_diameter - pre_bore_diameter) / 2`;
- `feed_per_minute = feed_per_revolution × spindle_rpm`;
- cutting depth lấy từ `DrillDepthDefinition`;
- duration lấy từ Toolpath IR khi spindle/process state đầy đủ.

## BoringBarGeometry

Tooling contract bổ sung `ToolFamily.BORING_BAR` và geometry variant versioned
`BoringBarGeometry` với:

- `minimum_bore_diameter`;
- `maximum_bore_diameter`;
- `cutting_length`;
- `hand`.

Variant mới không thay đổi payload hay fingerprint của các tool family cũ.
BORING_BAR bắt buộc dùng đúng BoringBarGeometry; geometry malformed hoặc version
tương lai bị từ chối fail-closed. Nose radius chưa thuộc v1 vì strategy không
thực hiện compensation hay validation công nghệ phụ thuộc nose radius.

## Accessibility và holder clearance

Với tolerance `t`, cutting depth `d`, pre-bore `p`, finished diameter `f`,
shank diameter `s`, minimum/maximum tool reach `b_min`/`b_max` và stickout `L`,
generator yêu cầu:

- `p >= b_min + t`;
- `f <= b_max + t`;
- `p - s > t` để có diametral shank clearance dương;
- `cutting_length + t >= d`;
- `usable_length + t >= d`;
- `L - d > t`.

Điều kiện cuối giữ holder reference plane ở phía trên entrance/top plane tại
final depth. Holder definition phải tồn tại và khớp revision/fingerprint trong
ToolAssembly. Holder profile hiện mô tả các section tính từ reference plane ra
phía ngoài dụng cụ; v1 cấm reference plane đi xuống dưới entrance plane thay vì
cố cho holder chui vào lỗ.

Đây là policy bảo thủ, không phải full collision simulation. Nó không kiểm tra
fixture, thành ngoài chi tiết, độ võng bar, insert envelope hoặc động học máy.

`ToolHand.RIGHT` đi với spindle clockwise; `ToolHand.LEFT` đi với spindle
counterclockwise. Không tự thay tool khi validation thất bại.

## Geometry resolver dùng chung

`DrillingGeometryResolver` tái-resolve từng `HoleReference` nằm trong
`HolePattern`; snapshot BREP location cũ không còn được tin trực tiếp. Một
reference missing, stale, ambiguous, source-mismatched hoặc topology-changed làm
toàn bộ pattern fail, không có partial result và không auto-rebind.

Explicit point giữ contract cũ. Pattern sau resolve được dựng lại qua
`HolePattern`, vì vậy canonical order và duplicate detection tại tolerance
boundary vẫn xác định. Selector OCP/XCAF và public geometry payload không đổi.

## Machine, feed và Toolpath IR

MachineDefinition không đổi. Validation dùng MachineKind MILL/MILL_TURN,
DRILLING capability, spindle direction/RPM range, maximum feed và coolant.
Không yêu cầu tapping synchronization, spindle orient hoặc capability BORING.

Feed nguồn là distance-per-revolution; Toolpath IR dùng
`FeedMode.UNITS_PER_REVOLUTION`. Mỗi lỗ có sequence:

1. rapid tới clearance và approach tới retract plane;
2. `bore.process_begin`, spindle/coolant begin;
3. controlled cutting feed tới final depth và optional dwell;
4. controlled axial feed retract tới retract height;
5. `bore.hole_complete`, rapid lên clearance;
6. spindle/coolant end và `bore.process_end`.

Không rapid trực tiếp từ đáy và không rapid ngang dưới clearance height.
Toolpath IR hiện tại đã đủ nên không thêm event, raw controller text hay cycle
code.

## Recompute, fingerprint và persistence

Input fingerprint gồm strategy/version, canonical resolved geometry, Setup/WCS,
operation revision/enabled, ToolAssembly/ToolDefinition/Holder và Machine
snapshots. Không có upstream artifact fingerprint.

Publish dùng computation token và candidate provenance hiện có. Candidate cũ,
operation đã sửa/xóa/disable hoặc project generation không còn current bị từ
chối. Generation/store failure không ghi partial artifact và giữ artifact VALID
cũ.

Strategy, geometry references và BORING_BAR tooling dùng codec JSON xác định
trong SQLite v4. Repository không lưu computation token đang chạy; không có
migration schema trong giai đoạn này.
