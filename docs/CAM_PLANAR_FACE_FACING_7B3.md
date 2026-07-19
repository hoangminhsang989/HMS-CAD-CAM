# CAM Planar Face Facing - Giai doan 7B.3

## Hop dong persistent FACE

`GeometryReference` la du lieu editable duy nhat duoc luu trong
`Operation.geometry_inputs`. Selector v1 co dang
`hms_face_v1:<container_sha256>:<face_sha256>` va chi dung `source_id`, occurrence
path, persistent container identity va fingerprint hinh hoc. `CadDocumentId`,
`CadObjectId` va runtime topology index cua Viewer khong duoc ghi vao project.

Resolver fail-closed khi source/revision/topology khong khop, mat occurrence,
khong duy nhat, hoac selector khong hop le. He thong khong tim face gan giong va
khong tu rebind. Bind/Rebind bi huy hoac loi giu nguyen reference dang co.

## Descriptor va don vi

`PlanarFaceDescriptor` la value object thuan Python, khong chua OCP handle. No
mang reference identity, plane origin/basis/normal, outer va inner loops, bounds,
don vi, geometry fingerprint va provenance cua occurrence transform. Resolver
chi khoi tao khi project manifest khai bao ro `mm` hoac `inch`; `UNKNOWN` bi tu
choi, khong suy doan thanh mm.

OCP adapter chi nhan planar BREP FACE co outer wire kin gom LINE/ARC. Arc duoc
sample co sai so chord co dinh de fingerprint xac dinh. Inner loop, open wire,
self-intersection va curve khac LINE/ARC tra diagnostic ro rang. Descriptor luon
duoc resolve lai tu CAD source; khong duoc luu lam master data.

## Quy uoc Facing

Selected FACE la `TARGET_PLANE`. `top_height` phai trung mat tren cua Stock BOX
trong Setup WCS; `target_height` phai trung mat phang FACE sau khi doi
world/model sang Setup WCS. Normal song song hoac doi huong tool axis deu hop le,
sau do duoc chuan hoa ve +Z Setup WCS. Target cao hon stock top ngoai tolerance
bi tu choi.

Raster duoc clip theo polygon that, khong thay bang bounding box. Polygon lom co
the sinh nhieu cutting segment tren mot scanline. Moi segment co approach,
cutting va retract rieng, nen lien ket qua khoang ngoai polygon luon o safe
motion. Planar boundary khong them overtravel ra ngoai polygon. Tong so cutting
pass sau clipping va depth levels khong vuot 20.000.

## Atomicity va lifecycle

Luon thuc hien `resolve -> Setup WCS -> generate -> publish` theo mot candidate.
Loi o bat ky buoc nao khong publish file partial. Recompute loi giu artifact
`VALID` truoc do. CAD reload/reimport lam reference khong resolve duoc se danh
candidate cu `DIRTY`; thay reference, WCS hoac Stock cung lam input fingerprint
thay doi.

SQLite van o schema v4. Save/Open, Save As va Autosave/Recovery round-trip
`GeometryReference`; stale reference khong xoa operation editable. Runtime
selection cua CAD Viewer van doc lap va chi duoc adapter hoa tai lenh Bind.

## Gioi han

V1 chua ho tro inner loops/islands, spline boundary, pocket, contour, drilling,
turning, Post Processor, G-code, stock-removal, collision hay simulation day du.
