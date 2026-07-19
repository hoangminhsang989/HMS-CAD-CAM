# CAM Tapping Viewer và Recompute Integration 7B.7.2

## Phạm vi

Giai đoạn này nối `ToolpathArtifact` của `tapping_v1` với presentation native-free,
OCP viewport và contract recompute/publish hiện có. Không có Tapping UI, mô phỏng
dao/spindle, thay đổi CAD/XCAF, migration SQLite, Post Processor hoặc G-code.

## Presentation native-free

`ToolpathPresentation` nhận diện Tapping bằng provenance `tap.*` và công bố:

- hand `RIGHT_HAND_TAP` hoặc `LEFT_HAND_TAP`;
- mode `RIGID` hoặc `FLOATING`;
- số lỗ và `pass_count`;
- đường kính danh nghĩa, pitch dương, RPM và chiều sâu ren;
- trạng thái artifact, bounds và statistics.

`pass_count` chỉ đếm marker `tap.hole_complete`. Spindle reversal, dwell và marker
đồng bộ không làm tăng số pass. Presentation chỉ chứa dataclass/enum/value object
Python; không chứa QObject, OCP handle, CAD runtime ID hay controller command.

Các motion semantic là `rapid`, `approach`, `synchronized_descent`,
`synchronized_retract` và `final_retract`. Annotation native-free gồm
`synchronization_begin`, `dwell`, `spindle_reversal`, `hole_complete` và
`synchronization_end`; tọa độ được suy ra tuần tự từ event stream.

Metadata đồng bộ dùng payload versioned `hms_tapping_sync_v1`, version `1`.
Payload ghi pitch/unit, hand, mode, đường kính, RPM và chiều sâu ren. Reader vẫn
đọc được artifact 7B.7.1 thiếu các trường metadata mở rộng bằng cách suy ra hand
và RPM từ spindle event. Pitch vẫn dương và feed-per-revolution vẫn do Toolpath IR
quản lý; không lưu linear feed bổ sung hoặc cú pháp máy điều khiển.

## OCP và thay thế atomic

OCP gom riêng từng motion/annotation semantic thành compound có màu riêng. Các
compound CAM nằm ngoài registry và selection của CAD/XCAF, được quản lý theo
`OperationId`; visibility của operation được giữ qua recompute.

Khi thay artifact, backend tạo, kiểm tra và hiển thị đầy đủ candidate, swap
registry/metadata, rồi mới remove presentation cũ. Lỗi conversion, display,
remove hoặc viewer update sẽ phục hồi registry và metadata cũ, dọn candidate và
hiển thị lại presentation cũ. Operation khác không bị tác động.

## Recompute và lifecycle

Viewer registry chỉ nhận callback khớp project generation, request sequence,
operation tồn tại/enabled, strategy và artifact fingerprint mong đợi. Vì vậy
callback cũ sau thay đổi parameter, tool, machine, WCS hoặc project không thể
thay presentation hiện hành. Chỉ artifact đã publish thành công mới được chuyển
sang viewer.

Nếu generation/publish/artifact store lỗi, `compute_tapping()` giữ artifact
`VALID` trước đó và caller giữ presentation cũ. Remove operation chỉ dọn đúng
operation; clear CAD, đổi project, New/Open và close dọn toàn bộ CAM presentation.
Presentation không giữ computation token, callback hoặc native object sau clear.

## Kiểm tra

Test tự động bao phủ RH/LH, rigid/floating, multi-hole, annotation/order,
pass-count, metadata controller-neutral, hide/show/replace, operation isolation,
rollback conversion/display/remove, stale callback/generation/fingerprint,
compute thành công, compute lỗi và artifact-store lỗi. Smoke GUI Windows dùng OCP
thật kiểm tra RH/LH nhiều lỗ, hide/show, replace, project switch, resize và close.
