# CAM Boring Viewer 7B.9.2

## Phạm vi

Giai đoạn này chuyển `ToolpathArtifact` của `boring_v1` thành presentation model
thuần Python và hiển thị nó qua OCP backend hiện có. Operation tiếp tục thuộc
`OperationFamily.DRILLING`; không có Boring UI, thay đổi CAD/XCAF kernel,
migration SQLite, controller cycle, Post Processor, G-code, animation hay
collision simulation đầy đủ.

## Nhận diện strategy và provenance

Converter chỉ nhận Boring khi event provenance đồng nhất với tiền tố `bore`,
process metadata khai báo chính xác `boring_v1` phiên bản 1 và operation family
là `drilling`. Bốn state event đầu phải là feed-per-revolution, spindle off,
coolant off và tool context hiện hành. Mọi event còn lại phải thuộc đúng một
`bore.hole.<index>.*`; provenance strategy trộn, prefix lạ, source operation
khác hoặc canonical hole order sai đều bị từ chối.

Process begin/end của mọi lỗ phải có payload giống nhau. Payload neo BORING_BAR
vào artifact bằng tool-context fingerprint, đồng thời xác nhận family,
`BoringBarGeometry` version 1, minimum/maximum bore, ID và expected/current
fingerprint của tool, assembly và holder. Viewer chỉ kiểm tra tính nhất quán của
provenance đã publish; nó không lặp lại toàn bộ accessibility, holder-clearance
hay machine validation của application service.

Trước conversion, artifact được round-trip qua codec hiện hành để xác minh
artifact fingerprint. Presentation không chứa native OCP/PySide6 object,
runtime CAD ID, `GeometryReference`, controller command hay G/M-code.

## Presentation semantic và metadata

Các segment được phân biệt thành:

- rapid tới clearance;
- `boring_approach` tới retract plane;
- `boring_descent` có kiểm soát tới final depth;
- `controlled_retract` từ đáy lên retract height;
- `final_retract` rapid từ retract height lên clearance.

Annotation native-free được tạo tại vị trí event stream cho process begin,
spindle/coolant begin, dwell, hole complete và process end. Annotation không
tham gia CAD/XCAF selection và không trở thành geometry reference.

`pass_count` là đúng số marker `bore.hole_complete`; process marker, dwell và
retract không tăng count. Metadata presentation gồm strategy/version/family,
hole/pass count, finished/pre-bore diameter, radial stock, RPM, feed/revolution,
feed/minute dẫn xuất, top/final depth, clearance/retract height, dwell,
spindle/coolant/retract policy, BORING_BAR family/version/min-max reach,
artifact status, bounds và statistics.

## Controlled retract safety

Mỗi lỗ phải có sequence đầy đủ và duy nhất: rapid, approach, process begin,
spindle/coolant begin, linear cutting descent, optional dwell, linear controlled
retract, hole complete, final rapid retract, coolant/spindle off và process end.
Descent phải kết thúc tại final depth; controlled retract phải bắt đầu tại đáy
và kết thúc tại retract height với cùng feed/revolution. Rapid không được bắt
đầu hoặc kết thúc dưới retract height, và rapid có dịch chuyển ngang phải ở
clearance height. Marker thiếu/trùng/sai thứ tự hoặc process state bị treo làm
candidate fail-closed.

## Atomic replacement và stale guards

OCP backend convert/validate toàn bộ candidate trước, tạo và display toàn bộ
candidate native objects, rồi mới đổi registry và remove presentation cũ. Lỗi
conversion, display hoặc remove kích hoạt rollback, giữ metadata, visibility và
native presentation cũ; operation khác không bị ảnh hưởng.

Registry chỉ thay presentation khi project generation, display request,
operation existence/enabled, strategy key/version/family, operation revision,
computation token, input fingerprint và artifact fingerprint đều hiện hành.
Application service vẫn là nơi quyết định publish: geometry/WCS/parameters,
tool/assembly/holder, machine hoặc revision đổi làm fingerprint đổi; generation,
store hoặc stale-token failure giữ artifact `VALID` và presentation cũ.

## Lifecycle và giới hạn

Registry theo `OperationId`/`ArtifactId`, giữ visibility khi replace, hỗ trợ
show/hide/select/remove và xóa toàn bộ khi New/Open/project switch/close. OCP
clear hoặc CAD reimport xóa toolpath presentation độc lập với CAD selection;
không tự rebind Boring geometry và callback generation cũ không thể truy cập
project mới. Runtime token, QObject và native CAD/OCP object không được lưu vào
presentation hay project.

Phần hiển thị chỉ mô tả semantic toolpath. Nó không dựng boring bar/head, stock
removal, holder/fixture collision, machine kinematics hoặc simulation.
