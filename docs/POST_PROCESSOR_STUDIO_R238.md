# R238 — Post Processor Studio, Tranche 2

## Ranh giới an toàn

R238 thêm engine deployment/rollback byte-exact cho **target cô lập được caller
chỉ rõ**. Engine từ chối mutation nếu target resolve thành global WorkNC
`FANUC-SHL.dat`. Không có CNC, FOCAS, MDI, NC upload, hay trạng thái physical
qualification nào trong module này. `Post ACTIVE` không đồng nghĩa
`MACHINE_READY`.

## Chuỗi transaction

`APPROVED -> PLANNED -> PRECHECK_PASS -> BACKUP_VERIFIED ->
REPLACEMENT_STARTED -> REPLACEMENT_VERIFIED -> ACTIVE_COMMITTED`

Mọi write mở ở binary mode, fsync staging/backup, `os.replace`, rồi rehash
readback. Failure tại bất cứ boundary nào fail-closed và sinh record state
immutable. Recovery luôn rehash target; không bao giờ suy đoán chỉ từ metadata.

`DeploymentPlan` là deterministic fingerprint của lineage, candidate/parent
SHA, binding machine/controller, approval, validation/regression và policy.
Approval có identity, timestamp timezone, candidate SHA/revision, binding và
fingerprint evidence. Thay đổi một identity load-bearing làm plan không còn hợp
lệ để dùng lại.

## Reconciliation và rollback

Target được phân loại typed: parent khớp, candidate đã tồn tại, unknown,
missing, unreadable, hoặc stale/required reconciliation. Bytes unknown không
bao giờ bị ghi đè. Backup được kiểm hash hoàn toàn trước replacement. Rollback
chỉ restore exact backup khi current target vẫn là exact managed candidate.

Lock file bao phủ post + absolute target + machine/controller binding, chứa
actor/PID và tuyệt đối không silently takeover lock stale; owner phải reconcile.

## UI và portability

Post Studio vẫn modeless/lazy và normal CAM Post/Export không gọi engine này.
Panel hiển thị Vietnamese-first activation/rollback/drift boundary. Package của
R237 vẫn import như library immutable và không tự activation; R238 audit record
giữ trong `post/studio/deployment/`, không ghi secrets/lock transient vào package.

## R238 execution boundary

End-to-end activation/rollback test chỉ dùng target temporary cô lập. Real
`C:\ProgramData\WORKNC\2021.0\pospro\FANUC-SHL.dat` chỉ có thể được hash bằng
read-only preflight và vẫn cần future owner decision để activation thật.
