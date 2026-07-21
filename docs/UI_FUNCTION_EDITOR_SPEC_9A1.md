# Đặc tả Unified Function Editor - Stage 9A.1

## 1. Mục tiêu

Unified Function Editor là khung presentation chung cho Setup, operation,
Simulation, Post và Program Assembly. Stage 9A.1 chỉ chốt UX contract; không
thay đổi CAM domain, Toolpath IR, SQLite hoặc production widget.

Editor phải đạt ba mục tiêu:

1. Chỉ hiện input có ý nghĩa với selection hiện tại.
2. Giữ 5-10 input cốt lõi trong Basic; phần ít dùng mở dần.
3. Draft, validation, calculation và Apply có lifecycle nhất quán.

## 2. Cấu trúc bắt buộc

### A. Header summary - luôn nhìn thấy

- Tên operation, strategy và semantic status.
- Tool Assembly, geometry summary và nguồn Setup/Stock.
- Validation summary: số error/warning và việc cần làm tiếp theo.
- Dirty/stale/current badge có text; không chỉ dùng màu.
- Action nhỏ: Rename và Help; không đặt tham số chi tiết ở header.

### B. Basic - mở mặc định

- Khoảng 5-10 input mà gần như mọi người dùng strategy đó phải quyết định.
- Thứ tự theo workflow: Geometry/Tool reference, cutting intent, depth/level,
  feed/speed; không theo thứ tự field trong dataclass.
- Giá trị kế thừa hiển thị read-only summary hoặc `Dùng giá trị Setup/Tool`.

### C. Geometry

- Selection summary theo domain ID, loại hình học, source và trạng thái resolved.
- Action Select/Rebind/Clear/Focus; pick thực hiện trong viewport.
- Không hiển thị raw persistent key ở luồng cơ bản.
- Machining Zone chọn mode trước, chỉ hiện control đúng mode.

### D. Tool

- Chọn Tool Assembly từ tài nguyên project.
- Hiển thị diameter, corner radius, holder, stick-out và compatibility read-only.
- Tạo/sửa Tool Definition mở panel tài nguyên riêng; operation không nhân bản
  dữ liệu Tool Library.

### E. Cutting

- Feed, spindle, direction, stepover/stepdown và allowance có áp dụng.
- Derived feed/pitch/rate ghi rõ công thức và nguồn, không tự đổi thầm lặng.
- Field phụ thuộc strategy chỉ xuất hiện khi strategy/mode tương ứng được chọn.

### F. Levels / Depths

- Top, bottom/final depth, clearance, retract và level policy.
- Ưu tiên lấy Top/Stock/face/hole depth từ geometry/Setup; cho override explicit.
- Sơ đồ nhỏ hoặc viewport overlay dùng semantic label, không dùng asset nguồn.

### G. Linking / Safe Motion

- Approach, retract, lead-in/out, entry và safe transition policy.
- Giá trị Setup safe motion hiển thị nguồn; override phải có nhãn và validation.
- Không hiện option không áp dụng; không chỉ disable một danh sách dài.

### H. Advanced - collapsed mặc định

- Tham số ít dùng nhưng vẫn thuộc intent người vận hành.
- Bên trong có nhóm Expert collapsed sâu hơn cho precision, smoothing,
  filtering, thuật toán hoặc post-specific option.
- Preference collapse lưu theo `editor_type + strategy_key`, không theo
  operation ID và không lưu draft value.

### I. Diagnostics / Preview

- Dòng summary luôn có; bảng/detail chỉ mở khi cần.
- Error gắn inline vào field; diagnostic tổng hợp có code, message, evidence và
  action Focus/Copy details.
- Preview là transient, ghi rõ nguồn draft hay applied state và CURRENT/STALE.

## 3. Footer action - luôn nhìn thấy

Thứ tự chuẩn:

```text
Reset Draft | Preview | Validate | Calculate | Apply | Close
```

- **Reset Draft**: trả draft về applied state/default có nguồn; không mutation.
- **Preview**: tạo preview transient nếu strategy hỗ trợ; không ghi artifact.
- **Validate**: chạy validation rõ ràng, focus error đầu tiên.
- **Calculate**: gửi immutable request cho worker; không tự Apply hay Export.
- **Apply**: commit atomic draft hợp lệ qua application service.
- **Close**: đóng editor; nếu dirty dùng policy Save/Discard/Cancel nhất quán.

Chỉ hiện action có nghĩa với function hiện tại. Ví dụ Setup không có Calculate;
Post dùng Generate thay cho Calculate nhưng vẫn giữ vị trí và lifecycle tương
đương; read-only NC Artifact không có Apply.

## 4. Ma trận action theo trạng thái

| Trạng thái | Reset | Preview | Validate | Calculate/Generate | Apply | Gợi ý UI |
|---|---:|---:|---:|---:|---:|---|
| CLEAN + valid | Ẩn/khóa | Nếu hỗ trợ | Bật | Khi input đầy đủ | Ẩn/khóa | `Không có thay đổi` |
| DIRTY + valid | Bật | Nếu hỗ trợ | Bật | Bật nếu calculation dùng draft | Bật | `Có thay đổi chưa áp dụng` |
| DIRTY + invalid | Bật | Khóa | Bật | Khóa | Khóa | Nêu lỗi đầu tiên và tổng số lỗi |
| CALCULATING | Khóa | Khóa | Khóa | Hiện Cancel nếu hỗ trợ | Khóa | Progress + phase; không spinner vô hạn |
| RESULT CURRENT | Bật nếu dirty | Bật | Bật | Recalculate khi dirty | Theo draft | Hiển thị checksum/fingerprint trong Details |
| RESULT STALE | Bật | Bật nếu draft hợp lệ | Bật | Bật | Theo draft | Nêu nguyên nhân stale và action tiếp theo |
| BLOCKED/FAILED | Bật | Theo khả năng | Bật | Khóa tới khi sửa | Theo draft | Diagnostic có action focus, không chỉ code kỹ thuật |

Nút disabled phải có lý do đọc được bằng tooltip/accessibility text hoặc dòng
validation; màu xám đơn thuần không đủ.

## 5. Draft và mutation contract

- Mỗi selection có một draft object presentation riêng, keyed bằng domain ID và
  project generation.
- UI parse và validate field; invalid draft không gọi mutation command.
- Apply gửi một command/snapshot duy nhất; thành công toàn bộ hoặc rollback.
- Calculate nhận immutable draft snapshot và request token; kết quả chỉ publish
  nếu project generation, operation ID và fingerprint còn current.
- Đổi operation không giữ draft cũ trong widget. Nếu policy cho phép cache draft,
  cache keyed bằng `ProjectId + OperationId + generation` và phải xóa khi switch
  project/close.
- Không tự thay giá trị người dùng sau validation. Suggested fix là action rõ.

## 6. Field presentation contract

Mỗi field cần metadata presentation, không thay domain contract:

| Thuộc tính | Ý nghĩa |
|---|---|
| `field_key` | Key ổn định trong editor type; không phải row index. |
| `label_vi` | Nhãn tiếng Việt chuẩn; thuật ngữ Anh có thể ở tooltip/help. |
| `level` | BASIC, ADVANCED hoặc EXPERT. |
| `unit` | Unit luôn nhìn thấy cạnh control; không chỉ ở placeholder. |
| `source` | USER, SETUP, STOCK, TOOL, MACHINE, GEOMETRY hoặc DEFAULT. |
| `applicability` | Predicate presentation theo strategy/mode/capability. |
| `dependency` | Field/value ảnh hưởng validation hoặc visibility. |
| `help_key` | Mở help riêng, không nhồi đoạn văn dài vào form. |
| `diagnostic_target` | Cho phép focus field từ diagnostic code. |

Default hiển thị nguồn, ví dụ `5.0 mm - từ Setup`, `500 mm/min - từ Tool`.
Nếu người dùng override, UI phải có action `Dùng lại giá trị kế thừa`.

## 7. Validation và diagnostics

- Parse error: inline ngay dưới field, giữ nguyên text người dùng.
- Field dependency: lỗi ở cả field nguồn và field phụ thuộc khi cần.
- Geometry/tool/machine compatibility: summary ở section liên quan và một bản
  ghi tổng hợp trong Diagnostics.
- Error chặn Calculate/Apply; Warning không tự động chặn trừ policy domain hiện
  có; Info không dùng màu cảnh báo.
- Technical code/fingerprint vẫn copy được trong Details để hỗ trợ vận hành.
- Không dùng `except Exception: pass`; lỗi I/O/service được chuyển thành
  diagnostic có log đầy đủ.

## 8. Keyboard, accessibility và DPI

- Tab order: header action -> Basic -> section đang mở -> footer; bỏ qua section
  collapsed, field hidden và action không áp dụng.
- `Alt` mnemonic không trùng trong cùng editor; Enter không tự Calculate/Apply.
- Esc đóng popover trước, sau đó Close editor theo dirty policy.
- Mọi control có accessible name, unit, error relationship và focus indicator.
- Header/footer sticky; content giữa có scroll. Không có horizontal scroll ở
  360 px trừ bảng dữ liệu thực sự không thể reflow.
- Test ở 100%, 125%, 150%, 200% DPI và keyboard-only.

## 9. Biến thể function

| Function | Section chính | Action khác biệt |
|---|---|---|
| Operation 2D/3D | A-I đầy đủ | Preview, Validate, Calculate, Apply |
| Setup/Stock | Header, Basic, Geometry, Advanced, Diagnostics | Validate, Apply; không Calculate |
| Simulation | Header, Basic policy, Geometry scene, Advanced sampling, Diagnostics | Run/Cancel, Clear Result; không Apply domain operation |
| Post | Header, Basic output, Tool binding, Advanced profile/export, Diagnostics | Validate, Generate, Preview, Save/Export explicit |
| Program Assembly | Header, ordered operations, Basic context, per-section binding, Advanced, Diagnostics | Validate, Generate, Preview, Save/Export explicit |
| NC Artifact | Header, read-only metadata/preview, Diagnostics | Copy, Export explicit, Close |

## 10. Ngoài phạm vi 9A.1

- Không viết component/framework PySide6 production.
- Không đổi operation/domain parameter hoặc persistence schema.
- Không triển khai Parallel Finishing hay CAM 3D algorithm.
- Không thay production workflow Post/Assembly trong stage này.
