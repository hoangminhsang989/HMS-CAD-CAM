# Simulation Viewer Integration — Giai đoạn 7C.2

7C.2 hiển thị một `SimulationResult` đã được `SimulationRuntimeService` publish
trong viewport hiện tại. Viewer không chạy simulation, không ghi SQLite và không
thay đổi `SimulationResult`, Toolpath IR hoặc CAD/XCAF document.

## Presentation model

`hms_cadcam.viewer.simulation` là lớp native-free. `SimulationPresentationKey`
định danh bằng `ProjectId + OperationId + SimulationResultId`; metadata còn giữ
artifact/input/result fingerprints, operation revision, project generation,
PASS/WARN/FAIL, statistics và `SimulationIssueEvidenceSummary`.

`SimulationPathSegment` giữ `segment_index`, event provenance, sample indices và
world points. Semantic là `rapid`, `cutting`, `link`, `retract` hoặc `approach`.
Dwell/marker event không tạo motion geometry. Các segment không bao giờ được
nối ngầm; junction trùng được giữ trong provenance của cả hai segment.

Viewer tái lấy mẫu deterministic bằng `SimulationSamplingPolicy` của result,
sau đó kiểm tra sample/segment count và bounds khớp statistics. Vì result v1
không chứa duplicate sampled path, cách này không thay đổi public result/codec.

## Validation and stale guards

Trước khi tạo native object, builder kiểm tra strict codec round-trip, model
version, result fingerprint, operation/artifact/input/result fingerprints,
operation tồn tại/enabled, operation revision, COMPLETE artifact, WCS fingerprint
và unit. Issue phải thuộc cùng operation/artifact, có index/bounds/point hợp lệ
và evidence deterministic. Mọi mismatch bị reject fail-closed.

`SimulationPresentationRegistry` được bind theo project generation. Request cũ,
project cũ, operation đã xóa/disable, artifact/source đổi hoặc result không còn
current đều không thể commit. Một operation chỉ có một current presentation;
replacement giữ visibility cũ khi thành công.

## Path/marker display policy

`SimulationDisplayPolicy` có cap riêng cho path points và markers; không hạ
resolution của simulation. Decimation deterministic, giữ endpoints mỗi segment,
semantic transitions và sample cùng/lân cận issue. Khi số mandatory points vượt
cap, cap là giới hạn mục tiêu và presentation ghi `path_cap_overflow=True` để
không làm mất evidence. Metadata luôn ghi displayed/total counts.

Marker ID là fingerprint deterministic của issue identity/evidence, không chứa
localized text. Marker giữ category, severity, code, operation/result/artifact
IDs, segment/event/sample indices, world point hoặc bounds, entity IDs và evidence
fingerprint. Marker cap ưu tiên toàn bộ ERROR trước WARNING/INFO; issue summary
vẫn phản ánh toàn bộ result. Bounds-only marker có anchor là tâm bounds để render;
issue không có point/bounds vẫn tồn tại ở metadata nhưng không tạo geometry.

## OCP backend and atomic replacement

`OcpCadViewportBackend` tạo path compound theo semantic, status vertex màu
PASS/WARN/FAIL và marker vertex/bounds. Overlay được deactivate khỏi CAD topology
selection; source toolpath và simulation overlay có dictionary/visibility độc lập.

Thứ tự thay thế là validate → build native candidate → display candidate → commit
registry → remove native cũ. Conversion, display, swap hoặc remove lỗi đều phục
hồi metadata/native/visibility cũ; candidate được discard và operation khác/source
toolpath không bị ảnh hưởng. Remove và clear cũng rollback khi `AIS` remove lỗi.

`lookup_simulation_issue(...)` và `lookup_native_simulation_marker(...)` là
selection metadata foundation: trả `SimulationIssueMarker`, không trả
`GeometryReference`, không active-select CAD và không resolve marker của project
hoặc result stale. Panel/click interaction đầy đủ được để cho 7C.3 nếu cần UI
contract lớn hơn.

## Lifecycle

`display_document`, `clear`, `close`, project switch và CAD clear đều cleanup
overlay native/registry. `bind_simulation_project(None, None)` đóng project;
project mới phải bind lại generation trước khi display. Remove operation đi qua
backend remove toolpath và simulation tương ứng. Source toolpath visibility không
đổi simulation visibility. Source artifact thay đổi làm overlay fingerprint cũ
không còn current và backend loại bỏ overlay sau khi source replacement thành
công; replacement thất bại giữ state cũ.

Visibility chỉ là runtime state trong 7C.2, không persistence SQLite.

## Giới hạn của 7C.2

Chưa có animation/tool solid motion, stock removal mesh, machine kinematics/IK,
progress/cancel UI, external simulation cache, Post Processor hoặc G-code.
