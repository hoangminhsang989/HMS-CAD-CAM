# Nhiệm vụ hiện tại - Stage 9A.1

## Trạng thái ưu tiên

- Baseline: `495d080` - hoàn thành CAM 3D Foundation 8A.1.
- **Stage 8A.2.1 - Parallel Finishing Algorithm Foundation: DEFERRED.**
- **Current priority: Stage 9A.1 - UI Reference Extraction and UX Architecture.**

## Phạm vi 9A.1

- Bóc tách có chọn lọc trang giao diện từ tài liệu Mastercam/WorkNC riêng.
- Audit production UI HMS hiện tại bằng PySide6/OCP thật.
- Chốt kiến trúc main workspace, Operation Manager và Unified Function Editor.
- Định nghĩa progressive disclosure BASIC/ADVANCED/EXPERT.
- Tạo wireframe HMS riêng và roadmap migration 9A.2-9A.9.

## Artifact được quản lý trong Git

- `tools/build_ui_reference_catalog.py`
- `docs/UI_REFERENCE_CATALOG.md`
- `docs/UI_UX_ARCHITECTURE_9A1.md`
- `docs/UI_FUNCTION_EDITOR_SPEC_9A1.md`
- `docs/UI_PARAMETER_DISCLOSURE_RULES.md`
- `docs/UI_REDESIGN_ROADMAP.md`
- `docs/ui_wireframes/*.svg`

Ảnh/PDF/ZIP và metadata render nằm trong
`reference_private/DERIVED/UI_REFERENCE/`, bị Git ignore và không phải source
of truth.

## Không thuộc phạm vi

- Không sửa production UI trong 9A.1.
- Không đổi CAD/CAM domain, Toolpath IR hoặc SQLite schema.
- Không triển khai Parallel Finishing hoặc algorithm CAM mới.
- Không bắt đầu 9A.2 khi chưa có chỉ thị riêng.
