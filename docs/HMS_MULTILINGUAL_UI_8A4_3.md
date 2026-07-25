# HMS CAD/CAM — Stage 8A.4.3: hệ thống đa ngôn ngữ

## Phạm vi và trạng thái

Stage 8A.4.3 đã `COMPLETED` trên baseline `4f7e8d7` sau khi người dùng duyệt
package GUI cuối. Giao diện hỗ trợ
`VI_VN`, `EN_US` và `KO_KR`; `VI_VN` là mặc định và fallback ưu tiên bất kể
locale Windows. Locale là preference người dùng, không phải project semantics.
Không có stage kế tiếp nào được bắt đầu.

Stage này không triển khai ProgramData/install layout, importer đa định dạng
đầy đủ, Export 3D, cấu hình phiên bản Export, CAM workflow ba bước, Tool đa họ,
màu đường chạy dao, Program Templates hoặc Production Post mới.

## Kiến trúc

- `UiLanguage` là `StrEnum` typed; chỉ ba giá trị ổn định được chấp nhận.
- `TranslationCatalog` đọc catalog JSON UTF-8 bất biến và giữ bằng chứng
  duplicate key; `TranslationService` phát sự kiện `language_changed`.
- `LocaleSettingsService` dùng `QSettings` abstraction hiện hành với khóa
  `ui/language`; lỗi đọc/ghi không ngăn ứng dụng khởi động.
- `localize_widget_tree()` giữ canonical source trên widget/item/action để
  retranslate idempotent, cập nhật title, tooltip, placeholder, tab, header và
  accessibility text mà không sửa model/domain data.
- Property giữ semantic value (`None`) và model/delegate resolve `DisplayRole`
  theo locale hiện tại; model phát `dataChanged`/`headerDataChanged`.
- Viewport fallback và managed diagnostic giữ source reason, rồi render lại title,
  body và output-log theo locale; review harness không chèn text review giả.
- `LocalizedFileDialog` luôn dùng non-native Qt dialog; `LocalizedMessageBox`
  đặt rõ nút Save/Don’t save/Cancel theo locale HMS, không phụ thuộc Windows.
- Ribbon giữ nhãn đầy đủ; hành động gửi 3D dùng nhãn ngắn tự nhiên theo locale
  (`Nạp 3D vào CAM`) nhưng tooltip/accessibility giữ câu đầy đủ.
- Sidebar file dialog đặt tên theo semantic thật (`Máy tính`/`Computer`/`내 컴퓨터`
  và thư mục người dùng), giữ nguyên URL/icon và đo minimum width bằng
  font metrics. Dock tab dùng nhãn compact từ catalog, còn tooltip/accessibility
  giữ full title; tab bar tự mở rộng theo chiều rộng nhãn. Dock được add/tabify
  đúng một lần trong layout lifecycle; retranslation chỉ đổi presentation.
- Nhóm dock trái chỉ có một semantic tab bar cho `Hình học / Dự án` và
  `Nguyên công`. Dock workflow phải hiển thị compact label `Post` nguyên vẹn ở
  cả ba locale tại DPI 100/125/150; full label vẫn là `Simulation / Post`.
- Notification 3D dùng formatter placeholder count/source theo locale cho
  0/1/n, gồm singular/plural và thứ tự placeholder riêng của tiếng Hàn; không
  còn badge số đứng trước một câu hoàn chỉnh.

## Catalog contract và fallback

Catalog phải có cùng tập key, chuỗi không rỗng, placeholder format đồng nhất,
glossary kỹ thuật ổn định và duplicate bằng không. Resolver dùng thứ tự:

1. locale người dùng;
2. catalog tiếng Việt;
3. chuỗi an toàn khai báo (`Nội dung giao diện`, `Interface text`,
   `인터페이스 텍스트`).

Raw translation key không bao giờ được render. Fallback hit được ghi
diagnostic nội bộ và xuất hiện trong audit, không hiện cảnh báo lặp lại trên
widget.

## Glossary và dữ liệu không dịch

CAD, CAM, CNC, Tool, Holder, Post, G-code, Toolpath IR, SQLite, OCP, BRep,
UUID, ID, STEP, IGES, STL và U/V/W được giữ theo glossary. Tên file, tên dự án,
Tool/Holder/máy/nguyên công do người dùng nhập, path, UUID, hash, extension,
raw G-code và Post content không đi qua resolver.

## Runtime switch và accessibility

Apply đổi ngay MainWindow, menu, ribbon, dock, project tree, properties,
Function Editor, notification và dialog đã mở an toàn. Không đóng tài liệu,
không Save/Calculate, không hủy worker, không đổi selection/workspace/dirty
state, hình học, manifest, `project.db`, Tool payload, simulation hoặc Post.
Accessible name/description, tooltip, bảng/header và shortcut description dùng
chung canonical source; shortcut logic không thay đổi theo label.

Font không được đóng gói: Latin/Vietnamese ưu tiên Segoe UI, Korean ưu tiên
Malgun Gothic hoặc fallback hệ thống. Audit kiểm tra accent, Hangul,
replacement/tofu, clipping, overlap và horizontal scroll ở DPI mục tiêu.

## QA và giới hạn review

Catalog/audit service sinh báo cáo theo từng locale. Review package native
Windows nằm trong `reference_private/DERIVED/UI_STAGE_8A4_3_MULTILINGUAL/`,
được Git-ignore và không stage/commit. Người dùng đã duyệt package cuối gồm
18 PNG, 9 JSON và 1 Markdown; 18 PNG có hash riêng, 12 source fingerprint khớp,
catalog có 1104 key cho mỗi locale và mọi counter fallback/mixed/raw/accessibility/
glyph/layout bắt buộc đều bằng 0.

Rendered audit đọc text thật của QAction/menu/ribbon, widget/placeholder,
QAbstractItemModel header/DisplayRole/accessibility roles, delegate, property,
notification, output-log, diagnostic và status bar. Audit cũng kiểm tra
`QFontMetrics.elidedText`, content rect, sidebar model roles, QTabBar/dock tab,
tooltip/accessibility full text và message formatter; các trường clipping/elision
được tách riêng theo ribbon/sidebar/dock. Audit dock còn đo duplicate tab bar,
duplicate semantic set/label, partial visibility, out-of-bounds và mất ký tự đầu;
object/tab identity được so sánh trước và sau chuỗi runtime switch. Dữ liệu
filesystem/người dùng được phân biệt khỏi UI text. Chuỗi English ngoài glossary trong VI/KO,
tiếng Việt/Hangul trong EN và các mẫu nhiễm đã biết đều làm audit thất bại.

SQLite vẫn schema v4, Tool payload v2/v1 compatibility, `.HMS` container,
geometry transfer, safety và CAM algorithms không đổi. Đây không phải bước
triển khai ProgramData hoặc cài đặt machine-wide.

QA cuối đạt 58 focused Stage mới, 140 regression liên quan, 198 nhóm Stage và
1748 passed, 2 deselected toàn repository. `pip check`, `compileall src tests
tools` và `git diff --check` đều đạt.
