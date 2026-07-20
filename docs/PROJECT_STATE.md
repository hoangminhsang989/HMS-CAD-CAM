# Trạng thái dự án HMS CAD/CAM

## Mốc hiện tại

- Giai đoạn 1–2: khung PySide6 và hệ thống dự án thư mục `.HMS` đã hoàn thành.
- Giai đoạn 3–4: Session Lock, Autosave, Recovery, CAD Kernel và CAD Viewer đã hoàn thành.
- Giai đoạn 5A–5D: import CAD, Measurement BREP, topology tree và CAD view state đã hoàn thành.
- Giai đoạn 6A.1–6A.4: XCAF technical spike, domain model, viewer/tree và persistence đã hoàn thành.
- Giai đoạn 7A.1–7B.9: CAM Foundation, UI, Facing, Contour 2D, Pocket, Drilling, Tapping, Reaming và Boring v1 đã hoàn thành.
- Boring commits: `808a232` Foundation; `881dbbb` Viewer/Recompute; `1843689` UI/Persistence.
- Review Boring v1 không phát hiện lỗi có bằng chứng; HEAD vẫn `1843689`, không tạo commit review.
- Toàn bộ pytest: **805 passed**; SQLite schema: **v4**; worktree sạch.

## Kiến trúc và dữ liệu dự án

- `ProjectService` là API dự án duy nhất được UI sử dụng; manifest JSON UTF-8 và dữ liệu chính dùng SQLite.
- Dự án là thư mục `.HMS`; file CAD nguồn giữ nguyên trong `source/`, không bị chỉnh sửa.
- Tác vụ I/O và import CAD chạy ngoài UI thread; cache có thể xóa và tái tạo.
- CAD API chỉ trao đổi ID/model thuần Python; object OCP, `TDF_Label`, `TopoDS`, AIS và runtime ID chỉ ở adapter nội bộ.

## CAD Kernel, import và Viewer

- Open CASCADE tích hợp qua `cadquery-ocp-novtk`; hỗ trợ STEP/STP, BREP, IGES/IGS và STL.
- STEP part/assembly XCAF hỗ trợ hierarchy lồng nhau, repeated occurrence và transform `parent × local`.
- Viewer/tree/selection/appearance tách theo occurrence; user override thắng source và có thể reset.
- CAD Viewer hỗ trợ camera, Fit All, hướng nhìn chuẩn và ba display mode.
- Import lỗi, worker cũ hoặc signal đến muộn không thay document hiện tại.

## CAM Foundation và Boring v1

- CAM có ID mạnh, unit tường minh, `GeometryReference` bền vững, Job, Setup, WCS, Stock, tooling, machine model và Toolpath IR.
- CAM editable state là dữ liệu chính trong SQLite v4; artifact là derived data dưới `toolpaths/`.
- Boring v1 dùng `boring_v1`, `ToolFamily.BORING_BAR`, `BoringBarGeometry` versioned và `pre_bore_diameter` bắt buộc.
- Radial stock và feed-per-minute dẫn xuất; feed-per-revolution là nguồn chính; controlled axial retract.
- Holder/shank clearance fail-closed; multi-BREP resolver all-or-nothing, re-resolve từng reference, không partial/auto-rebind.
- Semantic presentation, provenance validation, atomic viewer replacement/rollback và stale guards đã hoàn thành.
- CAM tree/editor hỗ trợ Bind/Rebind/Clear, Apply/Generate/Recompute, Show/Hide, Save/Open, Save As, Autosave/Recovery.

## CAM hiện có

- Facing; Planar Face Facing; 2D Contour; Pocket v1; Drilling v1; Tapping v1; Reaming v1; Boring v1.
- Facing hỗ trợ Stock BOX và persistent planar FACE; Contour hỗ trợ outer loop LINE/ARC; Pocket v1 không island.
- Drilling/Tapping/Reaming hỗ trợ persistent hole geometry, explicit/multi-hole và Viewer/Recompute/persistence.
- Feed-per-revolution, semantic spindle/coolant/dwell và provenance fail-closed áp dụng cho các operation liên quan.

## Persistence và giới hạn

- Save/Open/Save As, Autosave/Recovery giữ CAM editable state và artifact metadata trong SQLite v4; COMPUTING được normalize khi load.
- Persistent XCAF key dùng `source_id`, occurrence path có phiên bản và product fingerprint; CAD view state cũ vẫn được hỗ trợ.
- Measurement BREP là read-only; hình học chuẩn hóa chưa lưu vào `model/`; ứng dụng import lại nguồn bất biến khi mở dự án.
- Chưa có Simulation, Collision đầy đủ, Post Processor hoặc G-code.

## Kiểm tra gần nhất

- Boring/domain/viewer/recompute/UI/tooling/resolver: **154 passed**; GUI Windows/PySide6/OCP thật: đạt.
- `pip check`, package imports, `compileall`, UTF-8 source/text và `git diff --check`: đạt.
- Một lần pytest gặp quyền `%TEMP%`; chạy lại với `--basetemp` trong workspace đạt đầy đủ, không phải lỗi dự án.
