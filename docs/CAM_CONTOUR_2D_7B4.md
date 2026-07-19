# 2D Contour Foundation — Giai đoạn 7B.4

## Strategy và dữ liệu công khai

2D Contour dùng `family=milling`, `strategy_key=contour_2d`,
`strategy_version=1`. `ContourParameters` là model thuần Python, versioned và
mang unit tường minh. Toolpath luôn ở `SETUP_WCS`; không có cú pháp controller,
G41/G42 hoặc G-code trong parameter hay artifact.

`ContourProfileDescriptor` chứa `GeometryReference`, basis mặt phẳng, outer
loop LINE/ARC có thứ tự, orientation, bounds, geometry fingerprint và
`ProfileProvenance`. Descriptor không chứa OCP, AIS, `TopoDS`, runtime
`CadDocumentId` hoặc `CadObjectId` và chỉ là kết quả resolve, không phải dữ liệu
master được persistence.

## Selector và profile resolution

Selector `hms_profile_v1` hỗ trợ outer wire của một planar FACE hoặc một closed
WIRE được chọn rõ ràng. Selector dựa trên persistent container, geometry digest
và occurrence path; fingerprint còn chứa absolute occurrence transform. Vì vậy
repeated/nested XCAF occurrences được phân biệt và thay topology hoặc transform
làm reference fail-closed. Geometry từ presentation occurrence đã ở world space;
adapter chỉ đổi về local để tạo selector và generator chỉ đổi world sang Setup
WCS một lần.

V1 nhận đúng một outer loop kín, đơn giản, planar, gồm LINE và circular ARC.
Open chain, self-intersection, spline/curve khác, inner loop/island, source khác,
stale, ambiguous và topology changed trả diagnostic `contour.*`; hệ thống không
tìm profile gần giống, không tự rebind và không thay profile bằng bounding box.

## Orientation, side và offset

Sau khi đổi sang Setup WCS, loop được chuẩn hóa CCW và bắt đầu tại midpoint của
segment có midpoint nhỏ nhất theo `(X, Y)`. Quy tắc này độc lập thứ tự edge OCP.
Với spindle clockwise nhìn từ +Z:

- `INSIDE + CLIMB` chạy CCW; `INSIDE + CONVENTIONAL` chạy CW.
- `OUTSIDE/ON + CLIMB` chạy CW; `OUTSIDE/ON + CONVENTIONAL` chạy CCW.
- `ON` giữ nguyên đường profile làm đường tâm dao.
- `INSIDE/OUTSIDE` offset đường tâm theo `tool_radius + radial_allowance`.

Offset thuần Python xử lý LINE/LINE, LINE/ARC và ARC/ARC bằng giao support hình
học. Segment zero-length, join không liên tục, radius co sập, segment đảo hướng,
self-intersection hoặc topology đảo đều bị từ chối; không có fallback âm thầm.
Polygon lõm đơn giản được nhận khi offset vẫn tạo được một loop an toàn, nếu
không sẽ fail với `contour.offset_failed` hoặc `contour.offset_collapsed`.

## Depth, lead và safe motion

`top_height` và `final_depth` là tọa độ Z tuyệt đối trong Setup WCS; chiều cắt
đi theo −Z. `stepdown` luôn dương. Mặt cắt cuối là
`final_depth + axial_stock_allowance`, chạm đúng một lần trong tolerance; tắt
multiple-depth tạo một lớp cuối. `finishing_pass` v1 là một spring pass lặp lại
loop tại lớp cuối, không phải rest machining.

Entry/exit v1 là linear lead bắt buộc với độ dài dương. Start nằm giữa segment
nên có tangent xác định; generator thử hai normal và chỉ nhận lead khi toàn bộ
mẫu nằm đúng phía INSIDE hoặc OUTSIDE/ON của profile thật. Không tạo được lead
an toàn thì fail `contour.unsafe_lead`, không tự đổi sang direct plunge.

Mỗi lớp gồm rapid ở clearance, plunge/link tại lead point, lead-in, toàn bộ
closed contour LINE/ARC, lead-out, retract và chuyển lớp ở safe height. Rapid
không đi dưới retract; cuối artifact luôn trở lại clearance. Builder bị abort
nếu lỗi nên không có partial artifact.

## Tool, recompute, persistence và viewer

V1 chỉ nhận `END_MILL` và `BULL_NOSE_END_MILL`. Tool/assembly revision,
fingerprint, unit, diameter, axial cutting length, stickout, machine milling
capability, feed và spindle limits đều được kiểm tra; không tự chọn dao thay thế.

Input fingerprint gồm canonical GeometryReference, resolved profile fingerprint,
occurrence path/transform, Setup revision/WCS, parameters, offset loop, tool,
machine, operation revision qua publish provenance và strategy version. Tên hiển
thị không nằm trong fingerprint. Recompute dùng contract validate → begin token
→ resolve → offset → generate → validate → publish; token cũ hoặc operation đã
đổi/xóa/disable không được publish. Recompute lỗi giữ artifact `VALID` trước đó.

SQLite vẫn là schema v4: operation parameters và GeometryReference round-trip
qua payload hiện có; artifact vẫn ở `toolpaths/*.toolpath.json`. Save/Open, Save
As, Autosave/Recovery và missing/tampered artifact dùng lifecycle 7A.6, không có
migration. Viewer tách màu rapid, plunge/link, lead-in, cutting, lead-out và
retract; ARC được render cong, presentation không tham gia CAD selection và bị
clear khi đổi project.

## Giới hạn chủ ý

Chưa hỗ trợ multi-loop/island, Pocket, tab/bridge, ramp/helix, direct plunge,
rest machining, cutter compensation G41/G42, stock removal, collision engine
đầy đủ, simulation máy, Post Processor hoặc G-code.
