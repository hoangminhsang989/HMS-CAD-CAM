# Kiến trúc cửa sổ chức năng CAM

## Mục tiêu

HMS dùng một cột Quản lý nguyên công ở bên trái và một cửa sổ chỉnh sửa CAM
modeless duy nhất. Viewport CAD/OCP vẫn là vùng làm việc chính. Thiết kế lấy cảm
hứng từ quy trình thao tác tập trung của phần mềm CAM chuyên nghiệp nhưng không
sao chép bố cục, asset, icon, mã nguồn hoặc hình ảnh thương mại.

## Cột trái và thao tác mở

`OperationManagerPanel` tiếp tục chiếu trực tiếp data model hiện có; không có
danh sách operation song song. Dòng nguyên công cao compact 38 logical px và
dùng delegate hai dòng: dòng chính ưu tiên custom name của người dùng hoặc tên
chức năng tiếng Việt; dòng phụ chỉ giữ loại nguyên công và Tool. Badge trạng
thái nằm trong cột 70 px. Cây dùng indentation 10 px để tên các nguyên công sâu
vẫn nhận biết được. Summary lifecycle đầy đủ, loại, Tool và mọi status vẫn
có trong tooltip/accessibility; cây luôn tắt horizontal scrollbar.

- Nhấp một lần chỉ chọn dòng, đồng bộ identity với coordinator/viewport và không
  mở hoặc đổi editor.
- Nhấp đúp, Enter hoặc lệnh `Mở` mới yêu cầu editor.
- Mở lại operation đang active chỉ đưa cùng cửa sổ lên trước.
- Command tạo function chỉ phát `operation_created` sau khi persist thành công;
  MainWindow dùng identity vừa chọn để mở operation mới trong popup singleton.
- Context menu chỉ hiển thị lệnh đã có capability miền: mở, đổi tên, nhân bản,
  bật/tắt, tính/hủy, xóa, mô phỏng và Post.

## Popup singleton

`CAMFunctionPopupHost` là một `QDialog` modeless do `MainWindow` sở hữu.
`FunctionEditorHost` hiện có được tái sử dụng làm content host, vì vậy cả Facing,
Planar Face Facing, Contour, Pocket, Drilling, Tapping, Reaming, Boring và
Parallel Finishing dùng cùng một instance popup. Popup không tạo bản sao khi mở
lại và luôn dispose page cũ trước khi rebind, đánh dấu callback/preview cũ stale
và dọn signal.

Vị trí/kích thước được lưu trong `QSettings` thuộc UI preference, không nằm trong
project CAM payload. Geometry được clamp theo `availableGeometry()` của monitor
hiện tại để không lọt khỏi màn hình hoặc dưới taskbar. Dock editor Stage 9A.2 chỉ
còn là compatibility object ẩn cho state cũ; production UI không mở panel phải.

## Compact density policy và responsive sizing

`CAMPopupDensityPolicy`/`CAMPopupMetrics` trong `ui_tokens.py` là nguồn duy nhất
cho popup width/height, min/max, margin, section/row/label spacing, control và
button height, footer, font point size, table/tree row, illustration và child
limits. Cả chín editor nhận cùng metrics qua `FunctionEditorHost`; editor ít
field có preferred height thấp hơn, editor dài dùng cuộn dọc. Chuyển operation
không tạo popup mới và không cưỡng bức nhảy về kích thước lớn nhất.

Target theo logical work area:

| Work area | Kích thước ưu tiên | Giới hạn work area |
|---|---:|---:|
| 1366×768 | 587×630 | rộng 45%, cao 84% |
| 1600×900 | 624×702 | rộng 43%, cao 82% |
| 1920×1080 | 672×778 | rộng 42%, cao 80% |

Các số thực tế được clamp theo taskbar, monitor và font native. Qt đã trả logical
pixel nên display scale 125%/150% chỉ được ghi nhận, không nhân lần hai vào
layout. Font popup kế thừa family Windows, body được clamp 9–10 pt, section
10–11 pt, operation title 11–12 pt và status có floor 8,5 pt; không thay
application font hoặc icon font.

Margin ngoài 8 px, section spacing 6 px, row spacing 3 px, label gap 7 px,
input/combo 27 px, button 29 px, compact button 27 px và row table/tree 26 px.
Đây là logical metrics, vì vậy vẫn có hit target đọc/bấm được tại DPI cao.
Operation Manager giữ row cây thường 26 px; riêng operation dùng hai dòng 38 px.
Tên dài được elide theo chiều rộng thực nhưng không ép còn 2–3 ký tự trong cột
trái chuẩn. Status badge rút gọn còn 70 px để dành chiều rộng cho tên.

Popup mở lần đầu sát phải vùng MainWindow để danh sách nguyên công trái và phần
lớn viewport vẫn thấy. Geometry `cam_function_popup_v2/rect` lưu bằng
`QSettings`; dữ liệu v1 được migrate, mọi rect cũ/quá lớn/ngoài màn hình bị
responsive clamp. Window state maximized/full-screen không được persist và
screen change áp lại policy. Preference này tuyệt đối không vào CAM payload.

## Dirty switch và đóng cửa sổ

Nếu draft hiện tại chưa Apply, chuyển operation hiển thị đúng ba lựa chọn:

- `Áp dụng và chuyển`: validate, Apply nguyên tử, khôi phục identity đích và lấy
  session mới; không tự Calculate.
- `Bỏ thay đổi và chuyển`: dispose draft, không thay operation/artifact đã Apply.
- `Tiếp tục chỉnh sửa`: trở về operation cũ và giữ nguyên draft.

Validation lỗi giữ popup ở operation cũ, giữ draft và đưa focus đến lỗi đầu
tiên. Nút X/title bar dùng cùng close contract; không có đường bỏ draft âm thầm.
Calculation nền không bị hủy khi đổi editor và latest-wins vẫn do application
service/worker hiện có kiểm soát.

## Popup con

Popup chính có một child slot duy nhất. Tool selector, chẩn đoán an toàn và cửa
sổ phóng to minh họa đều được reparent vào popup chính, dùng window modality với
popup chính và khôi phục focus về control đã mở chúng. Mở child khác sẽ đóng child
cũ; không có tầng thứ ba. Child được căn giữa popup và clamp trong monitor hiện
tại, kể cả ở DPI 125%/150%. Esc đóng child trước.

Minh họa inline dùng `Mở rộng` và `Thu gọn minh họa`; child zoom có title
`Minh họa · <Tên chức năng>` và action duy nhất `Đóng minh họa`. Vì vậy nút
`Đóng` của footer editor không bị nhầm với action child. Đóng bằng nút hoặc Esc
khôi phục focus đúng về `Phóng to` và không làm mất draft.

Tool selector chỉ sửa primitive `tool_assembly_id` trong draft. Chẩn đoán và
minh họa không sửa domain. Numeric parameter thường vẫn ở popup chính, không bị
đẩy sang dialog khác.

Tool selector có filter compact và nhỏ hơn popup chính khi work area cho phép.
Diagnostics có thể rộng hơn vì bảng nhiều cột nhưng luôn nhỏ hơn work area, cho
phép horizontal scrollbar riêng và luôn giữ nút Đóng. Illustration child có thể
resize nhưng không tự full-screen. Dirty confirmation tiếp tục là message box
ngắn với đúng ba lựa chọn.

## Footer và scrolling

Summary, disclosure, progress và footer nằm ngoài `QScrollArea`; chỉ vùng section
cuộn dọc. Horizontal scrollbar của popup chính luôn tắt. Footer một hàng giữ thứ
tự lifecycle và button chỉ rộng theo nội dung; dưới breakpoint 500 logical px
mới reflow hai hàng. Khi Calculate, progress compact và Hủy tính toán hiện rõ,
các action xung đột bị disable nhưng footer không overlap hoặc biến mất.

Label dài được wrap trong row hoặc elide ở summary/caption kèm tooltip đầy đủ và
accessible description. Summary operation dành dòng đầu cho strategy/số/đơn vị/
Tự động–Tùy chỉnh, dòng hai cho Tool và hình học; phần đầy đủ luôn nằm trong
tooltip. Dấu tiếng Việt không bị loại khỏi source text.

## One-screen Basic và responsive grid

`CAMResponsiveGridPolicy` chọn hai cột từ content width, shared column minimum
và `minimumSizeHint()` thực của widget trong logical pixel. Ở ba popup target
1366×768, 1600×900 và 1920×1080, Basic dùng hai cột; chỉ vùng hẹp dưới khả năng
đọc mới fallback một cột và cho phép cuộn dọc. Không có horizontal scrollbar.

Parallel có scan path cố định: Hình học/Tool; Chất lượng/Minh họa; tóm tắt tự
động toàn chiều rộng với sáu ô hai cột. Basic ẩn các dòng provenance/default
phụ, nhưng giữ nguyên trong tooltip và accessible description. Policy metadata,
mode count và Holder scope chi tiết nằm ở Nâng cao. Advanced đóng không giữ body
height; khi mở, section grid tiếp tục hai cột nếu đủ rộng và vùng content được
phép cuộn. Summary, disclosure, progress và footer vẫn nằm ngoài vùng cuộn.

## Lifecycle và tham số tự động

- Preview dùng draft, không commit và không publish READY.
- Apply validate rồi commit nguyên tử; artifact theo contract trở thành
  MISSING/STALE và không tự Calculate.
- Calculate chỉ dùng applied snapshot, giữ progress/cancel/latest-wins và chỉ
  publish khi safety SAFE.
- Cancel editor bỏ draft, không sửa operation đã Apply.
- Xóa operation/project close làm session cũ stale và popup không giữ object đã
  bị xóa.

Parallel Basic tiếp tục không có numeric input mặc định. Người dùng chọn bề mặt,
Tool và hồ sơ chất lượng; hướng, bước ngang, dung sai, lượng dư, ordering và
linking được giải thích trong tóm tắt tự động. Override chỉ hiện ở Nâng cao khi
người dùng bật tùy chỉnh thủ công.

## Localization, persistence và accessibility

Toàn bộ title, action, prompt, caption, tooltip và accessible description mới
dùng tiếng Việt; enum được đưa qua mapper hiển thị. Tab order giữ footer, nút
Mở rộng/Thu gọn và Phóng to; Enter mở operation đang chọn và menu context dùng phím Menu hoặc
Shift+F10. Trạng thái không phụ thuộc riêng vào màu.

Không persist popup/widget/dialog/pixmap/renderer/signal/selection overlay. Chỉ
geometry popup là UI preference. Minh họa dùng logical coordinates của Qt nên
không mờ ở 125%/150%.

Mapper presentation duy nhất ánh xạ chín tên mặc định: `Phay mặt 2.5D`,
`Phay các mặt phẳng`, `Phay biên dạng 2D`, `Phay hốc 2.5D`, `Khoan`, `Taro`,
`Doa lỗ`, `Khoét lỗ` và `Gia công tinh song song`. Mapper được dùng cho popup,
header, Operation Manager, menu, tooltip, accessibility và runtime audit. Custom
name không bị dịch; enum, strategy ID, registry ID và dữ liệu persist không đổi.

## Giới hạn

Popup không thay đổi domain/algorithm của editor cũ. Machine-ready clearance
vẫn chưa xác minh; Parallel production Post vẫn fail-closed. Việc hoàn thiện
icon tiếp tục thuộc Stage 9A.I1 đang deferred. Tạo operation mới vẫn đi qua lệnh
ứng dụng hiện có; chỉ signal sau commit mới mở popup, nên dialog bị hủy hoặc
command lỗi không tạo node rác và không mở nhầm operation cũ.
