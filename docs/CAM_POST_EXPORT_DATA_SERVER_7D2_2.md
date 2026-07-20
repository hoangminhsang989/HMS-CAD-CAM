# Giai đoạn 7D.2.2 — NC File Export và Data Server Lifecycle

## Phạm vi

Giai đoạn này thêm service API không có UI để đưa một `PostResult` production còn current thành artifact NC do dự án quản lý, sau đó tùy chọn sao chép nguyên byte tới một thư mục filesystem. “Data server” v1 chỉ là thư mục local, ổ mạng đã map hoặc UNC path mà Windows đã cấp quyền; HMS không dò server, không lưu credential, không map ổ đĩa và không dùng shell/giao thức truyền file.

Chỉ `PostResult` trạng thái `PUBLISHED`, có production profile/provenance khớp, Toolpath/operation còn `VALID`, input fingerprint và Simulation gate còn current mới được xuất. Export service không lower, format hoặc chỉnh sửa chương trình lần nữa.

## Layout project-managed

```text
TEN_DU_AN.HMS/
├── nc/
│   └── <filename-da-kiem-tra>.fn
└── post/
    ├── manifest.json
    └── metadata/
        └── <nc-artifact-uuid>.json
```

`manifest.json` và sidecar dùng contract JSON UTF-8 có `format_version=1`, JSON deterministic và chỉ chứa relative POSIX path. Chúng ghi lại project/operation/Toolpath/PostResult IDs, toàn bộ fingerprint production cần thiết, profile/tool binding/program context, byte length, SHA-256, newline, encoding, extension, Post diagnostics/statistics và trạng thái `current`, `stale`, `missing` hoặc `tampered`. Timestamp và external absolute path không thuộc artifact identity.

## Contract byte `.fn`

Profile ROBODRILL 21i quyết định extension `.fn`, encoding UTF-8 tương thích ASCII, newline CRLF và không BOM. Bytes được lấy trực tiếp từ `PostResult.canonical_text`, phải khớp `output_checksum`, không đổi newline/số/comment, không thêm hoặc xóa dòng, timestamp hay output path. Mỗi lần ghi đều dùng temp trong cùng thư mục, `flush`/`fsync`, đọc lại kiểm tra length/SHA-256 rồi `os.replace`; temp được dọn khi thành công hoặc lỗi.

## Filename và path security

Tên file là input explicit và phải khớp `ProductionProgramContext.file_name`. Service loại traversal, separator, control character, ký tự cấm Windows, trailing dot/space, double extension, tên thiết bị `CON/PRN/AUX/NUL/COM1…/LPT1…` và tên quá dài. Profile là nguồn duy nhất của extension. Mọi managed path được canonicalize, kiểm tra nằm dưới project root; symlink/junction escape bị từ chối. External target không được trỏ vào `.git`, `src`, `tests` hoặc `source` trong project.

## Overwrite và atomic lifecycle

Mặc định `FAIL_IF_EXISTS`. `REPLACE_IF_SAME_ARTIFACT` chỉ thay file đã có sidecar/manifest hợp lệ với cùng artifact, project, operation và profile. `REPLACE_EXPLICIT` là lựa chọn rõ trong request. External/data-server file không bao giờ tự ghi đè; do external copy không có sidecar HMS, file đã tồn tại cần `REPLACE_EXPLICIT`.

Managed publish theo thứ tự output → sidecar → manifest; mỗi bước atomic và toàn transaction rollback về bytes cũ khi bước sau lỗi. Registry runtime chỉ publish sau stale recheck. External copy bắt đầu sau managed publish; lỗi mạng/quyền/checksum chỉ trả `EXTERNAL_FAILED`, không xóa artifact `.fn` trong project và không retry tự động.

## Save/Open/Save As/Autosave/Recovery

- Save flush/kiểm tra manifest hiện có, không chạy Post và không export data server.
- Open kiểm tra sidecar/path/checksum. Artifact missing/tampered/stale không làm hỏng việc mở toàn project; trạng thái được phân loại và load không làm project dirty. Manifest hỏng được ghi diagnostic runtime thay vì chặn project.
- Save As sao chép `post/` và `nc/`, kiểm tra checksum, đổi project identity và đánh dấu provenance cũ `stale`; không mang external target/path sang project mới.
- Autosave sao chép artifact/manifest vào chính snapshot workspace, không ghi ngược project gốc và không export ngoài.
- Recovery chỉ dùng artifact trong recovery snapshot workspace; temp/incomplete không được tham chiếu và external export không chạy lại.
- Project switch/Close hủy export token/registry nhưng giữ managed artifact. Operation bị xóa hoặc Toolpath recompute làm artifact liên quan `stale`; file ngoài project không bị xóa.

## Giới hạn còn lại

7D.2.2 chỉ hỗ trợ output production single-operation đã có từ 7D.2.1. Chưa có Post UI (chuyển sang 7D.2.3), multi-operation assembly, production Tapping, FTP/SFTP/HTTP/DNC, machine communication, tự chạy chương trình, stock removal hoặc 4/5-axis.

Manual smoke trên Windows:

```powershell
.\.venv\Scripts\python.exe tests\manual_stage7d22_export.py
.\.venv\Scripts\python.exe tests\manual_stage7d22_export.py --data-server Z:\ROBODRILL
```
