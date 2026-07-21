# Nhiệm vụ hiện tại — Checkpoint 7D.3.2

## Trạng thái

- `8555747` hoàn thành nền tảng Multi-operation Program Assembly 7D.3.1.
- `4d8deab` hoàn thành Program Assembly UI 7D.3.2 và tích hợp vào CAM
  workspace.
- Audit ngày 21-07-2026 xác nhận 7D.3.1/7D.3.2 chạy trên Python 3.14.6,
  worktree đầu vào sạch và toàn bộ 999 test đạt.

## Phạm vi đã hoàn thành trong 7D.3.2

- Danh sách operation theo `OperationId` và explicit order; Add/Remove/Move/Clear
  không tự tối ưu hoặc tự gom dao.
- Shared program context và per-operation T/H/D, safe Z, cutter compensation,
  tool comment có Apply atomic.
- Validation theo Job/Setup/Machine/profile, Toolpath provenance, Simulation
  gate, binding conflict và production Tapping fail-closed.
- Generate ngoài UI thread từ immutable request, stale/project-generation/
  fingerprint guard và không tự ghi file.
- Read-only preview dùng canonical production text, checksum, metadata,
  diagnostic và section navigation.
- Save Managed Artifact, explicit external export và Clear Managed Artifact dùng
  lại `NCExportService`/`NCArtifactStore`.
- Project switch/Open chỉ inspect state; không tự Generate hoặc external export.

## Chưa thuộc phạm vi đã hoàn thành

- Production Tapping.
- Automatic tool ordering/grouping hoặc tool-change optimization.
- Nhiều Setup/WCS, machine hoặc production profile trong một assembly.
- Stock removal, machine kinematics, 4/5-axis.
- Direct CNC communication, FTP/SFTP/HTTP/DNC hoặc machine certification.

Không có giai đoạn tiếp theo được tự động bắt đầu tại checkpoint này. Mọi `.fn`
vẫn phải được review và kiểm tra dry-run/single-block thủ công trước sản xuất.
