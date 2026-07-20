# Giai đoạn 7D.2.1 — FANUC ROBODRILL 21i Production Adapter

## Phạm vi

7D.2.1 bổ sung production adapter một nguyên công cho máy phay ba trục FANUC
ROBODRILL 21i theo cấu trúc WorkNC đã được xác nhận. Profile có khóa
`robodrill_fanuc_21i_worknc_expanded_v1`; adapter có khóa
`fanuc_robodrill_21i_worknc_v1`.

Đầu ra vẫn chỉ là `canonical_text` trong `PostResult`. Giai đoạn này không ghi
file, không truyền data server, không tạo UI/export, không đổi SQLite và không
ghép nhiều nguyên công.

## Hợp đồng production

`ProductionControllerProfile` là cấu hình immutable, có version, strict codec
và content fingerprint. Fingerprint loại `profile_id` và `display_name`, nhưng
bao gồm toàn bộ chính sách có ảnh hưởng đến NC: controller/machine, axis, unit,
feed, spindle, coolant, arc, work offset, tool activation, cutter compensation,
numeric format, comment, encoding, newline, giới hạn kích thước và safe
sequence.

`ControllerToolBinding` ánh xạ fingerprint của `ToolAssembly` sang station
`Tn`, length offset `Hn` và diameter offset `Dn`. `ProductionProgramContext`
giữ tên `.fn`, safe Z, binding, bán kính dao, lượng dư, chiều sâu và lựa chọn
G41 legacy rõ ràng. Hai đối tượng đều có strict codec và fingerprint riêng;
binding sai hoặc stale bị chặn trước khi format.

## Quy tắc `.fn`

- UTF-8, CRLF, đuôi `.fn`, không O-number và không N-number.
- MM only; không phát `G20`, `G21`, `G94` hoặc `G95`.
- Absolute, plane XY và work offset duy nhất `G54`.
- Tool activation `M06Tn`, length compensation `G43...Hn`.
- `G41Dn` chỉ xuất khi context chọn explicit legacy WorkNC policy; luôn cân bằng
  bằng `G40` trước footer.
- Linear/rapid được expanded thành `G01`/`G00`; arc là `G02`/`G03` với `I/J`
  incremental từ điểm bắt đầu. Arc lớn hơn 180 độ được giữ nguyên; full circle,
  multi-turn, helical và non-XY bị chặn.
- Coolant flood dùng `M08/M09`; spindle CW/CCW/stop dùng `M03/M04/M05`.
- Dwell hiện `UNSUPPORTED`. Tapping tay phải và tay trái đều fail-closed; adapter
  không tự suy diễn canned cycle hoặc synchronous feed.

Safe start cố định gồm delimiter, comment WorkNC, modal cancel, machine Z
reference, tool change, `G54`, length compensation, cutter policy và process
state. Safe end cố định gồm cutter cancel khi cần, `M09`, `M05`, Z/Y reference,
`M30` và delimiter cuối.

## Validation và runtime

Lowering production yêu cầu machine snapshot FANUC, model chứa `ROBODRILL`, loại
MILL, unit MM và đúng ba linear axis X/Y/Z. Production validator kiểm tra profile,
context, strategy, G54, safe Z, tool binding, process state, feed/spindle/coolant,
numeric rounding, arc geometry và cấu trúc output. Mọi mismatch đều fail-closed.

`PostRuntimeService` tự chọn adapter theo khóa đã đăng ký. `PostResult` production
ghi lại profile ID/version/fingerprint, tool-binding fingerprint, program-context
fingerprint, unit và các feed mode đã validation. Nhánh `canonical_dummy` 7D.1
giữ nguyên codec/fingerprint cũ khi không có production profile/context.

## Golden contract và giới hạn

Golden suite bao phủ facing, planar-face facing, contour line, G02, G03, arc lớn
hơn 180 độ, pocket, drilling expanded, peck expanded, reaming và boring
controlled retract. Mỗi `.fn` được so sánh byte-for-byte và SHA-256; CRLF cũng
được kiểm tra như một phần của contract.

Adapter này chưa phải chứng nhận máy hoặc xác nhận an toàn gia công. Cần review
NC, dry-run/single-block và quy trình xác nhận tại máy thật trước sử dụng sản
xuất. Chưa có export, truyền file/data server, UI, multi-operation, tapping
production, stock removal, machine kinematics, canned cycle hoặc Setup Sheet.
