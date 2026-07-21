# Roadmap redesign UI HMS - sau Stage 9A.1

## Nguyên tắc áp dụng cho mọi giai đoạn

- Giữ backward compatibility với project `.HMS`, domain command, Toolpath IR,
  SQLite schema và artifact hiện có.
- Không refactor domain/persistence cùng lúc với migration presentation.
- Cũ và mới dùng cùng application service; không nhân bản business rule vào UI.
- Mỗi stage có screenshot regression, GUI smoke, keyboard/resize check và test
  lifecycle liên quan trước khi bỏ code path cũ.
- Rollback bằng feature flag/factory hoặc commit revert của riêng presentation;
  không cần migrate ngược dữ liệu dự án.
- Không tự bắt đầu stage kế tiếp khi stage hiện tại chưa có mã chạy và test đạt.

## Giai đoạn migration

| Stage | Phạm vi | Backward compatibility / ranh giới domain | Screenshot regression và GUI smoke | Rollback rõ ràng |
|---|---|---|---|---|
| **9A.2 Main Workspace Shell** | Tạo shell ribbon theo workspace, layout left manager - central viewport - right editor - bottom diagnostics; breakpoint và DPI. | Dùng nguyên `MainWindow`, controller/service và dock state hiện có qua adapter; không đổi project/CAD/CAM domain. | Golden ở 1024/1366/1600/1920, DPI 100/125/150%; smoke New/Open/Save, import BREP, viewport, dock close/open. | Feature flag `classic_shell/new_shell`; bỏ new shell trả về composition cũ, không chạm dữ liệu. |
| **9A.3 Operation Manager** | Cây Project > Job > Setup/Machine Group > Stock/Tool Library/Operations/Program Assembly; semantic status và context command. | Mapping bằng domain ID hiện có; không dùng row index; không đổi operation tree/domain contract. | Golden empty/project/dirty/mixed status/stale; smoke selection, rename, reorder, project switch, Save/Open/Recovery. | Factory chọn classic `CamOperationTree`; tree mới có thể tắt mà ID/domain state giữ nguyên. |
| **9A.4 Unified Function Editor Framework** | Section A-I, field metadata presentation, sticky header/footer, draft/action state, inline validation. | Presenter ánh xạ contract hiện có; không thêm field domain, không đổi mutation command hoặc persistence. | Golden Basic/Advanced/Expert/error/disabled/worker progress; smoke keyboard-only, dirty close, invalid no-mutation, stale callback. | Editor registry feature flag theo function type; function chưa migrate tiếp tục widget cũ. |
| **9A.5 Migrate Facing/Contour/Pocket** | Chuyển ba operation milling 2D/2.5D sang framework; Geometry/Tool/Cutting/Levels/Linking theo ngữ cảnh. | Dùng nguyên `FacingParameters`, `Contour...`, `Pocket...`, resolver/service và artifact codec; không đổi toolpath. | Golden từng strategy ở clean/dirty/error; smoke create/bind geometry/calculate/visibility/Save/Open/Autosave/Recovery. | Flag theo strategy; fallback editor cũ đọc cùng operation state, không cần data migration. |
| **9A.6 Migrate Drilling/Tapping/Reaming/Boring** | Editor hole-family dùng chung shell nhưng field/validation theo strategy; dependency cycle/peck/pitch/pre-hole. | Giữ production Tapping fail-closed và mọi capability gate; không đổi hole domain/post contract. | Golden từng cycle và dependency; smoke geometry bind, resource compatibility, calculate, stale, project switch; Tapping không phát NC. | Registry trả từng strategy về editor cũ độc lập; không rollback dữ liệu. |
| **9A.7 Post/Program Assembly UI cleanup** | Gộp workflow action, progressive disclosure, diagnostics drawer, ordered operation table responsive, NC artifact view. | Giữ Post/Assembly request, Simulation gate, checksum, managed/external export và explicit order; không tự Generate/Export. | Golden missing/current/stale/blocked/preview/export; smoke single/multi operation, overwrite policy, Save/Open/Recovery, no machine execution. | Feature flag cho panel Post/Assembly mới; panel cũ vẫn bind cùng service/artifact. |
| **9A.8 CAM 3D Function UI** | Tạo UI đầu tiên cho Machining Zone 3D, Tool, tolerance, safe motion và calculation context; chưa tự động thêm strategy ngoài chỉ thị. | Dùng nguyên Foundation 8A.1; không đổi mesh/contact/persistence hoặc triển khai Parallel Finishing nếu chưa có task riêng. | Golden empty/selection/resolved/stale/error; smoke OCP surface bind, tessellation worker, cache, Save/Open và cancellation. | Tắt CAM 3D editor; config 8A.1 vẫn giữ và đọc được, không xóa cache/source. |
| **9A.9 Lathe UI** | Áp dụng framework cho workspace/operation tiện khi domain tương ứng được chỉ thị và ổn định. | Không suy diễn hay tạo Lathe domain/Post trong migration UI; chỉ presenter cho contract đã tồn tại. | Golden machine/setup/lathe function/linking; smoke lifecycle, keyboard, DPI và fail-closed cho capability chưa hỗ trợ. | Feature flag theo lathe function; tắt UI mới không thay project schema/artifact. |

## Gate hoàn thành cho từng stage

Một stage chỉ hoàn thành khi:

1. Có mã chạy được và hành vi người dùng được tài liệu hóa.
2. Classic/new path (nếu còn) cùng đi qua application service, không lệch domain.
3. Screenshot regression được review ở các state chính và không có clip/overlap.
4. GUI smoke thực hiện luồng project lifecycle liên quan.
5. Unit/integration test cũ không regress; test mới bảo vệ identity, draft, stale và
   background-worker guard.
6. Feature flag/factory rollback được chạy thử.
7. Worktree sạch và commit chỉ chứa phạm vi stage đó.

## Thứ tự và dependency

```text
9A.2 Shell
  -> 9A.3 Operation Manager
  -> 9A.4 Function Editor Framework
      -> 9A.5 Milling 2D/2.5D
      -> 9A.6 Hole operations
      -> 9A.7 Post/Assembly
      -> 9A.8 CAM 3D UI
      -> 9A.9 Lathe UI
```

9A.5-9A.9 không được dùng để refactor domain hoặc triển khai algorithm. Mọi thay
đổi domain lớn phải là task riêng, có kế hoạch/rủi ro riêng và được xác nhận.
