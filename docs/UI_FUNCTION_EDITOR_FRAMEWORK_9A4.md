# Unified Function Editor Framework — Stage 9A.4

## 1. Phạm vi đã triển khai

Stage 9A.4 tạo framework presentation PySide6 dùng chung cho Function Editor,
một Contour reference editor an toàn và adapter cho editor production cũ. Stage
này không migrate Facing/Contour/Pocket, không đổi CAM generator, Toolpath IR,
SQLite schema, Simulation/Post semantic và không triển khai Parallel Finishing
hoặc Lathe.

Reference editor có nhãn `REFERENCE DEMO`, dùng dữ liệu presentation test và
Apply chỉ cập nhật snapshot trong bộ nhớ của framework. Nó không được chọn làm
editor production mặc định và không tự Calculate, Simulation, Post hoặc Export.

## 2. Kiến trúc

Package `hms_cadcam.ui.function_editor` được chia theo trách nhiệm:

| Module | Trách nhiệm |
|---|---|
| `model.py` | Enum, field/section/summary/footer metadata, diagnostic và preview request thuần Python. |
| `schema.py` | Schema typed, kiểm tra ID/dependency, ordering xác định và registry theo strategy key. |
| `state.py` | Draft/applied snapshot, validation, reset/default, stale guard và QSettings user-only. |
| `fields.py` | Field row, unit, source/default, inline diagnostic, help, reset và responsive layout. |
| `sections.py` | Accordion section, summary, badge lỗi/cảnh báo, help và expansion state. |
| `widgets.py` | Header summary, disclosure bar, scroll content, diagnostics/help và fixed footer. |
| `host.py` | Right-dock host, thay page, cleanup callback và ranh giới registry/migration. |
| `legacy_adapter.py` | Giữ nguyên widget/callback production cũ trong một scroll và một footer Apply. |
| `reference.py` | Contour reference schema presentation-only cho smoke/screenshot. |

Model/schema/state không giữ `QWidget`, `QObject`, `QModelIndex`, callback,
worker hoặc OCP object. Widget chỉ render state và phát intent; domain mutation
tiếp tục thuộc application service hiện có.

## 3. Section model

`FunctionEditorSection` có stable ID, title, summary, disclosure level, default
expanded state, help, enabled/applicable state, order và field tuple. Widget
section hiển thị cả icon/shape và text cho error/warning badge.

Reference editor minh họa các section:

```text
BASIC → GEOMETRY → TOOL → CUTTING → LEVELS → LINKING
      → ADVANCED → EXPERT → DIAGNOSTICS
```

Header summary và footer nằm ngoài `FunctionEditorContentScroll`. Chỉ nội dung
section/help/diagnostics scroll. `Collapse All` thu gọn section đang hiện;
`Expand Relevant` chỉ mở section có diagnostic, không biến thành Expand All.

## 4. Parameter disclosure

`ParameterDisclosureLevel` có ba mức `BASIC`, `ADVANCED`, `EXPERT`. Selector là
mức tối đa được phép hiện, không tạo ba bản sao field:

- Basic hiện mặc định và giữ các input cốt lõi theo workflow.
- Advanced chỉ dựng khi selector đạt Advanced, collapsed mặc định.
- Expert chỉ dựng khi selector đạt Expert, collapsed mặc định và help nêu
  trade-off precision/chất lượng/thời gian.

Field hoàn toàn không applicable không được dựng lúc mở editor. Nếu dependency
thay đổi sau đó, widget được tạo lazy hoặc ẩn; nó không nằm trong tab/focus hay
snapshot Apply/Calculate khi đang không applicable.

## 5. Field source và recommended default

`FunctionEditorField` có stable field ID, label, kind hữu hạn, value, unit,
source, recommended default, required state, disclosure level, applicability,
typed validators, help và order. Nguồn gồm `USER`, `TOOL`, `SETUP`, `STOCK`,
`MACHINE`, `PROFILE`, `GEOMETRY`, `DEFAULT`.

Nguồn khác USER được hiển thị bằng text, ví dụ `Nguồn: Setup`. Recommended
default có label nguồn/version riêng. `Restore Recommended Defaults` chỉ nạp
giá trị vào draft, không tự Apply.

## 6. Applicability policy

Applicability chỉ dùng tập operator khai báo hữu hạn: equals, not-equals,
truthy, falsy, in và not-in. Không nhận expression hoặc user script. Schema từ
chối dependency tới field không tồn tại.

Policy khi field trở nên không applicable:

1. Giữ primitive draft trong bộ nhớ để người dùng có thể quay lại mode cũ.
2. Ẩn field thay vì giữ hàng dài control disabled.
3. Loại field khỏi applicable snapshot gửi Apply/Preview/Calculate.
4. Không validate giá trị hidden stale.

Reference `lead_length` minh họa policy: tắt `use_lead` sẽ ẩn field và loại nó
khỏi snapshot nhưng không tự sửa giá trị.

## 7. Draft, Apply, defaults và reset

`FunctionEditorDraftState` giữ hai bản tách biệt:

- `applied_values`: snapshot UI đã Apply gần nhất;
- `values`: draft hiện tại.

Lifecycle có `NO_CHANGES`, `MODIFIED`, `INVALID`, `APPLYING`, `APPLIED`,
`STALE`. Invalid draft không gọi Apply callback và không mutation project.
Apply validate toàn applicable draft, truyền một immutable snapshot và chỉ cập
nhật applied state khi callback thành công. Khi callback lỗi, applied snapshot
không đổi và diagnostic `apply.failed` được giữ để hiển thị. Atomic rollback của
domain tiếp tục do application service/command transaction bảo đảm.

Ba reset có nghĩa riêng:

- Reset Field: về applied value của field.
- Reset Section: về applied snapshot của section.
- Reset Draft: về toàn bộ applied snapshot.

Đóng framework editor khi dirty cần confirmation; không tự Apply và không âm
thầm bỏ draft. Selection/project switch đánh dấu page cũ `STALE`, remove widget
và bỏ callback cũ. Preference không dùng operation ID, còn draft reference
không được serialize.

## 8. Validation và diagnostics

Stage 9A.4 hỗ trợ required, finite number, min/max và quan hệ lớn/nhỏ hơn field
khác bằng typed rule. Validation:

- không mutation domain;
- bỏ qua field không applicable;
- hiển thị icon + severity text + message inline;
- tổng hợp badge ở section và count ở header;
- đưa chi tiết vào Diagnostics;
- tự mở/focus/scroll tới field error đầu tiên;
- giữ nguyên text người dùng khi parse lỗi.

Severity gồm ERROR, WARNING và INFO. Màu chỉ là kênh phụ; accessible description
luôn chứa severity và message.

## 9. Preview và footer action

Preview hook nhận `FunctionEditorPreviewRequest` bất biến gồm project key,
operation key, generation, draft fingerprint và applicable values. Callback chỉ
được chấp nhận nếu bốn thành phần vẫn current. Hook không Apply, không tạo
toolpath artifact và không chạm QWidget/OCP từ worker.

Footer chuẩn hỗ trợ Reset Draft, Preview, Validate, Calculate, Apply và Close,
nhưng chỉ dựng action có nghĩa theo schema. Calculate lấy duy nhất applied state
current/valid; draft dirty/invalid/stale làm action disabled. Reference editor
không có Calculate vì chưa nối application service production.

Legacy adapter chỉ có một Apply và một Close. Nút Apply nằm trong form cũ được
ẩn để không duplicate; callback vẫn là `_CamPropertiesEditor.apply_draft()` và
Generate/Recompute vẫn dùng guard hiện có trong `CamWorkspace`.

## 10. Host và backward compatibility

`FunctionEditorHost` tiếp tục là widget trong right dock 9A.2. Host có một
`QStackedWidget`: framework page hoặc `LegacyFunctionEditorAdapter`, không bọc
hai scroll lồng nhau. Stage 9A.4 để registry production rỗng, nên mọi selection
Facing/Contour/Pocket/Drilling/Tapping/Reaming/Boring vẫn mở Legacy Editor.

Operation Manager tiếp tục ánh xạ node con về operation owner. Simulation,
Post/NC và Program Assembly vẫn mở secondary panel hiện tại; host không tự chạy
action downstream. Các alias `editor`, `scroll_area`, `apply_button` của Stage
9A.2 được giữ để caller/test cũ không gãy.

## 11. Responsive, accessibility và performance

- Right dock cho phép 300–520 logical px; editor đã kiểm tra ở 300, 360, 420,
  520 px.
- Dưới 400 px, label/control xếp dọc; dưới 360 px footer chuyển hai hàng.
- Không có horizontal scroll trong content; unit/label/footer vẫn nhìn thấy.
- Mọi page/section/field/input/action có objectName và accessible name ổn định.
- Toggle section dùng `QToolButton`, hỗ trợ keyboard Space/Enter theo Qt.
- Validate focus field lỗi; hidden/collapsed field không nằm trong tab flow.
- Schema 20/50/100 field được test; field không applicable được dựng lazy.
- Dựng UI không tạo worker hoặc gọi calculation/domain mutation.
- Thay reference/legacy editor lặp cleanup page cũ và không tăng số widget active.

Expansion, disclosure và help visibility được lưu trong QSettings group
`function_editor_9a4`, `version = 1`, theo `editor_id + strategy`. Editor width
tiếp tục do Workspace Layout 9A.2 lưu. Không lưu draft value, validation result,
callback hoặc project/domain data; do đó không làm project dirty.

## 12. Kiểm thử và ảnh regression

Test tự động mới:

- `tests/unit/test_function_editor_model_9a4.py`;
- `tests/unit/test_function_editor_widgets_9a4.py`.

Manual harness: `tests/manual_stage9a4_function_editor.py`. Harness dùng MainWindow
production, OCP BREP thật và no-op viewport presentation khi Qt offscreen. Mười
ảnh bị Git ignore tại `reference_private/DERIVED/UI_STAGE_9A4/`: Basic,
Advanced collapsed/expanded, Expert expanded, inline error, source/default,
300 px, 420 px, legacy adapter và diagnostics/help.

Qt offscreen trên máy kiểm thử không có glyph tiếng Việt nên ảnh có thể hiện ô
vuông; geometry, spacing, badge, focus, scroll và responsive vẫn kiểm tra được.
Production Windows dùng font Segoe UI hệ thống.

## 13. Kế hoạch migration 9A.5

9A.5 sẽ đăng ký presenter/schema production riêng cho Facing, Contour và Pocket,
ánh xạ đúng domain parameter/service hiện có. Mỗi strategy có thể bật/tắt độc
lập và fallback về Legacy Editor mà không migrate dữ liệu. Trước khi bỏ legacy
path cần test geometry binding, Tool/Setup source, domain validation, Apply,
Calculate worker/stale guard, Save/Open/Autosave/Recovery và screenshot từng
strategy. Stage 9A.4 không tự bắt đầu các migration này.
