# Stage 8A.2.3 — Parallel Finishing Production Function Editor

## Trạng thái

**COMPLETED.** Người dùng đã duyệt gói GUI cuối tại
`reference_private/DERIVED/UI_STAGE_8A2_3_DPI_CLIPPING_FIX/`.

## Phạm vi

Stage 8A.2.3 đưa strategy `parallel_finishing_3d` hiện có vào Unified Function
Editor và Operation Manager. Editor dùng trực tiếp parameter payload v1, thuật
toán v3, CAM 3D foundation 8A.1, generator 8A.2.1 và safety pipeline 8A.2.2;
không có thuật toán hoặc domain Parallel thứ hai.

## Kiến trúc editor và popup tập trung

- `ui/function_editor/strategies/parallel.py` là binding native-free giữa
  operation/zone/resource snapshot và presentation primitives.
- `CamWorkspace` tạo production session và là application coordinator cho
  Preview, Apply, Calculate, selection và project lifecycle.
- Widget không đọc SQLite, không publish artifact và không giữ `TopoDS_Face`.
- OCP tessellation/contact projection và safety calculation chạy trong
  `ParallelFinishingTask`; progress/cancel được chuyển về UI bằng signal.
- `CAMFunctionPopupHost` là popup modeless singleton của Workspace; cột trái chỉ
  chọn bằng single-click và double-click/Enter mới mở editor. Facing, Planar
  Face Facing, Contour, Pocket, Drilling, Tapping, Reaming, Boring và Parallel
  đều dùng cùng host, không còn mở production editor trong panel phải.
- Popup giữ footer cố định, không có horizontal scrollbar, clamp theo work area,
  có dirty-switch Apply/Discard/Continue và một child-popup slot.
- Shared `CAMPopupDensityPolicy` áp dụng đồng nhất cho đủ chín editor: popup
  responsive 587×630, 624×702 và 672×778 tại work area 1366×768, 1600×900 và
  1920×1080; không mở maximized/full-screen và không thay font toàn ứng dụng.
- Font body 9–10 pt, section 10–11 pt, title 11–12 pt; margin 8 px, section 6 px,
  row 3 px, control 27 px, button 29 px và table/tree row 26 px theo logical
  metrics. DPI 125/150 do Qt/native scaling, không scale hai lần.

## Basic và Advanced

Basic chỉ yêu cầu Geometry, Tool, hồ sơ chất lượng, tóm tắt tự động và minh họa;
không có ô nhập số. Advanced đóng mặc định và mới chứa các override Direction,
Cut Parameters, Levels/Linking, feed, maximum segment length cùng summary/
threshold thực sự được algorithm dùng. Safety chi tiết mở bằng child popup,
không chiếm Basic trước Calculate. Không có Expert rỗng và không có safety
override.

Basic dùng responsive grid hai cột theo logical width khi minimum size hint cho
phép. Hàng đầu ghép Hình học/Tool, hàng hai ghép Chất lượng/Minh họa và hàng cuối
là sáu ô tóm tắt tự động theo hai cột. Ở native DPI 100%/125% và work area đủ,
nội dung Basic cùng footer fit không cần cuộn dọc hoặc ngang. Khi logical width/
height quá thấp, đặc biệt DPI 150%, layout fallback một cột và chỉ vùng content
cuộn dọc `AsNeeded`; footer vẫn cố định và không có cuộn ngang. Advanced đóng
không giữ spacer/body; khi mở có thể cuộn và tiếp tục dùng grid nếu đủ rộng.

Geometry hỗ trợ Select/Add, Reselect, Remove và Clear trên draft. Face được lưu
bằng `CamSurfaceReference`/`GeometryReference`; persistent selector được dùng để
khử trùng lặp và giữ identity đã Apply. Selection overlay/direction preview là
session-only, không persist và không thay CAD nguồn.

Direction preview U/V/W và minh họa Parallel được vẽ bằng widget vector
session-only, debounce theo draft angle/ordering/linking/quality; thao tác này
không tessellate, không chạy Calculate và tự cleanup khi đóng editor.
Minh họa mặc định thu gọn 110 logical px ở 1366×768 và tăng tối đa 140–150 px
trên màn hình lớn, có Mở rộng/Thu gọn minh họa và Phóng to; popup thấp tự thu gọn để
footer cố định vẫn thấy.

`IllustrationViewport` render fit-inside theo aspect ratio của registry, căn
giữa và scale X/Y đồng nhất. Cả chín operation có semantic Tool, phôi/bề mặt,
hướng chuyển động và vùng gia công riêng. Parallel có bảy state ID trực quan;
Boring dùng cutaway giữa mặt trước, quay quanh tâm và mũi tên tiến xuống Z, không
có mũi tên ngang hai chiều. Caption nằm ngoài geometry và giữ tiếng Việt trong
tooltip/accessibility ở compact state. Ordering one-way/zigzag và linking
direct/retract dùng renderer, semantic metadata và fingerprint riêng.
Expanded/child có legend cut–rapid–hướng cắt–rút/tiếp cận; child dùng
`Đóng minh họa`, Esc và focus restore về `Phóng to`.

Tool chỉ liệt kê ball-end assembly. UI hiển thị diameter, ball radius, cutting
length, shank và ba semantics holder: verified; declared absent (holder vẫn
unverified); reference missing/invalid (UNKNOWN/FAILED, không READY).
Operation Manager cung cấp action tạo bundle ball-end/holder/milling machine cơ
bản theo application service để một project MM mới có resource được hỗ trợ.

Mặc định Direction dùng chiều chính của hộp bao mặt/vùng được chọn trong Setup;
nếu thiếu evidence, UI ghi rõ cần xác nhận và dùng Setup X. Stepover, chordal
tolerance, part-normal allowance, One-way/Zigzag và linking được policy tự động
tính từ hình học, dao, Setup và profile Nhanh/Cân bằng/Chất lượng cao. Advanced
cho phép override riêng; linking vẫn chỉ cho phép retract-between-segments.
Clearance, Retract và Link Clearance tiếp tục được lưu trong payload v1.

## Safety và clearance

Safety Summary hiển thị trạng thái bằng text, algorithm, short report hash,
checked/unverified components, holder state, scope, diagnostic summary,
Simulation/Post gate. Dialog chi tiết có code, severity, pass, segment, motion,
component, geometry, closest distance, penetration, occurrence count và message;
không dump JSON hoặc exception thô.

`contact_tolerance` và internal detection threshold là giá trị kiểm tra hình
học, không phải certified machining clearance. Operation Clearance/Retract là
motion inputs. Mọi trạng thái đều ghi rõ:
`Machine-ready Clearance: Not verified` (`machine_ready_clearance_verified=false`).

## Lifecycle và versioning

- Preview dùng draft, chỉ tạo summary; không commit, không publish và không giả SAFE.
- Apply validate rồi thay đồng thời operation snapshot và CAM 3D zone trong
  project service, tăng revision, làm artifact stale và không tự Calculate.
- Calculate chỉ nhận applied state, stage token COMPUTING, chạy worker và chỉ
  commit kết quả khớp generation/input fingerprint (latest-wins).
- Cancel editor bỏ draft; cancel calculation cooperative và không publish partial.
- SAFE + current algorithm v3 + safety hash/scope hợp lệ mới READY. UNSAFE,
  UNKNOWN, CANCELLED, FAILED và v2/stale đều fail-closed.
- Strategy payload vẫn version 1; project database vẫn schema v4. Save/Open,
  Recovery/Save-As đi qua project lifecycle hiện có; worker/UI state không persist.

## Operation Manager, Simulation và Post

Menu Add có category CAM 3D và New Parallel Finishing. Select/open, rename,
enable/disable, calculate/cancel, duplicate và delete dùng command hiện có.
Duplicate tạo operation/node/geometry-input/zone identity mới, revision 0,
artifact MISSING và không sao chép safety READY/hash.

Tên loại nguyên công được Việt hóa tại presentation layer cho đủ chín editor;
strategy/enum/persistence không đổi. Operation Manager ưu tiên custom name, dùng
fallback tiếng Việt khi còn default, row hai dòng compact (tên; loại + Tool),
badge 70 px, tree indentation 10 px, tooltip đầy đủ và không có horizontal
scrollbar. Khi viewport hẹp, indentation giảm còn 6 px và badge tối đa 64 px để
ưu tiên tên dòng chính; delegate tô nền selection rõ trước khi vẽ text trắng.
Popup title,
header, New Operation menu, context/summary, tooltip và accessibility dùng cùng
mapper. Summary popup dùng tối đa hai dòng, ưu tiên số, đơn vị và trạng thái
Tự động/Tùy chỉnh trước Tool/hình học dài.

Operation status phân biệt NOT CHECKED, CANDIDATE, SCOPE CHECKED, UNSAFE,
UNKNOWN, FAILED, CANCELLED, SAFETY STALE và DISABLED bằng text. “SCOPE CHECKED”
chỉ có nghĩa verified within declared scope, không phải machine safe.

Simulation chỉ mở với current READY + SAFE algorithm-v3 artifact. Parallel
production Post hiện chưa hỗ trợ nên luôn fail-closed; editor không tạo G-code.

## Kiểm thử và GUI review

Focused tests bao phủ construction, binding, invalid draft preservation,
geometry/tool/holder semantics, SAFE/UNSAFE/UNKNOWN, artifact v2 gate,
responsive layout, Apply/Save/Open/Duplicate và project calculation gateway.
Regression giữ Stage 8A.2.1, 8A.2.2, Stage 9A.6, Operation Manager, Simulation,
Post và project persistence.

Native review harness: `tests/manual_stage8a2_3_parallel_editor.py` fail sớm nếu
không dùng Windows QPA/font native, tạo temp project deterministic và sinh 19
state screenshots cùng montage tại
`reference_private/DERIVED/UI_STAGE_8A2_3/` (Git-ignored).

Harness `tests/manual_stage8a2_3_auto_editor.py` tạo thêm 15 trạng thái
automatic/manual/invalidation cùng montage tại
`reference_private/DERIVED/UI_STAGE_8A2_3_AUTO/` (Git-ignored). Chính sách và kế
hoạch mở rộng nằm trong `CAM_AUTOMATIC_PARAMETER_UX_POLICY.md` và
`CAM_AUTOMATIC_PARAMETER_MIGRATION_PLAN.md`.

Kiến trúc popup và quy tắc minh họa nằm trong
`CAM_FUNCTION_POPUP_UX_ARCHITECTURE.md` và
`CAM_FUNCTION_ILLUSTRATION_GUIDELINES.md`. Gói review popup được tạo tại
`reference_private/DERIVED/UI_STAGE_8A2_3_POPUP/`, gồm 24 ảnh trạng thái và một
montage, chụp từ đúng các cửa sổ native HMS để không phụ thuộc ứng dụng ngoài
đang che desktop; thư mục tiếp tục giữ Git-ignored.

Gói compact review mới nằm tại
`reference_private/DERIVED/UI_STAGE_8A2_3_POPUP_COMPACT/`, gồm 24 ảnh trạng thái
và `UI_STAGE_8A2_3_POPUP_COMPACT_MONTAGE.png`. Harness kiểm tra footer, không có
horizontal scrollbar, illustration collapsed/expanded, child popup, đủ editor,
nhãn tiếng Việt, work-area ratio và native Windows QPA. Geometry/size chỉ là UI
preference `QSettings`, không thay algorithm v3, payload v1 hoặc SQLite schema v4.
Ảnh High DPI chạy bằng process Qt native riêng ở scale 125%/150% và assert
`devicePixelRatioF`; QA cuối đạt 320 focused, 1447 passed/2 deselected toàn repo,
static/runtime untranslated đều bằng 0.

Gói one-screen/aspect-ratio review nằm tại
`reference_private/DERIVED/UI_STAGE_8A2_3_GRID_ILLUSTRATIONS/`, gồm 24 ảnh yêu
cầu và `UI_STAGE_8A2_3_GRID_ILLUSTRATIONS_MONTAGE.png`. Harness native Windows
kiểm tra Basic hai cột không cuộn ở 1366/1600, footer, Advanced, fit-inside wide/
tall/zoom, đủ chín operation, bảy Parallel state, Boring semantics và nhãn tiếng
Việt; kết quả đã được hợp nhất vào gói duyệt cuối được người dùng chấp thuận.

QA sau one-screen grid đạt 287 focused và 1456 passed/2 deselected toàn repo;
static audit 1043 chuỗi và runtime audit 11170 chuỗi qua 20 trạng thái đều có
untranslated bằng 0. `pip check`, compileall và native nine-editor no-scroll
hard gate đều đạt.

Final review harness `tests/manual_stage8a2_3_final_gui.py` tạo đúng 24 ảnh trạng
thái và `UI_STAGE_8A2_3_FINAL_GUI_MONTAGE.png` tại
`reference_private/DERIVED/UI_STAGE_8A2_3_FINAL_GUI/`. Package chứng minh chín
title tiếng Việt, row Operation Manager dễ phân biệt, bốn semantics Parallel,
legend, child wording/focus restore, summary dài, Basic no-scroll, footer,
fit-inside và native DPI 125%/150%. Harness kết thúc sạch với 25 artifact; suite
khóa đạt 235 focused và 1461 passed/2 deselected toàn repo. Audit đạt 1046 chuỗi
tĩnh và 11150 chuỗi runtime qua 21 trạng thái, untranslated bằng 0; `pip check`
và compileall đều đạt. Kết quả đã được hợp nhất vào gói duyệt cuối được người
dùng chấp thuận; không bump algorithm v3, payload v1 hoặc SQLite schema v4.

Clipping/DPI correction audit xác định ba nguyên nhân bố cục: scroll content và
section từng tự báo size hint đã bị cap theo viewport; `QGridLayout` giữ stretch
cột/hàng cũ sau reflow; field compact vẫn ép control/nút vào hàng không đủ
logical width. Bản sửa bỏ height cap, reset stretch/minimum cũ, dùng size hint
theo font native, reflow field theo width thật và giữ vertical scrollbar
`AsNeeded`. Header/disclosure chỉ compact metadata phụ theo work area; nội dung
đầy đủ còn trong tooltip/accessibility.

Summary dùng hai cột khi đủ rộng và label-trên/value-dưới ở một cột, giữ trọn
Hướng chạy dao, Bước ngang, Dung sai, Lượng dư, Thứ tự cắt và Liên kết. Test
bounds thật kiểm tra parent/viewport geometry, sibling overlap, minimum size,
baseline tiếng Việt, footer và scroll range trên matrix 1366/1600/1920 @100%,
1600/1920 @125%, 1600/1920 @150% và DPR 2.0. Native 100%/125% fit không cuộn khi
đủ chỗ; 150% dùng cuộn dọc ngắn thay vì cắt nội dung.

Harness `tests/manual_stage8a2_3_dpi_clipping_fix.py` tạo 19 ảnh cùng
`UI_STAGE_8A2_3_DPI_CLIPPING_FIX_MONTAGE.png` tại
`reference_private/DERIVED/UI_STAGE_8A2_3_DPI_CLIPPING_FIX/`. QA cuối đạt
235 focused và 1470 passed/2 deselected; static audit 1046 chuỗi và runtime
audit 11145 chuỗi qua 21 trạng thái đều untranslated bằng 0. `pip check` và
compileall đạt; algorithm v3, payload v1, SQLite schema v4, dependency và icon
không đổi. Người dùng đã duyệt package và Stage 8A.2.3 được đánh dấu
**COMPLETED**.

## Giới hạn

Chỉ ball-end, fixed three-axis, selected trimmed BRep faces, One-way/Zigzag và
conservative linking. Chưa có flat/bull end, 5-axis, automatic holder avoidance,
rest machining, adaptive cusp, universal gouge-free, machine-ready clearance
certificate hoặc production Post. Icon Stage 9A.I1 tiếp tục **DEFERRED**.
