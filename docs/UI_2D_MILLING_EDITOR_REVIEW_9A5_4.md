# Stage 9A.5.4 — Đánh giá đồng bộ Function Editor phay 2D

## 1. Phạm vi và kết luận

Stage 9A.5.4 đã đánh giá trực quan và khả năng thao tác của bốn production
editor: Facing, Planar Face Facing, Contour và Pocket. Thay đổi chỉ nằm ở
presentation metadata/widget, script review, test và tài liệu. Không thay domain,
codec key, validator semantic, generator, fingerprint, Toolpath IR, Simulation,
Post/FANUC, SQLite v4 hoặc project dirty semantic.

Đây là một vòng audit/polish có kiểm soát, không phải tuyên bố toàn bộ HMS UI đã
hoàn thiện. Drilling/Tapping/Reaming/Boring, CAM 3D, Parallel Finishing, Lathe,
Pocket island và strategy mới không được triển khai trong stage này.

## 2. Kết quả audit trực quan

Các vấn đề phát hiện trước khi sửa:

- Header dùng một dòng context dài với thứ tự strategy trước Tool/Geometry; độ
  cao thay đổi theo nội dung và khó đọc ở 300 px.
- Facing/Planar vẫn cho chọn Expert dù không có Expert field thật.
- Footer tại đúng 360 px còn dùng sáu cột và có nguy cơ ép/cắt action.
- Mỗi lần sửa field đều rebuild toàn content; việc này có thể làm mất focus hoặc
  đưa scroll về đầu dù applicability không đổi.
- Field được tạo lazy khi mở Advanced có thể bị append sai vị trí schema.
- Facing khai báo field trong section Advanced nhưng metadata field vẫn là Basic.
- `Derived` chưa có source label; thuật ngữ Top/Depth/Stepover/Stepdown,
  Allowance, Clearance và Retract không thống nhất giữa các editor.
- Disclosure chưa đồng đều: Pocket giấu direction/feed/spindle nhưng để allowance
  trong Basic; Contour để safe motion/lead và allowance trong Basic.

Các sửa đổi đã thực hiện:

- Header giữ một chiều cao compact, hiển thị theo thứ tự Tool → Geometry → intent,
  đưa Toolpath/Draft sang status line và elide an toàn kèm tooltip/accessibility.
- Disclosure selector chỉ hiện tới level thực sự tồn tại; Facing/Planar không còn
  Expert giả. Advanced/Expert vẫn collapsed mặc định.
- Footer chuyển hai hàng dưới 400 px, nên 300 và 360 px đều giữ đủ đúng thứ tự
  `Reset Draft → Preview → Validate → Apply → Calculate → Close`.
- Content chỉ rebuild khi tập section được tạo thay đổi; edit thông thường giữ
  focus/scroll. Field lazy được insert theo `order + field_id` xác định.
- Thêm source `Derived`, disabled reason vào accessible description và đồng bộ
  nhãn Top, Depth, Stock Allowance, Stepover, Stepdown, Clearance, Retract.
- Basic được cân lại còn 9/8/10/10 editable field cho Facing/Planar/Contour/Pocket.
  Allowance và safe/linking tuning chuyển về Advanced; direction/feed/spindle của
  Pocket về Basic như các strategy có cùng tần suất sử dụng.

## 3. Ma trận Basic / Advanced / Expert

`Inherited` ở bảng là linked/read-only từ Geometry, Tool hoặc Stock; các
operation-owned quantity vẫn là USER value dù có recommended default từ
Setup/Stock/HMS.

| Editor | Basic editable | Advanced | Expert | Derived / inherited | Không áp dụng |
|---|---|---|---|---|---|
| Facing | Name, Tool, Stepover, Direction, Feed, RPM, Top, Depth, Stepdown (9) | Stock Allowance, Clearance, Retract, plunge feed, raster angle, Overtravel, Machine, Enabled | Không có | Stock region/bounds; Tool/Shank/Holder | Geometry identity, Entry, Expert |
| Planar Face Facing | Name, Tool, Stepover, Direction, Feed, RPM, Top, Stepdown (8); Depth read-only | Geometry identity, Stock Allowance, Clearance, Retract, plunge feed, raster angle, Machine, Enabled | Không có | FACE summary/identity, Depth từ Geometry; Tool/Shank/Holder | Stock bounds, Overtravel, Entry, Expert |
| Contour | Name, Tool, Side, Direction, Feed, RPM, Top, Depth, multiple-depth, Stepdown (10) | Profile source/identity, wall/floor Stock Allowance, Clearance, Retract, linear Lead, plunge feed, finishing pass, Machine, Enabled | Canonical start policy read-only | Geometry chain; Tool/Shank/Holder; computer compensation summary | Entry section; Stepdown khi multiple-depth tắt; tolerance/filter/G41/G42 |
| Pocket | Name, Tool, Direction, Stepover, Feed, RPM, Top, Depth, Stepdown, Entry (10) | Geometry identity, wall/floor Stock Allowance, plunge feed, Clearance, Retract, Machine, Enabled | Algorithm tolerance | Final cutter Z, level count; region/island summary; Tool/Shank/Holder | Island control, ramp/helix/pre-drill, pattern khác Offset inward |

Không có field giả được thêm để làm bốn editor giống nhau. Facing không có
Expert; Contour chỉ hiện policy thuật toán thật; Pocket chỉ có tolerance thật.
Pocket Entry vẫn chỉ có `Vertical Plunge`, còn machining pattern vẫn là
`Offset inward` read-only đúng domain v1.

## 4. Workflow và số thao tác

Số dưới đây đếm thao tác có chủ ý sau khi operation đã được tạo, Tool/Setup mặc
định đã tồn tại; một viewport pick và nút Select được tính là hai thao tác, không
tính scroll/tab traversal:

| Workflow cơ bản | Thao tác | Ghi chú |
|---|---:|---|
| Facing | 7 | Geometry Stock được kế thừa; chọn Tool, chỉnh ba Level, Validate, Apply, Calculate |
| Planar Face Facing | 9 | Pick + Select FACE, chọn Tool, chỉnh ba Level, Validate, Apply, Calculate |
| Contour | 12 | Pick + Select chain, Tool, Side, Direction, Depth, Stepdown, mở Linking, Lead, Validate, Apply, Calculate |
| Pocket | 9 | Pick + Select region, Tool, Stepover, Stepdown, Depth, Validate, Apply, Calculate; Pattern/Entry hiện chỉ có một policy |

Không phát hiện field bắt người dùng nhập lại Tool/Geometry-derived detail.
Operation-owned safe values vẫn explicit vì domain hiện tại chưa có inherited
override contract. Advanced chỉ cần mở cho allowance, safe/linking hoặc tuning;
action footer luôn nhìn thấy. Edit thường giữ focus và scroll; operation switch
không đưa draft sang editor khác.

## 5. Draft, Apply và downstream guard

Test tự động và manual harness xác nhận cho cả bốn editor:

- draft/invalid draft không mutation domain và không đổi project dirty;
- Validate focus inline error đầu, mở đúng disclosure/section;
- Apply nhận một snapshot, thành công mới refresh applied state; test rollback cũ
  vẫn đạt;
- Apply không tự Calculate; Calculate chỉ dùng applied state;
- không tự Simulate, Post hoặc Export;
- Apply/Discard/Cancel khi đổi operation, stale callback khi project switch và
  đóng dirty draft tiếp tục theo framework contract;
- disclosure, expansion, help, focus và width chỉ là user UI state.

## 6. Responsive và accessibility

Đã kiểm tra editor ở 300, 360, 420, 520 logical px và window 1366×768,
1600×900, 1920×1080. Footer không mất; content không có horizontal scroll;
inline diagnostic/source/default wrap trong editor; viewport giữ tối thiểu
520×360 theo Stage 9A.2. Header/footer sticky và content scroll nội bộ.

Tab/Shift+Tab dùng order xác định; section toggle dùng keyboard; label buddy,
tooltip, `accessibleName`, accessible disabled reason và focus indicator được
kiểm tra. Trạng thái draft/error/warning có text và symbol, không chỉ dùng màu.
Offscreen smoke không mô phỏng được màn hình vật lý 125%/150%; Qt logical-pixel
reflow đã được kiểm tra, còn native DPI cần tiếp tục quan sát trên Windows.

## 7. Screenshot regression

`tests/manual_stage9a54_milling_editor_review.py` tạo một project thật chứa bốn
production operation, đổi editor nhiều lần, chọn Geometry/Tool, thử
draft/reset/validate, Apply/Calculate rõ ràng, Save/Open, project switch và đóng
sạch. Harness tạo 42 screenshot review cùng 4 contact sheet trong thư mục
Git-ignored `reference_private/DERIVED/UI_STAGE_9A5_4/`.

Contact sheet gồm Basic bốn editor; Advanced collapsed/expanded; 300/360/420/520
và 1366×768/1920×1080; invalid, source/default/derived, Geometry, Tool và Legacy
comparison. Ảnh không được đưa vào Git. Qt offscreen trên máy kiểm thử thiếu
glyph tiếng Việt; Windows production tiếp tục dùng Segoe UI hệ thống.

## 8. Exact equivalence và deferred issues

Exact-equivalence được giữ: không sửa domain/codec/fingerprint/generator và các
regression fixture trước/sau vẫn so sánh operation, Toolpath, Simulation, neutral
Post và FANUC ROBODRILL output. SQLite vẫn schema v4; không golden NC nào đổi.

Deferred vì cần thay domain/capability hoặc ngoài phạm vi:

- true inherited/override contract cho safe motion và technology values;
- Pocket island, pattern khác Offset inward, ramp/helix/pre-drill;
- Contour open/multi-chain, arc lead, controller/wear compensation;
- native Windows DPI 125%/150% và screen-reader end-to-end session;
- mọi migration Drilling/Tapping/Reaming/Boring.

Sau khi Stage 9A.5.4 được chấp nhận, bước đề xuất tiếp theo là Stage 9A.6
Drilling Family. Stage 9A.6 chưa được bắt đầu trong thay đổi này.
