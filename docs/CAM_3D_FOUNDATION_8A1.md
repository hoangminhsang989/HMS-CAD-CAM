# Nền tảng hình học và tính toán CAM 3D - Giai đoạn 8A.1

## Mục tiêu

8A.1 thiết lập dữ liệu đầu vào, provenance, mesh tính toán và lifecycle an
toàn cho CAM 3D. Giai đoạn này chưa sinh đường chạy dao sản xuất. Các contract
mới độc lập với UI, Viewer và OCP; phần truy cập Open CASCADE chỉ tồn tại trong
adapter hạ tầng.

Phạm vi v1 là một Job/Setup, đơn vị MM, ba trục và tool axis cố định theo trục
Z của Setup WCS. Dữ liệu không dùng tọa độ máy và không hỗ trợ indexed 3+2 hay
4/5-axis.

## Tài liệu WorkNC đã tham khảo chọn lọc

Việc đọc chỉ giới hạn ở các phần liên quan trực tiếp, không quét toàn bộ PDF
Online Help 1.402 trang:

- `reference_private/WORKNC/ONLINE_HELP/worknc.pdf`: trang PDF 20 về Part
  Geometry, CAM Axis và vai trò của hệ tọa độ trong tính toán; trang 31-36 về
  phân tích hình học, Z-level và hướng chi tiết.
- `reference_private/WORKNC/TRAINING/3X_basic_training_guide_worknc_2018_R2_done.pdf`:
  trang PDF 11-21 về Offset/Stock Allowance, tolerance, chordal deviation,
  minimum triangle size, Part Geometry, Surface Lists và mesh dùng cho tính
  toán; trang PDF 111-121 về Z-Level, lead-in/out, linking và retract; trang
  PDF 126-132 về Parallel Finishing, machining zone và tolerance; trang PDF
  170-172 về Recommended Safe Tool Length; trang PDF 219-222 về Stock Model;
  trang PDF 225-243 về Machining Zone, surface/view/boundary selection,
  inclusion, offset và giới hạn chiều sâu.

Các khái niệm áp dụng là: phân vai part/check/fixture rõ ràng, machining zone
là tập đầu vào có WCS, boundary, tolerance và allowance; tessellation tính
toán có tolerance riêng; safe motion phải explicit; stock mô phỏng và stock
tính toán là derived data khác nhau.

Các khái niệm hoãn lại gồm: thuật toán Parallel/Z-Level hoàn chỉnh, global
roughing, rest/remachining, tự tối ưu linking, stock removal chính xác,
recommended safe tool length production, variable tool axis và mọi hành vi
độc quyền của WorkNC. HMS không sao chép giao diện hoặc nội dung tài liệu.

## Surface reference và surface role

`CamSurfaceReference` bao bọc `GeometryReference` hiện có và bổ sung project
identity, orientation, body/face identity cùng một role explicit:

- `PART`: bề mặt mục tiêu gia công.
- `CHECK`: bề mặt phải bảo vệ.
- `FIXTURE`: bề mặt đồ gá dùng cho kiểm tra fail-closed.
- `STOCK_REFERENCE`: tham chiếu stock cho giai đoạn sau.

Identity dựa trên source UUID, persistent container/occurrence/face selector,
geometry revision và fingerprint. Display name, màu, layer, UI row, địa chỉ
OCP và `id()` không tham gia identity. Face thiếu, bị xóa, stale hoặc ambiguous
không được resolve như dữ liệu current.

`CamSurfaceSelection` canonicalize thứ tự, loại trùng target, giữ role và kiểm
tra cùng project/revision. `PartSurfaceSet` bắt buộc không rỗng;
`CheckSurfaceSet` và `FixtureSurfaceSet` có empty policy explicit. Việc tạo
draft/preview chỉ dựng immutable value object và không tự làm project dirty.

## Machining boundary và machining zone

`MachiningBoundary3D` v1 hỗ trợ:

- `CLOSED_PLANAR_CONTOUR`;
- `SURFACE_SILHOUETTE_REFERENCE` ở mức provenance, chưa tạo silhouette sản
  xuất;
- `NONE`.

Closed contour mang Setup/plane, tolerance, orientation, inclusion policy,
source geometry và fingerprint. Contour hở, ngoài plane, cạnh zero-length,
self-intersection hoặc orientation sai bị từ chối; hệ thống không tự sửa hình
học âm thầm.

`MachiningZone3D` gom Part/Check/Fixture sets, optional boundary, Job/Setup,
Setup WCS, fixed tool axis, optional machining direction/height limits,
tolerance, allowance và geometry provenance. Boundary khác Setup, selection
khác project/revision hoặc tool axis khác Setup Z đều fail-closed.

## Mesh tính toán

`OcpCam3DSurfaceAdapter` resolve persistent face, sao chép face rồi mới gọi
OCP tessellation. Vì vậy tessellation tính toán không dùng Viewer mesh làm
nguồn chân lý và không gắn mesh vào CAD source đang giữ.

`Cam3DCalculationMesh` chứa vertex, triangle index, normal, mapping triangle
về source face, bounding box, chordal/angular tolerance, MM, source geometry
fingerprint, mesh fingerprint và statistics. Builder:

- kiểm tra finite value, index và triangle không suy biến;
- canonicalize tọa độ theo calculation epsilon, cyclic triangle order và
  surface order;
- giữ orientation nhất quán;
- giới hạn vertex/triangle;
- có cancellation checkpoint;
- hash canonical JSON, không hash float/runtime object chưa chuẩn hóa.

Mesh là derived artifact tại `cache/cam3d/<project-id>/`; không lưu mesh lớn
trong SQLite. Cache không được copy khi Save As hoặc Autosave và có thể xóa,
tái tạo từ source/config.

## Tolerance và allowance

`Cam3DTolerancePolicy` tách chordal tolerance, angular tolerance,
calculation epsilon, boundary tolerance, contact tolerance và optional minimum
triangle size. Tất cả dùng MM, finite, có giới hạn an toàn và tham gia
fingerprint; chúng không phụ thuộc DPI, Viewer quality hoặc zoom.

`Cam3DStockAllowance` tách part-normal, axial, check-surface clearance và
boundary offset. V1 chỉ lưu/validate semantic, cho phép zero, không offset
B-Rep sản xuất và không trộn allowance với tolerance.

## Tool-contact và safe motion foundation

`project_point_to_triangle` cung cấp phép chiếu trực giao kèm barycentric
evidence. `calculate_tool_contact` chứng minh hai trường hợp giới hạn:

- ball-end: tool center dịch từ contact theo normal một bán kính;
- flat/end mill: chỉ mặt phẳng có normal trùng fixed tool axis.

Contract trả contact point, tool-center point, surface normal, tool axis,
source triangle/surface và evidence. Triangle suy biến, normal/point không hợp
lệ, tool geometry ngoài phạm vi hoặc offset không thể thực hiện đều fail-closed.
Đây không phải contact kernel production-complete.

`Cam3DSafeMotionPolicy` lưu clearance Z, retract Z, approach distance, link
clearance, transition policy, Setup WCS fingerprint và tool axis. Validator
yêu cầu safe Z explicit, trên bounding box + allowance, đúng Setup/revision/WCS
và đúng fixed axis. 8A.1 không tạo linking motion và không khẳng định đường đi
không va chạm như một thuật toán sản xuất.

## Calculation context và lifecycle

Pipeline của `Cam3DGeometryService`:

```text
immutable project/CAD capture
  -> validate project, Setup, zone và surface references
  -> tessellate từng face qua adapter
  -> canonical mesh validation
  -> build Cam3DCalculationContext
  -> latest-wins stale recheck
  -> atomic publish
```

Context fingerprint gồm project generation, Job/Setup, geometry snapshot,
zone, mesh, tool assembly/definition fingerprints, tolerance, allowance, safe
motion và algorithm/version. Timestamp, UI state, progress callback, absolute
path, request token và worker handle không tham gia identity.

Service có các state `MISSING`, `VALIDATING`, `TESSELLATING`,
`VALIDATING_MESH`, `CURRENT`, `STALE`, `FAILED`, `CANCELLED`. Project switch,
close, generation/revision/Setup/selection/boundary/tolerance/allowance/tool
hoặc token thay đổi đều chặn publish. Candidate không được công bố từng phần;
context hợp lệ trước đó được giữ làm evidence khi request mới thất bại.

Editable config nằm tại `cam/cam3d_foundation.hms.json`, có format/version và
atomic write. Project Open chỉ đọc, không tự tessellate và không làm dirty.
Save As đổi project identity, Autosave/Recovery dùng workspace riêng. Project
chỉ có CAD không tự tạo config/cache CAM 3D. SQLite vẫn schema v4.

## Diagnostics và giới hạn an toàn

Catalog dùng namespace `cam3d.*` cho invalid request, missing/stale/duplicate
surface, invalid boundary/Setup/tolerance/allowance/mesh/orientation/safe
motion/tool/contact, geometry changed, stale, cancelled và failed. Diagnostic
có thể gắn source face, triangle index, boundary fingerprint, Setup và evidence
summary.

8A.1 không có production Parallel Finishing, Z-Level, roughing, rest
machining, remachining, stock removal chính xác, machine kinematics, Turning,
4/5-axis, Post mới, NC output hoặc machine certification. Kết quả mesh/contact
không phải chứng nhận an toàn máy.

## Dự kiến 8A.2

8A.2 có thể xây Parallel Finishing trên calculation context hiện tại: sampling
theo machining direction, surface contact traversal, clipping theo boundary,
safe approach/retract/linking có collision evidence, strategy diagnostics và
toolpath candidate. Giai đoạn đó phải có chỉ thị riêng và không được suy ra là
đã hoàn thành từ 8A.1.
