# R239 — Owner-operated production activation preparation

R239 hoàn thiện **Giai đoạn 1 — Chuẩn bị kích hoạt**. Mọi API trong
`production_workflow.py` chỉ đọc target, xác minh fingerprint, tạo package và
quản lý quyết định/cửa sổ. Không API nào thay thế Post sản xuất.

## Chuỗi quyết định

`Post đã duyệt -> snapshot target -> evidence freshness -> rollback ready ->
diff/package review -> quyết định owner -> activation window hữu hạn`

Giai đoạn 2 luôn hiển thị `CHƯA ĐƯỢC PHÉP KÍCH HOẠT` nếu thiếu quyết định mới
hoặc cửa sổ hợp lệ. Việc mở dialog không phải approval; không radio approval
nào được chọn mặc định. Window bị invalidated khi target snapshot, candidate,
decision hoặc fingerprint load-bearing thay đổi và không thể dùng sau expiry
hoặc consume.

## Target là nguồn sự thật

`TargetSnapshot` chứa SHA, size, mtime nanosecond, filesystem identity,
permission assessment, expected parent và timestamp có timezone. Snapshot chỉ
là evidence read-only và trở thành stale nếu metadata hoặc bytes thay đổi.
Future helper phải lock rồi rehash ngay trước write; verifier R239 chỉ chứng minh
checks và luôn trả `write_performed=false`.

## Package và UAC

Package deterministic chứa exact parent/candidate bytes, generated NC evidence,
deployment/rollback plan, target snapshot, freshness, validation/regression,
exact diff và policy audit/recovery. Manifest cố định
`auto_activate_on_import=false`, state `NOT_ACTIVE_GLOBALLY`.

Nếu authority tương lai cho phép activation thực và cần elevation, chỉ sử dụng
standard Windows UAC (`Start-Process -Verb RunAs`). Không scheduled task,
service, PsExec, credential lưu sẵn hay bypass.
