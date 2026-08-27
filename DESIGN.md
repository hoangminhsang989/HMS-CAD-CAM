# HMS CAD/CAM — Design Authority và quy tắc triển khai

## 1. Phạm vi và giới hạn authority

Tài liệu này là luật thiết kế phía implementation cho **HMS CAD/CAM**. Nó áp dụng cho giao diện PySide6 hiện tại và các bề mặt web được owner phê duyệt sau này. Tài liệu không cấp authority thay đổi nghiệp vụ CAM, dữ liệu dự án `.HMS`, logo đã được owner chốt, hay behavior production.

Owner/project requirements là authority cao nhất. `DESIGN.md` (hoặc tài liệu thiết kế tương đương được owner xác định) là design law phía implementation: chuyển yêu cầu được chấp thuận thành quy tắc có thể xây dựng và review. Không optional tool nào được override owner requirements, `DESIGN.md`, phạm vi revision, hay kiến trúc hiện có đã được chấp thuận.

Không có HMS QR authority, artifact, project, template, connector hay quy tắc nào được tái sử dụng cho HMS CAD/CAM. Đặc biệt, `penpot_stage72` và `penpot_hms_qr` không phải authority của dự án này.

### R275 current design authority

Trong phạm vi `R275_REST_MACHINING_UI_EDITOR_I18N_FOUNDATION` và các
descendant của revision này cho tới khi có owner authority mới, luồng bắt buộc
là:

```text
OWNER / PROJECT REQUIREMENTS
        ↓
PENPOT DESIGN SYSTEM
        ↓
DESIGN TOKENS + COMPONENT MAPPING
        ↓
DESIGN.md + APPROVED DESIGN PASS
        ↓
PYSIDE6 PRODUCTION IMPLEMENTATION
        ↓
FUNCTIONAL + VISUAL VERIFICATION
```

Penpot là **official design system**, **canonical visual authority** và
**design authority** hiện hành của HMS CAD/CAM. `DESIGN.md` là **project design
law**. Design Tokens + Component Mapping là nguồn canonical cho màu, spacing,
typography và ánh xạ Penpot component sang production component. Owner/project
requirements vẫn là authority cao nhất và có thể thay đổi luật này bằng
authority mới rõ ràng.

Codex + Penpot MCP phải tự nhận diện project/repository/stack, đọc UI và design
system hiện hành, đồng thời đọc và tái sử dụng token/component sẵn có. Khi task
có design work, Codex phải tạo nhiều candidate design trong Penpot, critique và
refine các phương án, rồi chỉ chọn phương án đạt chất lượng chấp nhận cao nhất;
không tạo component trùng lặp và không hard-code style khi token/component có
authority đã tồn tại.

Sau DESIGN PASS, Codex phải chuyển thiết kế Penpot đã được duyệt thành
implementation thật trong framework của dự án; với HMS CAD/CAM, đích production
là PySide6 UI. Penpot data là design source. Code export không phải design
authority và không thay thế việc triển khai, review hoặc kiểm thử production.

HMS CAD/CAM primary UI trong R275 bắt buộc là **LIGHT professional CAD/CAM**.
Ngôn ngữ thị giác phải gần WorkNC, high-density, compact,
engineering-first và workstation-oriented; có thể dùng compact
Mastercam-influenced grouping khi phù hợp nhưng không sao chép thương hiệu.
Authority dark-mode trước đây bị supersede riêng trong phạm vi R275. Các luật
dark mode tổng quát hoặc checklist dark mode ở các section lịch sử bên dưới
không được dùng để đổi R275 trở lại dark shell.

## 2. Luồng làm việc requirements-first và zero-config

Luồng mặc định bắt buộc là:

```text
PROJECT REQUIREMENTS / OWNER AUTHORITY
              ↓
PENPOT + DESIGN TOKENS + COMPONENT MAPPING
              ↓
DESIGN.md + APPROVED DESIGN PASS
              ↓
PYSIDE6 PRODUCTION IMPLEMENTATION + VERIFICATION
```

Mỗi nhiệm vụ UI mới phải khởi động theo nguyên tắc **ONE PROMPT — ANY PROJECT — ZERO MANUAL CONFIGURATION**. Codex tự thực hiện trong phạm vi read-only cần thiết:

1. Nhận diện repository hiện tại, project identity và Git state.
2. Nhận diện stack implementation và kiến trúc hiện hữu.
3. Định vị requirements, đọc `DESIGN.md`, Penpot design system, token và component mapping hiện hành.
4. Xác định scope design, tái sử dụng token/component và khảo sát implementation liên quan.
5. Khi cần design work, tạo nhiều candidate trong Penpot, critique/refine và chọn phương án đạt chất lượng chấp nhận cao nhất.
6. Sau DESIGN PASS, triển khai bằng PySide6 và thực hiện verification phù hợp với scope.

Owner không phải nhập thủ công project ID, đường dẫn, template, biến config hoặc browser readiness để một nhiệm vụ UI bắt đầu. Nếu Penpot/MCP không truy cập được thì phải báo trạng thái truthful và dừng design work phụ thuộc authority thay vì tự hạ Penpot thành optional hoặc thay bằng một nguồn không canonical.

## 3. Phân loại design authority và tooling

Phân loại hiện hành:

| Tool / bề mặt | Phân loại | Cách dùng được phép |
| --- | --- | --- |
| Penpot | Official Design System / Canonical Visual Authority | Nguồn thiết kế chính thức; quản lý design, token, component và candidate design. |
| UICanvas | Optional Rapid Prototype Tool | Exploration, prototype cô lập, preview hoặc screenshot khi cần. |
| Open Design local/project | Historical / Noncanonical Evidence | Chỉ bảo toàn evidence lịch sử R275; không override Penpot hoặc `DESIGN.md`. |
| Open Design Cloud / AMR | Historical / Noncanonical Tooling | Không là current design authority và không thay thế Penpot. |

Pipeline design hiện hành là `Penpot → DESIGN PASS → PySide6 production implementation → functional + visual verification`. UICanvas không là validation gate bắt buộc và screenshot prototype không chứng minh behavior production.

## 4. Historical R275 Open Design evidence — không phải current canonical authority

Open Design Local đã được dùng trong giai đoạn design R275 trước đây và tạo ra visual/architecture evidence lịch sử. Current owner authority đã supersede Open Design bằng Penpot canonical visual authority. Evidence lịch sử không được xóa hoặc relabel thành current authority.

`APPROVED_R275_OPEN_DESIGN_UI_ARCHITECTURE` được bảo toàn như kết quả của historical R275 Open Design phase; approval này không override Penpot canonical visual authority được ban hành sau đó.

Mandatory historical packaging disclosure:

```text
DELIVERABLE_VALID=false
DELIVERABLE_VALIDATION=entry_missing
REGISTRATION_RECONCILED=false
REGISTRATION_STATUS=workspace-context-required
```

Các trạng thái dưới đây chỉ ghi nhận evidence tooling lịch sử; chúng không là certification production, không là điều kiện release và không block functional development.

- `OPEN_DESIGN_RUNTIME=QUIESCED`.
- `OPEN_DESIGN_CODEX_MCP=ABSENT`.
- Open Design giữ quiesced/unregistered; không tự khởi động, đăng ký hoặc tái sử dụng artifact/attempt cũ làm authority.
- Historical `PENPOT_OPTIONAL_STATUS=PRESENT_BUT_NOT_READY` là snapshot cũ đã bị supersede; không mô tả authority Penpot hiện hành.
- `UICANVAS_OPTIONAL_STATUS=HARDENED_RUNTIME_VERIFIED`.
- UICanvas hardened local evidence: commit `8649ffaffa2d5fd5af94bad8e6b8d5a704df8f36`; tree `5cbfd772850f1b053210d766b6d4c32bae0233d5`; tests `28 passed`; `npm audit`: 0 critical, 0 high, 0 moderate, 0 low.
- `UICANVAS_SCREENSHOT_SMOKE=DEFERRED_UNTIL_NEEDED`. Browser automation error `failed to write kernel assets` không phải Stage blocker, không được bypass bằng custom WebSocket client và không yêu cầu owner mở browser.
- Không push hardened UICanvas fork trong revision reconciliation này. Hardened source, runtime, cache, screenshots và Codex config backup vẫn ở ngoài HMS repository.

Các snapshot tooling lịch sử không được dùng để hạ authority Penpot hiện hành hoặc công bố Open Design packaging là PASS.

## 5. Ngôn ngữ thị giác

- LIGHT professional CAD/CAM là chế độ chuẩn bắt buộc cho R275; dark-mode authority cũ đã bị supersede.
- Giao diện phải có mật độ cao, thiên về desktop CAD/CAM chuyên nghiệp và gần WorkNC.
- Tổ chức toolbar có thể chịu ảnh hưởng Mastercam khi phù hợp với workflow, nhưng không sao chép thiếu kiểm chứng.
- Không tạo cảm giác generic dashboard, SaaS hoặc consumer web app.
- Header và toolbar phải compact; control và icon không được quá lớn.
- Bo góc vừa phải; tránh excessive rounding, card hóa mọi vùng và trang trí không phục vụ tác vụ.
- Ưu tiên giảm mỏi mắt trong phiên lập trình CAM dài: tương phản rõ, bề mặt light có phân cấp và không dùng màu bão hòa trên diện tích lớn.
- Logo HMS đã được owner review phải được giữ nguyên cho tới khi có authority mới.

## 6. Token và màu

Design Tokens + Component Mapping là nguồn canonical cho màu, spacing, typography và ánh xạ Penpot component ↔ production component. Kích thước control, radius và state phải đi qua token được quản lý. Không thêm màu hoặc spacing hard-code rải rác khi authoritative token/component đã tồn tại.

Các màu toolpath là semantic contract, không phải theme accent:

| Ý nghĩa | Màu bắt buộc |
| --- | --- |
| Cutting | Vàng |
| Rapid | Đỏ |
| Lead-in | Trắng với độ tương phản đủ |
| Lead-out | Xanh lá |

- Legend phải hiển thị rõ tên và màu; không được dựa chỉ vào màu nếu có thể bổ sung kiểu nét, ký hiệu hoặc nhãn.
- Không đoán RGB/index mapping từ Mastercam. Owner visual match và mapping index RGB được kiểm chứng là hai trạng thái độc lập.
- Mọi token mới phải có tên semantic; không đặt tên theo vị trí tạm thời như `left_blue_2`.

## 7. Mật độ, typography và accessibility

- Dùng thang spacing nhỏ, nhất quán và phù hợp high-density CAD/CAM UI.
- Control nhập CAM phải compact nhưng vẫn có target tương tác và focus state rõ.
- Khoảng cách phải biểu đạt nhóm và hierarchy; không dùng whitespace lớn như dashboard marketing.
- Header, tab, tree row, table row và toolbar phải tối ưu cho nhiều thông tin trên màn hình desktop.
- Không giảm font hoặc control đến mức khó đọc/chạm; density không được đánh đổi accessibility.
- Dùng font hệ thống Windows/PySide6 tương thích đầy đủ tiếng Việt.
- Cấp chữ tối thiểu gồm: tiêu đề workspace, tiêu đề panel, label field, giá trị, unit, trạng thái và trợ giúp.
- Dùng weight có tiết chế; không dùng toàn bộ chữ đậm để thay hierarchy.
- Số, tọa độ và thông số CAM cần canh hàng ổn định; dùng tabular figures khi framework hỗ trợ.
- Text phải wrap, elide hoặc mở rộng hợp lý; không được overflow hoặc bị cắt mà không có cách xem đầy đủ. File path khi rút gọn phải có affordance để xem/copy đầy đủ.
- Bảo đảm contrast đọc được trên bề mặt thực tế, focus visible và keyboard navigation có nghĩa.
- Label phải liên kết với control; error phải chỉ rõ field và cách xử lý. Không phụ thuộc chỉ vào màu, animation hoặc hover.
- Hỗ trợ UI scale/DPI Windows; kiểm tra text tiếng Việt ở scale mục tiêu. Motion phải ngắn, có mục đích và tôn trọng reduced-motion khi áp dụng cho web.

## 8. Component, state và CAM parameter editor

Mỗi component được duyệt phải có các state phù hợp: default, hover, focus, active/selected, disabled, loading, validation warning và validation error.

- Button chính chỉ dùng cho hành động chính của vùng hiện tại; icon phải có label/tooltip khi nghĩa không hiển nhiên.
- Tree, table và property grid phải hỗ trợ scan nhanh, selection rõ và keyboard focus rõ. Trạng thái không được truyền đạt chỉ bằng màu.
- Tác vụ nền phải thể hiện progress, cancel và failure mà không đóng băng UI.
- Các đường liên kết, curve và toolpath phải mượt, liên tục và không đứt do scale/rendering.
- Editor CAM dùng cấu trúc hai cột resizable khi phù hợp: điều hướng/nhóm ở một phía và parameter/content ở phía còn lại.
- Basic và Advanced phải có hierarchy rõ, không nhân đôi cùng một parameter ở hai nơi. Label, input, unit, validation và nguồn giá trị phải thẳng hàng, dễ quét; parameter quan trọng không bị giấu sau nhiều lớp disclosure.
- Hình minh họa phải đúng tỷ lệ hoặc ghi rõ là schematic. Preview không được trình bày như kết quả toolpath production khi chưa có tính toán và evidence thật.

## 9. Layout resizable và responsive

- Hai cột phải resize được trong giới hạn min/max hợp lý; không khóa kích thước theo một màn hình phát triển.
- Viewport là vùng ưu tiên và không được bị panel che hoặc ép xuống kích thước không sử dụng được.
- Splitter, dock và panel phải giữ layout hợp lý khi cửa sổ thay đổi kích thước.
- Ở kích thước hẹp, nội dung phải reflow, collapse có chủ ý hoặc cuộn trong đúng vùng; không để text/control chồng nhau.
- Không gắn cứng đường dẫn hoặc kích thước dựa trên workstation của người phát triển.

## 10. Functional, visual gate và baseline protection

Evidence phải tỷ lệ với scope. Acceptance có thể gồm deterministic application execution, deterministic screenshots, pixel diff, SSIM, interaction tests, accessibility tests và functional tests. Implementation có thể được sửa cho tới khi conform với approved Penpot design và functional contract.

Screenshot/reference frame chỉ là verification evidence, không phải canonical design source. Khi dùng screenshot phải ghi rõ nguồn, page/frame/component, revision, viewport, UI scale, DPI, theme, data/interaction state, ngày và candidate identity. Acceptance cần kiểm tra overflow, clipping, alignment, density, contrast, focus, disabled/error state, resize behavior, path display, legend và toolpath semantics. Screenshot prototype không chứng minh behavior production.

Truy vết Penpot phải ghi project/page/frame/component identity, revision/timestamp, token/component/state, file implementation, screenshot/test evidence và sai khác đã được owner chấp thuận. Không được ghi conformance với Penpot nếu artifact hoặc comparison evidence không tồn tại. Không commit credential, token, session hoặc private URL chứa secret.

Visual baseline tuyệt đối không được sửa chỉ để làm regression test PASS. Mọi thay đổi design intent hoặc visual baseline đều cần approval riêng, rõ ràng. Code export và screenshot đều không phải design authority.

```text
PENPOT_CANONICAL_VISUAL_AUTHORITY=true
DESIGN_MD_PROJECT_LAW=true
DESIGN_TOKENS_COMPONENT_MAPPING_AUTHORITY=true
CODE_EXPORT_IS_DESIGN_AUTHORITY=false
SCREENSHOT_IS_DESIGN_AUTHORITY=false
BASELINE_SELF_UPDATE_FOR_PASS=forbidden
DESIGN_INTENT_CHANGE_REQUIRES_APPROVAL=true
BASELINE_CHANGE_REQUIRES_APPROVAL=true
```

## 11. Quy tắc dùng prototype và design tooling

- UICanvas chỉ dùng cho exploration nhanh, prototype cô lập, preview và screenshot khi thực sự cần.
- Prototype phải nằm ngoài production source hoặc trong khu vực tooling được owner chỉ định; dùng dummy data, không đưa dữ liệu khách hàng, credential hoặc project `.HMS` thật vào prototype.
- Không chuyển prototype thành production code bằng cách copy nguyên trạng mà chưa review architecture, accessibility và framework conventions.
- Penpot là design authority nhưng artifact/connection tồn tại không tự chứng minh production integration, health hoặc design conformance; các kết luận này vẫn cần evidence tương ứng. UICanvas và Open Design không có authority canonical.
- Không tool nào tự thay đổi config, rules, process hay repository để “sẵn sàng”; mọi runtime/config mutation phải có authority riêng.

## 12. Forbidden patterns và artifact không được commit

Không được dùng các pattern sau:

- Generic dashboard/web-app look, KPI card hoặc marketing hero không phục vụ workflow CAD/CAM.
- Control/icon quá lớn, excessive rounding, shadow và gradient trang trí.
- Text overflow, path bị che không thể xem, panel cố định không resize.
- Màu/spacing/font rải rác không qua token khi đã có semantic token.
- Hình minh họa sai tỷ lệ nhưng không ghi schematic.
- Prototype, mock, screenshot hoặc healthy sample được báo là implementation/certification.
- Bất kỳ optional tool nào override owner requirements hoặc `DESIGN.md`; Markdown không được gọi là native artifact của một tool khác.
- Tự đổi logo, palette hoặc layout đã owner chốt khi chưa có authority.
- Tạo manual project ID/path/template/config/browser-readiness gate cho workflow zero-config.
- Dùng HMS QR artifact, connector, project hoặc authority cho HMS CAD/CAM.

Không commit các artifact sau vào HMS CAD/CAM repository:

- `node_modules`, package cache, Penpot cache, UICanvas runtime/hardened source, process/log/temporary runtime artifact.
- Screenshot, prototype output hoặc export chỉ được dùng làm tooling evidence nhưng chưa được scope/owner phê duyệt để lưu trong repository.
- Codex config, config backup, credential, token, session, private URL chứa secret, browser profile hoặc dữ liệu khách hàng/project `.HMS` thật.

## 13. Checklist review có điều kiện

Luôn kiểm tra:

- [ ] Owner/project requirements, Penpot design system, token/component mapping, scope và `DESIGN.md` đã được đọc; implementation không vượt authority.
- [ ] LIGHT professional CAD/CAM, density, typography, spacing, token và visual language đúng luật này.
- [ ] Hai cột/panel resize đúng min/max; viewport không bị bóp hoặc che; path xem/copy được; không overflow/clipping.
- [ ] Basic/Advanced và CAM parameter editor giữ hierarchy compact.
- [ ] Cutting/Rapid/Lead-in/Lead-out và legend đúng semantic contract.
- [ ] State hover/focus/selected/disabled/loading/error, accessibility, keyboard và DPI/UI scale đã được kiểm tra phù hợp scope.
- [ ] Production behavior không thay đổi ngoài scope được duyệt; không có secret, runtime artifact hoặc dữ liệu người dùng trong commit.

Khi có design/verification artifact hoặc task yêu cầu:

- [ ] Penpot identity/revision và comparison evidence được ghi; sai khác có owner authority.
- [ ] Nếu dùng UICanvas: prototype cô lập, dummy data và review architecture/accessibility trước khi implementation; screenshot là supplemental evidence, không phải gate.
- [ ] Nếu tham chiếu Open Design: evidence được ghi là historical/noncanonical và không thay thế Penpot hoặc `DESIGN.md`.
- [ ] Nếu screenshot/reference frame được dùng: có source, identity, viewport, scale, DPI, theme, state và candidate revision.

## 14. Trạng thái implementation và design drift

Repository hiện có PySide6 token/style tập trung một phần trong `src/hms_cadcam/ui/design_system.py`, `src/hms_cadcam/ui/ui_tokens.py` và `src/hms_cadcam/ui/theme.py`, đồng thời vẫn còn giá trị visual hard-code ở nhiều stylesheet. Việc tồn tại hard-code là design drift đã biết; light workspace hiện hành phải được đánh giá theo Penpot, Design Tokens + Component Mapping và visual law R275.

R5F không cấp authority refactor production UI. Việc remediation token/style hoặc thay đổi visual intent/baseline phải được thực hiện trong revision riêng có owner approval và verification phù hợp.
