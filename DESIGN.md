# HMS CAD/CAM — Design Authority và quy tắc triển khai

## 1. Phạm vi và giới hạn authority

Tài liệu này là luật thiết kế phía implementation cho **HMS CAD/CAM**. Nó áp dụng cho giao diện PySide6 hiện tại và các bề mặt web được owner phê duyệt sau này. Tài liệu không cấp authority thay đổi nghiệp vụ CAM, dữ liệu dự án `.HMS`, logo đã được owner chốt, hay behavior production.

Owner/project requirements là authority cao nhất. `DESIGN.md` (hoặc tài liệu thiết kế tương đương được owner xác định) là design law phía implementation: chuyển yêu cầu được chấp thuận thành quy tắc có thể xây dựng và review. Không optional tool nào được override owner requirements, `DESIGN.md`, phạm vi revision, hay kiến trúc hiện có đã được chấp thuận.

Không có HMS QR authority, artifact, project, template, connector hay quy tắc nào được tái sử dụng cho HMS CAD/CAM. Đặc biệt, `penpot_stage72` và `penpot_hms_qr` không phải authority của dự án này.

## 2. Luồng làm việc requirements-first và zero-config

Luồng mặc định bắt buộc là:

```text
PROJECT REQUIREMENTS / OWNER AUTHORITY
              ↓
DESIGN.md hoặc design law tương đương
              ↓
Existing implementation architecture
              ↓
Implementation + verification
```

Mỗi nhiệm vụ UI mới phải khởi động theo nguyên tắc **ONE PROMPT — ANY PROJECT — ZERO MANUAL CONFIGURATION**. Codex tự thực hiện trong phạm vi read-only cần thiết:

1. Nhận diện repository hiện tại, project identity và Git state.
2. Nhận diện stack implementation và kiến trúc hiện hữu.
3. Định vị requirements và đọc `DESIGN.md` hoặc tài liệu tương đương.
4. Xác định scope design được yêu cầu và khảo sát implementation liên quan.
5. Phát hiện Open Design project hiện có, chỉ bind repository với project đó khi phù hợp và khi tooling optional thực sự sẵn sàng.
6. Thực hiện implementation-native workflow và verification phù hợp với scope.

Owner không phải nhập thủ công project ID, đường dẫn, template, biến config, browser readiness hoặc chuẩn bị browser bootstrap để một nhiệm vụ UI bắt đầu. Nếu optional tool không có, chưa sẵn sàng hoặc không phù hợp, workflow phải fallback sang implementation-native workflow; đây không phải lý do block nhiệm vụ.

## 3. Phân loại tooling optional

Các tool sau là optional helper, không phải release/Stage gate và không thay thế evidence implementation:

| Tool / bề mặt | Phân loại | Cách dùng được phép |
| --- | --- | --- |
| Penpot | Optional Collaboration / Secondary Design Tool | Collaboration, frame/reference khi một nhiệm vụ cụ thể cần và có evidence truy cập được. |
| UICanvas | Optional Rapid Prototype Tool | Exploration, prototype cô lập, preview hoặc screenshot khi cần. |
| Open Design local/project | Optional design workflow | Dùng khi có project phù hợp; không có project không block workflow. |
| Open Design Cloud / AMR | Optional Enhancement Only | Không là authority, không là yêu cầu khởi động, không là gate. |

Không có pipeline vận hành bắt buộc `Penpot → DESIGN.md → Implementation → UICanvas`. Penpot không “wins” mặc định; mọi khác biệt phải đối chiếu với owner/project requirements và `DESIGN.md`, sau đó chỉ thay đổi khi có authority phù hợp. UICanvas không là validation gate bắt buộc và screenshot prototype không chứng minh behavior production.

## 4. Trạng thái tooling tại authority reconciliation R2

Các trạng thái dưới đây chỉ ghi nhận evidence tooling; chúng không là certification production, không là điều kiện release và không block functional development.

- `OPEN_DESIGN_RUNTIME=QUIESCED`.
- `OPEN_DESIGN_CODEX_MCP=ABSENT`.
- Open Design giữ quiesced/unregistered; không tự khởi động, đăng ký hoặc tái sử dụng artifact/attempt cũ làm authority.
- `PENPOT_OPTIONAL_STATUS=PRESENT_BUT_NOT_READY`.
- Penpot attempted local deployment hiện thiếu sufficient reproducible lock/build evidence trong official release/package flow. Không cài, build, approve dependency hay yêu cầu owner login chỉ để đạt Stage gate; chỉ xem xét lại khi nhiệm vụ collaboration/design cụ thể thật sự cần.
- `UICANVAS_OPTIONAL_STATUS=HARDENED_RUNTIME_VERIFIED`.
- UICanvas hardened local evidence: commit `8649ffaffa2d5fd5af94bad8e6b8d5a704df8f36`; tree `5cbfd772850f1b053210d766b6d4c32bae0233d5`; tests `28 passed`; `npm audit`: 0 critical, 0 high, 0 moderate, 0 low.
- `UICANVAS_SCREENSHOT_SMOKE=DEFERRED_UNTIL_NEEDED`. Browser automation error `failed to write kernel assets` không phải Stage blocker, không được bypass bằng custom WebSocket client và không yêu cầu owner mở browser.
- Không push hardened UICanvas fork trong revision reconciliation này. Hardened source, runtime, cache, screenshots và Codex config backup vẫn ở ngoài HMS repository.

Penpot lockfile availability và UICanvas browser screenshot smoke không là hard gate; không công bố readiness tổng hợp như điều kiện release hoặc Stage.

## 5. Ngôn ngữ thị giác

- Dark mode là chế độ chuẩn bắt buộc cho thiết kế mới.
- Giao diện phải có mật độ cao, thiên về desktop CAD/CAM chuyên nghiệp và gần WorkNC.
- Tổ chức toolbar có thể chịu ảnh hưởng Mastercam khi phù hợp với workflow, nhưng không sao chép thiếu kiểm chứng.
- Không tạo cảm giác generic dashboard, SaaS hoặc consumer web app.
- Header và toolbar phải compact; control và icon không được quá lớn.
- Bo góc vừa phải; tránh excessive rounding, card hóa mọi vùng và trang trí không phục vụ tác vụ.
- Ưu tiên giảm mỏi mắt trong phiên lập trình CAM dài: tương phản rõ, bề mặt tối có phân cấp, không dùng màu bão hòa trên diện tích lớn.
- Logo HMS đã được owner review phải được giữ nguyên cho tới khi có authority mới.

## 6. Token và màu

Màu, spacing, typography, kích thước control, radius và state phải đi qua token được quản lý. Không thêm màu hoặc spacing hard-code rải rác khi có thể dùng token chung.

Các màu toolpath là semantic contract, không phải theme accent:

| Ý nghĩa | Màu bắt buộc |
| --- | --- |
| Cutting | Vàng |
| Rapid | Đỏ |
| Lead-in | Trắng |
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

## 10. Evidence và truy vết có điều kiện

Evidence phải tỷ lệ với scope. Một thay đổi không dùng Penpot, UICanvas hay Open Design không vì thế mà thiếu hợp lệ nếu implementation-native review/test đủ theo authority. Khi optional tool được dùng, record tool identity/revision/frame chỉ như supplemental evidence; không được gán authority hoặc certification vượt quá evidence đó.

Screenshot/reference frame chỉ là evidence khi ghi rõ nguồn (owner requirement, implementation hoặc optional prototype), page/frame/component, revision, viewport, UI scale, DPI, theme, data/interaction state, ngày và candidate identity. Acceptance cần kiểm tra overflow, clipping, alignment, density, contrast, focus, disabled/error state, resize behavior, path display, legend và toolpath semantics. Screenshot prototype không chứng minh behavior production.

Khi có Penpot artifact truy cập được và task yêu cầu comparison, truy vết có thể ghi project/page/frame/component identity, revision/timestamp, token/component/state, file implementation, screenshot/test evidence và sai khác đã được owner chấp thuận. Không được ghi conformance với Penpot nếu artifact hoặc comparison evidence không tồn tại. Không commit credential, token, session hoặc private URL chứa secret.

## 11. Quy tắc dùng optional prototype và design tooling

- UICanvas chỉ dùng cho exploration nhanh, prototype cô lập, preview và screenshot khi thực sự cần.
- Prototype phải nằm ngoài production source hoặc trong khu vực tooling được owner chỉ định; dùng dummy data, không đưa dữ liệu khách hàng, credential hoặc project `.HMS` thật vào prototype.
- Không chuyển prototype thành production code bằng cách copy nguyên trạng mà chưa review architecture, accessibility và framework conventions.
- Penpot, UICanvas, Open Design local/project và Open Design Cloud/AMR không tự chứng minh integration/health hoặc design conformance.
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

- [ ] Owner/project requirements, scope và `DESIGN.md`/equivalent đã được đọc; implementation không vượt authority.
- [ ] Dark mode, density, typography, spacing, token và visual language đúng luật này.
- [ ] Hai cột/panel resize đúng min/max; viewport không bị bóp hoặc che; path xem/copy được; không overflow/clipping.
- [ ] Basic/Advanced và CAM parameter editor giữ hierarchy compact.
- [ ] Cutting/Rapid/Lead-in/Lead-out và legend đúng semantic contract.
- [ ] State hover/focus/selected/disabled/loading/error, accessibility, keyboard và DPI/UI scale đã được kiểm tra phù hợp scope.
- [ ] Production behavior không thay đổi ngoài scope được duyệt; không có secret, runtime artifact hoặc dữ liệu người dùng trong commit.

Chỉ khi có optional artifact hoặc task yêu cầu:

- [ ] Nếu dùng Penpot: identity/revision và comparison evidence được ghi; sai khác có owner authority.
- [ ] Nếu dùng UICanvas: prototype cô lập, dummy data và review architecture/accessibility trước khi implementation; screenshot là supplemental evidence, không phải gate.
- [ ] Nếu dùng Open Design: project binding phù hợp và không thay thế owner requirements/`DESIGN.md`; Open Design Cloud/AMR chỉ là enhancement optional.
- [ ] Nếu screenshot/reference frame được dùng: có source, identity, viewport, scale, DPI, theme, state và candidate revision.

## 14. Trạng thái implementation và design drift

Repository hiện có PySide6 token/style tập trung một phần trong `src/hms_cadcam/ui/design_system.py`, `src/hms_cadcam/ui/ui_tokens.py` và `src/hms_cadcam/ui/theme.py`, đồng thời vẫn còn giá trị visual hard-code ở nhiều stylesheet. Baseline hiện hành có các mô tả và token light workspace không phù hợp với dark-mode authority ở tài liệu này.

Đây là design drift đã biết, không phải authority cho phép refactor trong revision tooling. Việc chọn token dark chính thức, đối chiếu optional design artifact nếu task cần, và remediation production UI phải được thực hiện trong revision riêng có owner authority và verification phù hợp.
