# Nền tảng Simulation/Collision — Giai đoạn 7C.1

## Phạm vi đã triển khai

7C.1 cung cấp một luồng mô phỏng headless, đồng bộ và xác định cho
`ToolpathArtifact` đã publish. Luồng này không điều khiển Viewer/UI, không tính
bóc vật liệu, không giải động học máy/IK và không sinh G-code.

Đầu vào chỉ hợp lệ khi operation đang bật, lifecycle artifact là `VALID`,
artifact là `COMPLETE`, có fingerprint đúng với operation hiện tại và dùng hệ
tọa độ `SETUP_WCS`. Mọi nguồn thiếu, stale, sai đơn vị, không rõ quyền sở hữu
native hoặc hình học không hỗ trợ đều dừng theo nguyên tắc fail-closed; kết quả
cũ không bị thay thế.

## Hợp đồng dữ liệu

- `SimulationRequest`, `SimulationResult`, `SimulationIssue`,
  `SimulationStatistics` và `SimulationSamplingPolicy` là dataclass bất biến.
- Codec JSON là strict, UTF-8, `format_version = 1`; field lạ, version tương lai,
  enum lạ, số NaN/Infinity và fingerprint sai đều bị từ chối.
- Fingerprint đầu vào bao phủ artifact, operation/revision/enabled, WCS, stock,
  fixture/reference/transform, tool/holder/assembly, machine, policy và phiên bản
  thuật toán. UUID request/result, thời gian chạy, thread và đường dẫn không tham
  gia fingerprint.
- Issue được sắp ổn định theo severity, category, event, segment, sample, entity
  và evidence. Evidence chỉ chứa cặp chuỗi có thứ tự; không chứa OCP/Qt/native.

## Sampling và tọa độ

- LINE dùng `ceil(length / max_linear_step)` và luôn giữ endpoint chính xác.
- ARC đồng thời tuân thủ chord tolerance và góc cung tối đa, giữ dấu sweep.
- Junction chỉ có một world sample và lưu provenance của cả đoạn vào/ra.
- Dwell/marker không tạo chuyển động; spindle/coolant được truyền theo trạng thái
  process. V1 chỉ nhận tool axis cố định.
- Điểm được đổi từ Setup WCS sang world bằng origin và basis đúng một lần; vector
  chỉ dùng basis. Fixture occurrence transform cũng chỉ được áp dụng một lần.
- Giới hạn mặc định: 250.000 sample, hard max 1.000.000, chunk 2.048, kiểm tra
  cancel không quá 256 sample, 10.000 issue và 256 MiB.

## Envelope và collision

Envelope gồm cutter, shank thực và toàn bộ holder profile. End mill/ball/drill
dùng hình học phù hợp; tap dùng nominal cylinder bảo thủ; boring dùng maximum
rotating envelope. Thiếu/malformed/stale placement của tool hoặc holder sẽ dừng.

Stock V1 chỉ hỗ trợ BOX có frame. Fixture chỉ nhận persistent BREP BODY hoặc
OCCURRENCE đã resolve duy nhất, đúng source/revision/fingerprint và xác nhận quyền
sở hữu OCP. Broad phase dùng AABB quét giữa sample; narrow phase do backend cung
cấp. Broad overlap không có bằng chứng narrow chỉ sinh clearance warning, không
được báo collision.

Ma trận kiểm tra V1 gồm cutter–fixture, shank–stock/fixture,
holder–stock/fixture, rapid dưới safe plane và cutter đi xuyên stock khi không
cutting. Cutter–stock trong cutting là hợp lệ. Machine solid, bàn/trục, IK,
target-part gouge khi cutting và continuous exact collision chưa nằm trong 7C.1.

## Publish và vòng đời

`SimulationRuntimeService` lưu runtime-only theo project generation và operation.
Mỗi lần tính có token latest-wins. Candidate chỉ được publish nguyên tử sau khi
kiểm tra lại token, generation, request/input/artifact fingerprint. Cancel,
sample/memory limit, stale source hoặc runtime failure không publish partial và
giữ kết quả hợp lệ trước đó. Project switch/close hoặc CAM input mutation xóa
registry runtime. Không có file cache, thay đổi SQLite hay migration ở 7C.1.
