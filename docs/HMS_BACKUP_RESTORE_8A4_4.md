# HMS backup/restore — Stage 8A.4.4

## Phạm vi và ranh giới

`.BAKUPHMS` là một file container nén ZIP-compatible do HMS quản lý. Đây là
bản sao của cài đặt giao diện, profile người dùng và dữ liệu thư viện được chọn;
không phải bản sao executable, bộ cài hay dự án. Người dùng tự chọn đường dẫn
đích và HMS không tự ghi đè file đã tồn tại nếu chưa xác nhận.

Các category typed gồm `USER_PROFILES`, `USER_INTERFACE`, `USER_SETTINGS`,
`KEYBOARD_SHORTCUTS`, `QUICK_ACCESS`, `RECENT_FILES`, `TOOL_LIBRARY`,
`HOLDER_LIBRARY`, `PROGRAM_TEMPLATES`, `POSTS`, `MACHINES`, `MATERIALS`,
`MACHINE_CONFIG` và `EXPORTABLE_SCHEMAS`. Sáu category đầu có scope
`USER_ROAMING`; tám category sau có scope `MACHINE_SHARED`. Recent files mặc
định bỏ chọn. Chọn tất cả chỉ chọn dữ liệu tồn tại, đọc được và exportable.

Không đưa project, `.HMS`, `project.db`, CAD geometry, CAM artifact, G-code,
autosave, cache, log, temp, crash, executable, DLL, font, credential, token hay
license secret vào container. Post chỉ được đọc/ghi như dữ liệu và không được
thực thi trong backup/restore.

## Format `.BAKUPHMS`

Container có `manifest.json`, `checksums.json` và resource dưới namespace
`profiles/` hoặc `machine-resources/`. Manifest schema/format v1 chứa backup ID,
application family, source/writer version, UTC timestamp, locale, category và
profile đã chọn, tổng size, compression/checksum algorithm, compatibility và
resource manifest. Mỗi resource có logical ID, category, scope, relative/container
path, size, SHA-256, resource version, required flag và dependency.

Reader kiểm magic/extension, family/version/schema, JSON, manifest/checksum,
duplicate/case collision, absolute/traversal path, reserved/trailing Windows
name, file/directory collision, symlink/special/reparse metadata, entry/size/
compression-ratio limit và unknown mandatory resource. Container hỏng hoặc
không hiểu bị chặn fail-closed; không extract thẳng vào root production.

## Tạo bản sao

Wizard năm trang cho chọn category/profile, ước tính size/resource, nơi lưu,
xác nhận và kết quả. Service acquire lock theo machine resource, thu snapshot,
ghi staging cùng volume, sắp xếp entry deterministic, ghi checksum, đọc/validate
lại rồi atomic publish. Cancel, write/disk/validation failure xóa đúng staging,
giữ nguồn và không để output 0 byte.

## Preview, conflict và restore

Wizard sáu trang chỉ validate khi người dùng chọn file; không tự restore. Preview
hiển thị resource/category/profile, compatibility, permission và conflict.
Conflict action typed là `KEEP_EXISTING`, `REPLACE`, `MERGE`,
`IMPORT_AS_COPY`, `SKIP`; mặc định conflict là giữ dữ liệu hiện tại. `MERGE` chỉ
được dùng cho category được khai báo và dữ liệu JSON object hợp lệ.

Restore kiểm quyền theo đúng scope, không fallback machine data sang AppData và
vẫn cho user resource hợp lệ tiếp tục khi machine resource bị chặn. Resource
được lock, backup trạng thái cũ, ghi atomic, đọc lại checksum và journal để
rollback. Lỗi giữa transaction phục hồi đúng resource đã đổi; source
`.BAKUPHMS`, project, `.HMS`, SQLite và active profile không bị thay đổi.

## Giới hạn chủ ý

Chưa có executable/installer/license/project backup, cloud sync, upload/download,
UAC tự động hay migration cho backup format khác v1. Program Templates mới là
dữ liệu lưu trữ; không có behavior tạo chương trình mẫu. Schema/catalog chỉ được
restore theo compatibility đã chứng minh; không có claim machine-ready.
