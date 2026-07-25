# HMS CAD/CAM — Stage 8A.4.4: kiến trúc cài đặt và dữ liệu

## Trạng thái và ranh giới

Stage 8A.4.4 đang `IN PROGRESS` trên baseline `3b70b5c`. Mục tiêu là tạo
contract runtime và installer handoff ổn định; stage này không tạo MSI/EXE
installer hoàn chỉnh, không yêu cầu UAC, không sửa registry và không có updater.

Bốn scope không được nhập nhằng:

1. `INSTALL`: mã và resource cài đặt, runtime chỉ đọc.
2. `MACHINE_SHARED`: thư viện/policy dùng chung toàn máy.
3. `USER_ROAMING`/`USER_LOCAL`: preference và dữ liệu runtime riêng user.
4. `DOCUMENT`/`CAM_PROJECT`: dữ liệu do người dùng chọn, nằm ngoài ba root trên.

## Application paths contract

| Scope | Production root | Quyền runtime |
|---|---|---|
| Install | `C:\HMS-CADCAM\` | Chỉ đọc; installer sở hữu |
| Machine shared | `C:\ProgramData\HMS-CADCAM\` | Đọc hoặc đọc/ghi theo ACL; không fallback AppData |
| User roaming | `%APPDATA%\HMS-CADCAM\` | Người dùng hiện tại |
| User local | `%LOCALAPPDATA%\HMS-CADCAM\` | Người dùng hiện tại |
| Document/CAM project | Người dùng chọn | Root tài liệu/dự án là boundary |

`ApplicationPathsService` trả `ResolvedAppPath` typed với kind, scope, path vật
lý/display, exists/readable/writable/creatable, owner, source, layout version,
diagnostic và status. Production dùng Windows Known Folder API; không đọc CWD,
không nhận environment override và không ghép tên user. Test/review phải khai
báo `TEST_SANDBOX`/`REVIEW_SANDBOX` cùng đủ bốn root tuyệt đối.

Install tree gồm `HMS-CADCAM.exe`, `runtime`, `resources`, `plugins`,
`translations`, `licenses`; runtime không ghi log/config/cache/library/project
vào đây. Đường dẫn cài mặc định không dùng Program Files để giữ path ngắn,
ASCII và không dấu cách.

## ProgramData và AppData

ProgramData có chính xác tám thư mục ASCII, không dấu cách:

- `Tool-Library`: Tool/Holder dùng chung, không phải snapshot project.
- `Program-Templates`: stage này chỉ có location, chưa có behavior.
- `Posts`, `Machines`, `Materials`: resource dùng chung; không thay Post safety
  hoặc chứng nhận machine-ready.
- `Config`: machine policy và `storage-layout.json`.
- `Schemas`: schema/catalog dùng chung; không bump project SQLite.
- `Backups`: chỉ backup machine-wide, không thay project `backups/autosave`.

Roaming AppData có `Config`, `UI-State`, `Profiles`; Local AppData có `Cache`, `Logs`,
`Temp`, `Crash`. Locale QSettings, recent files và workspace layout tiếp tục là
preference user. Cache có thể dọn qua kiểm tra containment; log/temp/crash không
bị thao tác dọn cache. AppData không chứa Tool-Library/Posts/Machines/Materials,
`project.db`, geometry/G-code production hoặc `.HMS` production.

## Bootstrap và layout manifest

`StorageBootstrapService` chỉ inspect install root; không tự tạo/ghi install.
ProgramData root production thiếu thì trả `ADMIN_INSTALL_REQUIRED`, không tự
elevate hoặc fallback. Khi root/ACL cho phép, service tạo subfolder còn thiếu.
AppData subfolder có thể được runtime tạo.

Bootstrap preflight path security, tạo directory transaction, ghi manifest
atomic cùng volume, đọc lại/verify checksum rồi mới báo `READY`. Khi lỗi, chỉ
`rmdir` các thư mục vừa tạo và vẫn rỗng; không xóa dữ liệu có sẵn. Lần chạy lại
idempotent. Manifest schema/layout v1 có application family, timestamp,
install/program-data reference, danh sách required/optional, migration state,
writer version và checksum; không chứa secret hoặc project path cá nhân.

## Permission, read-only và path security

Status typed phân biệt ready, missing, read-only, read denied, not creatable,
unsafe, unsupported và administrator installation required. ProgramData đọc
được nhưng không ghi được vẫn được dùng read-only; thao tác sửa machine library
bị chặn, không có file shadow trong AppData.

Mọi write target phải tuyệt đối, nằm lexical trong root và vượt qua policy về
traversal, UNC, reserved Windows name, trailing dot/space, invalid/control char,
length, case collision, file/directory collision và parent writable. Mọi symlink,
junction/reparse point trong chuỗi target đều fail-closed. Staging và target phải
cùng volume để `os.replace` atomic; service không gọi `resolve()` để vô tình đi
theo link ngoài root.

## Config precedence, concurrency và backup

Precedence là user preference hợp lệ → machine config → built-in install
resource read-only → fallback code định nghĩa. Key machine bị khóa không thể bị
user override; project-specific state không lấy từ machine config.

`ResourceFileLock` khóa riêng Tool Library, Posts, Machines, Config hoặc
Materials, có timeout, PID/session/token/timestamp và stale-lock detection.
Không khóa toàn bộ ProgramData khi chỉ sửa một resource. Atomic writer dùng temp
cùng directory/volume, fsync, replace và read-after-write checksum.

Trước khi thay machine config đã tồn tại, `MachineBackupService` tạo payload và
metadata có timestamp, resource type, checksum, source version/size, rồi áp dụng
retention theo resource. Backup `.HMS`/`project.db` bị chặn. Nếu publish config
lỗi, byte cũ được phục hồi từ backup đã verify.

## Migration foundation

`LegacyMigrationService` nhận location typed do caller xác định, scan và tạo
`MigrationItem` gồm source/target/resource/size/checksum/conflict/action/status.
Duplicate được skip; conflict khác byte bị block. Copy đi qua staging, checksum,
atomic no-overwrite rename và read-back; source cũ luôn được giữ. Không tự scan/move/xóa
`.HMS`, `project.db`, project source/autosave/toolpaths/nc hoặc G-code.

## UI và startup

`Cài đặt → Hệ thống → Vị trí dữ liệu` là dialog modeless có bốn nhóm install,
machine shared, user và document/project. UI hiển thị production target/status/
permission/layout, cho inspect, initialize phần được phép, mở folder và dọn đúng
Cache; không có field đổi root. Physical path/hash/ID/schema/executable không
dịch, còn label/status/error/tooltip/accessibility hỗ trợ VI_VN, EN_US, KO_KR.

Startup dùng `StorageNotificationBar` không modal. Cảnh báo cho phép xem chi
tiết, kiểm tra lại hoặc đóng; chỉ operation phụ thuộc shared data mới bị chặn.
Runtime không tự prompt UAC hoặc đổi path.

## Installer handoff và giới hạn

Installer tương lai phải tạo `C:\HMS-CADCAM\`, ProgramData root/tám subfolder,
đặt ACL phù hợp, cài executable/resources và để install read-only cho runtime.
Kiến trúc không yêu cầu installer đổi runtime path contract.

Chưa triển khai MSI/EXE installer, code signing, auto updater, service, shortcut,
file association/uninstall, importer đa định dạng đầy đủ, Export 3D/version,
CAM workflow ba bước, Tool đa họ, màu toolpath, Program Templates behavior hoặc
Production Post mới. SQLite v4, `.HMS`, project-root, Tool payload, geometry
transfer, Z-Level/Parallel, Simulation/Post safety và machine-ready không đổi.

## Review và QA

Package Git-ignored tại
`reference_private/DERIVED/UI_STAGE_8A4_4_STORAGE_ARCHITECTURE/` phải có đúng
40 PNG, 16 JSON và 1 Markdown. Capture dùng native Windows QPA, production
widget/model/service với sandbox injection; production target chỉ preview.
Package phải giữ source/hash mismatch, missing/fallback/mixed/raw key, path escape,
project-boundary violation, production-machine write, clipping/tofu/accessibility
missing bằng 0. QA cuối đạt **122 passed** focused, **194 passed** regression và
**1870 passed, 2 deselected** toàn dự án. `pip check`,
`compileall src tests tools` và `git diff --check` đều PASS. Stage chỉ được đánh
dấu `COMPLETED` sau khi người dùng duyệt.

Backup/restore và user-profile dùng lại đúng typed path, atomic writer, resource
lock và permission model của kiến trúc này. Contract chi tiết nằm tại
`docs/HMS_BACKUP_RESTORE_8A4_4.md` và `docs/HMS_USER_PROFILES_8A4_4.md`.
