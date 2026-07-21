"""Build the private Stage 9A.1 UI reference image catalog.

The script intentionally scans only a curated set of UI-relevant PDF sources.
PDF members from ZIP archives are read into memory and are never extracted to a
persistent directory.  All generated raster files stay below
``reference_private/``, which is ignored by Git.
"""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import os
import re
import sys
import unicodedata
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Final

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRect, QSize, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QGuiApplication,
    QImage,
    QPainter,
    QPen,
)
from PySide6.QtPdf import QPdfDocument

logger = logging.getLogger("hms.ui_reference_catalog")

RENDER_WIDTH: Final = 1440
THUMBNAIL_SIZE: Final = QSize(360, 250)
CONTACT_COLUMNS: Final = 3


@dataclass(frozen=True, slots=True)
class TopicPlan:
    """Describe one UI topic to locate by text before rendering."""

    key: str
    output_group: str
    ui_type: str
    anchors: tuple[str, ...]
    terms: tuple[str, ...]
    strength: str
    weakness: str
    apply_to_hms: str
    do_not_copy: str
    max_pages: int = 1


@dataclass(frozen=True, slots=True)
class DocumentPlan:
    """Describe one curated PDF source and its relevant UI topics."""

    software: str
    title: str
    source_path: str
    member_contains: str | None
    topics: tuple[TopicPlan, ...]


@dataclass(frozen=True, slots=True)
class CatalogItem:
    """Metadata for one rendered source page."""

    item_id: str
    software: str
    document: str
    source: str
    source_member: str | None
    source_sha256: str
    pdf_page: int
    pdf_page_count: int
    image: str
    thumbnail: str
    output_group: str
    ui_type: str
    matched_terms: tuple[str, ...]
    strength: str
    weakness: str
    apply_to_hms: str
    do_not_copy: str


@dataclass(frozen=True, slots=True)
class DocumentAudit:
    """Record whether a planned document could be searched and rendered."""

    software: str
    document: str
    source: str
    source_member: str | None
    pdf_page_count: int
    text_page_count: int
    rendered_page_count: int
    status: str


def _topic(
    key: str,
    output_group: str,
    ui_type: str,
    anchors: tuple[str, ...],
    terms: tuple[str, ...],
    strength: str,
    weakness: str,
    apply_to_hms: str,
    do_not_copy: str,
    *,
    max_pages: int = 1,
) -> TopicPlan:
    return TopicPlan(
        key,
        output_group,
        ui_type,
        anchors,
        terms,
        strength,
        weakness,
        apply_to_hms,
        do_not_copy,
        max_pages,
    )


DOCUMENT_PLANS: Final = (
    DocumentPlan(
        "MASTERCAM",
        "MasterCAM X4 - Lập trình gia công phay",
        "reference_private/MASTERCAM/INBOX/2.zip",
        "phan 2 -lap trinh gia cong phay.pdf",
        (
            _topic(
                "manager_tree",
                "OPERATION_MANAGER",
                "Operation Manager",
                ("mot vi du ve toolpaths manager", "toolpath manager sau khi"),
                (
                    "machine group",
                    "toolpath group",
                    "stock setup",
                    "safety zone",
                    "properties",
                ),
                "Cây Machine Group/Toolpath Group cho thấy quan hệ công việc ngay tại nơi chọn operation.",
                "Tên nhóm và icon phụ thuộc phiên bản; nhiều lệnh chen vào cây.",
                "Dùng cây ổn định theo ID với Setup, tài nguyên, operation và artifact là các node có vai trò rõ.",
                "Không sao chép icon, nhãn độc quyền hoặc thứ tự pixel của Toolpaths Manager.",
            ),
            _topic(
                "manager_workflow",
                "MAIN_WORKSPACE",
                "Main Workspace",
                ("khung operation manager", "lam viec voi toolpath manager"),
                ("sap xep", "hieu chinh", "tham tra", "mo phong", "ket xuat"),
                "Một manager duy nhất giữ ngữ cảnh từ tạo, sửa, kiểm tra đến xuất chương trình.",
                "Manager có nguy cơ trở thành nơi chứa quá nhiều action và trạng thái.",
                "Giữ workflow liền mạch nhưng đưa action theo ngữ cảnh lên ribbon và editor của HMS.",
                "Không tái tạo bố cục Mastercam hoặc cách đóng gói command theo phiên bản X4.",
            ),
        ),
    ),
    DocumentPlan(
        "MASTERCAM",
        "Giáo trình MasterCAM 2017 - WCS và toolpath",
        "reference_private/MASTERCAM/INBOX/1.zip",
        "giao trinh mastercam 2017.pdf",
        (
            _topic(
                "mill_parameters",
                "MILL_FUNCTION_DIALOGS",
                "Cutting Parameters",
                ("cut parameters", "linking parameters"),
                ("depth cuts", "entry motion", "stepover", "z clearance"),
                "Tham số được chia thành trang theo nhiệm vụ gia công thay vì một form liên tục.",
                "Nhiều trang và tên kỹ thuật khiến người mới phải dò tìm.",
                "Chuẩn hóa section Geometry, Tool, Cutting, Levels và Linking; Basic luôn ngắn.",
                "Không sao chép tên tab, biểu tượng hoặc bố cục hộp thoại Mastercam.",
            ),
            _topic(
                "backplot",
                "SIMULATION_POST",
                "Simulation",
                ("hop thoai backplot", "backplot selected operations"),
                ("display tool", "display holder", "play", "toolpaths manager"),
                "Backplot xuất phát trực tiếp từ operation đang chọn và giữ ngữ cảnh dao/holder.",
                "Backplot và Verify là hai khái niệm dễ gây nhầm nếu trạng thái không được giải thích.",
                "Đưa Preview/Simulation thành node và action có trạng thái CURRENT/STALE rõ ràng.",
                "Không dùng icon hoặc chrome của Backplot Mastercam.",
            ),
            _topic(
                "post_selected",
                "SIMULATION_POST",
                "Post/NC",
                ("post selected operations", "hop thoai post processing"),
                ("toolpath group", "nc code", "save", "code expert"),
                "Post gắn với selection và nhóm operation, giúp người dùng hiểu phạm vi đầu ra.",
                "Luồng dễ tạo file quá sớm nếu thiếu validate/simulation gate.",
                "HMS giữ Validate, Generate, Preview, Save Managed và Export thành các bước có điều kiện.",
                "Không sao chép dialog Post processing hoặc workflow ghi file ngầm.",
            ),
        ),
    ),
    DocumentPlan(
        "MASTERCAM",
        "Bài giảng MasterCAM 2D",
        "reference_private/MASTERCAM/INBOX/1.zip",
        "lap trinh gia cong mastercam 2d.pdf",
        (
            _topic(
                "tool_definition",
                "TOOL_AND_GEOMETRY",
                "Tool Selection",
                ("tool definition", "xac dinh 1 dung cu moi"),
                ("tool type", "tool holder definition", "tool parameters"),
                "Tách chọn loại dao, kích thước dao/holder và tham số công nghệ.",
                "Hộp thoại dày đặc và có nhiều thông tin chỉ phù hợp người dùng chuyên sâu.",
                "Basic chỉ chọn Tool Assembly; chỉnh hình học dao mở ở panel riêng khi cần.",
                "Không sao chép hình dao, thư viện dao hoặc bố cục hộp thoại.",
            ),
            _topic(
                "stock_setup",
                "TOOL_AND_GEOMETRY",
                "Geometry Selection",
                ("machine group properties", "stock setup"),
                ("stock origin", "shape", "tool settings", "program"),
                "Stock thuộc Setup/Machine Group và có nguồn hình học rõ.",
                "Trộn thiết lập phôi, program và feed trong cùng dialog làm tăng tải nhận thức.",
                "Để Stock ở Setup; editor operation chỉ tham chiếu và hiển thị summary kế thừa.",
                "Không sao chép tab Stock Setup hoặc hình minh họa Mastercam.",
            ),
            _topic(
                "simulate_verify_post",
                "SIMULATION_POST",
                "Simulation",
                ("backplot", "verify"),
                ("backplot", "verify", "post", "regen path"),
                "Các action kiểm tra và xuất nằm gần operation workflow.",
                "Action ngang hàng không thể hiện điều kiện hay mức rủi ro.",
                "Dùng semantic status và chỉ enable action hợp lệ theo lifecycle.",
                "Không sao chép thanh lệnh hoặc icon mô phỏng.",
            ),
        ),
    ),
    DocumentPlan(
        "MASTERCAM",
        "Phương pháp gia công tiện trong MasterCAM X6",
        "reference_private/MASTERCAM/INBOX/1.zip",
        "phuong phap gia cong tien trong mastercam x6.pdf",
        (
            _topic(
                "lathe_operation_manager",
                "OPERATION_MANAGER",
                "Operation Manager",
                ("hop thoai operation manager", "operation folder"),
                ("parameters", "tool parameter", "geometry", "update stock"),
                "Operation bung ra thành Parameters, Tool, Geometry và NC artifact dễ truy vết.",
                "Cây có thể sâu và lặp thông tin nếu mỗi node đều mang action riêng.",
                "Dùng node con có vai trò ổn định và summary trạng thái, không dùng row index làm identity.",
                "Không sao chép tên node, icon hoặc cấu trúc cây pixel-by-pixel.",
            ),
            _topic(
                "lathe_function",
                "LATHE_FUNCTION_DIALOGS",
                "Function Dialog",
                ("plunge cut parameters", "hop thoai lathe rough"),
                ("plunge parameters", "entry vector", "minimum vector length"),
                "Tham số đặc thù tiện chỉ xuất hiện trong ngữ cảnh strategy liên quan.",
                "Dialog con nối tiếp dễ che mất tổng quan operation.",
                "Giữ section Advanced theo strategy trong cùng Function Editor, có summary và cảnh báo.",
                "Không sao chép dialog Lathe Rough hay hình minh họa.",
            ),
            _topic(
                "lathe_post",
                "SIMULATION_POST",
                "Post/NC",
                ("hop thoai post processing", "active post"),
                ("change post", "file name", "geometry properties", "nc"),
                "Profile đầu ra và metadata file được trình bày trước khi tạo NC.",
                "Dialog cũ có thể cho phép chọn post không tương thích mà thiếu diagnostics tập trung.",
                "HMS hiển thị profile, compatibility, simulation gate và artifact status trong một workflow.",
                "Không sao chép lựa chọn post hoặc hình thức file dialog Mastercam.",
            ),
        ),
    ),
    DocumentPlan(
        "MASTERCAM",
        "Tổng quan CAM Milling trên MasterCAM",
        "reference_private/MASTERCAM/INBOX/1.zip",
        "tong quan ve cam (milling) tren mastercam.pdf",
        (
            _topic(
                "machine_toolpath_groups",
                "OPERATION_MANAGER",
                "Operation Manager",
                ("new machine group", "toolpath group"),
                ("cay quan ly", "mill", "lathe", "router", "post processor"),
                "Machine Group tạo ranh giới ngữ cảnh máy và nhóm operation rõ.",
                "Tên nhóm theo sản phẩm nguồn không ánh xạ trực tiếp domain HMS hiện tại.",
                "Ánh xạ sang Job > Setup/Machine Group > Operations, dùng ID domain ổn định.",
                "Không sao chép terminology hoặc giao diện cây Mastercam.",
            ),
            _topic(
                "safety_zone",
                "TOOL_AND_GEOMETRY",
                "Geometry Selection",
                ("the safety zone", "vung an toan"),
                ("stock setup", "machine group properties", "kich thuoc phoi"),
                "Vùng an toàn đặt ở cấp máy/setup thay vì lặp trong mọi operation.",
                "Safety Zone và Stock bị phân tán qua nhiều tab.",
                "Hiển thị giá trị kế thừa từ Setup và chỉ cho override có lý do rõ.",
                "Không sao chép tab hoặc cách nhập vùng an toàn của Mastercam.",
            ),
        ),
    ),
    DocumentPlan(
        "WORKNC",
        "WORKNC 3-Axis Basic Training Guide 2018 R2",
        "reference_private/WORKNC/TRAINING/3X_basic_training_guide_worknc_2018_R2_done.pdf",
        None,
        (
            _topic(
                "parallel_overview",
                "TOOLPATH_PARAMETERS",
                "Function Dialog",
                ("8.4 - parallel finishing",),
                ("machining zone", "tolerances", "cutter movements", "toolpath parameters"),
                "Tài liệu trình bày strategy cùng các nhóm tham số liên quan theo workflow.",
                "Màn hình nguồn vẫn chứa nhiều lựa chọn nâng cao ngay từ đầu.",
                "Giữ summary strategy và chỉ 5-10 input Basic; các nhóm còn lại mở theo nhu cầu.",
                "Không sao chép dialog, icon hoặc bố cục WorkNC.",
                max_pages=2,
            ),
            _topic(
                "machining_zone_training",
                "MACHINING_ZONE",
                "Geometry Selection",
                ("18.1 - machining zone", "hop thoai machining zone"),
                ("view", "boundary curve", "surfaces", "toolpath parameters"),
                "Machining Zone gom các giới hạn hình học vào một khái niệm nhất quán.",
                "Nhiều loại giới hạn có thể làm dialog phức tạp nếu hiển thị đồng thời.",
                "Geometry section chọn zone theo loại và chỉ hiện control tương ứng.",
                "Không sao chép hình minh họa hoặc bố cục hộp thoại.",
            ),
            _topic(
                "multi_edit",
                "ADDITIONAL_PARAMETERS",
                "Advanced Options",
                ("chinh sua thong so cua nhieu duong chay dao", "multi-edit menu"),
                ("workzone manager", "parameters", "shift", "ctrl"),
                "Multi-edit phân biệt giá trị chung và giá trị khác nhau giữa các operation.",
                "Bulk edit có rủi ro ghi đè ngầm nếu trạng thái hỗn hợp không rõ.",
                "Chỉ cho bulk edit field tương thích, hiển thị mixed state và xác nhận phạm vi mutation.",
                "Không sao chép ký hiệu, menu hoặc interaction WorkNC.",
            ),
        ),
    ),
    DocumentPlan(
        "WORKNC",
        "WORKNC 2021 Online Help",
        "reference_private/WORKNC/ONLINE_HELP/worknc.pdf",
        None,
        (
            _topic(
                "toolpath_parameters",
                "TOOLPATH_PARAMETERS",
                "Function Dialog",
                ("toolpath parameters title bar",),
                ("toolpath details", "standard parameters", "specific parameters", "default"),
                "Phân biệt summary, Standard Parameters và Specific Parameters ngay trong editor.",
                "Hai cột tham số có thể quá dày trên màn hình hẹp.",
                "HMS dùng header summary và section dọc responsive, Advanced collapsed mặc định.",
                "Không sao chép menu, biểu tượng hoặc tỷ lệ panel WorkNC.",
            ),
            _topic(
                "standard_parameter_groups",
                "TOOLPATH_PARAMETERS",
                "Function Dialog",
                ("standard parameters consist",),
                (
                    "machining zone",
                    "cutter details",
                    "machining parameters",
                    "nc machine parameters",
                    "tolerances",
                    "cutter movements",
                ),
                "Nhóm tham số phản ánh nhiệm vụ người vận hành thay vì cấu trúc thuật toán.",
                "Danh sách nhóm dài và một số mục chồng lấn trách nhiệm.",
                "Chuẩn hóa Geometry, Tool, Cutting, Levels, Linking và Advanced cho mọi strategy.",
                "Không sao chép tên mục hoặc icon WorkNC nguyên trạng.",
            ),
            _topic(
                "machining_zone_help",
                "MACHINING_ZONE",
                "Geometry Selection",
                ("a single machining zone parameters dialog box",),
                ("window", "view", "boundary curves", "machining plane", "surface selection"),
                "Một dialog thống nhất cho nhiều kiểu giới hạn vùng gia công.",
                "Nhiều mode hình học cần hướng dẫn và preview mạnh để tránh chọn sai.",
                "Dùng mode selector rõ, summary selection và preview/focus trong viewport.",
                "Không sao chép pictogram hoặc bố cục Machining Zone.",
            ),
            _topic(
                "cutter_details",
                "CUTTER_DETAILS",
                "Tool Selection",
                ("cutter details introduction",),
                ("cutter tool library", "dimensions", "form", "cutter type"),
                "Chọn dao từ library nhưng vẫn cho phép xem nhanh kích thước và form.",
                "Định nghĩa hình học dao trong operation editor gây trùng với Tool Library.",
                "Operation chỉ tham chiếu Tool Assembly và mở chi tiết read-only/linked editor.",
                "Không sao chép hình dao, dialog hoặc thư viện WorkNC.",
            ),
            _topic(
                "tolerances",
                "TOLERANCES",
                "Cutting Parameters",
                ("chordal deviation", "tolerance parameter defines"),
                ("stock allowance", "stepover", "scallop height", "additional parameters"),
                "Giải thích dependency giữa tolerance, lượng dư, số điểm và chất lượng bề mặt.",
                "Nhiều tham số precision dễ bị chỉnh mà không hiểu chi phí tính toán.",
                "Đưa tolerance phổ biến vào Advanced; Expert có cảnh báo ảnh hưởng thời gian/chất lượng.",
                "Không sao chép hình minh họa hay giá trị mặc định WorkNC.",
            ),
            _topic(
                "safe_movements",
                "CUTTER_MOVEMENTS",
                "Linking",
                ("safety plane retract movements",),
                ("approach distance", "rapid rate", "retract", "2d", "3d"),
                "Approach/retract được nhóm theo chuyển động an toàn và có sơ đồ ngữ nghĩa.",
                "Nhiều mode chuyển động có thể tạo tổ hợp khó kiểm chứng.",
                "Linking chỉ hiện policy áp dụng; safe values kế thừa từ Setup và luôn có đơn vị.",
                "Không sao chép sơ đồ, icon hoặc tên mode WorkNC.",
            ),
            _topic(
                "lead_in_out",
                "CUTTER_MOVEMENTS",
                "Linking",
                ("radial lead-ins/lead-outs",),
                ("transitions", "user-defined radius", "grayed out", "toolpath dependent"),
                "Field không áp dụng được vô hiệu hóa theo strategy và linking mode.",
                "Giữ field mờ vẫn làm tăng mật độ và khiến người dùng phải đọc nhiều.",
                "HMS ẩn field không áp dụng, giữ tooltip ngắn và giải thích chi tiết ở Help.",
                "Không sao chép dialog Cutter Movements hoặc behavior cụ thể của WorkNC.",
            ),
            _topic(
                "parallel_standard",
                "TOOLPATH_PARAMETERS",
                "Function Dialog",
                ("parallel finishing programming - standard parameters",),
                ("toolpath parameters menu", "machining zone", "cutter movements"),
                "Tách tham số chung với tham số riêng của Parallel Finishing.",
                "Standard và Specific ở hai khu vực có thể làm người dùng đổi qua lại nhiều.",
                "HMS giữ cùng section framework, field riêng strategy xuất hiện đúng section.",
                "Không sao chép dialog Parallel Finishing hoặc thuật toán.",
            ),
            _topic(
                "parallel_specific",
                "ADDITIONAL_PARAMETERS",
                "Advanced Options",
                ("parallel finishing programming - specific parameters",),
                ("standard and specific parameters", "tolerances", "cutter movements"),
                "Tham số strategy-specific có vùng riêng và liên kết tới help chi tiết.",
                "Có nguy cơ lộ quá nhiều tùy chọn chuyên gia trong luồng cơ bản.",
                "Đặt option thuật toán/smoothing/filtering ở Expert, collapsed và có cảnh báo.",
                "Không sao chép tham số, default hoặc bố cục WorkNC.",
            ),
        ),
    ),
)


OUTPUT_GROUPS: Final = {
    "MASTERCAM": (
        "MAIN_WORKSPACE",
        "OPERATION_MANAGER",
        "MILL_FUNCTION_DIALOGS",
        "LATHE_FUNCTION_DIALOGS",
        "TOOL_AND_GEOMETRY",
        "SIMULATION_POST",
    ),
    "WORKNC": (
        "TOOLPATH_PARAMETERS",
        "MACHINING_ZONE",
        "CUTTER_DETAILS",
        "TOLERANCES",
        "CUTTER_MOVEMENTS",
        "ADDITIONAL_PARAMETERS",
    ),
}


def project_root() -> Path:
    """Return the repository root containing this managed script."""

    return Path(__file__).resolve().parents[1]


def normalize_text(value: str) -> str:
    """Normalize Unicode text for accent-insensitive PDF searching."""

    decomposed = unicodedata.normalize("NFKD", value.casefold())
    without_marks = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    return re.sub(r"\s+", " ", without_marks).strip()


def slugify(value: str) -> str:
    """Return a deterministic ASCII slug suitable for derived filenames."""

    normalized = normalize_text(value)
    slug = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")
    return slug or "reference"


def _ensure_output_tree(root: Path) -> Path:
    derived = root / "reference_private" / "DERIVED" / "UI_REFERENCE"
    for software, groups in OUTPUT_GROUPS.items():
        for group in groups:
            (derived / software / group).mkdir(parents=True, exist_ok=True)
    (derived / "HMS_CURRENT").mkdir(parents=True, exist_ok=True)
    (derived / "CONTACT_SHEETS" / "THUMBNAILS").mkdir(parents=True, exist_ok=True)
    return derived


def _locate_zip_member(archive: zipfile.ZipFile, needle: str) -> zipfile.ZipInfo:
    normalized_needle = normalize_text(needle)
    matches = [
        item
        for item in archive.infolist()
        if item.filename.casefold().endswith(".pdf")
        and normalized_needle in normalize_text(item.filename)
    ]
    if not matches:
        raise FileNotFoundError(f"Không tìm thấy PDF '{needle}' trong {archive.filename}")
    matches.sort(key=lambda item: (len(item.filename), item.filename.casefold()))
    return matches[0]


def _load_document(
    root: Path, plan: DocumentPlan
) -> tuple[QPdfDocument, QBuffer | None, QByteArray | None, str | None, str]:
    source = root / Path(plan.source_path)
    if not source.is_file():
        raise FileNotFoundError(source)
    document = QPdfDocument()
    if plan.member_contains is None:
        error = document.load(str(source.resolve()))
        if document.pageCount() <= 0:
            document.close()
            raise RuntimeError(f"Không thể đọc {source}: {error}")
        return document, None, None, None, _sha256_path(source)

    with zipfile.ZipFile(source, "r") as archive:
        member = _locate_zip_member(archive, plan.member_contains)
        raw = archive.read(member)
    data = QByteArray(raw)
    buffer = QBuffer()
    buffer.setData(data)
    if not buffer.open(QIODevice.OpenModeFlag.ReadOnly):
        raise OSError(f"Không thể mở bộ đệm PDF từ {source}!{member.filename}")
    document.load(buffer)
    if document.pageCount() <= 0:
        document.close()
        buffer.close()
        raise RuntimeError(f"Không thể đọc PDF từ {source}!{member.filename}")
    return document, buffer, data, member.filename, hashlib.sha256(raw).hexdigest()


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _page_texts(document: QPdfDocument) -> tuple[str, ...]:
    return tuple(
        normalize_text(document.getAllText(index).text())
        for index in range(document.pageCount())
    )


def _select_topic_pages(
    page_texts: tuple[str, ...], topic: TopicPlan, used_pages: set[int]
) -> tuple[tuple[int, tuple[str, ...]], ...]:
    anchors = tuple(normalize_text(value) for value in topic.anchors)
    terms = tuple(normalize_text(value) for value in topic.terms)
    candidates: list[tuple[int, int, tuple[str, ...]]] = []
    for page_index, text in enumerate(page_texts):
        matched_anchors = tuple(value for value in anchors if value in text)
        if not matched_anchors:
            continue
        matched_terms = tuple(value for value in terms if value in text)
        score = sum(500 + len(value) for value in matched_anchors)
        score += sum(25 + len(value) for value in matched_terms)
        score += min(len(text), 4000) // 400
        if page_index in used_pages:
            score -= 200
        candidates.append((score, page_index, matched_anchors + matched_terms))
    candidates.sort(key=lambda item: (-item[0], item[1]))
    result = tuple(
        (page_index, matches)
        for _score, page_index, matches in candidates[: topic.max_pages]
    )
    used_pages.update(page_index for page_index, _matches in result)
    return result


def _render_page(document: QPdfDocument, page_index: int) -> QImage:
    points = document.pagePointSize(page_index)
    if points.width() <= 0 or points.height() <= 0:
        raise RuntimeError(f"Trang PDF {page_index + 1} có kích thước không hợp lệ")
    height = max(1, round(RENDER_WIDTH * points.height() / points.width()))
    image = document.render(page_index, QSize(RENDER_WIDTH, height))
    if image.isNull():
        raise RuntimeError(f"Không thể render trang PDF {page_index + 1}")
    return image


def _save_png_atomic(image: QImage, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    if not image.save(str(temporary), "PNG"):
        raise OSError(f"Không thể ghi ảnh {temporary}")
    temporary.replace(path)


def _thumbnail(source: QImage) -> QImage:
    canvas = QImage(THUMBNAIL_SIZE, QImage.Format.Format_ARGB32)
    canvas.fill(QColor("#f4f6f8"))
    scaled = source.scaled(
        THUMBNAIL_SIZE - QSize(16, 16),
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    painter = QPainter(canvas)
    painter.drawImage(
        (canvas.width() - scaled.width()) // 2,
        (canvas.height() - scaled.height()) // 2,
        scaled,
    )
    painter.end()
    return canvas


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _catalog_font_family() -> str:
    """Return a Unicode font, loading it explicitly for Qt offscreen if needed."""

    preferred = ("Segoe UI", "Arial", "Noto Sans", "DejaVu Sans")
    installed = set(QFontDatabase.families())
    for family in preferred:
        if family in installed:
            return family

    windows_directory = os.environ.get("WINDIR")
    if windows_directory:
        font_directory = Path(windows_directory) / "Fonts"
        for filename in ("segoeui.ttf", "arial.ttf"):
            font_path = font_directory / filename
            if not font_path.is_file():
                continue
            font_id = QFontDatabase.addApplicationFont(str(font_path))
            families = QFontDatabase.applicationFontFamilies(font_id)
            if families:
                return families[0]
    return "Sans Serif"


def _render_plan(
    root: Path, derived: Path, plan: DocumentPlan
) -> tuple[list[CatalogItem], DocumentAudit]:
    document: QPdfDocument | None = None
    buffer: QBuffer | None = None
    data: QByteArray | None = None
    member: str | None = None
    source_sha256 = ""
    try:
        document, buffer, data, member, source_sha256 = _load_document(root, plan)
        page_texts = _page_texts(document)
        text_page_count = sum(bool(text) for text in page_texts)
        if text_page_count == 0:
            logger.warning("Bỏ qua PDF không có text layer: %s", plan.title)
            return [], DocumentAudit(
                plan.software,
                plan.title,
                plan.source_path,
                member,
                document.pageCount(),
                0,
                0,
                "SKIPPED_NO_TEXT",
            )

        items: list[CatalogItem] = []
        used_pages: set[int] = set()
        for topic in plan.topics:
            selected = _select_topic_pages(page_texts, topic, used_pages)
            if not selected:
                logger.warning(
                    "Không tìm thấy trang cho %s / %s", plan.title, topic.key
                )
                continue
            for page_index, matched_terms in selected:
                item_id = (
                    f"{slugify(plan.software)}-{slugify(plan.title)}-"
                    f"{slugify(topic.key)}-p{page_index + 1:04d}"
                )
                filename = f"{item_id}.png"
                image_path = derived / plan.software / topic.output_group / filename
                thumbnail_path = (
                    derived
                    / "CONTACT_SHEETS"
                    / "THUMBNAILS"
                    / plan.software
                    / topic.output_group
                    / filename
                )
                image = _render_page(document, page_index)
                _save_png_atomic(image, image_path)
                _save_png_atomic(_thumbnail(image), thumbnail_path)
                items.append(
                    CatalogItem(
                        item_id,
                        plan.software,
                        plan.title,
                        plan.source_path,
                        member,
                        source_sha256,
                        page_index + 1,
                        document.pageCount(),
                        _relative(root, image_path),
                        _relative(root, thumbnail_path),
                        topic.output_group,
                        topic.ui_type,
                        matched_terms,
                        topic.strength,
                        topic.weakness,
                        topic.apply_to_hms,
                        topic.do_not_copy,
                    )
                )
        status = "RENDERED" if items else "NO_TOPIC_MATCH"
        return items, DocumentAudit(
            plan.software,
            plan.title,
            plan.source_path,
            member,
            document.pageCount(),
            text_page_count,
            len(items),
            status,
        )
    finally:
        if document is not None:
            document.close()
        if buffer is not None:
            buffer.close()
        del data


def _contact_sheet(
    root: Path, items: list[CatalogItem], destination: Path, title: str
) -> None:
    if not items:
        return
    cell_width = THUMBNAIL_SIZE.width() + 28
    cell_height = THUMBNAIL_SIZE.height() + 82
    rows = (len(items) + CONTACT_COLUMNS - 1) // CONTACT_COLUMNS
    width = CONTACT_COLUMNS * cell_width + 28
    height = rows * cell_height + 74
    sheet = QImage(width, height, QImage.Format.Format_ARGB32)
    sheet.fill(QColor("#eef1f4"))
    painter = QPainter(sheet)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    painter.setPen(QColor("#17212b"))
    font_family = _catalog_font_family()
    title_font = QFont(font_family, 16)
    title_font.setWeight(QFont.Weight.DemiBold)
    painter.setFont(title_font)
    painter.drawText(QRect(20, 12, width - 40, 42), Qt.AlignmentFlag.AlignVCenter, title)
    body_font = QFont(font_family, 9)
    painter.setFont(body_font)
    for index, item in enumerate(items):
        column = index % CONTACT_COLUMNS
        row = index // CONTACT_COLUMNS
        x = 14 + column * cell_width
        y = 60 + row * cell_height
        painter.fillRect(QRect(x, y, cell_width - 12, cell_height - 12), QColor("#ffffff"))
        painter.setPen(QPen(QColor("#c8d0d8"), 1))
        painter.drawRect(QRect(x, y, cell_width - 12, cell_height - 12))
        thumb = QImage(str(root / Path(item.thumbnail)))
        painter.drawImage(x + 8, y + 8, thumb)
        painter.setPen(QColor("#17212b"))
        label = (
            f"{item.document} | {item.ui_type}"
            if item.software == "HMS"
            else f"{item.document} | PDF p.{item.pdf_page} | {item.ui_type}"
        )
        painter.drawText(
            QRect(x + 8, y + THUMBNAIL_SIZE.height() + 14, cell_width - 28, 50),
            Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap,
            label,
        )
    painter.end()
    _save_png_atomic(sheet, destination)


def _hms_current_items(root: Path, derived: Path) -> list[CatalogItem]:
    """Build thumbnails for runtime HMS audit captures already present locally."""

    labels = {
        "01_main_window": ("Cửa sổ chính", "Main Workspace"),
        "02_project_open_save_workflow": ("Ribbon dự án", "Project Open/Save"),
        "03_cam_workspace_default_dock": ("CAM dock mặc định", "CAM Workspace"),
        "04_cam_workspace_floating": ("CAM workspace đầy đủ", "CAM Workspace"),
        "05_operation_tree": ("Cây operation", "Operation Manager"),
        "06_operation_editor_2d_contour": ("Editor 2D Contour", "Function Dialog"),
        "07_simulation_panel": ("Simulation 7C.3", "Simulation"),
        "08_post_panel": ("Post Processor 7D.2.3", "Post/NC"),
        "09_program_assembly_panel": ("Program Assembly 7D.3.2", "Program Assembly"),
    }
    current = derived / "HMS_CURRENT"
    result: list[CatalogItem] = []
    for image_path in sorted(current.glob("*.png")):
        image = QImage(str(image_path))
        if image.isNull():
            logger.warning("Bỏ qua ảnh HMS không đọc được: %s", image_path)
            continue
        thumbnail_path = (
            derived / "CONTACT_SHEETS" / "THUMBNAILS" / "HMS_CURRENT" / image_path.name
        )
        _save_png_atomic(_thumbnail(image), thumbnail_path)
        document, ui_type = labels.get(
            image_path.stem,
            (image_path.stem.replace("_", " ").title(), "HMS Current"),
        )
        result.append(
            CatalogItem(
                image_path.stem,
                "HMS",
                document,
                "runtime_ui_audit",
                None,
                _sha256_path(image_path),
                0,
                0,
                _relative(root, image_path),
                _relative(root, thumbnail_path),
                "HMS_CURRENT",
                ui_type,
                (),
                "",
                "",
                "",
                "",
            )
        )
    return result


def _write_metadata(
    root: Path,
    derived: Path,
    items: list[CatalogItem],
    audits: list[DocumentAudit],
) -> None:
    payload = {
        "format": "HMS_UI_REFERENCE_CATALOG",
        "format_version": 1,
        "generator": "tools/build_ui_reference_catalog.py",
        "selection_policy": "curated_documents_text_search_before_render",
        "documents": [asdict(value) for value in audits],
        "items": [asdict(value) for value in items],
    }
    json_path = derived / "reference_catalog.json"
    temporary_json = json_path.with_name(f".{json_path.name}.tmp")
    temporary_json.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary_json.replace(json_path)

    csv_path = derived / "reference_catalog.csv"
    temporary_csv = csv_path.with_name(f".{csv_path.name}.tmp")
    fieldnames = list(asdict(items[0]).keys()) if items else list(CatalogItem.__slots__)
    with temporary_csv.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        for item in items:
            row = asdict(item)
            row["matched_terms"] = " | ".join(item.matched_terms)
            writer.writerow(row)
    temporary_csv.replace(csv_path)
    logger.info("Đã ghi metadata: %s và %s", _relative(root, json_path), _relative(root, csv_path))


def build_catalog(root: Path | None = None) -> tuple[CatalogItem, ...]:
    """Search curated PDFs, render selected pages, and build private metadata."""

    application = QGuiApplication.instance() or QGuiApplication([sys.argv[0]])
    repository = (root or project_root()).resolve()
    private_root = repository / "reference_private"
    if not private_root.is_dir():
        raise FileNotFoundError(
            "Thiếu reference_private; không thể xây dựng catalog UI tham khảo"
        )
    derived = _ensure_output_tree(repository)
    items: list[CatalogItem] = []
    audits: list[DocumentAudit] = []
    for plan in DOCUMENT_PLANS:
        logger.info("Khảo sát: %s", plan.title)
        rendered, audit = _render_plan(repository, derived, plan)
        items.extend(rendered)
        audits.append(audit)

    items.sort(key=lambda value: (value.software, value.output_group, value.item_id))
    _write_metadata(repository, derived, items, audits)
    contact_root = derived / "CONTACT_SHEETS"
    for software in ("MASTERCAM", "WORKNC"):
        selected = [item for item in items if item.software == software]
        _contact_sheet(
            repository,
            selected,
            contact_root / f"{software.casefold()}_contact_sheet.png",
            f"HMS UI Reference - {software}",
        )
    _contact_sheet(
        repository,
        items,
        contact_root / "all_reference_contact_sheet.png",
        "HMS UI Reference - Selected PDF Pages",
    )
    hms_items = _hms_current_items(repository, derived)
    _contact_sheet(
        repository,
        hms_items,
        contact_root / "hms_current_contact_sheet.png",
        "HMS UI Audit - Current Production UI",
    )
    logger.info(
        "Hoàn tất: %d tài liệu khảo sát, %d ảnh được render",
        len(audits),
        len(items),
    )
    application.processEvents()
    return tuple(items)


def main() -> int:
    """Run the catalog builder and return a process exit status."""

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        build_catalog()
    except (OSError, RuntimeError, ValueError, zipfile.BadZipFile):
        logger.exception("Không thể xây dựng UI reference catalog")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
