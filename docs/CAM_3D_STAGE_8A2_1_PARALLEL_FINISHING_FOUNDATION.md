# Parallel Finishing Foundation — Stage 8A.2.1

## Phạm vi và trạng thái an toàn

Stage 8A.2.1 bổ sung chiến lược Parallel Finishing headless trên nền CAM 3D
8A.1. Phạm vi được kiểm chứng là dao ball-end, ba trục cố định theo Setup WCS,
machining region gồm một hoặc nhiều mặt `PART`, đường chạy one-way hoặc zigzag
và linking retract/clearance bảo thủ. Đây chưa phải thuật toán production-ready
cho mọi topology và kết quả hợp lệ luôn có diagnostic
`parallel.foundation_limitation`.

Không có Production Function Editor, thay đổi icon, dependency, SQLite schema,
Post Processor hay Toolpath IR mới. SQLite tiếp tục dùng schema v4. Post hiện tại
không quảng bá strategy `parallel_finishing_3d`, vì vậy capability gate từ chối
NC thay vì sinh G-code giả.

## Kiến trúc

Pipeline không phụ thuộc Qt hoặc OCP runtime:

```text
OperationParameterSet + Cam3DCalculationContext 8A.1
  -> resolve/validate immutable inputs
  -> machining frame và selected-region bounds
  -> deterministic V pass positions
  -> mesh/plane intersection và clipping
  -> stitch, discretize, ball-center conversion
  -> pass/segment ordering
  -> conservative retract linking
  -> ToolpathBuilder / Toolpath IR candidate
  -> stale/cancel recheck
  -> atomic ToolpathArtifactStore publish
```

`cam/cam3d/parallel/models.py` giữ contract native-free;
`geometry.py` xử lý mesh chuẩn hóa của 8A.1 và nhận optional contact resolver;
`OcpParallelContactResolver` là infrastructure adapter project mesh candidate về
trimmed source face rồi lấy surface differential normal. `service.py` điều phối
lifecycle, IR và publish. `ui/parallel_finishing_worker.py` chỉ là `QRunnable`
có progress, cancel và abandon; widget không chứa thuật toán hay database I/O.

## Phiên bản thuật toán và database

- Parallel Finishing dùng **algorithm version 2**. Revision này khóa BRep contact
  projection, differential surface normal và ball-center offset theo normal nguồn.
- Strategy parameter payload vẫn ở version 1 vì schema tham số không thay đổi;
  algorithm version và serialization/format version là các khái niệm độc lập.
- SQLite engine được Python runtime cung cấp; lần QA phê duyệt dùng SQLite 3.50.4.
- Project database schema chính thức vẫn là **v4**, xác nhận bởi
  `DATABASE_SCHEMA_VERSION` và test `ProjectDatabase.current_schema_version()`.
  Các `format_version` hoặc `schema_version: 1` trong review/artifact JSON không
  phải project database schema. Stage này không migration và không schema bump.

## Parameters và persistence

Strategy key là `parallel_finishing_3d`, version 1. Parameter set chỉ chứa các
giá trị thật sự được dùng: zone ID, stepover, direction angle, one-way/zigzag,
retract-only linking, feed và maximum segment length. Selected faces, Setup WCS,
tolerance, allowance, safe motion và tool snapshot được dùng từ zone/context
8A.1; operation vẫn giữ face references và Tool Assembly reference hiện có.

Payload chỉ gồm primitive/UUID ổn định, có round-trip JSON và SQLite v4. Reader
v1 chấp nhận payload cũ chưa có `maximum_segment_length_mm` với default 2 mm.
OCP object, UI state, đường dẫn máy và cache mesh không được serialize.

## Machining frame và bounds

- `W` là fixed tool axis của zone.
- Hướng cơ sở của `U` là machining direction; nếu thiếu thì dùng Setup WCS X.
- Hướng được chiếu lên mặt phẳng vuông góc `W`, chuẩn hóa, rồi quay quanh `W`
  theo direction angle.
- `V = W × U`; `U × V` cùng hướng `W`. Vector zero, gần song song với `W`, sai
  tolerance hoặc frame không trực chuẩn đều bị từ chối.

Bounds U/V/W chỉ lấy vertex của các triangle thuộc selected `PART` faces, độc
lập thứ tự topology. U được nới bằng contact tolerance để intersection không
mất endpoint; V bám mép region; W được clip bởi optional height limits. Face
không còn trong mesh, bounds rỗng hoặc mesh thiếu đều trả diagnostic ổn định.

## Pass planning, intersection và discretization

Pass bắt đầu tại `v_min`, tăng đúng stepover và thêm `v_max` khi có phần dư;
không có pass trùng hoặc pass vô nghĩa ngoài bounds. Giới hạn là 20.000 pass.

Mỗi pass là mặt phẳng `V = constant`. Thuật toán cắt các triangle đã tessellate
của selected faces, giữ source-face association, clip theo W limits và boundary
closed-planar `INSIDE`, rồi stitch endpoint theo calculation epsilon. Nút bậc
lớn hơn hai bị từ chối như intersection phân nhánh/non-manifold; không tự chọn
một nhánh có thể sai.

Đây là intersection piecewise-linear trên calculation mesh, không phải section
exact của B-Rep. Sai số hình học phụ thuộc chordal/angular tolerance của mesh
8A.1; contact tolerance dùng cho so khớp/loại đoạn, còn maximum segment length
khống chế mật độ polyline. Endpoint được giữ, point trùng bị gộp. Guardrail là
25.000 point/curve và 100.000 point/result; kiểm tra cancel diễn ra mỗi pass và
định kỳ trong vòng lặp triangle.

OCP tessellator dùng `IMeshTools_Parameters`: Deflection/DeflectionInterior nhận
chordal tolerance, Angle/AngleInterior nhận angular tolerance, optional MinSize
nhận minimum triangle size; Relative và InParallel đều false để giữ semantics
absolute-MM và deterministic. ControlSurfaceDeflection/ForceFaceDeflection được
bật. Guardrail point-count được estimate trước allocation lớn.

## Contact, tool center và allowance

Preview giữ đồng thời `contact_point` và `tool_center_point`. Khi có original
BRep, mesh candidate được project về đúng trimmed source face; UV projection
được kiểm tra trong face, differential normal được normalize và đảo theo native
face orientation kết hợp selection orientation. Tool center bằng projected
surface contact cộng BRep normal nhân `tool_radius + part_normal_allowance`.
Mỗi sample ghi `normal_source=brep_surface` và projection deviation.

Khi không có resolver/source face, đường chạy vẫn có thể dùng facet normal nhưng
phải ghi `normal_source=mesh_facet`; artifact publish mang warning
`parallel.mesh_normal_approximation` và không được coi là đạt surface chordal
tolerance. Projection vượt chordal + contact tolerance fail bằng
`parallel.mesh_tolerance_violation`. Normal từ nhiều face chỉ được hợp nhất khi
nằm trong angular tolerance và cùng contact; cạnh sắc fail bằng
`parallel.contact_normal_discontinuity`, không average xuyên cạnh.

Chỉ `part_normal` allowance được hỗ trợ. Axial allowance, check-surface
clearance và boundary offset khác zero bị từ chối bằng
`parallel.unsupported_allowance`. Flat/end mill hoặc tool geometry khác bị từ
chối bằng `parallel.unsupported_tool_geometry` với thông điệp
`UNSUPPORTED_TOOL_GEOMETRY`.

Ngay cả BRep contact-normal path vẫn không phải offset-surface topology exact và
không giải quyết self-intersection, cusp-height adaptive stepover, undercut hoặc
toàn bộ tiếp xúc tool/shank/holder. Vì vậy kết quả không phải chứng nhận
gouge-free.

## Clipping, ordering và linking

Chỉ triangle từ selected `PART` faces được cắt nên pass không tự chạy sang mặt
không chọn. Closed planar boundary `INSIDE` được clip theo segment/polygon;
silhouette, `OUTSIDE` và `TOUCHING` bị từ chối. Nhiều face kề nhau được stitch
khi endpoint trùng tolerance; vùng rời giữ thành nhiều segment.

Pass sắp theo V tăng. Segment sắp theo U ổn định; one-way luôn chạy U tăng,
zigzag đảo hướng ở pass lẻ. Mỗi segment, kể cả segment rời trên cùng pass, dùng
chuỗi: rapid tại clearance, xuống retract, approach/feed tới start, cut, retract,
rồi trở lại clearance. Rapid ngang chỉ xảy ra tại clearance. Chưa có
collision-aware linking, lead-in/out production, holder avoidance hay smoothing;
Check/Fixture surfaces khác rỗng bị từ chối thay vì bị bỏ qua âm thầm.

## Toolpath IR, Simulation và Post

`ToolpathBuilder` hiện có tạo initial pose, marker, rapid/link/cutting/retract
moves, feed, Setup/tool fingerprints, provenance và engagement metadata. Artifact
ID/event ID và content fingerprint theo lifecycle deterministic hiện có. Kết quả
được Simulation sampler hiện tại đọc, phân loại motion và tính sample/bounds mà
không cần một IR song song.

Artifact chỉ publish sau khi candidate hoàn chỉnh, cancel checkpoint và
latest-wins token/fingerprint check đều đạt. Store ghi file staging, fsync,
replace, đọc lại checksum rồi mới trả metadata. Error hoặc cancel không trả
artifact/preview READY; lỗi store được chuẩn hóa thành
`parallel.artifact_generation_failure`.

## Progress, cancellation và determinism

Progress có tám phase: validation, frame/bounds, pass generation, intersection,
discretization, ordering/linking, IR build và finalization. Worker dùng cooperative
cancellation; `abandon()` chặn progress/result trễ sau project close và test xác
nhận thread pool về zero active thread.

Canonical mesh, UUID references, sorted endpoint/source ordering, epsilon grid,
explicit pass/segment indices và ToolpathBuilder deterministic IDs tạo cùng pass,
point order, IR events và hash cho cùng input. Locale, elapsed time, UI state và
unordered set/dict không tham gia representation ổn định.

## Validation có cấu trúc

Namespace `parallel.*` bao phủ invalid parameters, no geometry, missing face,
null shape, invalid/unsupported tool, zero direction, invalid workplane,
stepover/tolerance/clearance, empty bounds, no intersection, all passes empty,
unsupported boundary/allowance/protective geometry, cancellation, intersection
failure, size limit, stale result và artifact failure. Exception bất ngờ được
log kèm traceback tại service boundary và chuyển thành diagnostic; UI worker phát
signal `failed` thay vì làm crash UI.

## Fixtures, test và review artifacts

Unit fixtures native-free và OCP fixtures nhỏ bao phủ planar rectangle, inclined
plane, `curved_coarse_mesh`, `curved_brep_tolerance`, multi-face contiguous,
disconnected regions, empty/non-intersecting và invalid inputs. Test kiểm tra
analytic plane/cylinder normal, radius/offset direction, no flip, normal jump,
sharp-edge fail-closed, source association, mesh policy, domain round-trip,
frame/pass/clipping/discretization/guardrail, linking, deterministic IR/hash,
cancellation, worker close, atomic publish, Simulation và ProjectService/SQLite.

Chạy harness sau để tái tạo review JSON Git-ignored:

```powershell
.\.venv\Scripts\python.exe tests\manual_stage8a2_1_parallel_finishing.py
```

`reference_private/DERIVED/CAM_3D_8A2_1/REVIEW_INDEX.md` liệt kê package lần hai.
Package có fixture JSON, mesh/normal comparison, ba-run determinism report,
cancellation/atomicity report, unsupported cases, IR summary + first/last 30
events và 11 ảnh PNG geometry thật. Ảnh phân màu geometry/contact/ball-center/
normal/rapid/link/retract/direction và có orientation marker; đó là headless
review projection, không giả là production Viewer screenshot.

Audit fixture cũ xác định nguyên nhân jump: nửa trụ native-free chỉ có 5 hàng
vertex/8 triangle nhưng mang policy metadata 0,01 mm; facet normal tạo maximum
transverse normal jump 45,000000029° và tool-center jump 6,189037569 mm. Fixture
đã đổi tên `curved_coarse_mesh`. Fixture OCP BRep mới tại tolerance 0,01 mm có
30 triangle/81 contact points, maximum measured projection deviation
0,00460034 mm, facet-normal jump 4,915987183°, transverse BRep-normal jump
13,272368461° và tool-center jump 2,311290297 mm.

QA tại Stage 8A.2.1 ghi nhận 62 focused tests đạt; full suite tuần tự đạt
1346 passed, 2 deselected. Hai test deselected là benchmark QA bị cấu hình loại
trừ theo marker expression trong `pyproject.toml`, giống policy baseline.

## Tài liệu nội bộ đã tham khảo

HMS design trong stage này là toàn bộ contract, mesh-plane algorithm, error
policy, lifecycle, guardrail, persistence và test nói trên. Tài liệu riêng chỉ
được dùng để hiểu workflow và tên nhóm tham số:

- WorkNC training guide, trang PDF 126–132: workflow Parallel Finishing,
  machining zone, direction/angle, tolerance, stepover, cut/link groups.
- Hai gói `reference_private/MASTERCAM/INBOX/1.zip` và `2.zip`: kiểm tra chọn
  lọc các tài liệu scan để đối chiếu cách trình bày workflow/fixture hình học.

Không sao chép code, asset, icon, pixel UI hay nội dung dài. Các tài liệu này
không mô tả đủ thuật toán intersection, offset/contact proof, determinism,
atomic lifecycle, collision/gouge guarantee hoặc error bounds; các phần đó không
được suy diễn thành hành vi độc quyền của sản phẩm tham khảo.

## Giới hạn và stage tiếp theo

Chưa hỗ trợ exact B-Rep section/offset, Check/Fixture avoidance, holder/shank
collision, universal gouge check, rest/remachining, adaptive cusp height,
steep/shallow split, corner smoothing, 3+2/4/5-axis, production linking,
production Parallel editor hoặc production NC. Bề mặt vertical/undercut có thể
tạo contact hình học cho quả cầu nhưng không có chứng minh toàn bộ dao/holder an
toàn; người dùng không được coi artifact foundation là đường chạy sản xuất.

Stage sau chỉ nên bắt đầu sau review riêng: nâng geometry/contact kernel và
collision evidence trước, rồi mới thiết kế Production Function Editor và mở Post
capability khi có test/golden phù hợp. Stage 8A.2.1 không tự kích hoạt công việc đó.
