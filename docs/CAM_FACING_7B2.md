# CAM Facing 2.5D — Giai đoạn 7B.2

## Strategy và quy ước tọa độ

Facing dùng `family=milling`, `strategy_key=facing_2_5d`, `strategy_version=1`.
Parameter payload thuần Python mang đơn vị chiều dài tường minh (`mm` hoặc `inch`),
feed theo đơn vị/phút và spindle theo RPM. Toolpath luôn ở `SETUP_WCS`; không dùng
machine coordinate, work-offset controller hay cú pháp G-code.

Trục dao của artifact hướng theo **+Z Setup WCS**; chuyển động xuống dao đi theo
**−Z**. `top_height`, `target_height`, `retract_height` và `clearance_height` đều là
tọa độ Z tuyệt đối trong Setup WCS, không phải depth dương tương đối. Vật liệu cần
bóc nằm từ `top_height` xuống `target_height`; mặt cắt cuối ở
`target_height + stock_allowance`. Retract phải cao hơn top và clearance phải bằng
hoặc cao hơn retract.

## Hình học và dao được hỗ trợ

Luồng sản phẩm v1 resolve mặt trên của `Stock BOX` sang polygon phẳng trong Setup
WCS. Stock khác bị fail-closed. Domain cũng có `PlanarFaceDescriptor` native-free
cho adapter CAD: face phải planar, normal cùng hướng +Z WCS và fingerprint phải do
resolver xác thực. UI 7B.2 chưa nối resolver OCP cho face nên nguồn này không được
Generate trong GUI; không có heuristic rebind và không dùng object identity.

Dao được chấp nhận: `FACE_MILL`, `END_MILL`, `BULL_NOSE_END_MILL`. Tool Assembly,
Tool Definition và Machine snapshot phải tồn tại, đúng revision/fingerprint/unit.
Máy phải là MILL hoặc MILL_TURN có capability milling; feed và spindle phải nằm
trong giới hạn machine. Generator không tự đổi dao hoặc máy.

## Raster và safe-motion policy

Boundary được chiếu lên hệ trục raster theo góc `[0, 180)`. Lane bắt đầu đúng tại
hai biên chiếu và khoảng còn lại cuối cùng luôn được thêm, nên stepover không làm
sót strip do làm tròn. Hai đầu lane được kéo dài bằng bán kính dao cộng
`overtravel`. Các lớp Z đi từ trên xuống, lớp cuối được chặn đúng tại target cộng
allowance; thứ tự lớp/lane là xác định.
Generation fail trước khi tạo artifact nếu tổ hợp số lớp và lane vượt quá 20.000
cutting passes, tránh input cực nhỏ làm treo UI hoặc tạo artifact vượt policy lưu trữ.

`CLIMB` giữ chiều âm→dương của trục raster, `CONVENTIONAL` giữ chiều ngược lại;
quy ước này giả định spindle quay clockwise khi nhìn từ +Z về phôi.
`BIDIRECTIONAL` đảo chiều từng lane để giảm rapid. Mỗi lane rapid tới clearance,
plunge tuyến tính bằng link feed, cắt tuyến tính, rồi retract tuyến tính. Mọi rapid
ngang nằm ở clearance; rapid cuối bắt đầu ở retract, cả hai đều trên stock. Nếu
không chứng minh được clearance hoặc continuity, builder bị abort và không có
artifact partial.

## Fingerprint, recompute và persistence

Input fingerprint gồm parameters, Setup revision/WCS, stock hoặc face fingerprint,
Tool Assembly + Tool Definition và Machine snapshot. `ToolpathBuilder` tạo event ID
xác định từ operation/input/sequence/provenance. Compute dùng generation token của
7A.4; token cũ, input đổi, operation bị xóa/disable hoặc provenance lệch không được
publish. Candidate chỉ được ghi atomically sau khi publish contract chấp nhận.

Schema SQLite vẫn là v4. Facing parameters đi qua `OperationParameterSet`; artifact
đi qua store `toolpaths/*.toolpath.json` hiện có. Save/Open, Save As,
Autosave/Recovery vì vậy không cần migration. File artifact thiếu/hỏng tiếp tục làm
operation DIRTY mà không làm mất editable parameters.

## Viewer và giới hạn

Viewer tạo presentation riêng theo operation cho rapid, cutting, link và retract;
presentation không tham gia CAD/XCAF selection và bị clear khi đổi/đóng project.
UI có thêm Facing, editor parameters, Generate/Recompute và visibility toggle.

Chưa hỗ trợ mặt cong, boundary có đảo/hole, face resolver OCP trong GUI, finish pass,
stock removal, collision engine, mô phỏng dao, Pocket, Contour, Drilling, Turning,
Post Processor hoặc G-code. Arc presentation hiện chỉ cần thiết khi generator tương
lai phát sinh arc; Facing v1 chỉ tạo đoạn thẳng.
