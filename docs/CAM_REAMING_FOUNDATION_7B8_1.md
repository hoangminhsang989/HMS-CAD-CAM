# CAM Reaming Foundation 7B.8.1

## Phạm vi

Giai đoạn 7B.8.1 bổ sung domain và strategy foundation cho Reaming 2.5D với
`strategy_key` là `reaming_v1`, `strategy_version` là `1` và
`OperationFamily.DRILLING`. Phần này không bổ sung UI, Viewer, mô phỏng,
collision, Post Processor hoặc G-code.

## Geometry được tái sử dụng

Reaming dùng nguyên trạng `HoleReference`, `HoleLocation`, `HolePattern`,
`DrillGeometryInput`, `DrillDepthDefinition`, `DrillingRegion` và
`DrillingGeometryResolver`. Vì vậy strategy hỗ trợ explicit point, BREP vertex,
full circular edge, repeated XCAF occurrence và multi-hole theo thứ tự canonical.

Resolver vẫn fail-closed khi reference missing, stale, ambiguous, source mismatch
hoặc topology changed. Reaming không tự rebind và không tạo geometry selector mới.
Đường kính của circular edge, khi có, chỉ dùng để xác nhận đường kính thành phẩm;
nó không chứng minh hoặc cung cấp đường kính pre-hole.

## Pre-hole và stock allowance

`pre_hole_diameter` là dữ liệu bắt buộc do người dùng khai báo. Reaming v1 không
tham chiếu artifact Drilling, không thêm dependency tới nguyên công trước và không
tự suy đoán pre-hole từ CAD hoặc toolpath.

Stock mỗi phía là giá trị suy ra:

```text
stock_per_side = (nominal_diameter - pre_hole_diameter) / 2
```

Pre-hole phải dương và nhỏ hơn nominal diameter. Stock mỗi phía phải lớn hơn
tolerance và nhỏ hơn nominal radius trừ tolerance. Không có allowance công nghệ
mặc định vì domain chưa có dữ liệu khuyến nghị từ nhà sản xuất dao.

## Strategy và unit

`ReamingStrategy` là dataclass frozen, versioned và thuần Python. Dữ liệu nguồn
gồm unit, geometry/depth, nominal diameter, pre-hole diameter, RPM, feed mỗi vòng,
clearance/retract height, spindle direction, controlled-retract policy, coolant,
dwell và tolerance. Chỉ MM hoặc INCH được chấp nhận; `UNKNOWN` bị từ chối.

Depth dùng tọa độ tuyệt đối trong Setup WCS:

```text
top_z > final_depth
clearance_height > retract_height > top_z
```

Cutting depth, stock mỗi phía và feed mỗi phút đều là derived-only. Chúng không
được lưu như các nguồn dữ liệu độc lập.

## Tool và machine validation

Chỉ `ToolFamily.REAMER` với `CylindricalGeometry` được chấp nhận. Generator kiểm
tra Tool Assembly/Definition snapshot, revision, fingerprint, unit, diameter,
flute length, usable length và stickout. Không có cơ chế tự chọn dao thay thế.

Machine phải là MILL hoặc MILL_TURN, khai báo `OperationCapability.DRILLING`, có
spindle hỗ trợ đúng chiều quay và RPM, đồng thời derived feed-per-minute không
được vượt `maximum_feed`. Reaming không yêu cầu synchronized tapping.

Khi coolant khác OFF, cả Tool và Machine phải khai báo capability tương ứng.
Mapping semantic hiện tại là FLOOD, MIST hoặc THROUGH_TOOL/THROUGH_SPINDLE; không
ánh xạ sang M-code.

## Feed, retract và Toolpath IR

Feed mỗi vòng là nguồn dữ liệu chính. Artifact đặt `FeedMode.UNITS_PER_REVOLUTION`;
feed mỗi phút chỉ được suy ra bằng `feed_per_revolution × RPM` để kiểm tra machine.
Toolpath statistics chỉ tính duration đầy đủ khi spindle state hợp lệ đã được biết.

Mỗi lỗ có sequence controller-neutral:

1. Rapid tới clearance và approach tới retract plane.
2. Marker `ream.process_begin`, spindle/coolant begin.
3. Linear cutting feed tới final depth.
4. Optional dwell nếu người dùng nhập giá trị lớn hơn 0.
5. Linear controlled feed retract tới retract height.
6. Marker `ream.hole_complete`, rapid lên clearance.
7. Coolant/spindle OFF và marker `ream.process_end`.

Không rapid trực tiếp từ đáy, không peck reaming và không dwell ngầm. Toolpath IR
hiện có đã đủ nên không cần thay đổi schema hoặc event model.

## Fingerprint, recompute và persistence

Input fingerprint bao gồm strategy/version, geometry fingerprint và canonical hole
order, operation/setup/WCS, Tool Assembly/Definition snapshot và Machine snapshot.
Nominal/pre-hole, RPM, feed, depth, height, dwell, spindle, coolant và retract
policy nằm trong strategy fingerprint. Reaming v1 không có upstream artifact
fingerprint.

Generator dùng computation token và `publish_toolpath`. Candidate stale, operation
bị disable/xóa hoặc provenance không khớp sẽ không được publish. Nếu recompute
thất bại sau khi đã có artifact VALID, service khôi phục snapshot và artifact cũ.
Không có partial artifact được publish.

Strategy được lưu qua `OperationParameterSet` và operation codec hiện tại trong
SQLite v4. Geometry JSON được chunk deterministic; future version bị từ chối.
Runtime computation token và native OCP/PySide6 object không được lưu lâu dài.

## Giới hạn Reaming v1

- Pre-hole là khai báo của người dùng, chưa được chứng minh từ artifact upstream.
- Chỉ controlled feed retract; không có lựa chọn rapid retract.
- Không có allowance công nghệ theo vật liệu hoặc catalog dao.
- Không có peck reaming, UI, Viewer, simulation, collision hoặc controller syntax.
