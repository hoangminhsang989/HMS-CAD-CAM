"""Typed, deterministic runtime localization services for HMS CAD/CAM.

The locale is a user preference only.  This module deliberately has no
dependency on project manifests, SQLite, CAM payloads, or the Windows locale.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
import json
import logging
from pathlib import Path
from string import Formatter
from types import MappingProxyType
from typing import NewType, Protocol

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication, QWidget

from hms_cadcam.ui.storage_translations import STORAGE_TRANSLATIONS

LOGGER = logging.getLogger(__name__)

TranslationKey = NewType("TranslationKey", str)


class UiLanguage(StrEnum):
    """Stable locale identifiers stored in user settings."""

    VI_VN = "VI_VN"
    EN_US = "EN_US"
    KO_KR = "KO_KR"

    @classmethod
    def coerce(cls, value: object) -> "UiLanguage":
        """Resolve persisted data without consulting the operating system."""
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value))
        except (TypeError, ValueError):
            return cls.VI_VN


LANGUAGE_SETTINGS_KEY = "ui/language"
SAFE_FALLBACK_TEXT = MappingProxyType(
    {
        UiLanguage.VI_VN: "Nội dung giao diện",
        UiLanguage.EN_US: "Interface text",
        UiLanguage.KO_KR: "인터페이스 텍스트",
    }
)


@dataclass(frozen=True, slots=True)
class CatalogValidation:
    """Strict validation result for one locale catalog."""

    locale: UiLanguage
    key_count: int
    missing_keys: tuple[str, ...]
    empty_keys: tuple[str, ...]
    duplicate_keys: tuple[str, ...]
    placeholder_mismatches: tuple[str, ...]

    @property
    def valid(self) -> bool:
        return not (
            self.missing_keys
            or self.empty_keys
            or self.duplicate_keys
            or self.placeholder_mismatches
        )


@dataclass(frozen=True, slots=True)
class TranslationCatalog:
    """Immutable catalog preserving duplicate evidence from its source pairs."""

    locale: UiLanguage
    entries: Mapping[str, str]
    duplicate_keys: tuple[str, ...] = ()

    @classmethod
    def from_pairs(
        cls,
        locale: UiLanguage,
        pairs: Iterable[tuple[str, str]],
    ) -> "TranslationCatalog":
        materialized = tuple((str(key), str(value)) for key, value in pairs)
        counts = Counter(key for key, _value in materialized)
        duplicates = tuple(sorted(key for key, count in counts.items() if count > 1))
        entries: dict[str, str] = {}
        for key, value in materialized:
            entries[key] = value
        return cls(locale, MappingProxyType(entries), duplicates)

    def validate(
        self,
        required_keys: Iterable[str],
        *,
        source_entries: Mapping[str, str] | None = None,
    ) -> CatalogValidation:
        required = tuple(dict.fromkeys(str(key) for key in required_keys))
        missing = tuple(key for key in required if key not in self.entries)
        empty = tuple(
            key for key in required
            if key in self.entries and not self.entries[key].strip()
        )
        placeholder_mismatches: list[str] = []
        if source_entries is not None:
            for key in required:
                if key not in self.entries or key not in source_entries:
                    continue
                if Counter(_format_fields(source_entries[key])) != Counter(
                    _format_fields(self.entries[key])
                ):
                    placeholder_mismatches.append(key)
        return CatalogValidation(
            locale=self.locale,
            key_count=len(self.entries),
            missing_keys=missing,
            empty_keys=empty,
            duplicate_keys=self.duplicate_keys,
            placeholder_mismatches=tuple(placeholder_mismatches),
        )


@dataclass(frozen=True, slots=True)
class TranslationDiagnostic:
    """One internal fallback event; never rendered as a widget warning."""

    requested_locale: UiLanguage
    key: str
    resolution: str


@dataclass(frozen=True, slots=True)
class GlossaryEntry:
    """One controlled CAD/CAM term across all supported locales."""

    source: str
    vietnamese: str
    english: str
    korean: str

    def value(self, locale: UiLanguage) -> str:
        return {
            UiLanguage.VI_VN: self.vietnamese,
            UiLanguage.EN_US: self.english,
            UiLanguage.KO_KR: self.korean,
        }[locale]


class SettingsBackend(Protocol):
    """Small QSettings-compatible boundary used by locale persistence."""

    def value(self, key: str, default_value: object = None) -> object: ...

    def setValue(self, key: str, value: object) -> None: ...  # noqa: N802

    def sync(self) -> None: ...


class LocaleSettingsService:
    """Persist the stable locale in existing user settings."""

    def __init__(self, settings: SettingsBackend) -> None:
        self._settings = settings

    def load(self) -> UiLanguage:
        try:
            return UiLanguage.coerce(
                self._settings.value(
                    LANGUAGE_SETTINGS_KEY,
                    UiLanguage.VI_VN.value,
                )
            )
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            LOGGER.warning("Không đọc được cài đặt ngôn ngữ; dùng VI_VN: %s", exc)
            return UiLanguage.VI_VN

    def save(self, language: UiLanguage) -> bool:
        selected = UiLanguage.coerce(language)
        try:
            self._settings.setValue(LANGUAGE_SETTINGS_KEY, selected.value)
            self._settings.sync()
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            LOGGER.error("Không lưu được cài đặt ngôn ngữ %s: %s", selected, exc)
            return False
        return True


class TranslationService(QObject):
    """Resolve managed UI text and emit one central runtime language event."""

    language_changed = Signal(object)

    def __init__(
        self,
        catalogs: Mapping[UiLanguage, TranslationCatalog],
        *,
        language: UiLanguage = UiLanguage.VI_VN,
        safe_fallback: Mapping[UiLanguage, str] = SAFE_FALLBACK_TEXT,
    ) -> None:
        super().__init__()
        self._catalogs = MappingProxyType(dict(catalogs))
        selected = UiLanguage.coerce(language)
        self._language = (
            selected if selected in self._catalogs else UiLanguage.VI_VN
        )
        self._safe_fallback = MappingProxyType(dict(safe_fallback))
        self._diagnostics: list[TranslationDiagnostic] = []
        self._reverse: dict[str, str] = {}
        for catalog in self._catalogs.values():
            for key, value in catalog.entries.items():
                self._reverse.setdefault(key, key)
                self._reverse.setdefault(value, key)
        # Reviewed shell/preference aliases win deterministic collisions in
        # the large legacy catalog (for example Vietnamese "Hiển thị" can be
        # either the noun "Display" or the top-level menu verb "View").
        for english, vietnamese, korean in (
            *CORE_TRANSLATIONS,
            *RIBBON_TRANSLATIONS,
            *LEGACY_TRANSLATIONS,
            *STORAGE_TRANSLATIONS,
        ):
            self._reverse[english] = english
            self._reverse[vietnamese] = english
            self._reverse[korean] = english
        for vietnamese, english, korean in VIETNAMESE_SOURCE_TRANSLATIONS:
            self._reverse[vietnamese] = vietnamese
            self._reverse[english] = vietnamese
            self._reverse[korean] = vietnamese
        for source, vietnamese, english, korean in DISPLAY_SOURCE_TRANSLATIONS:
            self._reverse[source] = source
            self._reverse[vietnamese] = source
            self._reverse[english] = source
            self._reverse[korean] = source
        for term in TECHNICAL_GLOSSARY:
            self._reverse[term.source] = term.source
            self._reverse[term.vietnamese] = term.source
            self._reverse[term.korean] = term.source
        self._reverse["Hiển thị"] = "View"
        self._reverse["보기"] = "View"

    @property
    def language(self) -> UiLanguage:
        return self._language

    @property
    def catalogs(self) -> Mapping[UiLanguage, TranslationCatalog]:
        return self._catalogs

    @property
    def diagnostics(self) -> tuple[TranslationDiagnostic, ...]:
        return tuple(self._diagnostics)

    def clear_diagnostics(self) -> None:
        self._diagnostics.clear()

    def canonical_key(self, value: object) -> str | None:
        return self._reverse.get(str(value))

    def set_language(self, language: UiLanguage) -> bool:
        selected = UiLanguage.coerce(language)
        if selected not in self._catalogs:
            selected = UiLanguage.VI_VN
        if selected is self._language:
            return False
        self._language = selected
        self.language_changed.emit(selected)
        return True

    def translate(self, value: object) -> str:
        """Translate managed text while leaving user/domain data untouched."""
        text = str(value)
        if not text:
            return text
        if any(text in catalog.entries for catalog in self._catalogs.values()):
            return self._resolve(text, typed=False)
        key = self.canonical_key(text)
        if key is None:
            return text
        return self._resolve(key, typed=False)

    def translate_key(self, key: TranslationKey | str) -> str:
        """Translate a declared key without ever exposing the raw key."""
        return self._resolve(str(key), typed=True)

    def format(
        self,
        key: TranslationKey | str,
        /,
        *args: object,
        **kwargs: object,
    ) -> str:
        return self.translate_key(key).format(*args, **kwargs)

    def _resolve(self, key: str, *, typed: bool) -> str:
        selected = self._catalogs.get(self._language)
        if selected is not None:
            translated = selected.entries.get(key, "")
            if translated.strip():
                return translated
        vietnamese = self._catalogs.get(UiLanguage.VI_VN)
        if vietnamese is not None:
            translated = vietnamese.entries.get(key, "")
            if translated.strip():
                self._record(key, "VI_VN_FALLBACK")
                return translated
        self._record(key, "SAFE_FALLBACK")
        if typed:
            return self._safe_fallback.get(
                self._language,
                SAFE_FALLBACK_TEXT[UiLanguage.VI_VN],
            )
        return key

    def _record(self, key: str, resolution: str) -> None:
        diagnostic = TranslationDiagnostic(self._language, key, resolution)
        self._diagnostics.append(diagnostic)
        LOGGER.warning(
            "I18N dự phòng locale=%s khóa=%r resolution=%s",
            self._language.value,
            key,
            resolution,
        )

    @contextmanager
    def using(self, language: UiLanguage) -> Iterator[None]:
        previous = self.language
        self.set_language(language)
        try:
            yield
        finally:
            self.set_language(previous)


# Canonical English, reviewed Vietnamese, and reviewed Korean.  These entries
# cover the application shell, preferences, standard dialogs, lifecycle,
# geometry transfer and accessibility surfaces exercised by the Stage package.
CORE_TRANSLATIONS: tuple[tuple[str, str, str], ...] = (
    ("3D Export", "Xuất 3D", "3D 내보내기"),
    (
        "Persistent 3D Export Defaults",
        "Mặc định Xuất 3D bền vững",
        "영구 3D 내보내기 기본값",
    ),
    (
        "These profiles seed 3D Export, Export Selected Objects, and 3D Save As.",
        "Các hồ sơ này được dùng làm mặc định cho Xuất 3D, Xuất đối tượng được chọn và Lưu thành định dạng 3D.",
        "이 프로필은 3D 내보내기, 선택한 객체 내보내기 및 3D 다른 이름으로 저장의 기본값입니다.",
    ),
    ("Unit policy", "Chính sách đơn vị", "단위 정책"),
    ("Model units (fixed)", "Đơn vị mô hình (cố định)", "모델 단위 (고정)"),
    ("Reset current format", "Khôi phục định dạng hiện tại", "현재 형식 초기화"),
    (
        "Reset all export defaults",
        "Khôi phục toàn bộ mặc định xuất",
        "모든 내보내기 기본값 초기화",
    ),
    (
        "Factory default restored for this format",
        "Đã khôi phục mặc định gốc cho định dạng này",
        "이 형식의 초기 기본값을 복원했습니다",
    ),
    (
        "Factory defaults restored for all formats",
        "Đã khôi phục mặc định gốc cho mọi định dạng",
        "모든 형식의 초기 기본값을 복원했습니다",
    ),
    (
        "3D Export settings applied",
        "Đã áp dụng thiết lập Xuất 3D",
        "3D 내보내기 설정을 적용했습니다",
    ),
    (
        "3D Export settings could not be saved",
        "Không thể lưu thiết lập Xuất 3D",
        "3D 내보내기 설정을 저장할 수 없습니다",
    ),
    (
        "3D Export settings are corrupted",
        "Thiết lập Xuất 3D bị lỗi",
        "3D 내보내기 설정이 손상되었습니다",
    ),
    (
        "Safe factory defaults are shown; Apply to replace the invalid values.",
        "Đang hiển thị mặc định gốc an toàn; chọn Áp dụng để thay thế các giá trị không hợp lệ.",
        "안전한 초기 기본값을 표시합니다. 잘못된 값을 바꾸려면 적용을 선택하십시오.",
    ),
    (
        "This format is not available",
        "Định dạng này chưa khả dụng",
        "이 형식은 사용할 수 없습니다",
    ),
    (
        "Interactive export defaults must use safe no-overwrite policy.",
        "Mặc định xuất tương tác phải dùng chính sách an toàn không ghi đè.",
        "대화형 내보내기 기본값은 안전한 덮어쓰기 금지 정책을 사용해야 합니다.",
    ),
    ("Confirm replacement", "Xác nhận thay thế", "바꾸기 확인"),
    (
        "The export destination already exists. Replace it?",
        "Đích xuất đã tồn tại. Bạn có muốn thay thế không?",
        "내보내기 대상이 이미 있습니다. 바꾸시겠습니까?",
    ),
    ("Export Selected Objects", "Xuất đối tượng được chọn", "선택한 객체 내보내기"),
    ("3D Export Profile", "Hồ sơ xuất 3D", "3D 내보내기 프로필"),
    ("Format", "Định dạng", "형식"),
    ("Version / standard", "Phiên bản / tiêu chuẩn", "버전 / 표준"),
    ("Availability", "Khả dụng", "가용성"),
    ("Available", "Khả dụng", "사용 가능"),
    ("Binary", "Nhị phân", "바이너리"),
    ("STL encoding", "Mã hóa STL", "STL 인코딩"),
    ("Linear deflection", "Sai lệch tuyến tính", "선형 편차"),
    ("Angular deflection", "Sai lệch góc", "각도 편차"),
    ("Relative mesh tolerance", "Dung sai lưới tương đối", "상대 메시 공차"),
    (
        "Existing mesh is re-encoded without remeshing; tessellation settings are not applicable.",
        "Lưới hiện có chỉ được mã hóa lại, không chia lưới lại; các thiết lập tessellation không áp dụng.",
        "기존 메시는 재메시 없이 다시 인코딩되며 테셀레이션 설정은 적용되지 않습니다.",
    ),
    ("Export 3D file", "Xuất file 3D", "3D 파일 내보내기"),
    ("Validation error", "Lỗi xác thực", "검증 오류"),
    ("Export failed", "Xuất thất bại", "내보내기 실패"),
    ("3D export completed", "Đã xuất 3D", "3D 내보내기 완료"),
    ("Exporting 3D data…", "Đang xuất dữ liệu 3D…", "3D 데이터 내보내는 중…"),
    ("Cancelling 3D export…", "Đang hủy xuất 3D…", "3D 내보내기 취소 중…"),
    (
        "Cannot cancel because the file is being finalized",
        "Không thể hủy vì tệp đang được hoàn tất",
        "파일 마무리가 시작되어 취소할 수 없습니다",
    ),
    ("3D export cancelled", "Đã hủy xuất 3D", "3D 내보내기 취소됨"),
    ("3D export failed", "Xuất 3D thất bại", "3D 내보내기 실패"),
    (
        "3D export operation status",
        "Trạng thái tác vụ xuất 3D",
        "3D 내보내기 작업 상태",
    ),
    ("3D export activity", "Hoạt động xuất 3D", "3D 내보내기 작업"),
    ("Cancel 3D export", "Hủy xuất 3D", "3D 내보내기 취소"),
    (
        "Unsupported CAD export extension.",
        "Phần mở rộng xuất CAD không được hỗ trợ.",
        "지원되지 않는 CAD 내보내기 확장자입니다.",
    ),
    (
        "Export profile format does not match the destination extension.",
        "Định dạng hồ sơ xuất không khớp phần mở rộng đích.",
        "내보내기 프로필 형식이 대상 확장자와 일치하지 않습니다.",
    ),
    (
        "No valid CAD selection is available for export.",
        "Không có lựa chọn CAD hợp lệ để xuất.",
        "내보낼 수 있는 유효한 CAD 선택 항목이 없습니다.",
    ),
    (
        "The CAD selection is stale or its geometry kind is unsupported.",
        "Lựa chọn CAD đã cũ hoặc loại hình học không được hỗ trợ.",
        "CAD 선택이 오래되었거나 형상 종류가 지원되지 않습니다.",
    ),
    (
        "Parasolid proprietary writer SDK is not present.",
        "Không có SDK writer độc quyền Parasolid.",
        "전용 형상 커널 쓰기 개발 도구가 없습니다.",
    ),
    (
        "ACIS proprietary writer SDK is not present.",
        "Không có SDK writer độc quyền ACIS.",
        "전용 형상 커널 쓰기 개발 도구가 없습니다.",
    ),
    (
        "No DWG export adapter is implemented.",
        "Chưa triển khai adapter xuất DWG.",
        "해당 도면 형식의 내보내기 어댑터가 구현되지 않았습니다.",
    ),
    (
        "DXF appears in the legacy source-file picker, but no export writer exists.",
        "DXF chỉ xuất hiện trong bộ chọn file nguồn cũ; chưa có writer xuất.",
        "해당 도면 형식은 기존 원본 파일 선택기에만 있으며 내보내기 작성기가 없습니다.",
    ),
    (
        "Unsupported Save As extension; no file was created.",
        "Phần mở rộng Save As không được hỗ trợ; không có file nào được tạo.",
        "지원되지 않는 다른 이름으로 저장 확장자이며 파일이 생성되지 않았습니다.",
    ),
    ("File", "Tệp", "파일"),
    ("Edit", "Sửa", "편집"),
    ("View", "Hiển thị", "보기"),
    ("Help", "Trợ giúp", "도움말"),
    ("Machine", "Máy", "머신"),
    ("Machine qualification", "Xác nhận máy", "기계 검증"),
    ("No qualification report.", "Chưa có báo cáo xác nhận.", "검증 보고서가 없습니다."),
    ("Controller", "Bộ điều khiển", "컨트롤러"),
    ("Qualification level", "Mức xác nhận", "검증 수준"),
    ("Blocking errors", "Lỗi chặn", "차단 오류"),
    ("Unverified physical items", "Mục vật lý chưa xác minh", "미검증 물리 항목"),
    ("Advanced qualification details", "Chi tiết xác nhận nâng cao", "고급 검증 세부 정보"),
    ("Machine profile fingerprint", "Dấu vân tay hồ sơ máy", "기계 프로필 지문"),
    ("NC SHA-256", "NC SHA-256", "NC 해시 SHA-256"),
    ("Work offset status", "Trạng thái gốc phôi", "작업 오프셋 상태"),
    ("Offset namespace status", "Trạng thái phạm vi offset", "오프셋 네임스페이스 상태"),
    ("Tapping status", "Trạng thái Tapping", "탭 가공 상태"),
    ("Physical travel status", "Trạng thái hành trình vật lý", "물리 이동 상태"),
    ("Safety status", "Trạng thái an toàn", "안전 상태"),
    ("Unqualified", "Chưa xác nhận", "미검증"),
    ("Statically validated", "Đạt kiểm tra tĩnh", "정적 검증 완료"),
    ("Dry-run qualified", "Đã dry-run", "드라이런 검증 완료"),
    ("Machine accepted", "Đã nghiệm thu trên máy", "기계 승인 완료"),
    ("Physical qualification", "Xác nhận kiểm tra vật lý", "물리 검증"),
    ("Step 1 — Machine", "Bước 1 — Máy", "1단계 — 기계"),
    ("Step 2 — NC", "Bước 2 — NC", "2단계 — NC"),
    ("Step 3 — Setup", "Bước 3 — Gá đặt", "3단계 — 설정"),
    ("Step 4 — Tool and Holder", "Bước 4 — Dao & Holder", "4단계 — 공구 및 홀더"),
    ("Step 5 — Fixture", "Bước 5 — Đồ gá", "5단계 — 고정구"),
    ("Step 6 — Travel validation", "Bước 6 — Kiểm tra hành trình", "6단계 — 이동 범위 검증"),
    ("Step 7 — Dry-run", "Bước 7 — Dry-run", "7단계 — 드라이런"),
    ("Step 8 — Result", "Bước 8 — Kết quả", "8단계 — 결과"),
    (
        "Select and verify the exact machine profile.",
        "Chọn và xác minh đúng hồ sơ máy.",
        "정확한 기계 프로필을 선택하고 확인합니다.",
    ),
    (
        "Bind the exact statically qualified NC artifact.",
        "Liên kết đúng kết quả NC đã đạt kiểm tra tĩnh.",
        "정적 검증된 정확한 NC 결과물을 연결합니다.",
    ),
    (
        "Enter or verify G54, part zero, and stock placement.",
        "Nhập hoặc xác minh G54, gốc chi tiết và vị trí phôi.",
        "G54, 부품 원점 및 소재 배치를 입력하거나 확인합니다.",
    ),
    (
        "Review Tool, Holder, gauge length, stick-out, and reach.",
        "Rà soát Dao, Holder, chiều dài chuẩn, độ nhô và tầm với.",
        "공구, 홀더, 게이지 길이, 돌출량 및 도달 범위를 검토합니다.",
    ),
    (
        "Record fixture identity, placement, geometry authority, and state.",
        "Ghi nhận danh tính, vị trí, thẩm quyền hình học và trạng thái đồ gá.",
        "고정구 식별, 배치, 형상 근거 및 상태를 기록합니다.",
    ),
    (
        "Show known machine-coordinate travel and every physical unknown.",
        "Hiển thị hành trình tọa độ máy đã biết và mọi mục vật lý chưa rõ.",
        "확인된 기계 좌표 이동과 모든 물리적 미확인 항목을 표시합니다.",
    ),
    (
        "Record evidence from an externally performed machine check.",
        "Ghi nhận bằng chứng từ kiểm tra máy được thực hiện bên ngoài phần mềm.",
        "외부에서 수행한 기계 점검 증거를 기록합니다.",
    ),
    (
        "Review Level1, external-readiness, and Level2 eligibility.",
        "Rà soát Level1, mức sẵn sàng kiểm tra ngoài và điều kiện Level2.",
        "Level1, 외부 검증 준비 상태 및 Level2 자격을 검토합니다.",
    ),
    ("Exact machine profile", "Đúng hồ sơ máy", "정확한 기계 프로필"),
    ("NC artifact", "Kết quả NC", "NC 결과물"),
    ("Translation in machine coordinates", "Tịnh tiến trong tọa độ máy", "기계 좌표 평행 이동"),
    ("Setup orientation", "Hướng gá đặt", "설정 방향"),
    ("Stock placement", "Vị trí phôi", "소재 배치"),
    ("Stick-out", "Độ nhô", "돌출량"),
    ("Reach", "Tầm với", "도달 범위"),
    ("Fixture evidence", "Bằng chứng đồ gá", "고정구 증거"),
    ("Verification state", "Trạng thái xác minh", "검증 상태"),
    ("Bounding envelope", "Bao giới hạn", "경계 범위"),
    ("Holder and fixture clearance", "Khoảng hở Holder và đồ gá", "홀더 및 고정구 간격"),
    ("Run mode", "Chế độ chạy", "실행 모드"),
    ("Operator", "Người vận hành", "작업자"),
    ("Verifier", "Người xác minh", "검증자"),
    ("Acceptance authority", "Thẩm quyền chấp thuận", "승인 권한"),
    ("Observations", "Quan sát", "관찰 사항"),
    ("Qualification result", "Kết quả xác nhận", "검증 결과"),
    ("Level3 boundary", "Ranh giới Level3", "Level3 경계"),
    ("Missing evidence", "Bằng chứng còn thiếu", "누락된 증거"),
    ("Physical blockers", "Lỗi chặn vật lý", "물리적 차단 항목"),
    ("Export verification package", "Xuất gói kiểm tra", "검증 패키지 내보내기"),
    ("Ready for machine verification", "Sẵn sàng kiểm tra trên máy", "기계 검증 준비 완료"),
    ("Waiting for dry-run", "Chờ dry-run", "드라이런 대기 중"),
    ("Dry-run passed", "Dry-run đạt", "드라이런 통과"),
    ("Dry-run failed", "Dry-run không đạt", "드라이런 실패"),
    ("Evidence is stale", "Bằng chứng đã lỗi thời", "증거가 오래됨"),
    ("Not machine accepted", "Chưa nghiệm thu trên máy", "기계 승인 전"),
    ("Setup", "Thiết lập", "가공 설정"),
    ("Exit", "Thoát", "종료"),
    ("About HMS CAD/CAM", "Giới thiệu HMS CAD/CAM", "HMS CAD/CAM 정보"),
    ("Recent projects", "Dự án gần đây", "최근 프로젝트"),
    ("Open CAM workspace", "Mở không gian làm việc CAM", "CAM 작업 공간 열기"),
    ("View direction", "Hướng nhìn", "보기 방향"),
    ("Display", "Hiển thị", "표시"),
    ("Selection", "Lựa chọn", "선택"),
    ("Quick access", "Truy cập nhanh", "빠른 실행"),
    ("Undo", "Hoàn tác", "실행 취소"),
    ("Redo", "Làm lại", "다시 실행"),
    ("not available", "chưa khả dụng", "사용할 수 없음"),
    ("Project / Topology", "Dự án / Cấu trúc hình học", "프로젝트 / 토폴로지"),
    ("CAD properties", "Thuộc tính CAD", "CAD 속성"),
    ("Edit CAM operation", "Chỉnh sửa nguyên công CAM", "CAM 작업 편집"),
    ("Project structure / Geometry management", "Cấu trúc hình học / Quản lý dự án", "형상 구조 / 프로젝트 관리"),
    ("Object", "Đối tượng", "객체"),
    ("Status", "Trạng thái", "상태"),
    ("Error", "Lỗi", "오류"),
    ("No project open", "Chưa mở dự án", "열린 프로젝트 없음"),
    ("Project", "Dự án", "프로젝트"),
    ("Properties", "Thuộc tính", "속성"),
    ("Property", "Thuộc tính", "속성"),
    ("Value", "Giá trị", "값"),
    ("Output / Log", "Đầu ra / Nhật ký", "출력 / 로그"),
    ("Ready", "Sẵn sàng", "준비"),
    ("Loading CAD…", "Đang tải CAD…", "CAD 로딩 중…"),
    ("CAD loaded.", "Đã tải CAD.", "CAD 로딩 완료."),
    (
        "CAD loading cancelled.",
        "Đã hủy tải CAD.",
        "CAD 로딩이 취소되었습니다.",
    ),
    (
        "CAD loading failed.",
        "Không thể tải CAD.",
        "CAD 로딩에 실패했습니다.",
    ),
    ("CAD loading status", "Trạng thái tải CAD", "CAD 로딩 상태"),
    (
        "CAD loading progress",
        "Tiến trình tải CAD đang chạy",
        "CAD 로딩 진행 중",
    ),
    (
        "Cancel CAD loading",
        "Hủy tải CAD",
        "CAD 로딩 취소",
    ),
    ("NO PROJECT", "KHÔNG CÓ DỰ ÁN", "프로젝트 없음"),
    ("NO CAM PROJECT", "CHƯA CÓ DỰ ÁN", "CAM 프로젝트 없음"),
    ("NOTIFICATIONS: 0", "THÔNG BÁO: 0", "알림: 0"),
    ("NOTIFICATIONS: {count}", "THÔNG BÁO: {count}", "알림: {count}"),
    ("3D geometry notification center", "Trung tâm thông báo dữ liệu 3D", "3D 형상 알림 센터"),
    ("OBJECTS: 0", "ĐỐI TƯỢNG: 0", "객체: 0"),
    ("Workspace", "Môi trường làm việc", "작업 공간"),
    ("Select HMS workspace", "Chọn môi trường làm việc HMS", "HMS 작업 공간 선택"),
    ("HOME", "TRANG CHỦ", "홈"),
    ("MILL 2D", "PHAY 2D", "2D 밀링"),
    ("MILL 3D", "PHAY 3D", "3D 밀링"),
    ("LATHE", "TIỆN", "선삭"),
    ("SIMULATION", "MÔ PHỎNG", "시뮬레이션"),
    ("Project overview and common commands", "Tổng quan dự án và lệnh thường dùng", "프로젝트 개요 및 자주 쓰는 명령"),
    ("Design, import and inspect CAD", "Thiết kế, nhập và kiểm tra CAD", "CAD 설계, 가져오기 및 검사"),
    ("Program existing 2D/2.5D CAM", "Lập trình CAM 2D/2.5D hiện có", "기존 2D/2.5D CAM 프로그래밍"),
    ("3D CAM is foundational; the production UI is not implemented", "CAM 3D mới ở mức nền tảng; giao diện chính thức chưa triển khai", "3D CAM은 기반 단계이며 정식 사용자 인터페이스는 아직 구현되지 않았습니다"),
    ("Lathe is not implemented in HMS", "Tiện chưa được triển khai trong HMS", "HMS에는 선삭 기능이 아직 구현되지 않았습니다"),
    ("Open the existing Simulation panel", "Mở bảng mô phỏng hiện có", "기존 시뮬레이션 패널 열기"),
    ("Open Post and Program Assembly", "Mở bảng Post và Lắp ráp chương trình", "Post 및 프로그램 어셈블리 열기"),
    ("Operation table", "Bảng nguyên công", "작업 테이블"),
    ("Artifact summary", "Tóm tắt sản phẩm", "산출물 요약"),
    ("WP4 artifact host is unavailable in WP2.", "Vùng sản phẩm WP4 chưa khả dụng trong WP2.", "산출물 영역은 2단계에서 사용할 수 없습니다."),
    (
        "This capability is not available in the current work package.",
        "Chức năng này chưa khả dụng trong gói công việc hiện tại.",
        "이 기능은 현재 작업 패키지에서 사용할 수 없습니다.",
    ),
    ("Preview", "Xem trước", "미리 보기"),
    # C3.1 General Settings user-facing contract.
    ("Advanced", "Nâng cao", "고급"),
    ("Applied: {percent}%", "Đang áp dụng: {percent}%", "적용 중: {percent}%"),
    ("CAD/Viewer", "CAD/Viewer", "CAD/뷰어"),
    ("CAM", "CAM", "CAM"),
    ("Child", "Con", "자식"),
    ("Configure interface and display preferences.", "Cấu hình tùy chọn giao diện và hiển thị.", "인터페이스 및 표시 설정을 구성합니다."),
    ("Enter any whole-number percentage from 50% to 200%.", "Nhập tỷ lệ phần trăm nguyên từ 50% đến 200%.", "50%에서 200% 사이의 정수 백분율을 입력하세요."),
    ("Features", "Tính năng", "기능"),
    ("General settings", "Cài đặt tổng", "일반 설정"),
    ("Keyboard shortcuts", "Phím tắt", "키보드 단축키"),
    ("Open general settings...", "Mở Cài đặt tổng...", "일반 설정 열기..."),
    ("Performance", "Hiệu năng", "성능"),
    ("Preview: {percent}%", "Đang xem trước: {percent}%", "미리 보기: {percent}%"),
    ("Quick presets", "Mức chọn nhanh", "빠른 사전 설정"),
    ("Reset", "Đặt lại", "재설정"),
    ("Reset to 100%", "Khôi phục 100%", "100%로 복원"),
    ("Root", "Gốc", "루트"),
    ("Sample button", "Nút mẫu", "샘플 버튼"),
    ("Sample input", "Ô nhập mẫu", "샘플 입력"),
    ("Sample option A", "Tùy chọn mẫu A", "샘플 옵션 A"),
    ("Sample option B", "Tùy chọn mẫu B", "샘플 옵션 B"),
    ("Sample option C", "Tùy chọn mẫu C", "샘플 옵션 C"),
    ("Sample title", "Tiêu đề mẫu", "샘플 제목"),
    ("Sample tree", "Cây mẫu", "샘플 트리"),
    ("Scale and density", "Tỷ lệ và mật độ", "배율 및 밀도"),
    ("Storage & projects", "Lưu và dự án", "저장 및 프로젝트"),
    ("The UI scale could not be saved.", "Không thể lưu tỷ lệ giao diện.", "UI 배율을 저장할 수 없습니다."),
    ("This scale may require a compact layout on the current screen.", "Tỷ lệ này có thể cần bố cục thu gọn trên màn hình hiện tại.", "이 배율은 현재 화면에서 압축 레이아웃이 필요할 수 있습니다."),
    ("This settings category has no available options in the current version.", "Nhóm cài đặt này chưa có tùy chọn khả dụng trong phiên bản hiện tại.", "현재 버전에서는 이 설정 범주에 사용할 수 있는 옵션이 없습니다."),
    ("UI scale", "Tỷ lệ giao diện", "UI 배율"),
    ("UI scale changes the logical presentation metrics without changing Windows DPI.", "Tỷ lệ giao diện thay đổi các chỉ số hiển thị logic mà không thay đổi DPI Windows.", "UI 배율은 Windows DPI를 변경하지 않고 논리적 표시 지표를 조정합니다."),
    ("Preview is not available in WP2.", "Xem trước chưa khả dụng trong WP2.", "미리 보기는 2단계에서 사용할 수 없습니다."),
    ("Diagnostics", "Chẩn đoán", "진단"),
    ("Diagnostics drawer is not available in WP2.", "Ngăn chẩn đoán chưa khả dụng trong WP2.", "진단 창은 2단계에서 사용할 수 없습니다."),
    ("Generate", "Tạo", "생성"),
    ("Save Managed", "Lưu có quản lý", "관리 저장"),
    ("Export External", "Xuất ra ngoài", "외부로 내보내기"),
    ("No status", "Chưa có trạng thái", "상태 없음"),
    ("Unavailable", "Chưa khả dụng", "사용할 수 없음"),
    ("Readiness unavailable", "Chưa có trạng thái sẵn sàng", "준비 상태를 확인할 수 없음"),
    ("No projection evidence.", "Chưa có bằng chứng trạng thái.", "상태 증거가 없습니다."),
    ("Presentation only; no downstream action was started.", "Chỉ hiển thị; không khởi chạy tác vụ tiếp theo.", "표시 전용이며 후속 작업은 시작되지 않았습니다."),
    ("Operation Manager", "Quản lý nguyên công", "작업 관리자"),
    ("Geometry / Project", "Hình học / Dự án", "형상 / 프로젝트"),
    ("Operations", "Nguyên công", "작업"),
    ("Function Editor", "Trình chỉnh sửa chức năng", "기능 편집기"),
    ("Diagnostics & Activity", "Chẩn đoán & Hoạt động", "진단 및 활동"),
    ("Diagnostics and background tasks", "Chẩn đoán và tác vụ nền", "진단 및 백그라운드 작업"),
    ("Simulation / Post", "Mô phỏng / Post", "시뮬레이션 / Post"),
    ("Simulation", "Mô phỏng", "시뮬레이션"),
    ("Post / Program Assembly", "Post / Lắp ráp chương trình", "Post / 프로그램 어셈블리"),
    ("3D geometry notifications", "Thông báo dữ liệu 3D", "3D 형상 알림"),
    ("New 3D geometry notification", "Thông báo dữ liệu 3D mới", "새 3D 형상 알림"),
    ("Review 3D geometry changes", "Xem thay đổi dữ liệu 3D", "3D 형상 변경 검토"),
    ("Reset Workspace Layout", "Khôi phục bố cục làm việc", "작업 공간 레이아웃 초기화"),
    ("Levels", "Cao độ", "레벨"),
    ("Toolpath", "Đường chạy dao", "공구 경로"),
    ("Toolpaths", "Đường chạy dao", "공구 경로"),
    ("Planes", "Mặt phẳng", "평면"),
    ("Name", "Tên", "이름"),
    ("Size", "Kích thước", "크기"),
    ("Type", "Loại", "유형"),
    ("Date modified", "Ngày sửa đổi", "수정한 날짜"),
    ("Computer", "Máy tính", "내 컴퓨터"),
    ("User folder", "Thư mục người dùng", "사용자 폴더"),
    ("Sidebar", "Thanh bên", "사이드바"),
    (
        "List of places and bookmarks",
        "Danh sách vị trí và dấu trang",
        "위치 및 북마크 목록",
    ),
    ("Folder", "Thư mục", "폴더"),
    ("File name", "Tên tệp", "파일 이름"),
    ("File type", "Loại tệp", "파일 형식"),
    ("Open", "Mở", "열기"),
    ("Save", "Lưu", "저장"),
    ("Don’t save", "Không lưu", "저장 안 함"),
    ("Cancel", "Hủy", "취소"),
    ("Close", "Đóng", "닫기"),
    ("Scroll Left", "Cuộn sang trái", "왼쪽으로 스크롤"),
    ("Scroll Right", "Cuộn sang phải", "오른쪽으로 스크롤"),
    ("Apply", "Áp dụng", "적용"),
    ("Create", "Tạo", "만들기"),
    ("Select", "Chọn", "선택"),
    ("Yes", "Có", "예"),
    ("No", "Không", "아니요"),
    ("Ignore", "Bỏ qua", "무시"),
    ("Defer", "Để sau", "나중에"),
    ("Update", "Cập nhật", "업데이트"),
    ("Replace", "Thay thế", "교체"),
    ("Add", "Thêm", "추가"),
    ("Language", "Ngôn ngữ", "언어"),
    ("Language…", "Ngôn ngữ…", "언어…"),
    ("Interface", "Giao diện", "인터페이스"),
    ("Settings", "Cài đặt", "설정"),
    ("Language settings", "Cài đặt ngôn ngữ", "언어 설정"),
    ("Interface language", "Ngôn ngữ giao diện", "인터페이스 언어"),
    ("Current language", "Ngôn ngữ hiện tại", "현재 언어"),
    ("Choose the language used by HMS CAD/CAM.", "Chọn ngôn ngữ hiển thị của HMS CAD/CAM.", "HMS CAD/CAM에서 사용할 표시 언어를 선택합니다."),
    ("The language changes immediately without modifying the project.", "Ngôn ngữ thay đổi ngay mà không sửa dữ liệu dự án.", "프로젝트 데이터를 변경하지 않고 언어가 즉시 바뀝니다."),
    ("Vietnamese — Default", "Tiếng Việt — Mặc định", "베트남어 — 기본값"),
    ("English", "English", "영어"),
    ("Korean", "한국어", "한국어"),
    ("Default language", "Ngôn ngữ mặc định", "기본 언어"),
    ("HMS uses Vietnamese for new or invalid settings.", "HMS dùng tiếng Việt khi cài đặt mới hoặc không hợp lệ.", "새 설정이나 잘못된 설정에는 베트남어가 사용됩니다."),
    ("Language preference could not be saved.", "Không thể lưu lựa chọn ngôn ngữ.", "언어 기본 설정을 저장할 수 없습니다."),
    ("New 3D data", "Dữ liệu 3D mới", "새 3D 데이터"),
    (
        "{count} new 3D update is available from “{source}”.",
        "Có {count} bản cập nhật 3D mới từ “{source}”.",
        "“{source}”에서 새 3D 업데이트 {count}건이 도착했습니다.",
    ),
    (
        "{count} new 3D updates are available from “{source}”.",
        "Có {count} bản cập nhật 3D mới từ “{source}”.",
        "“{source}”에서 새 3D 업데이트 {count}건이 도착했습니다.",
    ),
    ("No new 3D data.", "Không có dữ liệu 3D mới.", "새 3D 데이터가 없습니다."),
    ("Review changes", "Xem thay đổi", "변경 검토"),
    ("Send new 3D to CAM project", "Nạp 3D mới cho dự án CAM", "CAM 프로젝트로 새 3D 보내기"),
    ("Incoming geometry", "Dữ liệu hình học đến", "수신 형상"),
    ("Current models", "Mô hình hiện tại", "현재 모델"),
    ("New geometry", "Hình học mới", "새 형상"),
    ("Affected operations", "Nguyên công bị ảnh hưởng", "영향받는 작업"),
    ("Safety warning", "Cảnh báo an toàn", "안전 경고"),
    ("Update method", "Cách cập nhật", "업데이트 방법"),
    ("Target model", "Mô hình đích", "대상 모델"),
    ("Choose a target model…", "Chọn mô hình đích…", "대상 모델 선택…"),
    ("No model.", "Không có mô hình.", "모델이 없습니다."),
    ("No related operations.", "Không có nguyên công liên quan.", "관련 작업이 없습니다."),
    ("Save changes?", "Lưu thay đổi?", "변경 사항을 저장하시겠습니까?"),
    ("The current document has unsaved changes.", "Tài liệu hiện tại có thay đổi chưa lưu.", "현재 문서에 저장되지 않은 변경 사항이 있습니다."),
    ("Create CAM project", "Tạo dự án CAM", "CAM 프로젝트 만들기"),
    ("Project name", "Tên dự án", "프로젝트 이름"),
    ("Parent folder", "Thư mục cha", "상위 폴더"),
    ("Full path", "Đường dẫn đầy đủ", "전체 경로"),
    ("Create project", "Tạo dự án", "프로젝트 만들기"),
    ("CAD VIEWER UNAVAILABLE", "TRÌNH XEM CAD KHÔNG KHẢ DỤNG", "CAD 뷰어 사용 불가"),
    ("CAD VIEWER ERROR", "LỖI TRÌNH XEM CAD", "CAD 뷰어 오류"),
    ("Coordinate system: Top", "Hệ tọa độ: Trên", "좌표계: 위"),
    ("WCS: Top", "WCS: Trên", "작업 좌표계: 위"),
    ("METRIC", "HỆ MÉT", "미터법"),
    ("None", "Chưa có", "없음"),
    ("Machine — unavailable", "Máy — chưa khả dụng", "머신 — 사용할 수 없음"),
    ("Cutting tool — unavailable", "Dao — chưa khả dụng", "Tool — 사용할 수 없음"),
    ("Setup — unavailable", "Thiết lập — chưa khả dụng", "가공 설정 — 사용할 수 없음"),
    ("Toolpath — unavailable", "Đường chạy dao — chưa khả dụng", "공구 경로 — 사용할 수 없음"),
    ("Properties — unavailable", "Thuộc tính — chưa khả dụng", "속성 — 사용할 수 없음"),
    ("Surface", "Bề mặt", "서피스"),
    ("Solid selection", "Khối rắn", "솔리드 선택"),
    ("Open STEP/STP", "Mở STEP/STP", "STEP/STP 열기"),
    ("Open BREP", "Mở BREP", "BREP 열기"),
    ("Open IGES/IGS", "Mở IGES/IGS", "IGES/IGS 열기"),
    ("Open STL", "Mở STL", "STL 열기"),
    ("CAD production viewer is ready; CAM is not integrated.", "Trình xem CAD sản phẩm đã sẵn sàng; chưa tích hợp CAM.", "제품 CAD 뷰어가 준비되었으며 CAM은 아직 통합되지 않았습니다."),
    ("HMS CAD/CAM is ready.", "HMS CAD/CAM đã sẵn sàng.", "HMS CAD/CAM이 준비되었습니다."),
    ("CAD Viewer unavailable: {reason}", "Trình xem CAD không khả dụng: {reason}", "CAD 뷰어 사용 불가: {reason}"),
    (
        "CAD rendering backend is unavailable.",
        "Bộ dựng hình CAD không khả dụng.",
        "CAD 렌더링 백엔드를 사용할 수 없습니다.",
    ),
    ("CAD Viewer", "Trình xem CAD", "CAD 뷰어"),
    ("Viewer", "Trình xem", "뷰어"),
    ("Mesh", "Lưới", "메시"),
    ("strategy", "chiến lược", "전략"),
    ("Ribbon", "Dải lệnh", "명령 리본"),
    ("Run Simulation", "Chạy mô phỏng", "시뮬레이션 실행"),
    ("Cancel Calculation", "Hủy tính toán", "계산 취소"),
    ("Clear Post Result", "Xóa kết quả Post", "Post 결과 지우기"),
    ("Generate Post", "Tạo Post", "Post 생성"),
    ("Move Down", "Di chuyển xuống", "아래로 이동"),
    ("Move Up", "Di chuyển lên", "위로 이동"),
    ("Top", "Trên", "위"),
    ("Back", "Sau", "뒤"),
    ("Float", "Thả nổi", "분리"),
    ("Closes the dock widget", "Đóng bảng này", "이 패널 닫기"),
    (
        "Undocks and re-attaches the dock widget",
        "Tách hoặc gắn lại bảng này",
        "이 패널을 분리하거나 다시 연결",
    ),
    ("Geometry structure / Project Manager", "Cấu trúc hình học / Quản lý dự án", "형상 구조 / 프로젝트 관리자"),
    ("Post processor", "Bộ xử lý Post", "Post 프로세서"),
    ("Program Assembly", "Lắp ráp chương trình", "프로그램 어셈블리"),
)


RIBBON_TRANSLATIONS: tuple[tuple[str, str, str], ...] = (
    ("Send 3D to CAM", "Nạp 3D vào CAM", "3D를 CAM으로 전송"),
    ("Undo", "Hoàn tác", "실행 취소"),
    ("Redo", "Làm lại", "다시 실행"),
    ("Fit all", "Hiện toàn bộ", "전체 맞춤"),
    ("Isometric", "Trục đo", "등각 보기"),
    ("Shaded with edges", "Tô bóng kèm cạnh", "모서리 포함 음영"),
    ("Select solid", "Chọn khối rắn", "솔리드 선택"),
    ("Select face", "Chọn bề mặt", "면 선택"),
    ("Select wire", "Chọn chuỗi", "와이어 선택"),
    ("Select edge", "Chọn cạnh", "모서리 선택"),
    ("Select vertex", "Chọn đỉnh", "꼭짓점 선택"),
    ("Measure BREP", "Đo BREP", "BREP 측정"),
    ("Home", "Trang chủ", "홈"),
    ("Wireframe", "Khung dây", "와이어프레임"),
    ("Prepare model", "Chuẩn bị mô hình", "모델 준비"),
    ("Drawing", "Bản vẽ", "도면"),
    ("Transform", "Biến đổi", "변환"),
    ("Bottom", "Dưới", "아래"),
    ("Front", "Trước", "앞"),
    ("Left", "Trái", "왼쪽"),
    ("Right", "Phải", "오른쪽"),
    ("Shaded", "Tô bóng", "음영"),
    ("Cutting tool", "Dao", "Tool"),
    ("Move", "Di chuyển", "이동"),
    ("Rotate", "Xoay", "회전"),
    ("Mirror", "Đối xứng", "대칭"),
    ("Scale", "Tỷ lệ", "배율"),
    ("Dimension", "Kích thước", "치수"),
    ("Annotation", "Ghi chú", "주석"),
    ("Section", "Mặt cắt", "단면"),
    ("Layer", "Lớp", "레이어"),
    ("Create mesh", "Tạo lưới", "메시 생성"),
    ("Edit mesh", "Sửa lưới", "메시 편집"),
    ("Reduce mesh", "Giảm lưới", "메시 단순화"),
    ("Inspect", "Kiểm tra", "검사"),
    ("Push", "Đẩy", "밀기"),
    ("Simplify", "Đơn giản hóa", "단순화"),
    ("Repair", "Sửa lỗi", "복구"),
    ("Solid", "Khối", "솔리드"),
    ("Extrude", "Đùn", "돌출"),
    ("Boolean", "Boolean", "Boolean"),
    ("Fillet", "Bo tròn", "필렛"),
    ("Create surface", "Tạo mặt", "서피스 생성"),
    ("Offset", "Dịch biên", "오프셋"),
    ("Trim", "Cắt xén", "트림"),
    ("Blend", "Nối chuyển tiếp", "블렌드"),
    ("Point", "Điểm", "점"),
    ("Line", "Đường", "선"),
    ("Arc", "Cung", "호"),
    ("Corner fillet", "Bo góc", "모서리 필렛"),
    ("Clipboard", "Bảng tạm", "클립보드"),
    ("Cut", "Cắt", "잘라내기"),
    ("Copy", "Sao chép", "복사"),
    ("Paste", "Dán", "붙여넣기"),
    ("Analysis", "Phân tích", "분석"),
    ("Measure", "Đo", "측정"),
    ("Statistics", "Thống kê", "통계"),
)


LEGACY_TRANSLATIONS: tuple[tuple[str, str, str], ...] = (
    ("No operation selected", "Chưa chọn nguyên công", "선택한 작업 없음"),
    ("Open an editor from Operation Manager.", "Chọn một nút trong Quản lý nguyên công để mở trình chỉnh sửa cũ.", "작업 관리자에서 편집기를 열 작업을 선택합니다."),
    ("CAM function editor window", "Cửa sổ chỉnh sửa chức năng CAM", "CAM 기능 편집기 창"),
    ("Unique CAM editor window; geometry can still be selected in the viewport.", "Cửa sổ CAM chính duy nhất; vẫn cho phép chọn hình học trong khung nhìn.", "단일 CAM 편집기 창이며 뷰포트에서 형상을 계속 선택할 수 있습니다."),
    ("Legacy editor bridge", "Bộ chuyển tiếp trình chỉnh sửa cũ", "레거시 편집기 연결"),
    ("Legacy production editor", "Trình chỉnh sửa sản xuất cũ", "레거시 생산 편집기"),
    ("Legacy editor content", "Nội dung trình chỉnh sửa cũ", "레거시 편집기 내용"),
    ("Keep the current analysis, validation and atomic CAM commands", "Giữ nguyên phân tích, kiểm tra và lệnh CAM nguyên tử hiện tại", "현재 분석, 검증 및 원자적 CAM 명령 유지"),
    ("Apply draft with legacy editor", "Áp dụng bản nháp bằng trình chỉnh sửa cũ", "레거시 편집기로 초안 적용"),
    ("Hide panel; legacy draft keeps its current lifecycle and is not applied automatically", "Ẩn bảng; bản nháp cũ vẫn theo vòng đời hiện tại, không tự áp dụng", "패널을 숨기며 레거시 초안은 현재 수명 주기를 유지하고 자동 적용하지 않습니다"),
    ("Close function editor", "Đóng trình chỉnh sửa chức năng", "기능 편집기 닫기"),
    ("Collapse function editor", "Thu gọn trình chỉnh sửa chức năng", "기능 편집기 접기"),
    ("X coordinate", "hệ tọa độ X", "X 좌표"),
    ("Y coordinate", "hệ tọa độ Y", "Y 좌표"),
    ("Z coordinate", "hệ tọa độ Z", "Z 좌표"),
    ("Setup kind", "Loại thiết lập", "가공 설정 종류"),
    ("Stock kind", "Loại phôi", "스톡 종류"),
    ("Dimension A", "Kích thước A", "치수 A"),
    ("Dimension B", "Kích thước B", "치수 B"),
    ("Dimension C", "Kích thước C", "치수 C"),
    ("Cut direction", "Hướng cắt", "절삭 방향"),
    ("Contour side", "Phía contour", "윤곽 측면"),
    ("Multiple depth levels", "Nhiều lớp chiều sâu", "다중 깊이 레벨"),
    ("Boring retract policy", "Chính sách rút dao khi khoét lỗ", "보링 후퇴 정책"),
    ("Retract policy", "Chính sách rút dao", "후퇴 정책"),
    ("Create job", "Tạo công việc", "작업 만들기"),
    ("Create setup", "Tạo thiết lập", "가공 설정 만들기"),
    ("Create basic Tool and machine", "Tạo Tool và máy cơ bản", "기본 Tool 및 머신 만들기"),
    ("Create Tool and machine for Parallel Finishing", "Tạo Tool cầu/Máy cho Gia công tinh song song", "평행 정삭용 Tool 및 머신 만들기"),
    ("Create tapping Tool and machine", "Tạo Tool taro và máy cơ bản", "탭 Tool 및 머신 만들기"),
    ("Create reaming Tool and machine", "Tạo Tool doa và máy cơ bản", "리밍 Tool 및 머신 만들기"),
    ("Create boring Tool and machine", "Tạo Tool cán khoét và máy cơ bản", "보링 Tool 및 머신 만들기"),
    ("Add group", "Thêm nhóm", "그룹 추가"),
    ("Add Facing 2.5D", "Thêm Phay mặt 2.5D", "2.5D 페이싱 추가"),
    ("Add 2D Contour", "Thêm Phay biên dạng 2D", "2D 윤곽 가공 추가"),
    ("Add Pocket 2.5D", "Thêm Phay hốc 2.5D", "2.5D 포켓 추가"),
    ("Add Parallel Finishing", "Thêm Gia công tinh song song", "평행 정삭 추가"),
    ("Add Z-Level Finishing", "Thêm Gia công tinh theo cao độ Z", "Z 레벨 정삭 추가"),
    ("Add Drilling", "Thêm Khoan", "드릴링 추가"),
    ("Add Tapping", "Thêm Taro", "탭 가공 추가"),
    ("Add Reaming", "Thêm Doa lỗ", "리밍 추가"),
    ("Add Boring", "Thêm Khoét lỗ", "보링 추가"),
    ("Recalculate", "Tính lại", "다시 계산"),
    ("Create / recalculate", "Tạo/Tính lại", "생성 / 다시 계산"),
    ("Show/hide toolpath", "Hiện/ẩn đường chạy dao", "툴패스 표시/숨기기"),
    ("Link/relink geometry", "Liên kết/Liên kết lại hình học", "형상 연결/다시 연결"),
    ("Delete geometry", "Xóa hình học", "형상 삭제"),
    ("Move up", "Lên", "위로 이동"),
    ("Operation Manager commands", "Lệnh Quản lý nguyên công", "작업 관리자 명령"),
    ("Selection-scoped commands in Operation Manager", "Lệnh theo vùng chọn của Quản lý nguyên công", "작업 관리자 선택 항목 명령"),
    ("Filter Operation Manager status", "Lọc trạng thái Quản lý nguyên công", "작업 관리자 상태 필터"),
    ("Add operation (Operation Manager)", "Thêm operation", "작업 추가"),
    ("Add operation", "Thêm thao tác", "작업 추가"),
    ("Add more", "+ Thêm", "+ 추가"),
    ("Add operations to Program Assembly", "Thêm vào Lắp ráp chương trình", "프로그램 어셈블리에 작업 추가"),
    ("Delete selection with confirmation", "Xóa vùng chọn có xác nhận", "확인 후 선택 항목 삭제"),
    ("Delete Simulation result", "Xóa kết quả Mô phỏng", "시뮬레이션 결과 삭제"),
    ("Delete NC result", "Xóa kết quả NC", "수치 제어 결과 삭제"),
    ("Delete toolpath result", "Xóa kết quả đường chạy dao", "툴패스 결과 삭제"),
    ("Rename", "Đổi tên", "이름 바꾸기"),
    ("Duplicate", "Nhân bản", "복제"),
    ("Calculate", "Tính", "계산"),
    ("Recalculate toolpath", "Tính lại đường chạy dao", "툴패스 다시 계산"),
    ("All", "Tất cả", "모두"),
    ("Enabled", "Đang bật", "사용"),
    ("Disabled", "Đã tắt", "사용 안 함"),
    ("Needs calculation", "Cần tính", "계산 필요"),
    ("Warnings", "Cảnh báo", "경고"),
    ("Progress", "Tiến độ", "진행률"),
    ("Default", "Mặc định", "기본값"),
    ("Apply policy", "Áp dụng policy", "정책 적용"),
    ("Current project", "Dự án hiện hành", "현재 프로젝트"),
    ("Current job, setup and machine", "Công việc, thiết lập và máy hiện hành", "현재 작업, 가공 설정 및 머신"),
    ("Current project tree and CAM operations", "Cây dự án, công việc, thiết lập và nguyên công CAM", "프로젝트 트리, 작업, 가공 설정 및 CAM 작업"),
    ("No CAM project open", "Chưa mở dự án CAM", "열린 CAM 프로젝트 없음"),
    ("No job · No setup · No machine assigned", "Chưa có công việc · Chưa có thiết lập · Chưa gán máy", "작업 없음 · 가공 설정 없음 · 머신 미할당"),
    ("Operation summary", "Tổng hợp trạng thái nguyên công", "작업 상태 요약"),
    ("Search Operation Manager", "Tìm trong Quản lý nguyên công", "작업 관리자 검색"),
    ("Name, strategy, Tool, status or ID…", "Tên, chiến lược, dao, trạng thái hoặc ID…", "이름, 전략, Tool, 상태 또는 ID…"),
    ("No current result to delete.", "Chưa có kết quả hiện hành để xóa.", "삭제할 현재 결과가 없습니다."),
    ("No Simulation result to delete.", "Chưa có kết quả Mô phỏng để xóa.", "삭제할 시뮬레이션 결과가 없습니다."),
    ("No managed NC result to delete.", "Chưa có kết quả NC được quản lý để xóa.", "삭제할 관리 수치 제어 결과가 없습니다."),
    ("No Post result in this session to delete.", "Chưa có kết quả Post trong phiên để xóa.", "이 세션에 삭제할 Post 결과가 없습니다."),
    ("No NC export result to view.", "Chưa có kết quả Xuất NC để xem.", "볼 수치 제어 내보내기 결과가 없습니다."),
    ("No safe toolpath deletion command is available in the application service.", "Chưa có lệnh xóa kết quả đường chạy dao an toàn trong dịch vụ ứng dụng.", "애플리케이션 서비스에 안전한 툴패스 삭제 명령이 없습니다."),
    ("Simulation is open", "Mở Mô phỏng", "시뮬레이션 열기"),
    ("Open Post preview", "Mở bản xem trước Post", "Post 미리 보기 열기"),
    ("Open Simulation or Post", "Mở Post hoặc Lắp ráp chương trình", "Post 또는 프로그램 어셈블리 열기"),
    ("Open CAM project", "Mở dự án CAM", "CAM 프로젝트 열기"),
    ("Create CAM project from current document", "Tạo dự án CAM từ tài liệu hiện tại", "현재 문서에서 CAM 프로젝트 만들기"),
    ("Save as HMS document", "Lưu thành tài liệu HMS", "HMS 문서로 저장"),
    ("Close document/workspace", "Đóng tài liệu/không gian làm việc", "문서/작업 공간 닫기"),
    ("Add strategy", "Chọn chiến lược nguyên công để thêm", "추가할 작업 전략 선택"),
    ("Choose Simulation", "Chọn nút Mô phỏng.", "시뮬레이션 버튼을 선택합니다."),
    ("Choose an operation with a visible toolpath.", "Chọn nguyên công có hiển thị đường chạy dao.", "툴패스가 표시된 작업을 선택합니다."),
    ("Choose an operation or its Geometry.", "Chọn nguyên công hoặc Hình học của nguyên công.", "작업 또는 작업 형상을 선택합니다."),
    ("Choose an operation, Post result, NC result or Program Assembly.", "Chọn nguyên công, kết quả Post, kết quả NC hoặc Lắp ráp chương trình.", "작업, Post 결과, 수치 제어 결과 또는 프로그램 어셈블리를 선택합니다."),
    ("Choose a Post result, NC result or Program Assembly.", "Chọn kết quả Post, kết quả NC hoặc Lắp ráp chương trình.", "Post 결과, 수치 제어 결과 또는 프로그램 어셈블리를 선택합니다."),
    ("Select Setup, operation list or group to add an operation.", "Chọn thiết lập, danh sách nguyên công hoặc nhóm để thêm nguyên công.", "작업을 추가할 가공 설정, 작업 목록 또는 그룹을 선택합니다."),
    ("The draft operation must be valid, applied and have Geometry/Tool/machine.", "Bản nháp nguyên công phải hợp lệ, đã áp dụng và có đủ hình học/Tool/máy.", "초안 작업은 유효하고 적용되어야 하며 형상/Tool/머신이 있어야 합니다."),
    ("The operation must be valid and not already in the current Program Assembly.", "Nguyên công phải hợp lệ và chưa có trong Lắp ráp chương trình hiện tại.", "작업은 유효해야 하며 현재 프로그램 어셈블리에 없어야 합니다."),
    ("Current NC and valid export configuration are required.", "Cần NC hiện hành và cấu hình Xuất hợp lệ.", "현재 수치 제어 결과와 유효한 내보내기 구성이 필요합니다."),
    ("Current toolpath and validation are required before Simulation.", "Cần đường chạy dao HIỆN HÀNH và kiểm tra trước Mô phỏng hợp lệ.", "시뮬레이션 전에 현재 툴패스와 검증이 필요합니다."),
    ("Post source/capability must be valid and no Post task may be running.", "Nguồn/cổng Post phải hợp lệ và không có tác vụ Post đang chạy.", "Post 소스/기능이 유효하고 실행 중인 Post 작업이 없어야 합니다."),
    ("Only a Group/Operation with a domain order can be moved.", "Chỉ Nhóm/Nguyên công có thứ tự miền mới được di chuyển.", "도메인 순서가 있는 그룹/작업만 이동할 수 있습니다."),
    ("Only a domain operation can be duplicated.", "Chỉ nguyên công có định danh miền mới được nhân bản.", "도메인 식별자가 있는 작업만 복제할 수 있습니다."),
    ("Only the active Parallel Finishing task can be cancelled.", "Chỉ có thể hủy tác vụ Gia công tinh song song đang tính.", "활성 평행 정삭 작업만 취소할 수 있습니다."),
    ("HMS does not calculate, simulate or post-process automatically.", "Không tự tính toán, mô phỏng hoặc xử lý hậu kỳ.", "HMS는 자동으로 계산, 시뮬레이션 또는 후처리하지 않습니다."),
    ("Geometry transfer notification", "Thông báo dữ liệu 3D", "형상 전송 알림"),
    ("Add more", "+ Thêm", "+ 추가"),
    ("0 operations · 0 warnings · 0 errors", "0 nguyên công · 0 cảnh báo · 0 lỗi", "작업 0 · 경고 0 · 오류 0"),
    ("Select one item; use stable domain IDs; Enter opens and Delete requires confirmation.", "Cây chọn một mục, dùng ID miền ổn định; phím nhập mở mục, phím xóa yêu cầu xác nhận.", "항목을 선택하고 안정적인 도메인 ID를 사용합니다. 입력 키로 열고 삭제하려면 확인해야 합니다."),
    ("Diagnostics log", "Nhật ký chẩn đoán HMS", "HMS 진단 로그"),
    ("INFO · CAD: Ready", "THÔNG TIN · CAD: Sẵn sàng", "정보 · CAD: 준비됨"),
    ("Background task status", "Trạng thái tác vụ nền", "백그라운드 작업 상태"),
    ("Create or open a CAM project folder to begin.", "Tạo hoặc mở thư mục dự án CAM để bắt đầu.", "시작하려면 CAM 프로젝트 폴더를 만들거나 엽니다."),
    ("Collapse Diagnostics", "Thu gọn Chẩn đoán", "진단 접기"),
    ("Collapse Operation Manager", "Thu gọn Quản lý nguyên công", "작업 관리자 접기"),
)


VIETNAMESE_SOURCE_TRANSLATIONS: tuple[tuple[str, str, str], ...] = (
    ("+ Thêm", "+ Add", "+ 추가"),
    (
        "Absolute Setup-WCS Z, chia lớp và hai allowance độc lập của domain v1.",
        "Absolute Setup-WCS Z, depth levels, and two independent allowances in domain v1.",
        "절대 Setup-WCS Z, 깊이 레벨 및 domain v1의 독립적인 두 allowance.",
    ),
    (
        "Advanced chứa override ít dùng và collapsed mặc định.",
        "Advanced contains rarely used overrides and is collapsed by default.",
        "Advanced에는 드물게 사용하는 override가 있으며 기본적으로 접혀 있습니다.",
    ),
    (
        "Algorithm policy read-only; Contour v1 không có tolerance/filter/post override.",
        "Algorithm policy is read-only; Contour v1 has no tolerance, filter, or Post override.",
        "알고리즘 정책은 읽기 전용이며 Contour v1에는 tolerance, filter 또는 Post override가 없습니다.",
    ),
    (
        "Basic giữ các quyết định người vận hành thường xuyên dùng.",
        "Basic contains the decisions most frequently used by the operator.",
        "Basic에는 작업자가 자주 사용하는 결정 항목이 있습니다.",
    ),
    (
        "Chiều cắt đi theo -Z; axial allowance được cộng vào final cutter depth.",
        "Cutting proceeds in -Z; axial allowance is added to the final cutter depth.",
        "절삭은 -Z 방향으로 진행되며 axial allowance가 최종 cutter depth에 더해집니다.",
    ),
    (
        "Chọn Tool Assembly project-owned; thay đổi chỉ nằm trong draft.",
        "Select a project-owned Tool Assembly; changes remain only in the draft.",
        "프로젝트 소유 Tool Assembly를 선택합니다. 변경 내용은 draft에만 유지됩니다.",
    ),
    (
        "Chọn project-owned Tool Assembly; hình học dao là read-only.",
        "Select a project-owned Tool Assembly; tool geometry is read-only.",
        "프로젝트 소유 Tool Assembly를 선택합니다. Tool 형상은 읽기 전용입니다.",
    ),
    (
        "Chọn quan hệ của tâm dao với profile.",
        "Select the relationship between the tool center and the profile.",
        "Tool 중심과 profile 사이의 관계를 선택합니다.",
    ),
    (
        "Clearance, retract và linear lead v1; mọi Z đều thuộc Setup WCS.",
        "Clearance, retract, and linear lead v1; all Z values belong to the Setup WCS.",
        "Clearance, retract 및 linear lead v1을 사용하며 모든 Z 값은 Setup WCS에 속합니다.",
    ),
    (
        "Climb, conventional hoặc bidirectional theo contract Facing v1.",
        "Climb, conventional, or bidirectional according to the Facing v1 contract.",
        "Facing v1 contract에 따라 climb, conventional 또는 bidirectional을 사용합니다.",
    ),
    (
        "Contour v1 luôn dùng linear lead-in và linear lead-out cùng chiều dài.",
        "Contour v1 always uses linear lead-in and linear lead-out of equal length.",
        "Contour v1은 항상 같은 길이의 linear lead-in과 linear lead-out을 사용합니다.",
    ),
    (
        "Contour v1 nhận đúng một loop kín LINE/ARC; không tự đảo geometry.",
        "Contour v1 accepts exactly one closed LINE/ARC loop and does not reverse geometry automatically.",
        "Contour v1은 닫힌 LINE/ARC loop 하나만 허용하며 geometry를 자동으로 반전하지 않습니다.",
    ),
    (
        "Contour v1 offset trong HMS; không có CONTROL/WEAR, D offset hoặc G41/G42.",
        "Contour v1 offsets in HMS; CONTROL/WEAR, D offset, and G41/G42 are not available.",
        "Contour v1 offset은 HMS에서 계산하며 CONTROL/WEAR, D offset 및 G41/G42는 지원하지 않습니다.",
    ),
    (
        "Controller-neutral; cycle code chỉ được quyết định ở Post.",
        "Controller-neutral; cycle code is determined only by the Post.",
        "Controller-neutral이며 cycle code는 Post에서만 결정됩니다.",
    ),
    (
        "Cutting gom feed, spindle và lượng cắt theo workflow.",
        "Cutting groups feed, spindle, and cutting amounts according to the workflow.",
        "Cutting은 workflow에 따라 feed, spindle 및 절삭량을 그룹화합니다.",
    ),
    (
        "Các quyết định Pocket cốt lõi nằm trong Geometry, Tool, Cutting, Levels và Entry.",
        "Core Pocket decisions are in Geometry, Tool, Cutting, Levels, and Entry.",
        "Pocket의 핵심 결정 항목은 Geometry, Tool, Cutting, Levels 및 Entry에 있습니다.",
    ),
    (
        "Derived = Bottom Z + floor allowance; không phải input thứ hai.",
        "Derived = Bottom Z + floor allowance; it is not a second input.",
        "Derived = Bottom Z + floor allowance이며 두 번째 입력값이 아닙니다.",
    ),
    (
        "Derived theo thuật toán pocket_depth_levels hiện có.",
        "Derived by the existing pocket_depth_levels algorithm.",
        "기존 pocket_depth_levels 알고리즘으로 계산됩니다.",
    ),
    (
        "Domain yêu cầu Clearance >= Retract > Top.",
        "The domain requires Clearance >= Retract > Top.",
        "Domain은 Clearance >= Retract > Top을 요구합니다.",
    ),
    (
        "Expert là prototype presentation-only. Thay đổi precision có thể ảnh hưởng chất lượng và thời gian; Stage 9A.4 không gửi vào engine.",
        "Expert is a presentation-only prototype. Precision changes may affect quality and time; Stage 9A.4 does not send them to the engine.",
        "Expert는 표시 전용 prototype입니다. Precision 변경은 품질과 시간에 영향을 줄 수 있으며 Stage 9A.4에서는 engine으로 전달되지 않습니다.",
    ),
    (
        "Facing v1 yêu cầu Top Z bằng mặt trên Stock BOX.",
        "Facing v1 requires Top Z to equal the top face of the Stock BOX.",
        "Facing v1은 Top Z가 Stock BOX의 윗면과 같아야 합니다.",
    ),
    (
        "Generator Pocket v1 chỉ có deterministic inward offset loops.",
        "The Pocket v1 generator supports only deterministic inward offset loops.",
        "Pocket v1 generator는 결정적 inward offset loop만 지원합니다.",
    ),
    (
        "Generator plunge thẳng tại start của từng offset loop.",
        "The generator plunges vertically at the start of each offset loop.",
        "Generator는 각 offset loop 시작점에서 수직으로 plunge합니다.",
    ),
    (
        "Generator quyết định traversal từ Side + Direction; không sửa geometry.",
        "The generator determines traversal from Side + Direction and does not modify geometry.",
        "Generator는 Side + Direction으로 traversal을 결정하며 geometry를 수정하지 않습니다.",
    ),
    (
        "Geometry chứa selection summary và preview/focus hook.",
        "Geometry contains the selection summary and the preview/focus hook.",
        "Geometry에는 선택 요약과 preview/focus hook이 포함됩니다.",
    ),
    (
        "Geometry reference được hiển thị bằng summary, không lộ raw key.",
        "Geometry references are displayed as summaries without exposing raw keys.",
        "Geometry reference는 raw key를 노출하지 않고 요약으로 표시됩니다.",
    ),
    (
        "Không đồng nhất field này với orientation hoặc cutting direction.",
        "Do not treat this field as orientation or cutting direction.",
        "이 field를 orientation 또는 cutting direction과 동일하게 취급하지 마십시오.",
    ),
    (
        "Levels dùng semantic Top/Depth và nguồn Stock/Geometry.",
        "Levels uses Top/Depth semantics and Stock/Geometry sources.",
        "Levels는 Top/Depth 의미와 Stock/Geometry 소스를 사용합니다.",
    ),
    (
        "Linking hiển thị safe motion kế thừa và field phụ thuộc mode.",
        "Linking shows inherited safe motion and mode-dependent fields.",
        "Linking은 상속된 safe motion과 mode별 field를 표시합니다.",
    ),
    (
        "Lặp lại loop tại lớp cuối; không phải rest machining.",
        "Repeat the loop at the final level; this is not rest machining.",
        "마지막 레벨에서 loop를 반복하며 rest machining은 아닙니다.",
    ),
    (
        "Machine requirement hiện có; domain kiểm tra capability/feed/spindle.",
        "The machine requirement already exists; the domain validates capability, feed, and spindle.",
        "Machine requirement가 이미 있으며 domain이 capability, feed 및 spindle을 검증합니다.",
    ),
    ("Nguồn Facing", "Facing source", "Facing 소스"),
    (
        "Nguồn phải khớp loại GeometryReference đã Select.",
        "The source must match the selected GeometryReference type.",
        "소스는 선택한 GeometryReference 유형과 일치해야 합니다.",
    ),
    ("Nguồn profile", "Profile source", "Profile 소스"),
    (
        "Nominal bottom trong Setup WCS; floor allowance nâng final cutter Z.",
        "Nominal bottom in the Setup WCS; floor allowance raises the final cutter Z.",
        "Setup WCS의 nominal bottom이며 floor allowance가 최종 cutter Z를 높입니다.",
    ),
    (
        "Offset pattern, direction, stepover, wall allowance và tốc độ cắt.",
        "Offset pattern, direction, stepover, wall allowance, and cutting speed.",
        "Offset pattern, direction, stepover, wall allowance 및 절삭 속도.",
    ),
    (
        "Override ít dùng thuộc contract Facing v1.",
        "Rarely used overrides in the Facing v1 contract.",
        "Facing v1 contract에서 드물게 사용하는 override입니다.",
    ),
    ("Phía contour", "Contour side", "Contour 측면"),
    (
        "Pocket v1 chỉ hỗ trợ vertical plunge; không có ramp/helix/pre-drill.",
        "Pocket v1 supports only vertical plunge; ramp, helix, and pre-drill are unavailable.",
        "Pocket v1은 vertical plunge만 지원하며 ramp, helix 및 pre-drill은 지원하지 않습니다.",
    ),
    (
        "Pocket v1 fail-closed khi profile có inner loop; UI không tự suy ra island.",
        "Pocket v1 fails closed when the profile has an inner loop; the UI does not infer islands.",
        "Profile에 inner loop가 있으면 Pocket v1은 fail-closed하며 UI는 island를 추론하지 않습니다.",
    ),
    (
        "Pocket v1 nhận đúng một outer loop kín LINE/ARC.",
        "Pocket v1 accepts exactly one closed outer LINE/ARC loop.",
        "Pocket v1은 닫힌 outer LINE/ARC loop 하나만 허용합니다.",
    ),
    (
        "Precision của offset/depth algorithm; giá trị nhỏ có thể tăng chi phí tính.",
        "Precision of the offset/depth algorithm; smaller values may increase computation cost.",
        "Offset/depth 알고리즘의 precision이며 작은 값은 계산 비용을 높일 수 있습니다.",
    ),
    (
        "Precision duy nhất thực sự tồn tại trong Pocket v1.",
        "The only precision value that actually exists in Pocket v1.",
        "Pocket v1에 실제로 존재하는 유일한 precision 값입니다.",
    ),
    (
        "Preview STALE — đã bỏ kết quả cũ",
        "Preview stale — previous result discarded",
        "미리 보기 오래됨 — 이전 결과 폐기됨",
    ),
    ("Radial allowance không được âm.", "Radial allowance cannot be negative.", "Radial allowance는 음수일 수 없습니다."),
    (
        "Rút dao bảo thủ · fallback fail-closed",
        "Conservative retract · fail-closed fallback",
        "보수적 retract · fail-closed fallback",
    ),
    (
        "Safe motion v1 chỉ có explicit Clearance và Retract trong Setup WCS.",
        "Safe motion v1 has only explicit Clearance and Retract in the Setup WCS.",
        "Safe motion v1에는 Setup WCS의 명시적 Clearance와 Retract만 있습니다.",
    ),
    (
        "Select/Rebind dùng GeometryReference typed; không giữ OCP object.",
        "Select/Rebind uses a typed GeometryReference and does not retain OCP objects.",
        "Select/Rebind는 typed GeometryReference를 사용하며 OCP object를 유지하지 않습니다.",
    ),
    (
        "Select/Rebind tạo GeometryReference typed; preview không giữ OCP object.",
        "Select/Rebind creates a typed GeometryReference; preview does not retain OCP objects.",
        "Select/Rebind는 typed GeometryReference를 생성하며 preview는 OCP object를 유지하지 않습니다.",
    ),
    ("Setup chưa có operation", "Setup has no operations", "가공 설정에 작업이 없습니다"),
    (
        "Side, cutting direction, allowance và tốc độ công nghệ.",
        "Side, cutting direction, allowance, and process speeds.",
        "Side, cutting direction, allowance 및 가공 속도.",
    ),
    (
        "Spindle, feed và coolant theo contract hiện có.",
        "Spindle, feed, and coolant according to the existing contract.",
        "기존 contract에 따른 spindle, feed 및 coolant.",
    ),
    (
        "Stepover không được lớn hơn đường kính dao.",
        "Stepover cannot exceed the tool diameter.",
        "Stepover는 Tool 직경보다 클 수 없습니다.",
    ),
    (
        "Summary hình học; selection chi tiết thực hiện trong viewport.",
        "Geometry summary; detailed selection is performed in the viewport.",
        "Geometry 요약이며 세부 선택은 viewport에서 수행합니다.",
    ),
    ("Số lớp đã Apply", "Applied level count", "적용된 레벨 수"),
    ("Thêm operation", "Add operation", "작업 추가"),
    (
        "Thêm operation đầu tiên bằng strategy hiện có.",
        "Add the first operation using an existing strategy.",
        "기존 전략으로 첫 작업을 추가합니다.",
    ),
    ("Thêm thao tác", "Add operation", "작업 추가"),
    (
        "Tolerance hình học v1; không thêm tham số chuyên sâu giả.",
        "Geometry tolerance v1; no fabricated advanced parameter is added.",
        "Geometry tolerance v1이며 임의의 고급 파라미터를 추가하지 않습니다.",
    ),
    (
        "Tolerance nhỏ hơn thường tăng số điểm và thời gian tính.",
        "A smaller tolerance usually increases the point count and computation time.",
        "Tolerance가 작을수록 일반적으로 점 수와 계산 시간이 증가합니다.",
    ),
    (
        "Tool Assembly và chi tiết read-only từ Tool Library.",
        "Tool Assembly and read-only details from the Tool Library.",
        "Tool Library의 Tool Assembly 및 읽기 전용 세부 정보.",
    ),
    (
        "Tool Assembly đến từ Tool Library của project.",
        "The Tool Assembly comes from the project's Tool Library.",
        "Tool Assembly는 프로젝트 Tool Library에서 가져옵니다.",
    ),
    (
        "Tool Library là nguồn chân lý cho diameter, corner radius, holder và stickout.",
        "The Tool Library is the source of truth for diameter, corner radius, holder, and stickout.",
        "Tool Library는 diameter, corner radius, holder 및 stickout의 기준 소스입니다.",
    ),
    (
        "Tool Library là nguồn chân lý; Pocket v1 chỉ hỗ trợ END_MILL hợp lệ.",
        "The Tool Library is the source of truth; Pocket v1 supports only a valid END_MILL.",
        "Tool Library가 기준 소스이며 Pocket v1은 유효한 END_MILL만 지원합니다.",
    ),
    (
        "Top, target, allowance và phân lớp theo Setup WCS.",
        "Top, target, allowance, and depth levels in the Setup WCS.",
        "Setup WCS의 Top, target, allowance 및 깊이 레벨.",
    ),
    (
        "Tên, strategy, dao, status hoặc ID…",
        "Name, strategy, Tool, status, or ID…",
        "이름, 전략, Tool, 상태 또는 ID…",
    ),
    (
        "Tùy chọn ít dùng nhưng có trong Contour v1.",
        "A rarely used option that is available in Contour v1.",
        "Contour v1에서 사용할 수 있지만 드물게 쓰는 옵션입니다.",
    ),
    (
        "Tọa độ tuyệt đối trong Setup WCS; không phải machine coordinate.",
        "Absolute coordinates in the Setup WCS, not machine coordinates.",
        "Setup WCS의 절대 좌표이며 machine coordinate가 아닙니다.",
    ),
    (
        "Tốc độ trục chính; kiểm tra capability máy khi production binding.",
        "Spindle speed; validate machine capability during production binding.",
        "Spindle 속도이며 production binding 시 machine capability를 검증합니다.",
    ),
    ("WCS của Thiết lập", "Setup WCS", "Setup WCS"),
    ("projection hiện hành", "current projection", "현재 projection"),
    (
        "Đổi traversal của offset loops; không tự đảo geometry nguồn.",
        "Change the traversal of offset loops without reversing source geometry automatically.",
        "Offset loop의 traversal을 변경하며 소스 geometry를 자동으로 반전하지 않습니다.",
    ),
    ("Bật", "Enable", "켜기"),
    ("Tắt", "Disable", "끄기"),
    (
        "Bật hoặc tắt Chẩn đoán và nhật ký tác vụ",
        "Show or hide Diagnostics and the task log",
        "진단 및 작업 로그 표시 또는 숨기기",
    ),
    (
        "Bật hoặc tắt Quản lý nguyên công",
        "Show or hide Operation Manager",
        "작업 관리자 표시 또는 숨기기",
    ),
    (
        "Bật hoặc tắt bảng quy trình phụ",
        "Show or hide the secondary workflow panel",
        "보조 작업 흐름 패널 표시 또는 숨기기",
    ),
    (
        "Bật hoặc tắt bảng thuộc tính CAD",
        "Show or hide the CAD properties panel",
        "CAD 속성 패널 표시 또는 숨기기",
    ),
    (
        "Bật hoặc tắt cây Dự án và Topology CAD",
        "Show or hide the Project and CAD topology tree",
        "프로젝트 및 CAD 토폴로지 트리 표시 또는 숨기기",
    ),
    (
        "Chỉ khôi phục vị trí và trạng thái bảng của giao diện",
        "Reset only interface panel positions and states",
        "인터페이스 패널 위치와 상태만 초기화",
    ),
    ("Gia công lỗ", "Hole machining", "구멍 가공"),
    (
        "Hiện chi tiết NC/Xuất",
        "Show NC/export details",
        "수치 제어 및 내보내기 세부 정보 표시",
    ),
    (
        "Khôi phục bố cục UI mặc định; không thay đổi dữ liệu dự án",
        "Restore the default interface layout without changing project data",
        "프로젝트 데이터를 변경하지 않고 기본 인터페이스 레이아웃 복원",
    ),
    ("Lệnh CAM", "CAM commands", "CAM 명령"),
    (
        "Mở bảng xem trước dữ liệu 3D đang chờ",
        "Open the pending 3D data preview",
        "대기 중인 3D 데이터 미리 보기 열기",
    ),
    (
        "Mở cửa sổ chỉnh sửa cho nguyên công đang chọn; không tự áp dụng",
        "Open the editor for the selected operation without applying automatically",
        "선택한 작업의 편집기를 열되 자동으로 적용하지 않음",
    ),
    ("Nhập tệp CAD nguồn", "Import source CAD file", "원본 CAD 파일 가져오기"),
    (
        "Quản lý nguyên công chưa có ngữ cảnh dự án.",
        "Operation Manager has no project context.",
        "작업 관리자에 프로젝트 컨텍스트가 없습니다.",
    ),
    (
        "Nút này không có lệnh xóa trong miền hiện tại.",
        "This item has no deletion command in the current domain.",
        "이 항목에는 현재 도메인의 삭제 명령이 없습니다.",
    ),
    (
        "Nút trình chiếu không hỗ trợ đổi tên.",
        "Presentation nodes cannot be renamed.",
        "표시 노드는 이름을 바꿀 수 없습니다.",
    ),
    (
        "Nút đang bật hoặc không hỗ trợ thay đổi trạng thái bật/tắt.",
        "The item is enabled or cannot change its enabled state.",
        "항목이 켜져 있거나 활성 상태를 변경할 수 없습니다.",
    ),
    (
        "Nút đang tắt hoặc không hỗ trợ thay đổi trạng thái bật/tắt.",
        "The item is disabled or cannot change its enabled state.",
        "항목이 꺼져 있거나 활성 상태를 변경할 수 없습니다.",
    ),
    ("Xuất NC", "Export NC", "수치 제어 내보내기"),
    ("Xuống", "Down", "아래로"),
    (
        "Đổi ngôn ngữ giao diện mà không thay đổi dữ liệu dự án",
        "Change the interface language without changing project data",
        "프로젝트 데이터를 변경하지 않고 인터페이스 언어 변경",
    ),
    ("CAM 2D / Phay", "CAM 2D / Milling", "CAM 2D / 밀링"),
    ("Sau", "Back", "뒤"),
)


DISPLAY_SOURCE_TRANSLATIONS: tuple[tuple[str, str, str, str], ...] = (
    (
        "1 Chain · planar_face_outer · RESOLVED",
        "1 chuỗi · biên ngoài mặt phẳng · ĐÃ ĐỒNG BỘ",
        "1 chain · planar face outer · Resolved",
        "체인 1개 · planar face outer · 해결됨",
    ),
    ("ALLOW_WARN", "CHO PHÉP CẢNH BÁO", "Allow with warning", "경고와 함께 허용"),
    ("FAIL_IF_EXISTS", "KHÔNG GHI ĐÈ NẾU ĐÃ TỒN TẠI", "Fail if file exists", "파일이 있으면 실패"),
    ("FROM_PROGRAM_IR_ONLY", "CHỈ TỪ IR CHƯƠNG TRÌNH", "From Program IR only", "Program IR에서만"),
    ("HOLE MISSING", "THIẾU LỖ", "Hole missing", "홀 누락"),
    ("INVALID", "KHÔNG HỢP LỆ", "Invalid", "유효하지 않음"),
    ("MISSING", "THIẾU", "Missing", "누락"),
    ("NEVER_EXPORTED", "CHƯA TỪNG XUẤT", "Never exported", "내보낸 적 없음"),
    ("PRIMARY", "Chính", "Primary", "기본"),
    ("PROFILE MISSING", "THIẾU BIÊN DẠNG", "Profile missing", "프로파일 누락"),
    ("CHƯA CÓ DỰ ÁN", "CHƯA CÓ", "No CAM", "CAM 없음"),
    ("REPLACE_EXPLICIT", "GHI ĐÈ TƯỜNG MINH", "Explicit replace", "명시적 교체"),
    (
        "REPLACE_IF_SAME_ARTIFACT",
        "THAY NẾU CÙNG KẾT QUẢ",
        "Replace if same artifact",
        "동일 artifact이면 교체",
    ),
    ("REQUIRE_PASS", "YÊU CẦU ĐẠT", "Require pass", "통과 필요"),
    ("STALE", "ĐÃ LỖI THỜI", "Stale", "오래됨"),
    ("off", "tắt", "Off", "꺼짐"),
    (
        "parallel.size_limit: Estimated pass count exceeds guardrail.",
        "Số lượt cắt ước tính vượt giới hạn bảo vệ.",
        "Parallel size limit: Estimated pass count exceeds guardrail.",
        "평행 가공 크기 제한: 예상 패스 수가 보호 한계를 초과합니다.",
    ),
)


TECHNICAL_GLOSSARY: tuple[GlossaryEntry, ...] = tuple(
    GlossaryEntry(term, term, term, term)
    for term in (
        "CAD",
        "CAM",
        "CNC",
        "Tool",
        "Holder",
        "Post",
        "G-code",
        "Toolpath IR",
        "SQLite",
        "OCP",
        "BRep",
        "UUID",
        "ID",
        "STEP",
        "IGES",
        "STL",
        "U/V/W",
    )
)


KOREAN_OVERRIDES = MappingProxyType(
    {
        "HMS CAD/CAM — Design": "HMS CAD/CAM — 설계",
        "CAD document": "CAD 문서",
        "Document ID": "문서 ID",
        "Fingerprint": "형상 지문",
        "Geometry": "형상",
        "Bounding box": "바운딩 박스",
        "Bounding X": "X 크기",
        "Bounding Y": "Y 크기",
        "Bounding Z": "Z 크기",
        "Basic": "기본",
        "Advanced": "고급",
        "Expert": "전문가",
        "Preview": "미리 보기",
        "Validate": "검증",
        "Calculate": "계산",
        "Modified": "수정됨",
        "Invalid": "잘못됨",
        "Applied": "적용됨",
        "Stale": "오래됨",
        "Tool details": "Tool 세부 정보",
        "Machining Faces": "가공 면",
        "Selected Faces": "선택한 면",
        "Selected faces": "선택한 면",
        "Remove": "제거",
        "Clear": "지우기",
        "Tolerance": "공차",
        "Surface Allowance": "표면 여유",
        "Stepover": "스텝오버",
        "Feed rate": "이송 속도",
        "Safety Status": "안전 상태",
        "Calculation Status": "계산 상태",
        "Z-Level Finishing": "Z 레벨 정삭",
        "Facing 2.5D": "2.5D 페이싱",
        "Planar Face Facing": "평면 페이싱",
        "2D Contour": "2D 윤곽 가공",
        "Pocket 2.5D": "2.5D 포켓",
        "Drilling": "드릴링",
        "Tapping": "탭 가공",
        "Reaming": "리밍",
        "Boring": "보링",
        "Parallel Finishing": "평행 정삭",
    }
)


def build_default_catalogs() -> Mapping[UiLanguage, TranslationCatalog]:
    """Build complete catalogs from the legacy production key inventory."""
    # Lazy import avoids a module cycle: localization owns the established
    # 694-key Vietnamese inventory and imports only the public service API.
    from hms_cadcam.ui.localization import UI_TRANSLATIONS

    vi_entries = dict(UI_TRANSLATIONS)
    en_entries = {key: key for key in UI_TRANSLATIONS}
    ko_entries = {
        key: KOREAN_OVERRIDES.get(key, key)
        for key in UI_TRANSLATIONS
    }
    stage13b_entries = (
        ("stage13b.advisor.analyze", "Phân tích", "Analyze", "분석"),
        ("stage13b.advisor.cancel", "Hủy", "Cancel", "취소"),
        ("stage13b.advisor.apply_selected", "Áp dụng mục đã chọn", "Apply selected", "선택 항목 적용"),
        ("stage13b.advisor.reset_selection", "Đặt lại lựa chọn", "Reset selection", "선택 초기화"),
        ("stage13b.advisor.undo", "Hoàn tác thay đổi đã áp dụng", "Undo applied changes", "적용한 변경 실행 취소"),
        ("stage13b.advisor.close", "Đóng", "Close", "닫기"),
        ("stage13b.advisor.state.unavailable", "Không sẵn sàng", "Unavailable", "사용할 수 없음"),
        ("stage13b.advisor.state.waiting", "Đang chờ tài nguyên", "Waiting for resources", "리소스 대기 중"),
        ("stage13b.advisor.state.ready", "Kết quả sẵn sàng", "Result ready", "결과 준비됨"),
        ("stage13b.advisor.state.stale", "Kết quả đã cũ", "Stale result", "오래된 결과"),
    )
    stage13c_entries = (
        ("stage13c.advisor.strategy", "Chiến lược", "Strategy", "전략"),
        ("stage13c.advisor.workpiece_material", "Vật liệu phôi", "Workpiece material", "공작물 재질"),
        ("stage13c.advisor.tool_material", "Vật liệu dao", "Tool material", "공구 재질"),
        ("stage13c.advisor.not_selected", "Chưa chọn", "Not selected", "선택 안 함"),
        ("stage13c.advisor.hss", "Thép gió (HSS)", "High-speed steel (HSS)", "고속도강 (HSS)"),
        ("stage13c.advisor.carbide", "Hợp kim cứng (Carbide)", "Carbide", "초경합금"),
        ("stage13c.advisor.material.iso_p", "ISO P — Thép", "ISO P — Steel", "ISO P — 강"),
        ("stage13c.advisor.material.iso_m", "ISO M — Thép không gỉ", "ISO M — Stainless steel", "ISO M — 스테인리스강"),
        ("stage13c.advisor.material.iso_k", "ISO K — Gang", "ISO K — Cast iron", "ISO K — 주철"),
        ("stage13c.advisor.material.iso_n", "ISO N — Kim loại màu", "ISO N — Non-ferrous", "ISO N — 비철금속"),
        ("stage13c.advisor.material.iso_s", "ISO S — Siêu hợp kim", "ISO S — Superalloy", "ISO S — 내열합금"),
        ("stage13c.advisor.material.iso_h", "ISO H — Vật liệu tôi cứng", "ISO H — Hardened material", "ISO H — 경화재"),
        ("stage13c.advisor.active_diameter", "Đường kính đang dùng", "Active diameter", "사용 직경"),
        ("stage13c.advisor.current_values", "Giá trị hiện tại", "Current values", "현재 값"),
        ("stage13c.advisor.field.spindle", "Tốc độ trục chính", "Spindle speed", "주축 속도"),
        ("stage13c.advisor.field.feed", "Lượng chạy dao", "Feed per revolution", "회전당 이송"),
        ("stage13c.advisor.field.depth", "Chiều sâu cắt tối đa", "Maximum depth of cut", "최대 절입 깊이"),
        ("stage13c.advisor.state.material_required", "Cần chọn đủ vật liệu phôi và dao", "Workpiece and tool materials are required", "공작물 및 공구 재질을 선택해야 합니다"),
        ("stage13c.advisor.state.ready_to_analyze", "Sẵn sàng phân tích", "Ready to analyze", "분석 준비됨"),
        ("stage13c.advisor.state.ready", "Khuyến nghị sẵn sàng", "Recommendation ready", "권장값 준비됨"),
        ("stage13c.advisor.state.unavailable", "Không thể tạo khuyến nghị", "Recommendation unavailable", "권장값을 사용할 수 없음"),
        ("stage13c.advisor.state.stale", "Kết quả đã cũ; hãy phân tích lại", "Result is stale; analyze again", "결과가 오래되었습니다. 다시 분석하십시오"),
        ("stage13c.advisor.state.no_selection", "Chưa chọn trường để áp dụng", "No recommendation field selected", "적용할 권장 필드가 선택되지 않음"),
        ("stage13c.advisor.state.draft_applied", "Đã áp dụng vào bản nháp", "Applied to draft", "초안에 적용됨"),
        ("stage13c.advisor.state.undo_complete", "Đã hoàn tác bản nháp", "Draft changes undone", "초안 변경 실행 취소됨"),
        ("stage13c.advisor.state.undo_refused", "Không thể hoàn tác kết quả đã cũ", "Undo unavailable or refused", "실행 취소를 사용할 수 없거나 거부됨"),
        ("stage13c.advisor.state.cancelled", "Đã hủy kết quả khuyến nghị", "Recommendation cancelled", "권장 결과 취소됨"),
        ("stage13c.advisor.state.owner_invalidated", "Phiên biên tập đã đóng", "Editor session closed", "편집기 세션 닫힘"),
    )
    for key, vietnamese, english, korean in stage13b_entries + stage13c_entries:
        vi_entries[key] = vietnamese; en_entries[key] = english; ko_entries[key] = korean
    for english, vietnamese, korean in CORE_TRANSLATIONS:
        vi_entries[english] = vietnamese
        en_entries[english] = english
        ko_entries[english] = korean
    for english, vietnamese, korean in RIBBON_TRANSLATIONS:
        vi_entries[english] = vietnamese
        en_entries[english] = english
        ko_entries[english] = korean
        unavailable_key = f"{english} — unavailable"
        vi_entries[unavailable_key] = f"{vietnamese} — chưa khả dụng"
        en_entries[unavailable_key] = unavailable_key
        ko_entries[unavailable_key] = f"{korean} — 사용할 수 없음"
    for english, vietnamese, korean in LEGACY_TRANSLATIONS:
        vi_entries[english] = vietnamese
        en_entries[english] = english
        ko_entries[english] = korean
    for english, vietnamese, korean in STORAGE_TRANSLATIONS:
        vi_entries[english] = vietnamese
        en_entries[english] = english
        ko_entries[english] = korean
    for vietnamese, english, korean in VIETNAMESE_SOURCE_TRANSLATIONS:
        vi_entries.setdefault(vietnamese, vietnamese)
        en_entries[vietnamese] = english
        ko_entries[vietnamese] = korean
    for source, vietnamese, english, korean in DISPLAY_SOURCE_TRANSLATIONS:
        vi_entries[source] = vietnamese
        en_entries[source] = english
        ko_entries[source] = korean
    for term in TECHNICAL_GLOSSARY:
        vi_entries[term.source] = term.vietnamese
        en_entries[term.source] = term.english
        ko_entries[term.source] = term.korean
    fallback_catalogs = {
        UiLanguage.VI_VN: TranslationCatalog.from_pairs(
            UiLanguage.VI_VN,
            vi_entries.items(),
        ),
        UiLanguage.EN_US: TranslationCatalog.from_pairs(
            UiLanguage.EN_US,
            en_entries.items(),
        ),
        UiLanguage.KO_KR: TranslationCatalog.from_pairs(
            UiLanguage.KO_KR,
            ko_entries.items(),
        ),
    }
    catalog_directory = Path(__file__).with_name("catalogs")
    filenames = {
        UiLanguage.VI_VN: "vi_VN.json",
        UiLanguage.EN_US: "en_US.json",
        UiLanguage.KO_KR: "ko_KR.json",
    }
    loaded: dict[UiLanguage, TranslationCatalog] = {}
    for locale, filename in filenames.items():
        try:
            pairs = _read_catalog_pairs(catalog_directory / filename)
            existing_keys = {key for key, _value in pairs}
            pairs = (*pairs, *(
                (key, value)
                for key, value in fallback_catalogs[locale].entries.items()
                if key not in existing_keys
            ))
            loaded[locale] = TranslationCatalog.from_pairs(locale, pairs)
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            LOGGER.error(
                "Không nạp được catalog %s; dùng catalog tích hợp: %s",
                locale.value,
                exc,
            )
            loaded[locale] = fallback_catalogs[locale]
    return MappingProxyType(loaded)


_DEFAULT_SERVICE: TranslationService | None = None


def translation_service() -> TranslationService:
    """Return the process-wide UI service used by production widgets."""
    global _DEFAULT_SERVICE
    if _DEFAULT_SERVICE is None:
        _DEFAULT_SERVICE = TranslationService(build_default_catalogs())
    return _DEFAULT_SERVICE


def set_translation_service(service: TranslationService | None) -> None:
    """Replace the process service for deterministic tests and app bootstrap."""
    global _DEFAULT_SERVICE
    _DEFAULT_SERVICE = service


def language_display_name(
    language: UiLanguage,
    *,
    service: TranslationService | None = None,
) -> str:
    active = service or translation_service()
    key = {
        UiLanguage.VI_VN: "Vietnamese — Default",
        UiLanguage.EN_US: "English",
        UiLanguage.KO_KR: "Korean",
    }[UiLanguage.coerce(language)]
    return active.translate_key(key)


def format_geometry_update_message(
    count: int,
    source_display_name: object,
    *,
    service: TranslationService | None = None,
) -> str:
    """Format one complete geometry notice in locale-specific word order."""
    active = service or translation_service()
    normalized_count = max(0, int(count))
    if normalized_count == 0:
        return active.translate_key("No new 3D data.")
    source = str(source_display_name).strip()
    if not source.casefold().endswith(".hms"):
        source = f"{source}.HMS"
    key = (
        "{count} new 3D update is available from “{source}”."
        if normalized_count == 1
        else "{count} new 3D updates are available from “{source}”."
    )
    return active.format(key, count=normalized_count, source=source)


def validate_glossary(
    catalogs: Mapping[UiLanguage, TranslationCatalog],
) -> tuple[str, ...]:
    """Return deterministic locale/term violations for the controlled glossary."""
    violations: list[str] = []
    for term in TECHNICAL_GLOSSARY:
        for locale in UiLanguage:
            catalog = catalogs.get(locale)
            actual = "" if catalog is None else catalog.entries.get(term.source, "")
            expected = term.value(locale)
            if actual != expected:
                violations.append(
                    f"{locale.value}:{term.source}:{actual!r}!={expected!r}"
                )
    return tuple(violations)


def apply_application_font(
    language: UiLanguage,
    application: QApplication | None = None,
) -> str:
    """Use only installed Windows/system fonts and return the selected family."""
    app = application or QApplication.instance()
    families = set(QFontDatabase.families())
    preferences = (
        ("Malgun Gothic", "맑은 고딕", "Segoe UI")
        if UiLanguage.coerce(language) is UiLanguage.KO_KR
        else ("Segoe UI", "Arial")
    )
    family = next((candidate for candidate in preferences if candidate in families), "")
    if app is not None and family:
        # Keep the active point/pixel size mode and all style/fallback attributes.
        current = QFont(app.font())
        current.setFamily(family)
        app.setFont(current)
    return family or (app.font().family() if app is not None else "")


def apply_widget_font_tree(
    root: QWidget,
    language: UiLanguage,
    application: QApplication | None = None,
) -> str:
    """Apply the selected system font to an already-created widget tree.

    Qt copies the application font into widgets when they are constructed.
    Runtime locale changes therefore need to refresh existing widgets as well
    so Korean glyphs do not remain bound to the previous Latin-only font.
    """
    family = apply_application_font(language, application)
    if not family:
        return family
    widgets = (root, *root.findChildren(QWidget))
    for widget in widgets:
        current = widget.font()
        if current.family() == family:
            continue
        current.setFamily(family)
        widget.setFont(current)
    return family


def _format_fields(value: str) -> tuple[str, ...]:
    fields: list[str] = []
    try:
        for _literal, field, _format_spec, _conversion in Formatter().parse(value):
            if field is not None:
                fields.append(field)
    except ValueError:
        return ("<invalid-format>",)
    return tuple(fields)


def _read_catalog_pairs(path: Path) -> tuple[tuple[str, str], ...]:
    """Read a flat JSON object while retaining duplicate-key evidence."""
    document = json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=lambda pairs: tuple(pairs),
    )
    if not isinstance(document, tuple):
        raise ValueError(f"Catalog root must be an object: {path}")
    pairs: list[tuple[str, str]] = []
    for pair in document:
        if not (
            isinstance(pair, tuple)
            and len(pair) == 2
            and isinstance(pair[0], str)
            and isinstance(pair[1], str)
        ):
            raise ValueError(f"Catalog values must be strings: {path}")
        pairs.append(pair)
    return tuple(pairs)


__all__ = [
    "CORE_TRANSLATIONS",
    "CatalogValidation",
    "DISPLAY_SOURCE_TRANSLATIONS",
    "GlossaryEntry",
    "LANGUAGE_SETTINGS_KEY",
    "LocaleSettingsService",
    "SAFE_FALLBACK_TEXT",
    "STORAGE_TRANSLATIONS",
    "TranslationCatalog",
    "TranslationDiagnostic",
    "TranslationKey",
    "TranslationService",
    "TECHNICAL_GLOSSARY",
    "UiLanguage",
    "VIETNAMESE_SOURCE_TRANSLATIONS",
    "apply_application_font",
    "apply_widget_font_tree",
    "build_default_catalogs",
    "format_geometry_update_message",
    "language_display_name",
    "set_translation_service",
    "translation_service",
    "validate_glossary",
]
