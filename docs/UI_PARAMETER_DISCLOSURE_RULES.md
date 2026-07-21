# Quy tắc phân cấp tham số UI - Stage 9A.1

## 1. Ba mức hiển thị

### BASIC

- Gần như mọi operator đều phải kiểm tra hoặc quyết định cho operation.
- Hiển thị mặc định, mục tiêu khoảng 5-10 input.
- Có tác động trực tiếp và dễ hiểu tới geometry, tool, depth hoặc cutting intent.

### ADVANCED

- Ít dùng hơn, thường là override hoặc tinh chỉnh strategy.
- Nằm trong accordion/menu nhỏ, collapsed mặc định theo operation type.
- Không được dùng Advanced như nơi đẩy mọi field chưa phân loại.

### EXPERT

- Thuật toán, smoothing, filtering, precision, controller/post-specific hoặc
  option có rủi ro/cost khó thấy.
- Nằm sâu hơn Advanced, có mô tả ảnh hưởng và cảnh báo.
- Không tạo field Expert nếu domain contract hiện tại chưa có; UI không được
  phát minh tham số rồi làm người dùng tưởng engine đã hỗ trợ.

## 2. Quy tắc quyết định mức

Một field chỉ ở Basic khi trả lời "có" cho hầu hết các câu hỏi sau:

1. Operator phải quyết định field này cho đa số operation cùng loại?
2. Không thể lấy an toàn từ Geometry, Tool, Setup, Stock hoặc Machine?
3. Sai field gây thay đổi rõ ràng mà người dùng cần thấy trước khi Calculate?
4. Field có thể giải thích bằng một nhãn ngắn và unit rõ?

Field là Advanced nếu là override, mode ít dùng hoặc tuning công nghệ. Field là
Expert nếu liên quan precision/algorithm/controller, có trade-off chất lượng -
thời gian hoặc cần kiến thức chuyên sâu.

## 3. Quy tắc chung bắt buộc

- Một giá trị chỉ có một control chỉnh sửa trong một editor.
- Không hiện field không áp dụng; chỉ disable khi cần nhìn thấy dependency ngay.
- Không ép nhập dữ liệu đã có từ Tool/Setup/Stock/Geometry/Machine.
- Không tự đổi giá trị âm thầm khi một dependency thay đổi. Đưa suggested value
  và action `Áp dụng gợi ý` nếu cần.
- Default luôn có nguồn: `Tool`, `Setup`, `Stock`, `Geometry`, `Machine`,
  `Project` hoặc `HMS default vN`.
- Unit luôn nhìn thấy cạnh field; lưu/chuyển đổi theo contract hiện tại.
- Invalid draft không mutation domain, artifact hay database.
- Parse/dependency error inline; summary ở header và Diagnostics.
- Tooltip ngắn; help chi tiết mở riêng.
- Trạng thái collapsed có thể giữ theo operation type/strategy, không giữ theo
  operation instance.
- Đổi selection không đưa draft operation cũ sang operation mới.
- Raw ID, fingerprint, revision và provenance là read-only Details, không phải
  input người dùng.

## 4. Nguồn và override

| Nguồn | Cách trình bày | Cho override khi |
|---|---|---|
| Geometry | Giá trị derived + tên selection/zone. | Domain hiện có hỗ trợ và override không làm mất provenance. |
| Tool | Giá trị read-only kèm Tool Assembly. | Operation contract có field override rõ. |
| Setup/Stock | `Từ Setup/Stock` cạnh giá trị. | Operation thực sự cần khác biệt; có action trở về inherited. |
| Machine | Capability/limit read-only. | Không override trong operation nếu machine contract không cho phép. |
| Project | Unit, project/job identity read-only. | Không override ở function editor. |
| HMS default | Hiển thị version/key trong Details. | Người dùng chỉnh field thuộc contract hiện có. |

## 5. Bộ tham số đề xuất theo operation

Tên dưới đây là presentation proposal ánh xạ lên contract hiện có; không đổi
domain trong 9A.1.

| Operation | BASIC | ADVANCED | EXPERT | Tự lấy từ domain | Không còn cần người dùng nhập | Validation/dependency chính |
|---|---|---|---|---|---|---|
| **Facing** | Tool Assembly; Target Z; Stepdown; Stepover; Feed; Spindle RPM | Top Z override; allowance; cut direction; raster angle; overtravel; plunge feed; clearance/retract override | Chưa có trong contract hiện tại | Project unit; Setup/WCS; Stock bounds/top; Machine; tool diameter/holder | WCS X/Y/Z; kích thước A/B/C; Setup kind; Stock kind; Machine khi Setup đã chọn | Tool/machine compatible; Top > Target; Stepdown/Stepover/Feed/Spindle > 0; clearance >= retract > top; stepover hợp lệ theo tool |
| **Planar Face Facing** | Face selection; Tool Assembly; Target/offset; Stepdown; Stepover; Feed; Spindle RPM | allowance; direction; raster angle; overtravel; safe motion override | Chưa có trong contract hiện tại | Face plane/bounds; source/revision; Setup/WCS; Tool/Machine | Nhập lại Top/WCS/bounds đã resolve từ face; raw geometry key | Face phải planar/current; normal hợp WCS; offset/depth hợp lệ; tool reach và safe motion hợp lệ |
| **Contour** | Profile; Tool Assembly; Side; Final Depth; Stepdown; Feed; Spindle RPM | Top override; cut direction; multiple depth; finishing pass; radial/axial allowance; lead length; plunge; clearance/retract | Chưa có trong contract hiện tại | Profile plane/closed state; Setup/WCS; Tool diameter; Machine | WCS/Setup/Stock fields; Machine selector lặp; profile source khi đã bind | Profile resolved/current; side/direction tương thích; Top > Final; Stepdown > 0; lead phù hợp geometry; safe levels có thứ tự |
| **Pocket** | Boundary/Profile; Tool Assembly; Bottom Z; Stepdown; Stepover; Entry policy; Feed; Spindle RPM | Top override; cutting direction; radial/floor allowance; plunge; clearance/retract | Tolerance/precision (khi contract hiện có) | Pocket islands/bounds; Setup/WCS/Stock; Tool/Machine | WCS/stock dimensions; Machine selector lặp; geometry text đã derive | Boundary kín/resolved; Bottom < Top; step values > 0; tool lọt vùng pocket; entry policy khả thi; tolerance > 0 |
| **Drilling** | Hole/Pattern; Drill Tool; Depth; Cycle; Feed; Spindle RPM | Top override; clearance/retract; peck depth; dwell; retract policy | Geometry tolerance | Hole axes/diameters/count; Setup/WCS; Tool diameter; Machine capability | WCS; nhập từng tọa độ nếu đã có HolePattern; Machine selector lặp | Hole source current; axes phù hợp setup; depth/feed/spindle > 0; peck chỉ hiện và phải > 0 với peck cycle; dwell chỉ hiện khi cycle dùng |
| **Tapping** | Hole/Pattern; Tap Tool; Final Depth; Pitch; Spindle RPM; hand summary | Top/clearance/retract; synchronization mode; dwell | Geometry tolerance và controller-specific synchronization details | Diameter, pitch và hand từ Tap Tool khi có; feed = pitch x RPM; Setup/Machine capability | Nominal diameter/hand nhập lại khi Tool đã xác định; feed riêng gây lệch pitch | Tap/hole diameter và pitch tương thích; depth/safe order; RPM > 0; machine hỗ trợ mode; production Post hiện vẫn fail-closed |
| **Reaming** | Hole/Pattern; Reamer Tool; Final Depth; finished diameter summary; Feed/rev; Spindle RPM | Top/clearance/retract; pre-hole diameter; dwell; retract policy; coolant; spindle direction | Geometry tolerance | Reamer diameter; hole data; Setup/WCS; Machine; derived feed rate | Finished diameter nhập lại nếu bằng Tool; Machine/WCS fields | Pre-hole < finished diameter; reamer/tool match; depth và safe order; feed/rev/RPM > 0; tolerance > 0 |
| **Boring** | Hole/Pattern; Boring Tool; Final Depth; finished diameter; Feed/rev; Spindle RPM | Top/clearance/retract; pre-bore diameter; dwell; retract policy; coolant; spindle direction | Geometry tolerance và profile-specific boring constraints | Hole/pre-bore geometry; holder/profile compatibility; Setup/Machine | Machine/WCS; derived Boring bar details nhập tay; raw profile key | Pre-bore < finished diameter; boring profile/tool supported; reach/clearance hợp lệ; feed/rev/RPM > 0; tolerance > 0 |
| **Post** | Source operation; output filename; production profile summary; T/H/D binding; Work Offset; Simulation gate | Safe Z override; tool comment; global metadata; overwrite policy; external target | Cutter compensation/controller-specific option | Project/Job/Setup/Machine; ToolpathArtifact; Tool/Holder; unit; extension/CRLF từ profile; provenance | Raw identity/fingerprint; project/job/setup nhập lại; extension hoặc encoding trái profile | Source current; supported strategy/profile/machine; Simulation gate; T/H/D range/conflict; Safe Z; filename/path policy; Tapping fail-closed |
| **Program Assembly** | Explicit ordered operations; output filename; profile summary; Work Offset; Simulation gate | Global metadata; target/overwrite; per-operation T/H/D, Safe Z và tool comment | Cutter compensation và controller-specific section policy | Immutable source snapshots; Job/Setup/Machine compatibility; estimated lines; checksum/provenance | Row index làm identity; nhập lại operation metadata; tự group/tối ưu dao không được yêu cầu | Operation ID duy nhất; cùng Job/Setup/Machine/profile; source/current Simulation gate; binding conflict; explicit order; no unsupported Tapping |

## 6. Dependency và visibility mẫu

- `Drilling cycle != peck` -> ẩn Peck depth và Peck retract.
- `Finishing pass = false` -> ẩn finishing-only allowance/pass count.
- `Cutter compensation = disabled` -> ẩn D offset.
- `Geometry mode = whole stock` -> ẩn curve/surface selectors.
- `Use Setup safe motion = true` -> Safe Z read-only; tắt override controls.
- `Feed source = Tool` -> Feed read-only với source; `Override` mới mở input.
- `Simulation gate = require_pass` -> Generate disabled khi result không CURRENT
  PASS; lý do hiện ngay cạnh action.
- `External target` chỉ hiện khi người dùng chọn Export; Save Managed không hỏi
  filesystem path.

## 7. Bulk edit

- Chỉ cho field tồn tại và cùng ý nghĩa ở mọi selection.
- Mixed value dùng trạng thái `Nhiều giá trị`, không tự chọn giá trị đầu tiên.
- Field untouched không mutation.
- Trước Apply hiển thị số operation và field sẽ đổi.
- Apply là một command atomic hoặc rollback toàn bộ.
- Không bulk edit Geometry/Tool nếu selection không tương thích về strategy,
  Setup hoặc unit.

## 8. Kiểm tra khi triển khai ở 9A.4+

- Unit test cho applicability, level, source và dependency của field schema.
- Test invalid draft không mutation và đổi selection không rò draft.
- Keyboard test bảo đảm Tab bỏ qua field hidden/collapsed.
- Screenshot regression cho Basic/Advanced/Expert, validation và mixed state.
- GUI smoke ở 1366 x 768, 1920 x 1080; 100%, 125%, 150% DPI.
