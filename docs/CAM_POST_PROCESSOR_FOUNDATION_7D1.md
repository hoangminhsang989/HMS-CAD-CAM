# Giai đoạn 7D.1 — Post Processor Foundation

## Phạm vi

7D.1 cung cấp nền tảng post processor thuần Python cho **một nguyên công**. Nguồn chuyển động duy nhất là `ToolpathArtifact` đã publish và còn current. Kết quả chỉ tồn tại trong bộ nhớ; giai đoạn này không ghi file NC, không thêm SQLite/schema, không nối UI/Viewer và không cung cấp controller post dùng cho sản xuất.

Các strategy v1 được nhận diện bằng khóa chính xác: `facing_2_5d`, `contour_2d`, `pocket_2_5d`, `drilling_v1`, `tapping_v1`, `reaming_v1`, `boring_v1`. Planar Face Facing dùng chung `facing_2_5d`.

## Luồng dữ liệu

`PostRequest` và `PostSourceSnapshot` được preflight trước khi lowering. Lowering sao chép từng motion đã publish sang `NCProgramIR`; không gọi lại generator, không dựng lại tham số nguyên công, không tối ưu, không canned cycle và không đổi arc thành đoạn thẳng. Program IR có begin/end, đơn vị, chế độ tọa độ tuyệt đối, mặt phẳng, WCS/work offset, đúng một tool activation và các record trạng thái/chuyển động có kiểu.

Arc giữ nguyên start/end/center/normal/signed sweep. Full circle, multi-turn, helical hoặc non-planar không được hạ cấp âm thầm. Feed giữ nguyên đơn vị theo phút hoặc theo vòng; inverse-time, UNKNOWN và số không hữu hạn bị từ chối. Tapping, reaming và boring chỉ nhận marker có schema/version/metadata/provenance hợp lệ; post không suy diễn chu trình từ tên marker hoặc operation parameters.

## Definition, adapter và kết quả

`PostProcessorDefinition` khai báo machine kind, axis, unit, feed, spindle, coolant, arc, strategy, work offset, tool activation cùng chính sách số/văn bản. Fingerprint dùng nội dung chuẩn hóa, bỏ display name và ID runtime. Adapter chỉ nhận request/Program IR và không được truy cập Qt, Viewer, OCP/CAD hay CAM generator.

`CanonicalDummyAdapter` tạo văn bản UTF-8 trung lập như `PROGRAM_BEGIN`, `UNITS MM`, `MODE ABSOLUTE`, `MOVE_RAPID` và `PROGRAM_END`. Đây là đầu ra kiểm thử xác định, không phải G-code, không được chứng nhận CNC và không được export trong 7D.1.

`PostResult` bất biến và versioned, chứa provenance/fingerprint của artifact, operation, setup/WCS, tool/holder/machine, simulation, Program IR; checksum và canonical text chỉ nằm trong bộ nhớ. Fingerprint input loại request token/UUID, project generation, timestamp, đường dẫn và trạng thái runtime.

## Safety và simulation gate

Preflight yêu cầu artifact `COMPLETE`, operation enabled/`VALID`, provenance hiện hành, setup/WCS và tool context nhất quán; machine requirement được so khớp nghiêm ngặt. Program và adapter validation kiểm tra cấu trúc begin/end, units/mode/plane/work offset/tool activation, feed/state balance, motion/arc, khả năng adapter và văn bản UTF-8/newline/độ dài/control character/controller syntax.

- `REQUIRE_PASS` (mặc định): chỉ simulation current `PASS` được phép.
- `ALLOW_WARN`: simulation current `PASS` hoặc `WARN` được phép.
- `OPTIONAL`: cho phép thiếu simulation và vẫn chặn `FAIL`; stale/malformed không được coi là pass.

Simulation `PASS` là một gate đã đạt, không phải xác nhận tuyệt đối rằng chương trình an toàn để chạy máy.

## Runtime lifecycle

`PostRuntimeService` giữ tối đa một current result cho khóa `(ProjectId, OperationId, ToolpathArtifactId, PostProcessorDefinitionId)`. Publish dùng token, generation và full current-input fingerprint theo latest-wins; kết quả cũ được giữ khi lần chạy mới thất bại trên input không đổi. Registry bị xóa khi project load/close và bị invalidated khi snapshot/artifact/operation thay đổi.

## Chưa thực hiện

Multi-operation program, sắp lịch nguyên công, production controller post, đánh số tool, file export/persistence, thư mục `post`/`nc`, UI, Setup Sheet, stock removal, animation và machine kinematics thuộc các giai đoạn sau và không nằm trong 7D.1.
