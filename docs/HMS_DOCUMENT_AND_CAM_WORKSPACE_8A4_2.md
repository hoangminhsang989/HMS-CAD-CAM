# Stage 8A.4.2 — Tài liệu HMS và workspace CAM

## Trạng thái

Stage 8A.4.2 đã **COMPLETED** trên baseline
`88aff7829244c610d68d099de3147c8bb17f2443`. Không stage kế tiếp hoặc stage đa
ngôn ngữ nào được bắt đầu.

## Domain và ranh giới service

`DocumentMode` có đúng hai giá trị typed:

- `CAD_DOCUMENT`: một tài liệu CAD/3D đơn lẻ;
- `CAM_PROJECT`: một workspace dự án CAM.

`WorkspaceState` mang identity, display name, physical/source path, suggested
save directory, dirty/read-only, thời điểm mở, session ID, format version và
lifecycle generation. UI chỉ đọc typed mode và text tiếng Việt tương ứng; UI
không truy cập ZIP hoặc SQLite trực tiếp.

`ProjectService` là application boundary chung cho Open, Save, Save As, Close,
Autosave, Recovery và chuyển mode. Import hình học tiếp tục dùng worker/generation
guard hiện có trong `CadUiController`.

## Contract file `.HMS` đơn lẻ

`.HMS` trong `CAD_DOCUMENT` là ZIP container nội bộ, nhưng người dùng chỉ thao
tác trên một file. Đây không phải container dự án CAM; `CAM_PROJECT` luôn là
workspace thư mục. Container v1 gồm:

- `manifest.json`;
- `document.json`;
- `geometry/model.<định dạng đang hỗ trợ>`;
- `cad/metadata.json`;
- `cad/display-state.json`;
- `checksums.json`.

Serialization JSON là UTF-8 canonical, entry được sắp xếp và timestamp ZIP cố
định. Nội dung không đổi tạo cùng byte/checksum. Save ghi sibling temporary,
flush/fsync, tự đọc kiểm checksum rồi atomic replace; lỗi giữ file cũ và dirty
state.

Loader chặn path tuyệt đối, `..`, backslash escape, drive prefix, symlink, entry
trùng, archive mã hóa, số entry/tổng size/size từng entry và compression ratio
vượt policy. Loader không dùng `extractall`; chỉ ghi geometry đã xác minh vào
runtime root do service sở hữu.

Container không chứa `project.db`, CAM artifact production, Simulation result,
G-code, READY/SAFE claim, machine-ready state, Tool/Post/machine definition
dùng chung.

## Tên file `.HMS`

Tên file được giữ nguyên tiếng Việt, Unicode, dấu cách, ngoặc, `_` và ký tự
Windows hợp lệ. Không bỏ dấu hoặc chuyển sang ASCII. Validation chặn:

- `< > : " / \ | ? *` và control character;
- dấu chấm/dấu cách cuối;
- `CON`, `PRN`, `AUX`, `NUL`, `COM1..COM9`, `LPT1..LPT9`.

Lần Save đầu mở Save As và gợi ý thư mục source. Sau khi lưu, Save ghi file hiện
tại; Save As gợi ý thư mục chứa file `.HMS`. Fallback lần lượt là thư mục `.HMS`
hiện tại, source/last-valid directory và application-safe directory.

## Open và kéo-thả

Open dialog, các action mở định dạng hiện có và drag/drop đều gọi
`ProjectUiController.request_open_path`. Command thực hiện lifecycle
Save/Discard/Cancel, capability check, container/source validation rồi chuyển
`PreparedDocumentOpen` cho importer nền hiện hành.

Mode chỉ commit sau khi importer và viewport nhận hình học thành công. Lỗi giữ
workspace/tài liệu trước đó. Drag nhiều file bị từ chối rõ ràng và không trộn
mô hình. Vùng thả production là `Thả tệp để mở trong HMS`, không nhận focus và
ẩn ngay khi drag rời/drop.

Định dạng hiện có trong pipeline: STEP/STP, BREP/BRP, IGES/IGS và STL. Stage
không tuyên bố translator cho DWG, Parasolid, ACIS hoặc CATIA.

## Contract workspace CAM

Người dùng phải chọn thư mục cha, nhập tên hiển thị và xem preview trước khi
tạo. Tên hiển thị Unicode giữ trong manifest; tên vật lý được NFKD/bỏ dấu,
space/special chuyển thành một dấu `-`, chỉ còn `[A-Za-z0-9-]`.

Toàn bộ segment của parent path phải dùng policy an toàn, không UNC/network,
không read-only, có quyền tạo child và tổng target path không vượt 240 ký tự.
HMS không đổi tên/move parent bên ngoài.

Dự án mới:

```text
Project-Root/
├── manifest.json
├── project.db
├── source/
├── working-geometry/
├── autosave/
├── backups/
├── temp/
├── replaced/
└── incoming-geometry/
    ├── staging/
    ├── pending/
    ├── applied/
    ├── rejected/
    └── failed/
```

SQLite giữ schema v4. Loader vẫn đọc project thư mục `.HMS` cũ với
`project.hms.json`; cả `replaced` và `.replaced` được nhận diện, không tự xóa
hoặc migrate phá dữ liệu.

File source bên ngoài không đổi tên. Bản sao nội bộ dùng tên ASCII/hyphen và
hậu tố `-2`, `-3` deterministic khi trùng. Manifest lưu original filename/path,
internal filename, SHA-256, imported time, importer, units, geometry type,
read-only/provenance và working geometry path.

`working-geometry/` chứa bản làm việc không nén có fingerprint/version/stale
metadata. Representation hiện được khai báo thận trọng là
`unpacked-source-compatible`; không tuyên bố đã có một normalized OCCT format
chung. Mesh display cache không được coi là exact CAM geometry.

## Conversion, atomicity và recovery

`Tạo dự án CAM từ tài liệu hiện tại` dùng geometry/provenance của source hoặc
container đang mở. Creator tạo sibling staging, cấu trúc, source copy, working
geometry, manifest và database; chỉ publish sau validation. Application mode
chỉ đổi sau khi acquire session/activation thành công. Activation lỗi sẽ xóa
đúng workspace mới có project identity khớp; tài liệu/source ban đầu được giữ.

Project Save tiếp tục transaction SQLite, manifest atomic và artifact store
theo contract hiện có. Autosave/recovery chấp nhận cả `manifest.json` mới và
`project.hms.json` legacy. Project root là write boundary; external export chỉ
xảy ra khi người dùng chọn rõ đường dẫn.

## Nạp 3D từ tài liệu HMS sang dự án CAM

Lệnh production `Nạp 3D mới cho dự án CAM` chỉ khả dụng khi workspace hiện
tại là `CAD_DOCUMENT`, có exact geometry, đã lưu thành file `.HMS` và không có
Save/lifecycle transition đang chạy. Lệnh không đóng tài liệu, không đổi mode,
không sửa hoặc tự lưu `.HMS`. Nếu gọi khi chưa lưu, UI chỉ cho `Lưu` hoặc `Hủy`.

Dialog `Chọn dự án CAM` hiển thị project root, tên phát hiện, Project ID,
workspace version, đường dẫn đầy đủ và kết quả validation. Target phải là CAM
folder workspace thật có `manifest.json`, SQLite v4, Project ID nhất quán,
structure/path policy/quyền ghi/dung lượng hợp lệ và không có migration,
recovery, corrupt hoặc APPLYING transaction chưa giải quyết. Active session
lock không chặn sender; sender chỉ được ghi request atomic vào inbox, không sửa
`project.db`, `source/` hoặc `working-geometry/`.

Sender tạo `incoming-geometry/staging/request-<UUID>.tmp`, ghi metadata,
exact payload, preview directory và checksums, flush, validate lại Project ID
và checksum rồi atomic rename sang `pending/request-<UUID>`. Scanner không đọc
`staging`. Lỗi chỉ dọn staging request do chính sender sở hữu. Idempotency dùng
request ID, source document ID, geometry fingerprint, target project ID và
payload checksum; request `PENDING`, `DEFERRED` hoặc `APPLYING` tương đương bị
chặn, không tự gửi trùng.

Request typed có lifecycle:

```text
STAGING → PENDING ─┬→ DEFERRED ─┐
                  ├→ REJECTED  │
                  └→ APPLYING ─┴→ APPLIED
                                  └→ FAILED
```

UI chỉ render text tiếng Việt: Đang chuẩn bị, Chờ xử lý, Để sau, Đang cập
nhật, Đã áp dụng, Đã bỏ qua và Lỗi. `requested_action` ban đầu là
`UNSPECIFIED`; không có mặc định Replace.

## Phát hiện và thông báo

Sau khi lock validation, crash recovery, database open và geometry load hoàn
tất, controller quét inbox bằng worker. `QFileSystemWatcher` và polling 2,5
giây chỉ là tín hiệu kích hoạt; mỗi lần mở project vẫn scan filesystem lại.
Notification bar/dock không modal, `NoFocus`, không thay selection/document,
không che viewport, ribbon hoặc status bar và không tự mở panel. Khi có popup
khác, chỉ badge thay đổi. `Để sau` không nhắc lại trong cùng session nhưng
request vẫn ở notification center; `Bỏ qua` cần xác nhận, chuyển sang
`rejected/` và giữ dữ liệu audit.

`Xem thay đổi` mở panel không modal với source `.HMS`, thời gian, identity và
fingerprint rút gọn, units, representation, topology counts, asset hiện tại,
asset mới, deterministic match, operation dự kiến stale và cảnh báo
Simulation/Post. Không có lựa chọn apply mặc định.

## Apply, stale và recovery

Ba lựa chọn explicit:

- `Thêm làm mô hình mới`: tạo source ID mới, giữ model/operation hiện tại;
- `Thay thế mô hình hiện tại`: bắt buộc chọn source ID đích;
- `Cập nhật phiên bản mô hình tương ứng`: chỉ bật khi source document ID,
  container ID, units và representation tạo đúng một lineage match; không đoán
  theo tên file.

Apply atomic-claims request sang `staging/*.applying`, kiểm checksum, exact
representation, Project ID và units. Service tạo backup manifest/database và
working geometry cũ trong project root, ghi transaction evidence, copy source
và working asset qua file tạm rồi atomic commit, persist manifest/SQLite, đánh
dấu đúng operation phụ thuộc source ID bị `GEOMETRY_CHANGED`, stale
Simulation/Post liên quan và chuyển request sang `applied/`. Service không tự
Calculate, Simulation, Post hoặc sao chép READY/SAFE/G-code từ tài liệu.

Lỗi checksum/units/persistence/atomic replace/manifest thực hiện rollback,
khôi phục metadata và mô hình cũ, chỉ xóa asset mới khớp checksum/evidence rồi
chuyển request sang `failed/`. Khi mở lại, request `APPLYING` không có mutation
được trả về `PENDING`; transaction đã persist đủ evidence được hoàn tất sang
`APPLIED`; transaction chưa persist được rollback. Evidence path/identity/
checksum sai làm project fail-closed, không đoán.

## Localization, accessibility và giới hạn

Production UI dùng tiếng Việt, accessible name/description cho field, validation,
overlay và button. Dialog không ghi đè project trùng và không tự chọn project
root.

Package review native Windows Git-ignored
`reference_private/DERIVED/UI_STAGE_8A4_2_HMS_CAM_WORKSPACE/` đã được người
dùng duyệt. Contract package đạt 43 file: 30 PNG có 30 hash riêng, 12 JSON và
1 Markdown; không có file rỗng.

QA khóa cuối đạt **136 passed** cho focused Stage, **113 passed** cho regression
liên quan và **1690 passed, 2 deselected** cho toàn repository. `pip check`,
`compileall src tests tools`, audit GUI tiếng Việt với toàn bộ nhóm lỗi bằng 0
và `git diff --check` đều đạt. SQLite giữ schema **v4**; loader tiếp tục tương
thích project thư mục `.HMS` legacy và không migrate phá dữ liệu.

Geometry transfer không phải chứng nhận an toàn CAM hoặc máy. Apply không tự
Calculate, Simulation hoặc Post, không tái sử dụng claim READY/SAFE/G-code và
không xác nhận machine-ready clearance.

Ngoài phạm vi:

- hệ thống đa ngôn ngữ và runtime chọn ngôn ngữ;
- ProgramData/install layout;
- importer đa định dạng đầy đủ;
- Export 3D và export version settings;
- three-step CAM workflow;
- multi-family Tool algorithm;
- toolpath colors;
- Program Templates;
- Production Post mới, G-code production hoặc machine-ready certification.
