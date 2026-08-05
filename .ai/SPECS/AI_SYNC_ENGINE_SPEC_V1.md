# HMS AI Sync Engine — Đặc tả kỹ thuật V1

Trạng thái tài liệu: `READY_FOR_REVIEW`  
Phạm vi: thiết kế, chưa triển khai engine  
Môi trường đích ban đầu: Windows 10/11 64-bit, Python 3.14  
Package dự kiến: `tools/ai_sync/`  
Entry point dự kiến: `tools/update_ai_sync.py`

## 1. Mục tiêu và phạm vi

### 1.1 Mục tiêu

HMS AI Sync Engine V1 là công cụ cục bộ, chạy theo yêu cầu, chỉ đọc repository và xuất bản một tập trạng thái AI có kiểm soát. Engine phải dùng lại được cho HMS CAD/CAM, HMS AI Audio Story Studio, CF Offline và các repository Python khác mà không phụ thuộc mã nghiệp vụ của từng dự án.

Luồng trách nhiệm của V1:

1. Đọc cấu hình `.ai/config.json`.
2. Thu thập ảnh chụp Git bằng lệnh chỉ đọc.
3. Đọc test evidence có sẵn nếu được cung cấp; V1 không tự chạy test.
4. Kết hợp metadata do người dùng cung cấp với evidence đã xác minh.
5. Render các file trạng thái, handoff và checkpoint.
6. Validate toàn bộ candidate trước publication.
7. Chỉ publish các đích nằm trong allowlist.
8. Trả báo cáo chính xác và dừng trước mọi thao tác stage/commit/push.

### 1.2 Ngoài phạm vi V1

V1 không:

- sửa mã nguồn, test, tài liệu dự án hoặc cấu hình hiện hữu;
- tự chạy pytest hay bất kỳ command test nào;
- stage, commit, push, stash, reset, clean hoặc checkout file;
- sửa remote, branch, tag, index, worktree hoặc Git configuration;
- quyết định phần trăm tiến độ từ số file, số test hay nội dung diff;
- suy diễn task đã hoàn thành;
- giao tiếp với GitHub, ChatGPT Web hay dịch vụ mạng;
- chạy nền hoặc theo dõi filesystem;
- thực thi chỉ thị từ handoff từ xa;
- thay thế hệ thống quản lý dự án hoặc lịch sử Git.

### 1.3 Nguồn sự thật và bản tóm tắt

| Dữ liệu | Nguồn sự thật | Vai trò của file `.ai` |
|---|---|---|
| Branch, HEAD, upstream, dirty state, file status | Kết quả Git read-only của đúng repository tại thời điểm capture | Bản chụp có timestamp; không thay thế Git |
| Commit tồn tại | Git object database, được xác minh bằng `git cat-file -e <oid>^{commit}` | Có thể tham chiếu OID đã xác minh |
| Kết quả test | `.ai/TEST_RESULTS.json` và evidence được schema/hash/exit-code validation | Bản tóm tắt; không tự biến evidence unverified thành verified |
| Stage, task, remaining work, blocker, next action | Metadata rõ ràng do người dùng/caller cung cấp | Bản chuẩn hóa có nhãn provenance |
| Tiến độ phần trăm | Chỉ giá trị người dùng cung cấp có provenance; nullable | Không được engine tự tính hoặc phát minh |
| Trạng thái publication | `PublicationResult` và checkpoint commit marker của run | Bằng chứng publication của engine, không phải Git commit |

Thứ tự ưu tiên khi có xung đột: evidence Git/test đã xác minh thắng bản tóm tắt cũ; metadata mới không được phủ định evidence; trường không xác minh được phải là `null`, `unknown` hoặc mang nhãn `unverified`, không được điền bằng suy đoán.

## 2. Nguyên tắc an toàn

### 2.1 Bất biến bắt buộc

- Engine không sửa source code hoặc bất kỳ file nào ngoài allowlist publication.
- Engine không tự chạy test khi `tests.run_tests_automatically=false`; V1 còn cấm chạy test bất kể cấu hình.
- Engine không stage, commit, push, stash, reset, clean hoặc checkout file.
- Engine không gọi command có thể sửa Git index, refs, worktree, remote hay configuration.
- Engine không ghi đè, xóa, đổi tên hoặc chuẩn hóa line ending của uncommitted work.
- Engine không phát minh kết quả test, commit, blocker, task completion hay phần trăm tiến độ.
- Mọi output tự động dùng UTF-8 không BOM và newline LF xác định.
- Mọi candidate được ghi vào vùng tạm cùng filesystem, flush, `fsync` khi khả dụng và validate trước khi replace.
- Publication phải có allowlist check lần cuối, source-protection check và optimistic concurrency check.
- Publication lỗi phải rollback các đích đã thay; lần chạy sau phải phát hiện và phục hồi journal chưa hoàn tất trước khi làm việc mới.
- Không checkpoint nào được ghi đè. Collision là lỗi dừng, không tự thêm hậu tố ngẫu nhiên.
- Không log secret, token, credential, nội dung file nhạy cảm hoặc toàn bộ diff.

### 2.2 Fail closed

Các tình huống sau phải dừng trước publication với exit code thích hợp:

- repository root/config/path không hợp lệ;
- output path thoát khỏi repository hoặc qua symlink/reparse point không được phép;
- cấu hình yêu cầu khả năng V1 cấm;
- Git snapshot không nhất quán hoặc Git command lỗi;
- test evidence sai schema, mâu thuẫn hoặc giả danh verified;
- candidate thiếu output bắt buộc hoặc render không xác định;
- source/protected path thay đổi trong vùng engine định ghi;
- HEAD/index/worktree fingerprint thay đổi giữa capture và pre-publish;
- checkpoint name collision;
- không thể tạo staging/backup/journal cùng volume;
- không thể rollback hoàn chỉnh.

### 2.3 Bảo vệ concurrent change

Engine ghi fingerprint ban đầu gồm repository canonical path, HEAD, branch state, index tree hash nếu đọc được, và danh sách porcelain-v2 chuẩn hóa. Ngay trước publication engine capture lại. Nếu fingerprint khác, engine trả safety violation và không publish; không cố merge trạng thái mới với candidate cũ.

Một lock độc quyền theo repository canonical path ngăn hai engine run publish đồng thời. Lock không được coi là quyền sửa repository và phải có owner metadata tối thiểu (`run_id`, PID, created_at). Stale lock chỉ được dọn sau quy tắc xác minh riêng; V1 mặc định báo lỗi thay vì tự phá lock.

## 3. Kiến trúc module

Dependency hướng một chiều dự kiến:

```text
cli -> engine -> config/git_reader/test_results/state_builder
              -> renderers -> validation -> publisher -> checkpoint
mọi module -> models
```

Không module nào được import `cli` hoặc gọi ngược vào `engine`. `models.py` không phụ thuộc adapter I/O.

### 3.1 `tools/ai_sync/__init__.py`

- Trách nhiệm: khai báo package, `__version__`, và public API tối thiểu.
- Input: không có I/O.
- Output: symbol ổn định như `SyncEngine`, `SyncRequest`, `SyncResult` nếu được export.
- Lỗi: import-time compatibility error phải rõ ràng.
- Tuyệt đối không: đọc repository, chạy Git, publish file hoặc tạo side effect khi import.

### 3.2 `tools/ai_sync/models.py`

- Trách nhiệm: enum, dataclass bất biến và type alias dùng chung; serialization primitives.
- Input: giá trị đã parse/normalize.
- Output: các model tại mục 4.
- Lỗi: `ValueError`/model validation error cho invariant cục bộ.
- Tuyệt đối không: truy cập filesystem, subprocess, clock hệ thống trực tiếp hoặc Git.

### 3.3 `tools/ai_sync/config.py`

- Trách nhiệm: đọc UTF-8 strict `.ai/config.json`, kiểm tra schema version, resolve path tương đối từ repo root và tạo typed config.
- Input: canonical repository root, config path.
- Output: immutable `AiSyncConfig` và allowlist canonical.
- Lỗi: file thiếu, BOM/encoding/JSON lỗi, schema unsupported, key/type/path/capability không hợp lệ.
- Tuyệt đối không: sửa/migrate config, chấp nhận path ngoài repo, bật capability cấm hoặc dùng default âm thầm cho field an toàn.

### 3.4 `tools/ai_sync/git_reader.py`

- Trách nhiệm: resolve Git root và capture snapshot bằng subprocess argument list, `shell=False`, byte capture rồi decode UTF-8 strict phù hợp.
- Input: candidate path, timeout cho lệnh chỉ đọc.
- Output: `GitSnapshot`, raw-command audit metadata không chứa diff content.
- Lỗi: không phải repo, Git không có, timeout, nonzero exit, malformed porcelain, path decode/normalization lỗi.
- Tuyệt đối không: gọi `git add`, `commit`, `push`, `stash`, `reset`, `clean`, `checkout`, `switch`, `restore`, `update-index`, `read-tree`, `write-tree`, `config`, `fetch`, `pull` hoặc lệnh sửa trạng thái khác; không đọc nội dung diff nếu không cần.

Các lệnh V1 allowlist dự kiến: `git rev-parse`, `git status --porcelain=v2 -z --branch`, `git diff --numstat -z`, `git diff --cached --numstat -z`, `git remote get-url` khi cấu hình cho phép, và `git cat-file -e` để xác minh commit. Mọi executable/verb ngoài allowlist bị từ chối.

### 3.5 `tools/ai_sync/test_results.py`

- Trách nhiệm: đọc, schema-validate và phân loại `.ai/TEST_RESULTS.json`; tùy chọn xác minh log hash.
- Input: path evidence, repository root, clock/reference time.
- Output: tuple `TestEvidence`; warnings có cấu trúc.
- Lỗi: missing khi required, JSON/encoding/schema lỗi, counter âm/mâu thuẫn, time đảo, hash sai, path log không an toàn.
- Tuyệt đối không: chạy test, sửa evidence/log, suy diễn PASS từ text tự do, dùng số liệu cũ làm số liệu hiện tại.

### 3.6 `tools/ai_sync/state_builder.py`

- Trách nhiệm: kết hợp snapshot, evidence và user metadata theo precedence; gắn provenance và verification state.
- Input: `GitSnapshot`, `TestEvidence[]`, metadata đã validate, config.
- Output: immutable `ProjectState`.
- Lỗi: metadata mâu thuẫn với Git, trường bắt buộc thiếu, commit khai báo không tồn tại, progress ngoài miền.
- Tuyệt đối không: tự tính progress, tự đánh dấu complete, sửa input hoặc I/O.

### 3.7 `tools/ai_sync/renderers.py`

- Trách nhiệm: render deterministic Markdown/JSON từ `ProjectState`; escape dữ liệu người dùng; sắp xếp key/list theo quy tắc.
- Input: `ProjectState`, `SyncRequest`, render schema version.
- Output: mapping relative path -> bytes UTF-8 không BOM.
- Lỗi: unsupported template/schema, field không render được, output trùng path.
- Tuyệt đối không: ghi filesystem, đọc template ngoài package không được xác minh, nhúng secret hoặc thay đổi model.

### 3.8 `tools/ai_sync/checkpoint.py`

- Trách nhiệm: chọn tên timestamp ổn định, render `CheckpointRecord`, reserve/publish bằng create-exclusive.
- Input: UTC timestamp, `CheckpointRecord`, checkpoints root.
- Output: relative checkpoint path và bytes; kết quả create-exclusive.
- Lỗi: collision, path không canonical, directory unavailable, write/flush failure.
- Tuyệt đối không: overwrite checkpoint, dùng local time, tuyên bố commit không được Git xác minh hoặc tự chọn hậu tố để che collision.

### 3.9 `tools/ai_sync/publisher.py`

- Trách nhiệm: lock, staging, backup, journal, validate-before-replace, allowlist enforcement, atomic per-file replace, rollback/recovery và publication result.
- Input: candidate bytes, expected fingerprints/hashes, allowlist, checkpoint candidate, `run_id`.
- Output: `PublicationResult` và publication manifest.
- Lỗi: lock, disk, permission, collision, concurrency, replace, fsync, rollback hoặc cleanup failure.
- Tuyệt đối không: publish path ngoài allowlist, follow unsafe link, delete unrelated file, mutate Git hoặc tiếp tục sau partial failure chưa recovery.

### 3.10 `tools/ai_sync/validation.py`

- Trách nhiệm: validate config/model/JSON/Markdown/path/allowlist/cross-field invariants và candidate set.
- Input: typed config/model hoặc rendered bytes.
- Output: ordered tuple `ValidationIssue`; boolean chỉ là derived view.
- Lỗi: validator internal error phải chuyển thành fatal issue, không nuốt lỗi.
- Tuyệt đối không: sửa candidate để làm pass, publish, chạy Git mutating command hoặc bỏ qua error severity.

### 3.11 `tools/ai_sync/engine.py`

- Trách nhiệm: orchestration đúng state machine; dependency injection cho clock, Git runner, filesystem/publisher; map lỗi sang `SyncResult`/exit code.
- Input: `SyncRequest`.
- Output: `SyncResult` không chứa secret.
- Lỗi: mọi domain/adaptor error được giữ cause và component nhưng thông báo người dùng được sanitize.
- Tuyệt đối không: tự mở rộng scope, bỏ qua gate, gọi stage/commit/push hoặc publish nếu validation có error.

### 3.12 `tools/ai_sync/cli.py`

- Trách nhiệm: parse CLI, dựng request, gọi engine, in summary machine/human-readable và trả exit code.
- Input: `argv`, cwd.
- Output: stdout report, stderr diagnostic, process exit code.
- Lỗi: usage/argument/config errors.
- Tuyệt đối không: chứa business logic, tự chạy subprocess Git/test, prompt tương tác trong automation hoặc cung cấp command mutating.

### 3.13 `tools/update_ai_sync.py`

- Trách nhiệm: bootstrap mỏng, thêm repository root/package location theo cách xác định và gọi `ai_sync.cli.main()`.
- Input: process arguments.
- Output: `raise SystemExit(main())`.
- Lỗi: import/bootstrap error rõ ràng.
- Tuyệt đối không: triển khai engine trong một file, ghi file khi import hoặc có command stage/commit/push.

## 4. Mô hình dữ liệu

Quy ước chung:

- Dataclass domain dùng `@dataclass(frozen=True, slots=True)` khi khả thi.
- `Path` chỉ dùng trong lớp I/O; dạng serialized là POSIX-style relative path (`/`), không có `..`, drive hoặc leading slash.
- Canonical filesystem path dùng `Path.resolve(strict=...)`, `os.path.normcase` trên Windows và kiểm tra containment bằng path components, không bằng string prefix.
- Timestamp là UTC aware, serialized RFC 3339 dạng `YYYY-MM-DDTHH:MM:SS.ffffffZ`; không nhận naive datetime.
- Duration là số giây không âm; counter là integer không âm, không dùng boolean thay integer.
- SHA-256 là 64 ký tự hex lowercase.
- Nullable được ghi rõ; field không ghi nullable là bắt buộc.
- Enum serialized bằng lowercase snake_case ổn định.

### 4.1 `WorkingTreeEntry`

| Trường | Kiểu | Quy tắc |
|---|---|---|
| `path` | `str` | Bắt buộc, relative POSIX path |
| `index_status` | `str` | Bắt buộc, mã porcelain hoặc `.` |
| `worktree_status` | `str` | Bắt buộc, mã porcelain hoặc `.` |
| `kind` | enum | `ordinary`, `renamed`, `copied`, `unmerged`, `untracked`, `ignored` |
| `original_path` | `str | None` | Bắt buộc với rename/copy, còn lại null |
| `submodule_state` | `str | None` | Raw normalized submodule state |
| `is_staged` | `bool` | Derived từ index status |

Sắp xếp theo `path.casefold()`, rồi raw `path`, `original_path`; duplicate canonical path là validation error.

### 4.2 `DiffSummary`

| Trường | Kiểu | Quy tắc |
|---|---|---|
| `scope` | enum | `unstaged` hoặc `staged` |
| `files_changed` | `int` | Không âm |
| `insertions` | `int | None` | Null nếu có binary/unknown |
| `deletions` | `int | None` | Null nếu có binary/unknown |
| `binary_files` | `int` | Không âm |
| `entries` | tuple summary | Chỉ path và numstat, không chứa diff text |

### 4.3 `GitSnapshot`

| Trường | Kiểu | Quy tắc |
|---|---|---|
| `repository_root` | `Path` | Canonical absolute, không serialize ra handoff nếu policy che path |
| `captured_at` | `datetime` | UTC aware |
| `branch` | `str | None` | Null khi detached/unborn |
| `is_detached` | `bool` | Bắt buộc |
| `head_oid` | `str | None` | 40/64 hex theo hash algorithm; null ở unborn repo |
| `upstream` | `str | None` | Null nếu không có |
| `ahead` | `int | None` | Null nếu không có upstream |
| `behind` | `int | None` | Null nếu không có upstream |
| `remote_urls` | mapping | Optional theo config; secrets được redact |
| `entries` | `tuple[WorkingTreeEntry, ...]` | Có thứ tự xác định |
| `staged_diff` | `DiffSummary` | Bắt buộc |
| `unstaged_diff` | `DiffSummary` | Bắt buộc |
| `is_dirty` | `bool` | Derived, gồm tracked/untracked theo policy |
| `fingerprint_sha256` | `str` | Hash canonical snapshot payload |

### 4.4 `TestEvidence`

| Trường | Kiểu | Quy tắc |
|---|---|---|
| `run_id` | `str` | Unique trong file evidence |
| `command` | `tuple[str, ...]` | Exact argv ưu tiên; display string chỉ bổ sung |
| `exit_code` | `int | None` | Null nghĩa là không thể verified PASS |
| `started_at`, `completed_at` | `datetime` | UTC; completed không trước started |
| `duration_seconds` | `float` | Không âm, khớp time trong tolerance |
| `passed`, `failed`, `skipped`, `deselected`, `xfailed`, `xpassed`, `warnings` | `int | None` | Không âm; null nếu không được evidence cung cấp |
| `status` | enum | `passed`, `failed`, `cancelled`, `timed_out`, `error`, `unknown` |
| `evidence_source` | enum | `runner_json`, `manual`, `imported_log` |
| `log_path` | `str | None` | Repo-relative, không follow path unsafe |
| `log_sha256` | `str | None` | Nếu có thì phải xác minh trước label verified |
| `verification` | enum | `verified`, `unverified` |
| `verification_issues` | tuple[str, ...] | Lý do cụ thể |

### 4.5 `ProjectState`

| Trường | Kiểu | Quy tắc |
|---|---|---|
| `schema_version` | `int` | V1 là `1` |
| `project_name` | `str` | Từ config |
| `generated_at` | `datetime` | UTC |
| `stage` | `str | None` | User metadata, không suy diễn |
| `status` | enum | `not_started`, `work_in_progress`, `blocked`, `ready_for_review`, `complete`, `unknown` |
| `current_task` | `str | None` | User metadata |
| `git` | `GitSnapshot` | Evidence bắt buộc |
| `tests` | tuple[TestEvidence] | Có thể rỗng |
| `remaining_work` | tuple[str, ...] | Có thể rỗng, provenance user |
| `blockers` | tuple[str, ...] | Có thể rỗng, không đồng nghĩa “không có” nếu chưa xác minh |
| `next_action` | `str | None` | User metadata |
| `stage_progress_percent` | `float | None` | 0..100, chỉ user-provided |
| `overall_progress_percent` | `float | None` | 0..100, chỉ user-provided |
| `provenance` | mapping | Nguồn và verification cho từng nhóm field |

### 4.6 `SyncRequest`

| Trường | Kiểu | Quy tắc |
|---|---|---|
| `repository` | `Path` | Candidate path |
| `command` | enum | `inspect`, `sync`, `validate`, `show_plan` |
| `config_path` | `Path` | Mặc định `.ai/config.json` |
| `metadata_path` | `Path | None` | Optional `.ai/INPUT.json` hoặc path được phép đọc |
| `inline_stage` | `str | None` | Không dùng đồng thời field tương ứng trong metadata nếu khác |
| `inline_task` | `str | None` | Như trên |
| `dry_run` | `bool` | `inspect`, `validate`, `show_plan` luôn true về write |
| `expected_head` | `str | None` | Optimistic guard tùy chọn |
| `run_id` | `str` | UUID chuẩn hóa, không dùng làm claim test |

### 4.7 `PublicationResult`

| Trường | Kiểu | Quy tắc |
|---|---|---|
| `run_id` | `str` | Bắt buộc |
| `status` | enum | `not_attempted`, `published`, `rolled_back`, `failed`, `recovery_required` |
| `started_at`, `completed_at` | `datetime | None` | completed null nếu chưa kết thúc |
| `published_paths` | tuple[str, ...] | Chỉ allowlisted paths thực sự publish |
| `unchanged_paths` | tuple[str, ...] | Candidate byte-identical |
| `rolled_back_paths` | tuple[str, ...] | Paths đã restore |
| `manifest_sha256` | `str | None` | Hash manifest canonical |
| `error_code` | `str | None` | Stable internal code |

### 4.8 `CheckpointRecord`

| Trường | Kiểu | Quy tắc |
|---|---|---|
| `schema_version` | `int` | V1 là `1` |
| `checkpoint_id` | `str` | Timestamp filename stem |
| `created_at` | `datetime` | UTC |
| `run_id` | `str` | Liên kết publication |
| `branch`, `head_oid`, `is_dirty` | typed | Copy từ verified Git snapshot |
| `working_tree_summary` | tuple | Không chứa diff content |
| `test_evidence` | tuple | Mỗi run có verified/unverified rõ ràng |
| `remaining_work`, `blockers` | tuple[str, ...] | User metadata |
| `next_action` | `str | None` | User metadata |
| `publication_manifest_sha256` | `str` | Ràng buộc output set |
| `commit_claim_verified` | `bool` | Chỉ true nếu OID tồn tại trong Git snapshot/recheck |

### 4.9 `ValidationIssue`

| Trường | Kiểu | Quy tắc |
|---|---|---|
| `code` | `str` | Stable uppercase snake case |
| `severity` | enum | `info`, `warning`, `error`, `fatal` |
| `component` | `str` | Module logical name |
| `field` | `str | None` | JSON-pointer hoặc model field |
| `path` | `str | None` | Repo-relative/redacted |
| `message` | `str` | Không chứa secret |
| `details` | mapping | JSON-safe, sanitized |

### 4.10 `SyncResult`

| Trường | Kiểu | Quy tắc |
|---|---|---|
| `run_id` | `str` | Bắt buộc |
| `success` | `bool` | Chỉ true khi command goal đạt đủ gate |
| `exit_code` | `int` | Theo mục 11 |
| `state` | `ProjectState | None` | Có thể null nếu lỗi sớm |
| `issues` | tuple[ValidationIssue] | Ordered deterministic |
| `publication` | `PublicationResult` | Kể cả not attempted |
| `planned_paths` | tuple[str, ...] | Cho dry-run/report |
| `message` | `str` | Summary chính xác |

## 5. Luồng hoạt động

State machine chuẩn cho `sync`:

1. **Resolve repository root.** Dùng `git rev-parse --show-toplevel`; canonicalize và xác nhận config nằm trong root.
2. **Load config.** Đọc bytes UTF-8 strict, reject BOM nếu policy yêu cầu, parse JSON không duplicate key.
3. **Validate config.** Kiểm tra `schema_version`, path containment, allowlist và V1 capability gates.
4. **Capture Git snapshot.** Capture branch/HEAD/upstream/status/diff summary bằng command allowlist; ghi fingerprint.
5. **Read optional test evidence.** Missing file là “không có evidence”, trừ khi request/config yêu cầu; không phải PASS.
6. **Load user-provided checkpoint metadata.** Đọc inline hoặc JSON UTF-8 strict; reject xung đột và unknown security-sensitive fields.
7. **Build normalized project state.** Áp dụng precedence/provenance; verify mọi commit claim.
8. **Render vào vùng tạm.** Dựng toàn bộ candidate bytes và publication manifest trong staging directory cùng volume.
9. **Validate JSON và Markdown.** Reparse JSON; kiểm tra schema/cross-file invariants, UTF-8 không BOM, headings bắt buộc, newline và no-placeholder claims.
10. **Kiểm tra source-file protection.** Re-capture Git fingerprint; xác nhận candidate target đúng allowlist, không symlink/reparse escape, không đè protected uncommitted target trái policy.
11. **Atomic publish chỉ file được phép.** Lock, backup, replace từng file atomically, verify hash sau replace; checkpoint create-exclusive; commit marker last.
12. **Tạo checkpoint bất biến.** Checkpoint mang timestamp UTC và manifest hash; nếu collision/failure thì rollback state publication.
13. **Trả báo cáo.** Nêu files published/unchanged, evidence class, issues và exact exit code.
14. **Dừng trước stage/commit/push.** Không đưa ra hoặc tự chạy Git mutation như phần của engine.

`inspect` dừng sau bước 4 và không ghi. `validate` chạy đến validate candidate/current inputs nhưng không publish. `show-plan` chạy đủ resolve/config/snapshot/build/render/validation cần thiết, chỉ trả danh sách intended writes và hashes; không tạo staging persistent.

## 6. File được phép sửa

### 6.1 Final-output allowlist mặc định

Engine V1 chỉ được tạo/cập nhật:

- `.ai/CURRENT_STATUS.md`
- `.ai/NEXT_TASK.md`
- `.ai/SESSION.json`
- `.ai/METRICS.json`
- `.ai/HANDOFF/TO_CHATGPT.md`
- `.ai/CHECKPOINTS/<timestamp>.md`

`<timestamp>` theo UTC format cấu hình, sau khi validate rằng format tạo đúng một filename an toàn. Checkpoint là create-only.

### 6.2 Protected denylist

Engine không tự sửa:

- `.ai/HANDOFF/TO_CODEX.md`
- `.ai/DECISIONS/**`
- `.ai/KNOWLEDGE/**`
- `.ai/PROMPTS/**`
- `.ai/config.json`
- `src/**`
- `tests/**`
- `docs/**`
- `.codex/**`
- mọi path không nằm trong final-output allowlist.

Staging/lock/journal/backup là artifact nội bộ tạm thời dưới `.ai/.sync-tmp/<run_id>/`, không phải final output. Engine phải tạo cùng volume, dùng tên fixed-format, không follow links và dọn sau success/rollback. Nếu crash để lại journal, engine phải recovery trước run mới; không được coi artifact tạm là file trạng thái. Một chế độ tương lai muốn mở rộng allowlist phải có schema/capability mới và phê duyệt riêng; V1 reject cấu hình mở rộng.

## 7. Định dạng `.ai/TEST_RESULTS.json`

### 7.1 Schema logic V1

```json
{
  "schema_version": 1,
  "project": "HMS CAD/CAM",
  "generated_at": "2026-08-04T00:00:00.000000Z",
  "runs": [
    {
      "run_id": "stage13c-focused-001",
      "command": {
        "argv": ["python", "-m", "pytest", "tests/unit/test_example.py"],
        "display": "python -m pytest tests/unit/test_example.py"
      },
      "exit_code": 0,
      "started_at": "2026-08-04T00:00:00.000000Z",
      "completed_at": "2026-08-04T00:00:02.500000Z",
      "duration_seconds": 2.5,
      "counts": {
        "passed": 1,
        "failed": 0,
        "skipped": 0,
        "deselected": 0,
        "xfailed": 0,
        "xpassed": 0,
        "warnings": 0
      },
      "status": "passed",
      "evidence_source": "runner_json",
      "log_path": ".ai/EVIDENCE/stage13c-focused-001.log",
      "log_sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
      "verification": "verified",
      "verification_notes": []
    }
  ]
}
```

### 7.2 Quy tắc validation

- `schema_version`, `project`, `generated_at`, `runs` bắt buộc; `runs` có thể rỗng.
- `run_id` unique, không chứa path separator và không được dùng để chứng minh exact-once nếu thiếu authority riêng.
- `command.argv` là nguồn exact command; `display` nullable và không dùng thực thi.
- Mọi run phải có đủ time/duration/status/source/verification; counter có thể null nếu source không cung cấp nhưng object `counts` bắt buộc.
- `completed_at >= started_at`; duration phải khớp chênh lệch trong tolerance cấu hình nhỏ.
- `status=passed` chỉ có thể `verified` khi `exit_code == 0`, không có count `failed > 0`, evidence source được chấp nhận, và hash/path yêu cầu đã xác minh.
- Thiếu `exit_code`, log bắt buộc, hash bắt buộc hoặc provenance phù hợp thì không được suy diễn PASS; engine hạ nhãn thành `unverified` hoặc reject nếu input tự khai `verified` mâu thuẫn.
- `exit_code != 0` không thể có status `passed`.
- `failed > 0` không thể có status `passed`, kể cả exit code 0.
- `timed_out`, `cancelled`, `error`, `unknown` không bao giờ là verified PASS.
- `manual` có thể được ghi nhận nhưng mặc định `unverified`; chỉ policy tương lai rõ ràng mới nâng cấp.
- `log_path` phải relative trong repo hoặc approved evidence root; engine chỉ hash bytes, không parse tự do để bịa counts.
- Nhiều runs được giữ nguyên từng lần; summary phải chỉ rõ run nào được chọn và lý do, không cộng counts của các scope khác nhau thành một full run.
- Evidence cũ vẫn có timestamp, không tự mang nghĩa “current”. Caller phải chọn hoặc engine áp dụng freshness policy minh bạch.

## 8. Cơ chế checkpoint

### 8.1 Tên và tính bất biến

- Timestamp lấy từ injected UTC clock, không dùng local time.
- Với cấu hình hiện tại, filename là `.ai/CHECKPOINTS/YYYY-MM-DD_HHMMSS.md`.
- Filename phải ổn định từ `created_at`; không dùng PID/random suffix.
- Publisher dùng create-exclusive (`open(..., "xb")` hoặc `os.open` với `O_CREAT|O_EXCL`) và không gọi `os.replace` lên checkpoint đã tồn tại.
- Collision trả lỗi validation/publication; người dùng phải chạy lại tại timestamp khác. Không ghi đè, không đổi tên âm thầm.

### 8.2 Nội dung bắt buộc

Checkpoint gồm:

- schema/checkpoint ID, `run_id`, UTC timestamp;
- project, stage/status và provenance;
- branch hoặc nhãn detached/unborn;
- full HEAD OID đã capture, không dùng short hash làm identity;
- dirty state và summary staged/unstaged/untracked/renamed/deleted;
- từng test run: exact command, exit code, counts, timestamp, hash và `verified`/`unverified`;
- remaining work, blockers và next action từ metadata;
- publication manifest hash và danh sách output;
- commit claim verification.

Nếu metadata nói có “commit mới”, engine phải kiểm tra OID tồn tại và quan hệ với captured HEAD theo claim. Nếu không có commit thật, checkpoint ghi `unverified`/issue và không tuyên bố commit mới. Empty blocker list phải phân biệt `none_reported` với `verified_none`.

## 9. Cơ chế atomic publication

### 9.1 Chuẩn bị

1. Acquire repository-scoped exclusive lock.
2. Recovery journal cũ nếu có; nếu không thể hoàn tất rollback thì dừng `recovery_required`.
3. Tạo `.ai/.sync-tmp/<run_id>/` trên cùng filesystem với `.ai`.
4. Render từng candidate thành temp file UTF-8 không BOM, LF; flush Python buffer, `os.fsync(file_fd)` khi filesystem hỗ trợ.
5. Ghi manifest canonical gồm target, candidate SHA-256, expected old SHA-256/null, byte length và mode.
6. Validate candidate bytes bằng reparse độc lập; validate manifest và allowlist.
7. Re-capture Git/source fingerprint và target hashes để phát hiện concurrent modification.

### 9.2 Publication transaction

1. Với mỗi target cập nhật, tạo backup cùng staging area bằng bytes hiện tại và hash; target chưa tồn tại được đánh dấu `absent`.
2. Ghi journal trạng thái `prepared`, flush/fsync; journal không chứa content nhạy cảm.
3. Publish các mutable state files theo thứ tự cố định bằng `os.replace(temp, target)`. Mỗi replace là atomic ở cấp file trên cùng volume.
4. Sau mỗi replace, verify target SHA-256 và cập nhật journal atomically.
5. Tạo checkpoint cuối bằng create-exclusive, flush/fsync và verify hash.
6. Ghi publication commit marker trong journal với manifest hash; flush directory handle khi nền tảng hỗ trợ.
7. Xóa backup/journal/staging sau khi success được xác minh; release lock.

Checkpoint được publish sau các mutable files và đóng vai trò commit marker bất biến mà consumer có thể kiểm tra. Consumer engine-aware không đọc snapshot mới trong khi lock/journal ở trạng thái prepared. Vì Windows/POSIX không cung cấp một atomic rename chung cho nhiều file ở nhiều thư mục, V1 bảo đảm atomic theo từng file và tính nhất quán của tập bằng lock + journal + checkpoint marker + rollback/recovery; không tuyên bố syscall-level multi-file atomicity.

### 9.3 Partial failure và rollback

- Bất kỳ lỗi nào trước checkpoint commit marker đều đảo ngược target theo thứ tự ngược: backup được `os.replace` về target; target originally absent bị xóa chỉ khi current hash đúng candidate hash.
- Rollback không được xóa/ghi đè file đã bị tiến trình khác thay đổi; trường hợp đó trả `recovery_required`, giữ journal/backup và dừng.
- Sau rollback, verify toàn bộ old hashes. Chỉ khi tất cả khớp mới trả `rolled_back` và dọn staging.
- Nếu checkpoint đã create nhưng bước xác nhận sau đó lỗi, chỉ được xóa checkpoint nếu hash/identity chứng minh chính run vừa tạo và journal chưa committed; không bao giờ đụng checkpoint có sẵn.
- Startup luôn kiểm tra journal trước request mới. Không publication mới khi transaction cũ chưa resolved.
- Output JSON/Markdown không bao giờ được ghi trực tiếp từng phần vào final path, nên không có file nội dung nửa vời.

## 10. CLI dự kiến

```powershell
python tools/update_ai_sync.py inspect
python tools/update_ai_sync.py sync --stage "Stage 13C" --task "..."
python tools/update_ai_sync.py sync --metadata .ai/INPUT.json
python tools/update_ai_sync.py validate
python tools/update_ai_sync.py show-plan
```

### 10.1 Hành vi command

- `inspect`: chỉ in Git/config capability summary; không render/publish.
- `sync`: thực hiện đầy đủ pipeline và publication nếu mọi gate pass.
- `validate`: validate config, evidence, metadata và current/candidate schema; không ghi.
- `show-plan`: in ordered target list, create/update/unchanged action, candidate hash và validation issues; không ghi.

Global options dự kiến: `--repo PATH`, `--config PATH`, `--format human|json`, `--expected-head OID`, `--verbose`. `sync` hỗ trợ `--stage`, `--task`, `--metadata`; inline và file metadata xung đột là CLI/config error. Không nhận shell command string để thực thi.

V1 tuyệt đối không có subcommand hoặc option `stage`, `commit`, `push`, `stash`, `reset`, `clean`, `checkout`, `run-tests`, `watch`. Help text phải nói rõ engine dừng trước Git mutation.

## 11. Exit codes

| Code | Tên ổn định | Ý nghĩa |
|---:|---|---|
| 0 | `SUCCESS` | Command hoàn thành đúng mục tiêu; sync đã publish hoặc xác nhận byte-identical theo policy |
| 2 | `CLI_ERROR` | Sai cú pháp/argument hoặc command không hỗ trợ |
| 3 | `CONFIG_INVALID` | Config thiếu, parse/schema/capability/path lỗi |
| 4 | `NOT_GIT_REPOSITORY` | Không resolve được Git root |
| 5 | `GIT_READ_FAILED` | Git executable/read-only capture/parse/timeout lỗi |
| 6 | `TEST_EVIDENCE_INVALID` | Evidence được cung cấp nhưng sai schema/invariant/hash |
| 7 | `VALIDATION_FAILED` | Metadata/model/render/cross-file validation không đạt |
| 8 | `PUBLICATION_FAILED` | I/O publication lỗi nhưng rollback hoàn chỉnh hoặc không bắt đầu được |
| 9 | `SAFETY_BOUNDARY_VIOLATION` | Vi phạm allowlist/protection/concurrency/capability; gồm recovery required |

Một process trả đúng một code cao nhất theo precedence: safety (9) > publication (8) > validation/evidence/config/Git/CLI theo phase gây dừng. Chi tiết nội bộ nằm trong structured issues, không tạo thêm exit code tùy tiện trong V1.

## 12. Logging

Mỗi event là một JSON object trên một dòng UTF-8 LF:

```json
{"run_id":"...","timestamp_utc":"2026-08-04T00:00:00.000000Z","event":"git_snapshot_captured","severity":"info","component":"git_reader","message":"Git snapshot captured","details":{"dirty":true,"entry_count":37}}
```

Field bắt buộc:

- `run_id`: correlation ID của request;
- `timestamp_utc`: RFC 3339 UTC;
- `event`: stable snake_case event name;
- `severity`: `debug`, `info`, `warning`, `error`, `critical`;
- `component`: module logical name;
- `message`: thông báo ngắn, sanitized;
- `details`: JSON object có cấu trúc.

Quy tắc:

- Log lifecycle/gate/hash/count/path relative, không log file content hoặc diff body.
- Redact remote URL userinfo, token, password, environment secret và query credential.
- Không log full absolute home path theo mặc định; dùng repo-relative hoặc `<repo>`.
- Exact test argv có thể xuất trong evidence/report sau secret scanner; argument nhạy cảm được thay `<redacted>` và evidence ghi đã redact.
- Log I/O lỗi bằng stable code/type, không dump environment.
- JSON serializer deterministic, `ensure_ascii=False`; mọi event parse được độc lập.
- Log sink failure trước publication là warning hoặc fatal theo policy; không fallback vào source tree tùy tiện.

## 13. Kế hoạch test

V1 implementation phải có unit test cho model/config/parser/renderer/validation và integration test trong temporary Git repositories. Test không dùng repository làm fixture có write.

| Trường hợp | Evidence bắt buộc |
|---|---|
| Clean tree | `is_dirty=false`, entries rỗng, không phát minh progress |
| Dirty tree | Preserve exact status; engine chỉ thay allowlisted outputs |
| Modified/untracked/renamed/deleted | Porcelain `-z` parse đúng path, rename origin, index/worktree status |
| Staged file tồn tại trước run | Snapshot phản ánh staged; sync không đổi index hash |
| Detached HEAD | `branch=null`, `is_detached=true`, HEAD đầy đủ |
| Unborn branch | HEAD nullable, không claim commit |
| Repo không remote | Upstream/remote nullable, không lỗi nếu optional |
| Test results thiếu | Tests là no evidence/unknown, không PASS |
| JSON lỗi/duplicate key/BOM | Fail đúng exit code, zero publication |
| Evidence exit code thiếu | Không thể verified PASS |
| Evidence hash sai | Exit 6, zero publication |
| Timestamp collision | Checkpoint cũ byte-identical, không overwrite; run fail |
| Atomic failure trước replace | Final paths không đổi |
| Atomic failure giữa nhiều replace | Rollback exact old hashes |
| Crash/journal recovery | Run sau recovery trước publication mới |
| Rollback gặp concurrent write | Không đè concurrent content; exit 9/recovery required |
| Source-file protection | Candidate/protected path escape bị reject |
| Symlink/reparse escape | Reject dù lexical path nằm dưới `.ai` |
| Git state đổi trước publish | Optimistic fingerprint gate fail, zero publication |
| Windows path | Drive case, separator, long/Unicode path canonical đúng |
| Unicode | Vietnamese content round-trip UTF-8 không BOM |
| CRLF/LF | Render luôn LF; không sửa line ending file ngoài allowlist |
| Checkpoint không ghi đè | `O_EXCL` được chứng minh bằng test concurrent/collision |
| No Git mutation | Fake Git runner reject verb; index/HEAD/refs/config hashes không đổi |
| No test execution | Fake subprocess chứng minh không gọi pytest/test command |
| No stage/commit/push CLI | Parser/help không có command; negative test exit 2 |
| Deterministic render | Cùng normalized state tạo bytes/hash giống nhau, trừ injected timestamp/run_id |
| Secret redaction | Token trong URL/argv không xuất log/handoff |

Test integration publication phải fault-inject tại từng boundary: temp write, fsync, backup, journal, mỗi `os.replace`, checkpoint create, hash verify và cleanup. Acceptance không dựa chỉ vào mock happy path.

## 14. Chia work package

Mỗi WP bắt đầu từ baseline được xác minh riêng, chỉ sửa manifest đã duyệt và dừng ở gate; không tự động bắt đầu WP kế tiếp.

### WP1 — Models, config và validation

- Phạm vi: model/enum, strict config parser, path/time/hash normalization, validation issue framework.
- File dự kiến: `tools/ai_sync/__init__.py`, `models.py`, `config.py`, `validation.py`; unit tests tương ứng.
- Tiêu chí hoàn thành: parse cấu hình hiện tại thành typed config; reject unknown schema/capability/path escape; model invariants đầy đủ.
- Test bắt buộc: JSON strict/duplicate/BOM, Windows path/Unicode, timestamp, nullable/counter, allowlist/denylist.
- Gate dừng: `WP1_READY_FOR_REVIEW`; chưa đọc Git hoặc publish.

### WP2 — Git read-only snapshot

- Phạm vi: Git root, porcelain-v2 `-z`, diff numstat, detached/unborn/upstream/remote redaction và fingerprint.
- File dự kiến: `git_reader.py`; fixtures/temp-repo tests.
- Tiêu chí hoàn thành: mọi trạng thái file mục 13 parse đúng; exact command allowlist; no mutation invariant.
- Test bắt buộc: clean/dirty/staged/modified/untracked/rename/delete/unmerged khi khả thi, detached, no remote, timeout/malformed output.
- Gate dừng: `WP2_GIT_READ_ONLY_READY_FOR_REVIEW`; index/HEAD/refs không đổi.

### WP3 — Test evidence parser

- Phạm vi: schema `.ai/TEST_RESULTS.json`, multiple runs, hash/time/count/status verification.
- File dự kiến: `test_results.py`, JSON schema nếu chọn, unit fixtures/tests.
- Tiêu chí hoàn thành: verified/unverified classification deterministic; missing evidence không thành PASS.
- Test bắt buộc: missing, malformed JSON, negative/mismatched counts, missing/nonzero exit, timeout, hash match/mismatch, multiple scopes.
- Gate dừng: `WP3_TEST_EVIDENCE_READY_FOR_REVIEW`; không chạy pytest ngoài chính test suite được authorize cho WP.

### WP4 — State builder và renderer

- Phạm vi: precedence/provenance, deterministic JSON/Markdown cho năm mutable outputs và checkpoint candidate.
- File dự kiến: `state_builder.py`, `renderers.py`, golden files và unit tests.
- Tiêu chí hoàn thành: cross-file consistent; no invented progress/completion/test; UTF-8 no BOM/LF.
- Test bắt buộc: metadata conflict, null/unknown, escaping Unicode/Markdown, deterministic bytes, commit claim handling.
- Gate dừng: `WP4_RENDER_CANDIDATES_READY_FOR_REVIEW`; chưa ghi final `.ai` paths.

### WP5 — Atomic publisher và checkpoint

- Phạm vi: lock, staging, manifest, backup/journal, optimistic guards, replace, create-exclusive checkpoint, rollback/recovery.
- File dự kiến: `publisher.py`, `checkpoint.py`, fault-injection integration tests.
- Tiêu chí hoàn thành: chỉ allowlist final outputs; all-or-rollback semantic transaction; checkpoint immutable; recovery fail-closed.
- Test bắt buộc: từng publication fault point, collision, concurrent write, unsafe link/path, same-volume check, old/new hash verification.
- Gate dừng: `WP5_ATOMIC_PUBLICATION_READY_FOR_REVIEW`; không orchestration production run.

### WP6 — CLI và orchestration

- Phạm vi: engine state machine, dependency injection, CLI commands/options, stable exit mapping, structured logging.
- File dự kiến: `engine.py`, `cli.py`, `tools/update_ai_sync.py`, CLI/integration tests.
- Tiêu chí hoàn thành: bốn command hoạt động đúng write boundary; không có Git mutation/test runner command.
- Test bắt buộc: every exit code, dry-run zero write, stdout JSON parse, stderr sanitize, Ctrl-C/timeout behavior.
- Gate dừng: `WP6_ENGINE_CLI_READY_FOR_REVIEW`; chưa sync repository thật.

### WP7 — Unit/integration certification

- Phạm vi: hoàn thiện matrix mục 13 trên temp repos, coverage theo risk, platform Windows.
- File dự kiến: `tests/unit/ai_sync/**`, `tests/integration/ai_sync/**`, test helpers giới hạn scope.
- Tiêu chí hoàn thành: toàn matrix pass trên Python 3.14/Windows; no mutation assertions chứng minh bằng hashes.
- Test bắt buộc: focused suite rồi authorized regression scope; full suite chỉ khi có authority riêng.
- Gate dừng: `WP7_AI_SYNC_V1_TESTS_READY_FOR_REVIEW`; không dùng test result để tự commit.

### WP8 — Documentation và first dry-run

- Phạm vi: hướng dẫn operator, schema examples, `inspect`/`validate`/`show-plan` trên repository thật; `sync` thật cần phê duyệt riêng.
- File dự kiến: tài liệu được duyệt riêng; không mặc định sửa file trạng thái trong dry-run.
- Tiêu chí hoàn thành: dry-run report exact intended paths/hashes, safety gates pass, Stage 13C dirty files giữ nguyên.
- Test bắt buộc: pre/post Git porcelain/index/HEAD comparison và no-write audit.
- Gate dừng: `WP8_DRY_RUN_READY_FOR_SYNC_AUTHORIZATION`; không publish, stage, commit hoặc push.

## 15. Tiêu chí chấp nhận V1

V1 chỉ được chấp nhận khi có evidence kiểm chứng được cho tất cả điều sau:

1. Chạy trên Windows 10/11 64-bit với Python `>=3.14,<3.15`, không thêm dependency chưa xác minh tương thích.
2. `inspect`, `validate`, `show-plan` không tạo/sửa/xóa file và không đổi Git HEAD/index/refs/config/worktree.
3. `sync` chỉ có thể thay năm mutable outputs và tạo đúng một checkpoint allowlisted; mọi path khác giữ nguyên byte/hash.
4. Source/protected uncommitted work tồn tại trước run giữ nguyên byte/hash/status, trừ các output allowlisted được người dùng biết rõ.
5. Không code path/CLI option nào chạy test, stage, commit, push, stash, reset, clean hoặc checkout.
6. Git snapshot biểu diễn chính xác clean/dirty, staged/unstaged, untracked, renamed, deleted, detached và no-remote cases.
7. Test evidence thiếu/sai/không có exit code không bao giờ render thành verified PASS.
8. Progress nullable và không bao giờ được engine tự tính; completion chỉ đến từ metadata và không được mâu thuẫn evidence.
9. JSON outputs reparse và validate schema; Markdown có heading bắt buộc; mọi text UTF-8 không BOM, LF.
10. Candidate set được validate toàn bộ trước final write; pre-publish fingerprint bắt được concurrent repository change.
11. Fault injection ở mọi publication boundary chứng minh final set mới hoàn chỉnh hoặc old set được rollback chính xác; unresolved concurrent recovery fail closed.
12. Checkpoint dùng UTC, full HEAD, dirty/test/remaining/blocker/next action/provenance và không thể overwrite khi collision.
13. Structured logs parse được từng dòng và secret-redaction tests pass.
14. Stable exit codes 0, 2–9 được integration test xác nhận.
15. Hai render với cùng normalized inputs/injected clock/run ID tạo byte-identical outputs.
16. First real-repository dry-run chứng minh Stage 13C dirty state không đổi; first real sync cần authorization riêng sau review.
17. Tài liệu operator ghi rõ `.ai` là summary, Git/test evidence mới là source of truth, và engine dừng trước commit/push.

Không được dùng “test pass” chung chung thay acceptance evidence; báo cáo phải ghi exact command, exit code, counts, platform và hash/provenance phù hợp.

## 16. Để dành cho V2+

Các khả năng sau chưa thiết kế triển khai trong V1 và cần threat model, config schema/capability gate, review và phê duyệt riêng:

- tự chạy pytest;
- tự stage;
- tự commit;
- tự push;
- GitHub API;
- knowledge graph;
- conversation memory;
- multi-repository dashboard;
- bidirectional remote instructions;
- background watcher.

Đặc biệt, việc liệt kê stage/commit/push ở V2+ không tạo quyền thực hiện. Mọi phiên bản tương lai vẫn phải mặc định tắt, yêu cầu explicit authorization và bảo vệ uncommitted work.

## 17. Baseline và gate triển khai

Đặc tả này được thiết kế trên cấu hình repository hiện có với `schema_version=1`, `allow_stage=false`, `allow_commit=false`, `allow_push=false`, `run_tests_automatically=false`, và safety flags bảo vệ source/uncommitted work/evidence. Baseline Git khi khảo sát là branch `main`, HEAD `c3021f62553168fcd8b1a60b986f6bea0546fc44`, đồng bộ `origin/main`, với nhiều thay đổi Stage 13C chưa commit.

Tài liệu này không phải evidence rằng engine đã được triển khai hoặc test. Work package đầu tiên được đề xuất là WP1; chỉ bắt đầu trong nhiệm vụ riêng sau khi đặc tả được duyệt.

Gate hiện tại: `AI_SYNC_ENGINE_SPEC_V1_READY_FOR_REVIEW`
