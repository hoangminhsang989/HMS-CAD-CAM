# Simulation UI, Progress/Cancel và External Cache — Giai đoạn 7C.3

## Phạm vi

7C.3 bổ sung Simulation panel cho operation đang chọn trong CAM Workspace,
runtime state/progress/cancel, issue list và cache ngoài SQLite. SQLite vẫn ở
schema v4; Simulation model/codec v1 và Toolpath IR không đổi. Collision FAIL
do hình học là một kết quả hợp lệ, khác với runtime FAILED. Giai đoạn này không
có stock removal, animation tool solid, machine kinematics/IK, Post Processor
hoặc G-code.

## Luồng UI

Khi chọn operation, panel kiểm tra operation enabled/VALID, ToolpathArtifact
COMPLETE/current, BOX stock, tooling và provenance. Nếu kiểm tra không đạt,
Run bị khóa và chẩn đoán typed (`sim.source_stale`, `sim.tool_stale`,
`sim.unsupported_geometry`, ... ) được hiển thị. Panel hiển thị nguồn artifact
và fingerprint, stock/fixture/tool/holder/machine, sampling policy, run state,
PASS/WARN/FAIL, counts, overlay points/markers, elapsed UI-only và current/stale.

`Run Simulation` capture một `SimulationInputSnapshot` immutable rồi tạo scene.
`Cancel` là cooperative; `Show/Hide Overlay` chỉ tác động overlay, không ẩn
ToolpathArtifact. `Clear Result` xóa runtime result, overlay và cache entry của
operation theo yêu cầu UI, nhưng không xóa ToolpathArtifact. Đổi policy hợp lệ
đánh dấu kết quả cũ stale và xóa overlay; draft sai không làm mutation/dirty.
Reset policy dùng giá trị mặc định deterministic và panel giữ hard caps của
model (display points tối đa 1.000.000, markers tối đa 10.000).

Issue table lọc ALL/ERROR/WARNING/INFO, sắp theo thứ tự deterministic của
`SimulationResult`, và hiển thị severity/category, operation/result ID,
event/segment/sample, entity IDs, message/evidence. Chọn dòng chỉ lookup marker
metadata hiện hành và highlight native marker màu vàng; không tạo
`GeometryReference` hoặc thay CAD selection. Khi marker/result/project stale,
focus bị bỏ qua; `Clear selection` khôi phục màu marker.

## Runtime, ownership và progress

`SimulationRunController` là runtime-only registry theo project generation với
các state IDLE, VALIDATING, RUNNING, CANCELLING, COMPLETED, FAILED, STALE.
Record giữ request ID, project generation, operation ID/revision, source/input
fingerprints, progress, cancellation token và timestamp chỉ dành cho UI.
Callback luôn qua identity/generation guard; project switch/close, delete,
disable, artifact recompute hoặc đổi CAD state sẽ cancel/invalidate run cũ.

Snapshot pure-Python được capture trên application thread. Sampling và broad
phase dùng dữ liệu immutable. Native OCP fixture shape không được truyền qua
worker; adapter `ActiveOcpFixtureResolver` kiểm tra document/source/persistent
map/tree và chỉ resolve đúng một BODY/OCCURRENCE theo ownership contract.
Narrow phase OCP chạy trên owner/UI thread. Vì chưa có contract thread-safe cho
OCP, đường chạy 7C.3 dùng cooperative synchronous execution với các callback
progress và `QApplication.processEvents(..., 5)` ở bounded intervals. Không có
QObject/AIS trong runtime controller. Cách này giữ Cancel/project switch an
toàn; native call đơn lẻ vẫn chỉ có thể hủy sau khi call trả về.

Các phase progress là validating, resolving, sampling, broad_phase,
narrow_phase, building_result, publishing và rendering_overlay. Callback được
throttle theo thời gian nhưng luôn giữ mốc bắt đầu/kết thúc phase. Cancel không
publish partial candidate, không thay current result/overlay và callback muộn
bị bỏ. Publish runtime xảy ra trước khi viewer replace overlay; nếu render lỗi,
overlay cũ vẫn được giữ.

## External cache

Cache nằm trong project thật tại:

```text
<TEN_DU_AN>.HMS/cache/simulation/<operation-hash>/
    <cache-key>.result.json
    <cache-key>.metadata.json
```

`metadata.json` có format `HMS_SIMULATION_CACHE_ENTRY`, version 1, project ID,
operation/artifact/result IDs, source/input/result fingerprints, payload size,
SHA-256 và tên file payload. Cache key là SHA-256 của các identity này; không có
user text hay absolute path. JSON metadata và result payload đều canonical,
UTF-8. Ghi theo temp file + flush + `os.fsync` + atomic replace. Temp/incomplete
files và payload mồ côi được dọn trong maintenance pass.

Load kiểm tra format/version, filename, payload size/checksum và toàn bộ
provenance trước khi trả kết quả. Missing, stale, checksum mismatch, future
version hoặc malformed entry chỉ trở thành chẩn đoán cache; Open project không
thất bại toàn bộ và không auto-run. Cache không chứa OCP object, AIS, QObject,
token, callback hay runtime timestamp.

Giữ tối đa ba entry metadata gần nhất cho mỗi operation. Entry stale có thể
tồn tại để chẩn đoán nhưng không bao giờ được coi là current. Xóa operation
chỉ xóa thư mục hash nằm trực tiếp dưới `project/cache/simulation`; không xóa
ngoài project root.

## Save/Open, Save As và Recovery

- Save flush các runtime result hiện hành ra cache trước khi ghi project.
- Open chỉ discover/validate cache lazy khi operation được chọn; load cache
  không làm project dirty.
- Source artifact hoặc bất kỳ input fingerprint nào đổi sẽ làm cache stale;
  không auto-run.
- Save As copy entry hợp lệ sang cache project mới và re-key bằng project ID
  mới, bảo đảm project isolation và không giữ path cũ.
- Autosave ghi cache trong recovery workspace. Recovery chỉ copy entry hợp lệ
  từ snapshot đã chọn về project đích sau khi snapshot được restore; không trỏ
  nhầm cache project gốc. Temp không hoàn chỉnh bị bỏ qua.
- Cache write failure chỉ ghi log/chẩn đoán và không làm hỏng Save/project
  chính. `persist_simulation_result` từ result không còn current bị từ chối.

## Kiểm tra GUI thật

Chạy trên Windows desktop có Open CASCADE:

```powershell
.venv\Scripts\python.exe tests/manual_stage7c3_gui.py
```

Kịch bản tạo project có operation VALID, chạy PASS bằng owner-thread OCP scene,
kiểm tra progress, show/hide, policy invalid/Reset/Clear, Save/Open/Save As,
autosave, resize và dùng fixture deterministic để kiểm tra WARN/FAIL, issue
filter/focus. Khi kiểm tra thủ công đầy đủ, bổ sung Cancel trong sampling,
project switch/CAD reimport trong lúc run và đóng cửa sổ; mọi trường hợp phải
giữ overlay cũ và không để callback cũ chạm project mới.
