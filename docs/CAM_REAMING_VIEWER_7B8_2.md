# HMS CAM — Reaming Viewer và Recompute Integration 7B.8.2

## Phạm vi

Giai đoạn 7B.8.2 bổ sung presentation native-free, hiển thị OCP và tích hợp
recompute cho artifact `reaming_v1`. Phạm vi này không có Reaming UI, mô phỏng,
kiểm tra va chạm, Post Processor, G-code hay canned cycle G85/G86. SQLite và CAD/
XCAF kernel không thay đổi.

## Presentation semantic

`ToolpathPresentation.from_artifact()` nhận diện provenance `ream.*` là strategy
`reaming_v1`. Mỗi lỗ có năm nhóm chuyển động theo thứ tự:

1. `rapid`: tới clearance.
2. `reaming_approach`: rapid có phân loại LINK từ clearance tới retract height.
3. `reaming_descent`: tiến dao cắt tới final depth.
4. `controlled_retract`: rút bằng đúng feed/revolution tới retract height.
5. `final_retract`: chỉ rapid sau khi đã về retract height.

Các annotation native-free là `process_begin`, `spindle_begin`, `coolant_begin`
(nếu coolant được bật), `dwell` (nếu có), `hole_complete` và `process_end`. Vị
trí được suy ra tuần tự từ IR; các process-begin annotation ở retract height,
dwell ở final depth, hole-complete ở retract height và process-end ở clearance
height. `pass_count` và
`hole_count` chỉ đếm marker `ream.hole_complete`; dwell và marker process không
được tính là pass.

## Metadata

Marker process v1 chứa format/version và các giá trị cần để kiểm chứng artifact:
đơn vị, đường kính hoàn thiện, đường kính lỗ trước, stock mỗi phía, RPM,
feed/revolution, top Z, final depth, retract height, clearance height, dwell,
chiều quay, retract policy và coolant. Presentation công bố các giá trị này,
đồng thời tính `feed_per_minute = feed_per_revolution × RPM`; đây là giá trị dẫn
xuất, không phải nguồn tham số chỉnh sửa. Bounds, statistics, trạng thái artifact,
fingerprint và số pass vẫn lấy từ artifact bất biến.

## Kiểm chứng an toàn fail-closed

Biên chuyển đổi presentation kiểm tra metadata đồng nhất cho mọi begin/end,
phiên bản được hỗ trợ, chỉ số lỗ liên tục, loại và thứ tự đầy đủ của từng event,
đơn vị, feed, spindle/coolant lifecycle và hình học Z. Controlled retract bắt
buộc là `LinearMove`/`RETRACT` với feed/revolution; mọi `RapidMove` phải bắt đầu
tại hoặc phía trên retract height. Rapid từ final depth, thiếu marker, sai thứ tự,
sai feed hoặc metadata mâu thuẫn đều ném `ValueError` trước khi tạo native OCP.

## OCP và thay thế nguyên tử

OCP có màu riêng cho rapid, Reaming approach, cutting descent, controlled retract,
final rapid retract và bốn nhóm annotation. Candidate được chuyển đổi, tạo native,
tô màu và display hoàn chỉnh trước khi registry đổi sang candidate. Lỗi conversion
hoặc display giữ nguyên presentation cũ. Nếu xóa native cũ lỗi, registry/metadata,
visibility và native cũ được phục hồi; candidate bị loại bỏ. Registry phân tách theo
`OperationId`, vì vậy rollback/remove không ảnh hưởng operation khác.

## Recompute và lifecycle

Luồng hiện có `ProjectService.compute_reaming()` tiếp tục resolve, generate và
publish atomically. Viewer chỉ nhận artifact khi project generation, request mới
nhất, operation còn tồn tại/được bật, strategy và artifact fingerprint đều khớp.
Kết quả stale hoặc lỗi validation/publish giữ artifact và presentation hợp lệ cũ.
Show/hide được giữ qua replace. Remove, clear, project switch, CAD clear/reimport
và close dọn registry native/metadata để không còn orphan hoặc stale rebind.

## Kiểm tra

- `tests/unit/test_reaming_viewer.py`: semantics, metadata, native-free, pass count,
  safety, guards, OCP colors/groups, visibility, atomic rollback và lifecycle.
- `tests/unit/test_reaming_recompute.py`: recompute hiện hành, stale/validation,
  publish rollback và project-generation switch.
- `tests/manual_stage7b82_gui.py`: smoke thật trên Windows/OCP cho single/multi,
  dwell/feed retract, show/hide, replace, conversion rollback, project switch,
  resize, remove và close; fixture được dùng trực tiếp vì Reaming UI chưa thuộc
  phạm vi giai đoạn này.

## Giới hạn còn lại

Presentation chỉ hỗ trợ đúng `hms_reaming_process_v1` và retract policy
`controlled_feed`. Chưa có UI nhập/sửa Reaming, animation simulation, collision,
Post Processor hoặc G-code.
