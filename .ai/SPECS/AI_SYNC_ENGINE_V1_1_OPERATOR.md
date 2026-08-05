# HMS AI Sync Engine V1.1 — Hướng dẫn vận hành

## 1. Mục đích và ranh giới

AI Sync Engine V1.1 đọc trạng thái Git, bằng chứng kiểm thử tùy chọn và metadata do người vận hành cung cấp để tạo một snapshot máy đọc được cùng các bản tóm tắt dẫn xuất. Engine không chạy test, không stage, commit, push, fetch, pull, merge, rebase, stash, reset, clean hoặc checkout.

Bốn lệnh duy nhất là `inspect`, `validate`, `show-plan` và `sync`. Ba lệnh đầu tuyệt đối không ghi. `sync` chỉ được chạy khi có authority riêng; lần dry-run đầu tiên trên `E:\CAD_CAM_Project` không phải authority để chạy `sync`.

## 2. Source of truth

Thứ tự tin cậy:

1. Git repository và Git object/index/working-tree thực tế là nguồn thật về revision và thay đổi.
2. `.ai/TEST_RESULTS.json` chỉ là bằng chứng test khi parser V1.1 xác minh được contract; thiếu file nghĩa là không có bằng chứng, không phải PASS.
3. Metadata người dùng là claim có provenance, không được ghi đè Git/test evidence đã xác minh và không được engine tự suy diễn progress/completion.
4. `.ai/STATE.json` là snapshot canonical của một run đã publish.
5. `.ai/MANIFEST.json` là commit marker public cuối cùng. Markdown, SESSION và METRICS chỉ là bản dẫn xuất, không phải input ngược vào engine.

Consumer phải đọc `MANIFEST.json` trước, kiểm tra compatibility, self-digest, artifact hash/size và `publication_status=complete`, rồi mới chấp nhận `STATE.json` và các file dẫn xuất. Verifier V1.1 strict-parse đủ tám artifact public (`MANIFEST`, `STATE`, `SESSION`, `METRICS`, checkpoint, `CURRENT_STATUS`, `NEXT_TASK`, `HANDOFF/TO_CHATGPT`), từ chối duplicate JSON key/encoding sai và cross-check run ID, generated time, project, schema, Git identity, publication envelope, checkpoint identity, exact allowlist, role, `required=true`, SHA-256, size và reader compatibility. Không chấp nhận một tập file không có MANIFEST hợp lệ.

## 3. Cài đặt và môi trường

- Windows 10/11 64-bit.
- Python `>=3.14,<3.15`; baseline dự án dùng Python 3.14.6 64-bit.
- Git khả dụng trên `PATH`.
- V1.1 chỉ dùng Python standard library; không cần cài dependency mới.
- Chạy từ bất kỳ working directory nào bằng cách truyền `--repo`; engine không gọi `os.chdir()`.
- `--repo` có thể là repository root, thư mục con hoặc file bên trong repository. Engine dùng Git read-only để resolve canonical root trước, sau đó mới load `.ai/config.json` từ root đó.

Ví dụ dùng virtual environment dự án:

```powershell
.\.venv\Scripts\python.exe tools\update_ai_sync.py inspect --repo E:\CAD_CAM_Project --format json
```

## 4. Config schema/profile

Config mặc định là `.ai/config.json`, JSON UTF-8 không BOM, `schema_version=1`, profile compatibility V1.1. Có thể chỉ định một config nằm trong repository bằng `--config`.

Các safety locks bắt buộc:

- `git.allow_stage=false`
- `git.allow_commit=false`
- `git.allow_push=false`
- `tests.run_tests_automatically=false`
- `checkpoint.overwrite_existing=false`
- `safety.preserve_uncommitted_work=true`
- `safety.reject_unrelated_staging=true`
- `safety.never_modify_source_files=true`
- `safety.never_invent_test_results=true`
- `safety.never_invent_progress=true`

Config sai schema, path escape, output path khác contract, capability không hỗ trợ hoặc safety lock bị nới sẽ fail closed.

## 5. Capability contract

V1.1 khai báo cố định:

- `canonical_state_json`
- `dry_run`
- `git_read_only_snapshot`
- `immutable_checkpoint`
- `journaled_publication`
- `markdown_render`
- `rollback_recovery`
- `source_protection`
- `structured_logging`
- `test_evidence_read`

Không có capability chạy test hoặc làm biến đổi Git. Reader phải fail closed nếu MANIFEST yêu cầu capability không được hỗ trợ.

## 6. TEST_RESULTS schema và chính sách bằng chứng

File tùy chọn: `.ai/TEST_RESULTS.json`, UTF-8 không BOM, JSON không duplicate key:

```json
{
  "schema_version": 1,
  "project": "HMS CAD/CAM",
  "generated_at": "2026-08-04T00:00:03Z",
  "runs": [
    {
      "run_id": "focused-1",
      "command": {"argv": ["python", "-m", "pytest", "tests/unit/ai_sync"]},
      "exit_code": 0,
      "started_at": "2026-08-04T00:00:00Z",
      "completed_at": "2026-08-04T00:00:02Z",
      "duration_seconds": 2.0,
      "counts": {"passed": 2, "failed": 0, "skipped": 0, "deselected": 0, "xfailed": 0, "xpassed": 0, "warnings": 0},
      "status": "passed",
      "evidence_source": "runner_json",
      "verification": "verified",
      "verification_notes": [],
      "log_path": "evidence/focused.log",
      "log_sha256": "<64 lowercase hex>"
    }
  ]
}
```

Exact command lấy từ `command.argv`, không lấy từ display string. Verified PASS chỉ có thể đến từ `runner_json` với exit/count/time/log/hash hợp lệ. `manual` và `imported_log` luôn unverified trong V1.1. Engine chỉ đọc evidence, không chạy command trong evidence và không sửa log.

## 7. Metadata input

Validate, show-plan, and sync accept exactly one metadata form:

- --metadata <json>; or
- inline --stage <text> and/or --task <text>.

Inspect rejects metadata, stage, task, and expected-metadata-sha256 arguments with CLI_ERROR. File metadata cannot be combined with inline metadata. The metadata schema supports schema_version, project, stage, status, current_task, remaining_work, blockers, blockers_state, next_action, nullable progress, provenance, and an optional commit claim. Omitted values remain null or unknown; the engine does not invent them.

A relative metadata path is resolved from the canonical repository root and must remain contained by it. An absolute path is an explicitly operator-supplied, read-only external authority. An external file must be a regular file; it must not be a symlink, junction, reparse point, device path, UNC path, alternate data stream, or Git metadata. It is limited to 1 MiB and parsed as strict UTF-8 without BOM. The engine reads bytes once, hashes the exact parsed bytes with SHA-256, and fails closed if the file changes while it is read. It never copies external metadata into the repository.

JSON payloads for validate, show-plan, and sync include metadata_present, metadata_sha256, and metadata_mode (none, inline, repository_file, or external_file), never the metadata path. An external path is never written to public STATE, MANIFEST, checkpoint, Markdown artifacts, stdout, stderr, or structured logs. Path failures use a sanitized message.

Use --expected-metadata-sha256 with a 64-lowercase-hex digest on validate, show-plan, and sync to bind the authority bytes from preflight to publication. A malformed or mismatching value is VALIDATION_FAILED and sync does not publish. The SHA is a binding, not a value inferred from display output.

R84A external-authority example:

`powershell
Get-FileHash -Algorithm SHA256 -LiteralPath E:\FILE\HMS_CAD_CAM_AI_SYNC_METADATA_R84A.json
.\.venv\Scripts\python.exe -B tools\update_ai_sync.py validate --repo E:\CAD_CAM_Project --metadata E:\FILE\HMS_CAD_CAM_AI_SYNC_METADATA_R84A.json --expected-metadata-sha256 <exact-sha256> --format json
.\.venv\Scripts\python.exe -B tools\update_ai_sync.py show-plan --repo E:\CAD_CAM_Project --metadata E:\FILE\HMS_CAD_CAM_AI_SYNC_METADATA_R84A.json --expected-metadata-sha256 <exact-sha256> --format json
# Only after separate authority and a passing review:
.\.venv\Scripts\python.exe -B tools\update_ai_sync.py sync --repo E:\CAD_CAM_Project --metadata E:\FILE\HMS_CAD_CAM_AI_SYNC_METADATA_R84A.json --expected-metadata-sha256 <exact-sha256> --format json
`

## 8. Các lệnh read-only

### inspect

```powershell
python tools/update_ai_sync.py inspect --repo E:\CAD_CAM_Project --format json
```

Pipeline `inspect` chỉ resolve canonical Git root, load/validate config, capture Git snapshot và báo engine/schema/minimum-reader/capabilities. Nó không đọc `TEST_RESULTS.json`, không đọc metadata, không verify commit claim, không build `ProjectState`, không render candidate và không tính intended output/candidate hash. Vì vậy evidence malformed không làm `inspect` thất bại. Không ghi file.

### validate

```powershell
python tools/update_ai_sync.py validate --repo E:\CAD_CAM_Project --format json
```

Chạy toàn bộ parse/build/render validation nhưng không publish. Blocking issue làm lệnh thất bại và không ghi.

### show-plan

```powershell
python tools/update_ai_sync.py show-plan --repo E:\CAD_CAM_Project --format json
```

Bổ sung candidate path, raw SHA-256, size và MANIFEST self-digest. Hash phụ thuộc injected/current run ID và clock nên hai process thực tế có thể tạo run-specific hash khác nhau. Không ghi file.

Khi `git.collect_remote=true`, Git snapshot chỉ truy vấn remote tên cố định `origin` bằng `git remote get-url origin`; không có network command. URL được sanitize/redact credential trước khi đưa vào payload. `origin` không tồn tại được biểu diễn là không có remote; timeout, malformed output và lỗi Git khác vẫn fail closed. Khi `collect_remote=false`, engine không truy vấn remote.

Tùy chọn chung: `--repo`, `--config`, `--format human|json`, `--expected-head`, `--verbose`. `--expected-head` mismatch fail closed. `--verbose` xuất structured JSONL vào stderr; JSON result vẫn ở stdout.

## 9. Lệnh sync

```powershell
python tools/update_ai_sync.py sync --repo <TEMP_REPOSITORY> --format json --stage WP --task "Mô tả"
```

Không chạy lệnh này trên repository thật nếu chưa có authority riêng. Publisher chỉ được thay đúng:

- `.ai/STATE.json`
- `.ai/MANIFEST.json`
- `.ai/CURRENT_STATUS.md`
- `.ai/NEXT_TASK.md`
- `.ai/SESSION.json`
- `.ai/METRICS.json`
- `.ai/HANDOFF/TO_CHATGPT.md`
- một `.ai/CHECKPOINTS/YYYY-MM-DD_HHMMSS.md`

MANIFEST luôn được replace cuối cùng. Publisher không tuyên bố multi-file syscall atomicity; tính nhất quán đạt bằng validation, journal, rollback/recovery và public MANIFEST commit marker.

## 10. Exit codes

| Code | Tên | Ý nghĩa |
|---:|---|---|
| 0 | `SUCCESS` | Hoàn tất đúng command boundary |
| 2 | `CLI_ERROR` | Cú pháp hoặc command không hỗ trợ |
| 3 | `CONFIG_INVALID` | Config/schema/capability/safety lock không hợp lệ |
| 4 | `NOT_GIT_REPOSITORY` | `--repo` không phải Git repository |
| 5 | `GIT_READ_FAILED` | Git read-only snapshot thất bại |
| 6 | `TEST_EVIDENCE_INVALID` | Evidence tồn tại nhưng không hợp lệ |
| 7 | `VALIDATION_FAILED` | Metadata/state/render/expected HEAD không hợp lệ |
| 8 | `PUBLICATION_FAILED` | Publication thất bại nhưng đã rollback an toàn |
| 9 | `SAFETY_BOUNDARY_VIOLATION` | Lock/path/concurrency/recovery/interrupt cần fail closed |

## 11. STATE và MANIFEST

`STATE.json` chứa engine/schema/version, run ID, UTC generation time, capabilities, project state, Git snapshot đã chuẩn hóa, test evidence summary, provenance và publication envelope `pending_manifest`. Nó không chứa repository absolute path, raw diff, environment dump hay secret.

`MANIFEST.json` chứa version/schema/minimum reader, run ID, branch/full HEAD, dirty state, latest checkpoint, exact published paths, artifact raw hashes/sizes, capabilities, reader compatibility, self-digest canonical và `publication_status=complete`. MANIFEST không tự đưa raw MANIFEST hash vào artifact list; `publication_manifest_sha256` là digest của payload sau khi loại chính field đó.

Hai giá trị hash có nghĩa khác nhau và được báo bằng typed field riêng: `manifest_self_digest` là canonical self-digest nói trên; `manifest_file_sha256` là SHA-256 của toàn bộ bytes file MANIFEST cuối cùng. Hai giá trị phải khác nhau và không được gọi raw file hash là self-digest.

## 12. Checkpoint

Checkpoint dùng UTC filename `YYYY-MM-DD_HHMMSS.md`, create-exclusive (`O_EXCL`) và không overwrite/collision suffix. Contract Markdown ghi rõ checkpoint schema, engine/state/manifest/minimum-reader versions, `created_by`, run ID/timestamp UTC, project/stage/status/provenance, branch/full HEAD/dirty, working-tree counts và full entries. Capability được ghi trong đúng hai subsection theo thứ tự deterministic: `### Supported` phải khớp chính xác MANIFEST supported; `### Required` phải khớp chính xác MANIFEST required, không duplicate và là subset của Supported. Capability supported nhưng không required không được xuất hiện trong Required. Mỗi test run ghi argv, exit code, timestamps, counts, status/source/verification, log/hash/issues. Checkpoint cũng ghi remaining work, blockers và blockers_state, next action, `state_sha256`, `artifact_set_sha256`, commit-claim OID/verification. Checkpoint không chứa final MANIFEST self-digest hoặc raw file hash.

## 13. Journal, lock và recovery

Internal transaction area là `.ai/.sync-tmp/`, tách khỏi public MANIFEST. Lock có run ID/PID/time, create-exclusive; engine không tự phá lock stale.

Trước MANIFEST, recovery phân loại hash hiện tại của toàn bộ journal records trước khi ghi: bằng old hash thì giữ nguyên; bằng candidate hash thì được rollback; khác cả hai là `PUBLICATION_RECOVERY_AMBIGUOUS`. Với old hash `null`, absent là old state, candidate được xóa khi rollback, còn target tồn tại với hash khác candidate là ambiguous. Bất kỳ ambiguous content nào đều trả exit 9, không ghi đè và không cleanup journal/backup/transaction. Sau rollback, recovery xác minh mọi record đã trở về đúng old hash hoặc absent; nếu old MANIFEST tồn tại thì raw hash phải đúng và restored snapshot phải qua strict verifier trước cleanup. Sau khi MANIFEST candidate hợp lệ đã public nhưng journal chưa committed, recovery strict-verify MANIFEST self-digest và toàn bộ artifact hash/size rồi roll forward, không rollback snapshot hợp lệ.

Startup recovery chỉ chạy cho lệnh `sync`:

1. Nếu lock tồn tại, fail closed với exit 9; engine không tự phá lock. Xác minh process owner/PID và repository state bằng authority vận hành riêng; không xóa chỉ vì PID biến mất hoặc timestamp cũ.
2. Nếu journal tồn tại nhưng không có lock, engine gọi recovery trước khi capture Git/build run mới.
3. `rolled_back` hoặc `rolled_forward` được ghi structured log và trả trong JSON. Sau recovery thành công, cùng command tiếp tục một sync mới theo hành vi deterministic.
4. Recovery ambiguous fail closed với exit 9, giữ journal/backup để điều tra và không sửa tay MANIFEST.
5. `inspect`, `validate`, `show-plan` không gọi recovery và không mutate lock/journal/pending state.
6. Việc operator xác nhận và xóa stale lock là authority ngoài engine; xóa lock không tự cấp quyền chạy real sync.

## 14. Troubleshooting

- Exit 3: kiểm tra UTF-8/BOM, duplicate keys, fixed paths, version/capability và safety flags.
- Exit 4/5: kiểm tra Git executable, repository root, timeout và thay đổi HEAD giữa các read.
- Exit 6: kiểm tra project, timestamps/duration/counts/exit status, evidence source, safe log path và SHA-256.
- Exit 7: kiểm tra expected HEAD, metadata conflict, null/unknown semantics và candidate consistency.
- Exit 8: kiểm tra permission/sharing violation, checkpoint collision và available space; không retry mù.
- Exit 9: coi là safety incident; giữ evidence, lock, journal và backup cho review.

Không đưa token/password/credential vào argv, metadata, remote URL hoặc log. Structured errors được sanitize nhưng người vận hành vẫn phải bảo vệ input.

## 15. Quy trình Codex → AI Sync → GitHub → ChatGPT Web

1. Codex làm việc trong phạm vi được duyệt và chạy test theo authority riêng; AI Sync không chạy test thay Codex.
2. Test runner hoặc người vận hành tạo evidence có provenance; parser AI Sync xác minh conservatively.
3. Chạy `inspect`, `validate`, `show-plan`; review intended paths/hashes và zero-write evidence.
4. Khi có authority riêng, chạy `sync` để publish local `.ai` snapshot.
5. Git review/stage/commit/push là quy trình riêng ngoài AI Sync V1.1. Engine không thực hiện và không được dùng kết quả sync làm authority Git.
6. GitHub chỉ nhận nội dung qua quy trình Git được con người/Codex phê duyệt riêng.
7. ChatGPT Web đọc MANIFEST trước, xác minh snapshot rồi dùng STATE/handoff; không coi Markdown đơn lẻ là source of truth.

## 16. Cảnh báo first real sync

Ba lệnh `inspect`, `validate`, `show-plan` thành công chỉ chứng minh dry-run read-only. Chúng không cho phép chạy `sync`. First real sync trên `E:\CAD_CAM_Project` cần authorization riêng sau review package, đồng thời phải capture và kiểm tra HEAD/index/refs/config/status/Stage 13C trước và sau publication.
