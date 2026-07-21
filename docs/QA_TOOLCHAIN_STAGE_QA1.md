# Stage QA.1 — Development Test and GUI Automation Toolchain

## Phạm vi và môi trường đã xác minh

Stage QA.1 chỉ bổ sung công cụ phát triển và kiểm thử. Không sửa mã CAD/CAM
domain, không thay schema dự án và không bắt đầu Stage 9A.6.

- Python: 3.14.6 64-bit trên Windows.
- PySide6: 6.11.1; `PySide6.QtTest.QTest` import thành công.
- Open CASCADE: `cadquery-ocp-novtk` 7.9.3.1.1.
- pytest: 9.1.1.
- Mọi lệnh cài đều dùng `.venv\Scripts\python.exe -m pip`.
- Dry-run của từng package đã qua resolver trên Python 3.14 trước khi cài.
- Không package nào hạ cấp/nâng cấp PySide6, OCP hoặc pytest hiện hữu.

## QA dependencies

Các dependency trực tiếp nằm trong optional dependency `qa` của
`pyproject.toml`, tách khỏi runtime dependencies:

| Công cụ | Phiên bản | Mục đích |
| --- | ---: | --- |
| pytest-qt | 4.5.0 | `qtbot`, thao tác widget, chờ signal và bắt lỗi Qt event loop |
| pytest-cov | 7.1.0 | line/branch coverage, terminal hoặc HTML report |
| pytest-timeout | 2.4.0 | phát hiện test treo bằng giới hạn riêng theo nhóm test |
| psutil | 7.2.2 | kiểm tra PID, child process, lifecycle và memory cơ bản |
| pytest-xdist | 3.8.0 | chạy đúng nhóm unit thuần đã chứng minh an toàn với `-n 2` |
| pytest-benchmark | 5.2.3 | benchmark quan sát codec/projection/generator, không đặt hard gate |
| pywinauto | 0.6.9 | smoke UIA/Win32 tùy chọn trên Windows interactive session |

Dependency bắc cầu mới: `typing_extensions 4.16.0`, `coverage 7.15.2`,
`execnet 2.1.2`, `py-cpuinfo 9.0.0`, `six 1.17.0`, `comtypes 1.4.16` và
`pywin32 312`. Wheel native của coverage/pywin32 tương thích CPython 3.14;
psutil dùng wheel Windows `abi3`.

Không cài `pyautogui`, Selenium, Playwright, Appium, AutoIt, công cụ quay màn
hình, công cụ click theo tọa độ, profiler native chưa xác minh hoặc plugin tự
retry test thất bại. `pyautogui` chưa cần thiết vì QTest xử lý widget Qt ổn
định hơn, còn pywinauto chỉ dành cho smoke cửa sổ Windows thật.

## Thứ tự ưu tiên và giới hạn sử dụng

Công cụ QA được cài để hỗ trợ khi cần, không phải để bật mặc định trong mọi
lần kiểm tra. Thứ tự ưu tiên là pytest/test hiện hữu, `PySide6.QtTest.QTest`,
pytest-qt khi cần `qtbot` hoặc chờ signal, psutil khi cần quan sát process HMS,
pytest-timeout để chặn test treo, xdist cho nhóm unit đã xác minh độc lập,
pytest-benchmark cho yêu cầu hiệu năng cụ thể, và cuối cùng pywinauto cho smoke
Windows-native có chủ đích.

- Không chuyển test QTest đang ổn định sang pytest-qt và không tạo fixture Qt
  phức tạp nếu QTest trực tiếp đã đủ.
- Quick không chạy GUI native, coverage hoặc benchmark.
- Full chạy tuần tự và loại `benchmark` cùng `windows_native` mặc định.
- Lệnh pytest trực tiếp cũng loại hai marker này qua `addopts`; mode Benchmark
  phải chọn lại marker `benchmark` một cách tường minh.
- ParallelSafe chỉ chạy danh sách unit thuần bằng `-n 2`, sau đó chạy lại chính
  danh sách đó tuần tự; kết quả tuần tự là nguồn xác nhận chính.
- Coverage và Benchmark chỉ chạy khi có yêu cầu cụ thể, không phải hard gate.
- WindowsNative không nằm trong Full, không tự chạy mỗi commit và chỉ điều
  khiển cửa sổ HMS test do script tạo. Selector dùng title, role và hierarchy;
  không click tọa độ và không gắn vào ứng dụng ngoài HMS.
- Khi không có Windows interactive desktop, WindowsNative ghi `SKIP` và trả
  thành công thay vì làm hỏng toàn bộ regression suite.
- psutil chỉ theo dõi/cleanup PID thuộc process tree do test hiện tại tạo ra.
- Không có timeout chung ở `pyproject.toml`. Quick/ParallelSafe dùng 60 giây,
  Coverage dùng 120 giây và Gui dùng 180 giây. Full không áp một timeout cứng
  lên các test OCP/import/GUI/SQLite có đặc tính khác nhau. Timeout phải được
  điều tra như dấu hiệu deadlock hoặc cleanup lỗi trước khi tăng.
- Không giữ test chỉ nhằm chứng minh plugin đã được cài. Việc thiếu dependency
  được runner phát hiện theo đúng mode đang yêu cầu công cụ đó.
- Runner tắt auto-load plugin bên thứ ba trong process của nó và chỉ bật plugin
  cần cho mode đang chạy; ví dụ Quick không nạp coverage/xdist/benchmark, còn
  Full chỉ nạp pytest-qt để chạy smoke `qtbot` hiện hữu.

## Cấu hình pytest và phân loại thực thi

Full/Gui truyền `qt_api=pyside6` khi nạp pytest-qt, thay vì buộc mọi lần chạy
pytest thuần phải nạp plugin chỉ để đọc cấu hình. Các marker được khai báo gồm
`gui`, `windows_native`, `serial`, `ocp`, `benchmark`, `slow`,
`filesystem` và `sqlite`. Stage này không gắn marker hàng loạt lên test cũ khi
chưa audit từng file.

Chỉ nhóm unit thuần, không GUI/OCP/SQLite/export/shared temp mới được đưa vào
`ParallelSafe`. Các test GUI, OCP Viewer, SQLite lifecycle, Save/Open/Recovery,
filesystem export, Post golden và Windows-native automation vẫn phải chạy tuần
tự. Full suite cũng luôn chạy tuần tự; không dùng `-n auto`.

Coverage là tín hiệu tìm vùng chưa được test, không phải thước đo chất lượng duy
nhất. Stage QA.1 chưa đặt coverage threshold. Benchmark chỉ đo code ổn định đã
được chọn trước, không đo GUI bằng thời gian nhìn thấy và không làm hard gate
giữa các máy. Chỉ báo cáo hồi quy hiệu năng lớn, có thể tái lập; không kết luận
từ chênh lệch nhỏ của một lần chạy.

## Chạy QA trên Windows

Từ project root:

```powershell
tools/run_qa.ps1 -Mode Quick
tools/run_qa.ps1 -Mode Full
tools/run_qa.ps1 -Mode Gui
tools/run_qa.ps1 -Mode Coverage
tools/run_qa.ps1 -Mode ParallelSafe
tools/run_qa.ps1 -Mode Benchmark
tools/run_qa.ps1 -Mode WindowsNative
```

Script tự tìm project root và `.venv`, không cài package, dùng
`.pytest_tmp/current`, trả lại đúng trạng thái lỗi và không xóa dữ liệu dự án.
`Full` và `Gui` dùng Qt offscreen để chạy không giám sát. `Full` luôn tuần tự và
không chạy benchmark/Windows-native. `ParallelSafe` chạy `-n 2` rồi xác nhận
lại tuần tự. `WindowsNative` bỏ offscreen, yêu cầu desktop Windows đang tương
tác và chỉ điều khiển cửa sổ PySide6 test HMS do
`tests/manual_qa_windows_native.py` tự tạo; nếu thiếu interactive session thì
SKIP. Script chọn control qua UIA, không click theo tọa độ và không gắn vào ứng
dụng khác.

Ví dụ coverage thủ công:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit\test_cam_ids.py `
  --cov=hms_cadcam --cov-branch --cov-report=term-missing
```

Không commit `.coverage`, `coverage.xml`, `htmlcov/` hoặc `.benchmarks/`.

## Snapshot và rollback

Snapshot trước/sau cài nằm tại vùng Git-ignored
`reference_private/DERIVED/QA_TOOLCHAIN/`. `dependency_diff.txt` là nguồn kiểm
tra package trực tiếp và bắc cầu thực tế.

Nếu cần rollback toàn bộ Stage QA.1, chỉ gỡ các package được ghi là mới trong
snapshot, rồi chạy `pip check`:

```powershell
.\.venv\Scripts\python.exe -m pip uninstall pytest-qt pytest-cov `
  pytest-timeout psutil pytest-xdist pytest-benchmark pywinauto `
  typing_extensions coverage execnet py-cpuinfo six comtypes pywin32
.\.venv\Scripts\python.exe -m pip check
```

Không dùng `--force-reinstall`, `--ignore-requires-python`, `--no-deps`,
`--pre` hoặc nâng cấp pip/setuptools/wheel để xử lý rollback.
