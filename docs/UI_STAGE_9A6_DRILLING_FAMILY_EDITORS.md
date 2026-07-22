# Stage 9A.6 — Drilling Family Production Function Editors

## 1. Phạm vi

Stage 9A.6 đưa Drilling, Tapping, Reaming và Boring vào Unified Function
Editor bằng mã production. Thay đổi chỉ bổ sung presentation schema, chuyển đổi
draft, điều phối UI, Duplicate operation, test và tài liệu. Domain v1, codec,
generator, Toolpath IR, Simulation, Post, project manifest và SQLite schema v4
không thay đổi. Stage 9A.I1 icon pack vẫn deferred.

## 2. Nền tảng dùng chung

Bốn editor dùng một foundation native-free để ánh xạ chính xác operation v1
thành field và dựng lại candidate operation mà chưa mutation project. Các phần
dùng chung gồm Geometry, Tool, Levels/Depth, Cutting, Clearance/Retract,
Machine/Post Capability, tolerance, validator, disclosure và footer
`Preview → Validate → Calculate → Apply → Close`.

Draft giữ riêng geometry rebind và identity input đang chờ. Preview chỉ resolve
input; Validate dùng schema và generator hiện có; Apply chạy một command nguyên
tử, không tự Calculate/Simulate/Post/Export. Calculate chỉ dùng applied state.
Đổi operation hoặc project tiếp tục tuân theo lifecycle Apply/Discard/Cancel của
framework hiện có.

## 3. Nội dung theo editor

| Editor | Process/Cutting thật trong domain v1 | Advanced | Derived/read-only |
|---|---|---|---|
| Drilling | Standard/Spot/Peck, feed/min, RPM | Peck depth khi Peck, dwell, retract policy | Coolant/approach summary |
| Tapping | diameter, pitch, hand, RPM | synchronization policy, dwell | thread system, synchronized feed, coolant |
| Reaming | target/pre-hole diameter, RPM, feed/rev, coolant | spindle direction, dwell, retract policy | diametral allowance, feed/min |
| Boring | finished/pre-bore diameter, mode, RPM, feed/rev, coolant | spindle direction, dwell, retract policy | radial stock, feed/min |

Basic chỉ hiển thị field cần cho công việc thường xuyên; type/công tắc kích hoạt,
metadata kiểu chọn/hệ tọa độ, holder và coolant summary chỉ đọc được đưa sang
Advanced để tránh lặp summary. Advanced và Expert collapsed mặc định; Expert chỉ
có tolerance thật. Không thêm deep-hole mode,
orient spindle, radial shift, back boring, canned-cycle code hoặc tham số giả mà
domain v1 chưa mô hình hóa. Tapping feed đồng bộ bằng pitch × RPM theo generator
hiện có; Drilling/Tapping coolant chỉ là summary vì codec v1 không lưu coolant.

## 4. Validation và capability

Editor kiểm tra số hữu hạn/dương, final depth thấp hơn Top, Retract cao hơn Top,
Clearance cao hơn Retract, peck chỉ khi chọn Peck, pitch/diameter hợp lệ và
pre-hole/pre-bore nhỏ hơn đường kính đích. Lỗi được gắn đúng field/section và
không mutation domain.

Machine requirement ánh xạ Tapping sang capability `TAPPING`; Drilling,
Reaming và Boring dùng `DRILLING`, đúng contract hiện tại. Generator hiện có
kiểm tra machine/tool/input trước Calculate. Tapping phát cảnh báo không chặn khi
Post chưa được bind; controller cycle chỉ được quyết định tại Generate Post,
không được hard-code trong editor.

## 5. Operation Manager và persistence

Operation Manager bật Duplicate chỉ cho node operation. Bản sao có operation ID,
node ID và geometry input ID mới; giữ parameter/tool/machine/reference, đặt
revision 0 và artifact `MISSING`, không sao chép diagnostic hay toolpath artifact.
Tên dùng hậu tố `Copy`, `Copy 2`, ... và node mới nằm ngay sau bản gốc.

Save/Open round-trip giữ bốn operation qua persistence/codec hiện có. Apply không
đổi dữ liệu khi draft tương đương; khi thay đổi thì revision, dirty reason và
artifact state đi theo command/domain contract sẵn có. UI không đọc/ghi trực tiếp
SQLite hay tạo G-code.

## 6. Kiểm thử và GUI review

Test Stage 9A.6 bao phủ schema/disclosure/widget, binding, draft invalid,
applicability, capability warning, Preview/Apply/Calculate, disabled operation,
operation switching, Duplicate, exact-equivalence và save/open bốn editor. Các
regression editor phay 2D, Operation Manager, hole strategy/generator,
persistence, Simulation và Post tiếp tục được chạy trong full suite.

Manual harness `tests/manual_stage9a6_drilling_family_editors.py` tạo project
`.HMS` thật và 12 ảnh ở 1366×768, 1600×900, 1920×1080 cho Basic, Advanced,
validation error, dirty, disabled và operation switching. Ảnh nằm trong thư mục
Git-ignored `reference_private/DERIVED/UI_STAGE_9A6/`. Harness review trên Windows
chỉ chấp nhận QPA `windows`, font Segoe UI có đủ glyph tiếng Việt và đúng state
scroll của Basic/Advanced; QPA `offscreen` không có font sẽ fail sớm thay vì tạo
ảnh ô vuông. Panel mặc định rộng 460 px; layout ngang chia chỗ label/control theo
tỷ lệ 1:2, còn dưới 400 px label reflow lên trên control và footer chuyển hai hàng
để không bị ép chữ hoặc tràn ngang.

Kết quả review: manual harness đạt 12/12 ảnh; drilling-family regression 292/292;
Duplicate/Operation Manager/Workspace 88/88; framework và editor phay 2D
121/121; full suite 1284 passed, 2 deselected. Native Windows GUI đã được người
dùng duyệt; Stage 9A.6 được xác nhận `COMPLETED` trong commit hoàn tất.

## 7. Giới hạn còn lại

Các chức năng cần domain/capability mới như deep-hole chuyên dụng, spindle
orientation, radial shift/back boring và coolant của Drilling/Tapping không nằm
trong stage này. Native DPI 125%/150%, screen reader end-to-end và icon pack cần
được đánh giá ở nhiệm vụ riêng. Stage tiếp theo không tự động bắt đầu.

Backlog polish không chặn Stage 9A.6:

- Badge `FRAMEWORK` có thể được ẩn hoặc đổi tên trong một stage polish sau.
- Tên operation bị rút gọn ở panel hẹp có thể bổ sung tooltip hoặc width policy
  chuyên biệt sau; không thay đổi trong Stage 9A.6 để tránh regression.
