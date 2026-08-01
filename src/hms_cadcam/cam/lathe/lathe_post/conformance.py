"""Qt-free Stage 12.4C sample contract and bounded NC conformance analyzer."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
from pathlib import Path
import re
from typing import Final


class LatheNcConformanceStatus(StrEnum):
    CONFORMANT = "CONFORMANT"
    CONFORMANT_WITH_INTENTIONAL_SAFE_DEVIATIONS = (
        "CONFORMANT_WITH_INTENTIONAL_SAFE_DEVIATIONS"
    )
    PARTIALLY_CONFORMANT = "PARTIALLY_CONFORMANT"
    NONCONFORMANT = "NONCONFORMANT"
    NO_SAMPLE_COVERAGE = "NO_SAMPLE_COVERAGE"
    INVALID_INPUT = "INVALID_INPUT"


class LatheNcConformanceCategory(StrEnum):
    PROGRAM_ENVELOPE = "PROGRAM_ENVELOPE"
    COMMENTS = "COMMENTS"
    UNITS = "UNITS"
    TOOL_CALL = "TOOL_CALL"
    SPINDLE = "SPINDLE"
    COOLANT = "COOLANT"
    WORK_OFFSET = "WORK_OFFSET"
    FEED_MODE = "FEED_MODE"
    MOTION = "MOTION"
    THREAD = "THREAD"
    ARC = "ARC"
    REFERENCE_RETURN = "REFERENCE_RETURN"
    OPTIONAL_STOP = "OPTIONAL_STOP"
    PROGRAM_END = "PROGRAM_END"
    NUMERIC_FORMAT = "NUMERIC_FORMAT"
    LINE_NUMBERING = "LINE_NUMBERING"
    OPTIONAL_MACHINE_EXTENSION = "OPTIONAL_MACHINE_EXTENSION"
    SAFETY_DEVIATION = "SAFETY_DEVIATION"
    UNSUPPORTED_SAMPLE_FEATURE = "UNSUPPORTED_SAMPLE_FEATURE"
    PRIVACY = "PRIVACY"


class LatheNcConformanceSeverity(StrEnum):
    INFO = "INFO"
    PASS = "PASS"
    NOTICE = "NOTICE"
    WARNING = "WARNING"
    ERROR = "ERROR"


class ExternalSampleDiscoveryStatus(StrEnum):
    AVAILABLE_VERIFIED = "AVAILABLE_VERIFIED"
    EXTERNAL_SAMPLE_NOT_AVAILABLE = "EXTERNAL_SAMPLE_NOT_AVAILABLE"
    SAMPLE_HASH_MISMATCH = "SAMPLE_HASH_MISMATCH"


@dataclass(frozen=True, slots=True)
class LatheSampleSignature:
    alias: str
    filename: str
    filename_sha256: str
    sha256: str
    byte_count: int
    line_count: int
    ordered_signature: tuple[str, ...]
    optional_tokens: tuple[str, ...] = ()
    contains_arc_ik: bool = False
    strategy_evidence: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class LatheSampleContractV1:
    schema_version: str
    signatures: tuple[LatheSampleSignature, ...]
    program_envelope: tuple[str, ...]
    operation_order: tuple[str, ...]
    optional_machine_tokens: tuple[str, ...]
    sample_backed_strategies: tuple[str, ...]
    no_owner_sample_coverage_strategies: tuple[str, ...]
    unsupported_sample_features: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.schema_version != "lathe.sample.contract.v1":
            raise ValueError("unsupported Lathe sample contract version")
        if tuple(item.alias for item in self.signatures) != (
            "SAMPLE_A",
            "SAMPLE_B",
            "SAMPLE_C",
        ):
            raise ValueError("the three owner sample aliases are locked")


@dataclass(frozen=True, slots=True)
class LatheNcLineClassification:
    line_number: int
    raw: str
    tokens: tuple[str, ...]
    categories: tuple[LatheNcConformanceCategory, ...]


@dataclass(frozen=True, slots=True)
class LatheNcConformanceFinding:
    code: str
    category: LatheNcConformanceCategory
    severity: LatheNcConformanceSeverity
    line_number: int | None = None
    detail: str = ""


@dataclass(frozen=True, slots=True)
class LatheNcConformanceReport:
    status: LatheNcConformanceStatus
    contract_version: str
    profile_id: str
    behavior_revision: int
    external_sample_state: str
    line_classifications: tuple[LatheNcLineClassification, ...]
    findings: tuple[LatheNcConformanceFinding, ...]
    strategy_coverage: tuple[tuple[str, str], ...]
    summary_counts: tuple[tuple[str, int], ...]

    @property
    def intentional_safe_deviations(self) -> tuple[LatheNcConformanceFinding, ...]:
        return tuple(
            item
            for item in self.findings
            if item.category is LatheNcConformanceCategory.SAFETY_DEVIATION
        )

    @property
    def mandatory_findings(self) -> tuple[LatheNcConformanceFinding, ...]:
        return tuple(
            item
            for item in self.findings
            if item.severity in {
                LatheNcConformanceSeverity.WARNING,
                LatheNcConformanceSeverity.ERROR,
            }
            and item.category is not LatheNcConformanceCategory.SAFETY_DEVIATION
        )

    @property
    def unsupported_sample_features(self) -> tuple[LatheNcConformanceFinding, ...]:
        return tuple(
            item
            for item in self.findings
            if item.category
            is LatheNcConformanceCategory.UNSUPPORTED_SAMPLE_FEATURE
        )


@dataclass(frozen=True, slots=True)
class ExternalSampleFileResult:
    alias: str
    filename: str
    state: str
    sha256: str | None = None
    byte_count: int | None = None
    line_count: int | None = None
    newline: str | None = None
    encoding: str | None = None


@dataclass(frozen=True, slots=True)
class ExternalSampleDiscoveryReport:
    status: ExternalSampleDiscoveryStatus
    directory: str | None
    files: tuple[ExternalSampleFileResult, ...]
    filesystem_scan_performed: bool = False


def _filename_hash(filename: str) -> str:
    return hashlib.sha256(filename.encode("utf-8")).hexdigest()


_SIGNATURES: Final = (
    LatheSampleSignature(
        "SAMPLE_A",
        "260516---CTS26079-M001-24--25X489_9-L2.NC",
        _filename_hash("260516---CTS26079-M001-24--25X489_9-L2.NC"),
        "942741ac0e02aacbd1f9a8a966ed2204b74b9e12b51ffd8d8785473ef10ccf32",
        905,
        60,
        ("PERCENT", "O_NUMBER", "COMMENTS", "G21", "OPERATIONS", "T0303", "M30", "PERCENT"),
        (),
        True,
        ("lathe.od_rough.v1", "lathe.od_finish.v1"),
    ),
    LatheSampleSignature(
        "SAMPLE_B",
        "260516---CTS26079-M001-40--20X8-L1.NC",
        _filename_hash("260516---CTS26079-M001-40--20X8-L1.NC"),
        "805d9d97c247bfb318a1a67c87ada1d3eca9d2d671c40fcb91d314ed9107a92e",
        2779,
        211,
        ("PERCENT", "O_NUMBER", "COMMENTS", "G21", "OPERATIONS", "T0303", "M30", "PERCENT"),
        ("M73", "M74", "G55", "M0"),
        True,
        (
            "lathe.face.v1",
            "lathe.axial_drill.v1",
            "lathe.id_rough.v1",
            "lathe.id_finish.v1",
            "lathe.od_groove.v1",
            "lathe.id_groove.v1",
            "lathe.part_off.v1",
        ),
    ),
    LatheSampleSignature(
        "SAMPLE_C",
        "260516---CTS26079-M001-24--25X489_9-L1.NC",
        _filename_hash("260516---CTS26079-M001-24--25X489_9-L1.NC"),
        "cd99df3a8a941e6417b7ef04e02af3e74f1229df3eb6a18bdc6d8811ecb01488",
        989,
        88,
        ("PERCENT", "O_NUMBER", "COMMENTS", "G21", "OPERATIONS", "T0303", "M30", "PERCENT"),
        ("G55",),
        True,
        ("lathe.od_rough.v1", "lathe.od_finish.v1"),
    ),
)


DEFAULT_LATHE_SAMPLE_CONTRACT_V1: Final = LatheSampleContractV1(
    schema_version="lathe.sample.contract.v1",
    signatures=_SIGNATURES,
    program_envelope=("%", "O0000", "TEN_FILE_COMMENT", "SHL_TECH", "G21", "OPERATIONS", "T0303", "M30", "%"),
    operation_order=(
        "OPERATION_COMMENT",
        "TOOL_DESCRIPTION_COMMENT",
        "G0_TNNNN",
        "M8",
        "G97_S_M03_OR_M04",
        "G0_G54_APPROACH",
        "G99_FIRST_CUT",
        "CUTTING_MOTIONS",
        "M9",
        "G28_U0_W0",
        "M01_BETWEEN_OPERATIONS",
    ),
    optional_machine_tokens=("M73", "M74", "G55", "G0 T0303", "M0"),
    sample_backed_strategies=(
        "lathe.face.v1",
        "lathe.od_rough.v1",
        "lathe.od_finish.v1",
        "lathe.id_rough.v1",
        "lathe.id_finish.v1",
        "lathe.od_groove.v1",
        "lathe.id_groove.v1",
        "lathe.part_off.v1",
        "lathe.axial_drill.v1",
    ),
    no_owner_sample_coverage_strategies=("lathe.od_thread.v1", "lathe.id_thread.v1"),
    unsupported_sample_features=(
        "SAMPLE_FEATURE_NOT_REPRESENTABLE_CURRENT_IR_ARC_IK",
        "BASIC_POST_DWELL_SYNTAX_UNDEFINED",
        "CONTRACT_DERIVED_NO_OWNER_SAMPLE_COVERAGE",
    ),
)


def lathe_sample_contract_v1() -> LatheSampleContractV1:
    """Return the immutable authoritative owner-sample-derived contract."""

    return DEFAULT_LATHE_SAMPLE_CONTRACT_V1


def discover_external_samples(
    directory: Path | None,
    contract: LatheSampleContractV1 = DEFAULT_LATHE_SAMPLE_CONTRACT_V1,
) -> ExternalSampleDiscoveryReport:
    """Read only the three exact files in an explicitly supplied directory."""

    if directory is None:
        return ExternalSampleDiscoveryReport(
            ExternalSampleDiscoveryStatus.EXTERNAL_SAMPLE_NOT_AVAILABLE,
            None,
            (),
        )
    root = Path(directory).expanduser().resolve()
    if not root.is_dir():
        return ExternalSampleDiscoveryReport(
            ExternalSampleDiscoveryStatus.EXTERNAL_SAMPLE_NOT_AVAILABLE,
            str(root),
            (),
        )
    results: list[ExternalSampleFileResult] = []
    mismatch = False
    for signature in contract.signatures:
        path = root / signature.filename
        if not path.is_file():
            mismatch = True
            results.append(
                ExternalSampleFileResult(signature.alias, signature.filename, "MISSING")
            )
            continue
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        try:
            data.decode("ascii")
            encoding = "ASCII"
        except UnicodeDecodeError:
            try:
                data.decode("utf-8-sig")
                encoding = "UTF-8-SIG" if data.startswith(b"\xef\xbb\xbf") else "UTF-8"
            except UnicodeDecodeError:
                encoding = "UNKNOWN"
        crlf = data.count(b"\r\n")
        bare_lf = data.count(b"\n") - crlf
        bare_cr = data.count(b"\r") - crlf
        newline = "CRLF" if crlf and not bare_lf and not bare_cr else "LF" if bare_lf and not crlf and not bare_cr else "MIXED" if crlf or bare_lf or bare_cr else "NONE"
        valid = (
            digest == signature.sha256
            and len(data) == signature.byte_count
            and len(data.splitlines()) == signature.line_count
        )
        mismatch = mismatch or not valid
        results.append(
            ExternalSampleFileResult(
                signature.alias,
                signature.filename,
                "VERIFIED" if valid else "HASH_OR_METADATA_MISMATCH",
                digest,
                len(data),
                len(data.splitlines()),
                newline,
                encoding,
            )
        )
    return ExternalSampleDiscoveryReport(
        ExternalSampleDiscoveryStatus.SAMPLE_HASH_MISMATCH
        if mismatch
        else ExternalSampleDiscoveryStatus.AVAILABLE_VERIFIED,
        str(root),
        tuple(results),
    )


_TOKEN = re.compile(r"(?<![A-Z0-9_])[A-Z][+-]?(?:\d+(?:\.\d*)?|\.\d+)")
_NUMERIC = re.compile(r"\b([XZFUWIK])([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?)")
_TOOL_CALL = re.compile(r"^G0 T\d{4}$")
_CUT = re.compile(r"^(?:G99 )?(?:G1|G32)\b")
_SPINDLE = re.compile(r"^G97 S\d+ M0[34]$")
_O_NUMBER = re.compile(r"^O\d{4}$")
_LINE_NUMBER = re.compile(r"^N\d+(?:\s|$)")


def _categories(line: str, tokens: tuple[str, ...]) -> tuple[LatheNcConformanceCategory, ...]:
    found: list[LatheNcConformanceCategory] = []
    def add(category: LatheNcConformanceCategory) -> None:
        if category not in found:
            found.append(category)
    if line == "%" or _O_NUMBER.fullmatch(line):
        add(LatheNcConformanceCategory.PROGRAM_ENVELOPE)
    if line.startswith("("):
        add(LatheNcConformanceCategory.COMMENTS)
    if "G21" in tokens:
        add(LatheNcConformanceCategory.UNITS)
    if any(token.startswith("T") for token in tokens):
        add(LatheNcConformanceCategory.TOOL_CALL)
    if any(token in {"G97", "M03", "M04", "M05"} or token.startswith("S") for token in tokens):
        add(LatheNcConformanceCategory.SPINDLE)
    if any(token in {"M8", "M9"} for token in tokens):
        add(LatheNcConformanceCategory.COOLANT)
    if any(token in {"G54", "G55"} for token in tokens):
        add(LatheNcConformanceCategory.WORK_OFFSET)
    if "G99" in tokens:
        add(LatheNcConformanceCategory.FEED_MODE)
    if any(token in {"G0", "G1", "G2", "G3", "G32"} for token in tokens):
        add(LatheNcConformanceCategory.MOTION)
    if "G32" in tokens:
        add(LatheNcConformanceCategory.THREAD)
    if any(token in {"G2", "G3"} or token.startswith(("I", "K")) for token in tokens):
        add(LatheNcConformanceCategory.ARC)
    if "G28" in tokens:
        add(LatheNcConformanceCategory.REFERENCE_RETURN)
    if "M01" in tokens:
        add(LatheNcConformanceCategory.OPTIONAL_STOP)
    if "M30" in tokens:
        add(LatheNcConformanceCategory.PROGRAM_END)
    if any(token.startswith(("X", "Z", "F", "U", "W", "I", "K")) for token in tokens):
        add(LatheNcConformanceCategory.NUMERIC_FORMAT)
    if _LINE_NUMBER.match(line):
        add(LatheNcConformanceCategory.LINE_NUMBERING)
    if any(token in {"M73", "M74", "G55", "M0"} for token in tokens):
        add(LatheNcConformanceCategory.OPTIONAL_MACHINE_EXTENSION)
    return tuple(found)


class LatheNcConformanceAnalyzerV1:
    """Classify only the bounded Stage 12.4C contract; never execute NC text."""

    def __init__(self, contract: LatheSampleContractV1 | None = None) -> None:
        self.contract = contract or DEFAULT_LATHE_SAMPLE_CONTRACT_V1

    def analyze(
        self,
        text: str,
        *,
        strategy_ids: tuple[str, ...] = (),
        profile_id: str = "hms.lathe.fanuc_basic_sample_v1",
        behavior_revision: int = 1,
        external_sample_state: str = "EXTERNAL_SAMPLE_NOT_AVAILABLE",
    ) -> LatheNcConformanceReport:
        if not isinstance(text, str) or not text:
            return self._invalid("INVALID_OR_EMPTY_TEXT", profile_id, behavior_revision, external_sample_state)
        if any(ord(char) < 32 and char not in "\r\n" for char in text) or any(ord(char) == 127 for char in text):
            return self._invalid("UNSUPPORTED_CONTROL_CHARACTER", profile_id, behavior_revision, external_sample_state)
        lines = tuple(text.splitlines())
        classifications = tuple(self._classify(index + 1, line) for index, line in enumerate(lines))
        findings: list[LatheNcConformanceFinding] = []
        add = findings.append
        self._check_envelope(lines, add)
        self._check_comments_and_injection(lines, add)
        self._check_required_tokens(lines, add)
        self._check_numeric(lines, add)
        self._check_operations(lines, add)
        self._check_deviations_and_coverage(lines, strategy_ids, add)
        coverage = tuple(
            (
                strategy,
                "CONTRACT_DERIVED_NO_OWNER_SAMPLE_COVERAGE"
                if strategy in self.contract.no_owner_sample_coverage_strategies
                else "SAMPLE_BACKED",
            )
            for strategy in dict.fromkeys(strategy_ids)
        )
        errors = tuple(item for item in findings if item.severity is LatheNcConformanceSeverity.ERROR)
        warnings = tuple(
            item
            for item in findings
            if item.severity is LatheNcConformanceSeverity.WARNING
            and item.category is not LatheNcConformanceCategory.SAFETY_DEVIATION
        )
        only_no_sample = bool(coverage) and all(item[1] == "CONTRACT_DERIVED_NO_OWNER_SAMPLE_COVERAGE" for item in coverage)
        if errors:
            status = LatheNcConformanceStatus.NONCONFORMANT
        elif warnings:
            status = LatheNcConformanceStatus.PARTIALLY_CONFORMANT
        elif only_no_sample:
            status = LatheNcConformanceStatus.NO_SAMPLE_COVERAGE
        elif any(item.category is LatheNcConformanceCategory.SAFETY_DEVIATION for item in findings):
            status = LatheNcConformanceStatus.CONFORMANT_WITH_INTENTIONAL_SAFE_DEVIATIONS
        else:
            status = LatheNcConformanceStatus.CONFORMANT
        counts = tuple(
            (severity.value, sum(item.severity is severity for item in findings))
            for severity in LatheNcConformanceSeverity
        )
        return LatheNcConformanceReport(
            status,
            self.contract.schema_version,
            profile_id,
            behavior_revision,
            external_sample_state,
            classifications,
            tuple(findings),
            coverage,
            counts,
        )

    def _invalid(self, code: str, profile_id: str, behavior_revision: int, external_state: str) -> LatheNcConformanceReport:
        finding = LatheNcConformanceFinding(
            code,
            LatheNcConformanceCategory.PRIVACY,
            LatheNcConformanceSeverity.ERROR,
        )
        return LatheNcConformanceReport(
            LatheNcConformanceStatus.INVALID_INPUT,
            self.contract.schema_version,
            profile_id,
            behavior_revision,
            external_state,
            (),
            (finding,),
            (),
            ((LatheNcConformanceSeverity.ERROR.value, 1),),
        )

    @staticmethod
    def _classify(line_number: int, line: str) -> LatheNcLineClassification:
        code = "" if line.startswith("(") and line.endswith(")") else line
        tokens = tuple(match.group(0) for match in _TOKEN.finditer(code))
        return LatheNcLineClassification(line_number, line, tokens, _categories(line, tokens))

    @staticmethod
    def _finding(code: str, category: LatheNcConformanceCategory, passed: bool, detail: str = "") -> LatheNcConformanceFinding:
        return LatheNcConformanceFinding(
            code,
            category,
            LatheNcConformanceSeverity.PASS if passed else LatheNcConformanceSeverity.ERROR,
            detail=detail,
        )

    def _check_envelope(self, lines: tuple[str, ...], add) -> None:  # type: ignore[no-untyped-def]
        add(self._finding("PROGRAM_PERCENT_ENVELOPE", LatheNcConformanceCategory.PROGRAM_ENVELOPE, len(lines) >= 2 and lines[0] == "%" and lines[-1] == "%"))
        add(self._finding("PROGRAM_O_NUMBER_EXACTLY_ONE", LatheNcConformanceCategory.PROGRAM_ENVELOPE, sum(bool(_O_NUMBER.fullmatch(line)) for line in lines) == 1))
        add(self._finding("PROGRAM_G21_PRESENT", LatheNcConformanceCategory.UNITS, "G21" in lines))
        add(self._finding("PROGRAM_M30_EXACTLY_ONE", LatheNcConformanceCategory.PROGRAM_END, lines.count("M30") == 1))
        add(self._finding("PROGRAM_FINAL_T0303_M30_PERCENT", LatheNcConformanceCategory.PROGRAM_END, len(lines) >= 3 and lines[-3:] == ("T0303", "M30", "%")))
        add(self._finding("LINE_NUMBERS_DEFAULT_OFF", LatheNcConformanceCategory.LINE_NUMBERING, not any(_LINE_NUMBER.match(line) for line in lines)))

    def _check_comments_and_injection(self, lines: tuple[str, ...], add) -> None:  # type: ignore[no-untyped-def]
        malformed = [line for line in lines if ("(" in line or ")" in line) and not (line.startswith("(") and line.endswith(")") and line.count("(") == 1 and line.count(")") == 1)]
        add(self._finding("COMMENTS_PARENTHESES_BALANCED", LatheNcConformanceCategory.COMMENTS, not malformed))
        code_lines = ["" if line.startswith("(") and line.endswith(")") else line for line in lines]
        injected = any(any(char in line for char in "#;=[]") for line in code_lines)
        add(self._finding("RAW_CODE_INJECTION_REJECTED", LatheNcConformanceCategory.PRIVACY, not injected))
        if any("G76" in line.split() for line in code_lines):
            add(self._finding("G76_FORBIDDEN", LatheNcConformanceCategory.THREAD, False))

    def _check_required_tokens(self, lines: tuple[str, ...], add) -> None:  # type: ignore[no-untyped-def]
        add(self._finding("TOOL_CALL_G0_TNNNN_PRESENT", LatheNcConformanceCategory.TOOL_CALL, any(_TOOL_CALL.fullmatch(line) for line in lines)))
        add(self._finding("REFERENCE_RETURN_PRESENT", LatheNcConformanceCategory.REFERENCE_RETURN, "G28 U0 W0" in lines))
        for token in ("M73", "M74", "G55", "M0"):
            if any(token in line.split() for line in lines):
                add(LatheNcConformanceFinding(f"SAMPLE_OPTIONAL_MACHINE_SPECIFIC_{token}", LatheNcConformanceCategory.OPTIONAL_MACHINE_EXTENSION, LatheNcConformanceSeverity.NOTICE))
        if any(re.match(r"^(?:G99 )?G(?:2|3)\b", line) for line in lines):
            add(LatheNcConformanceFinding("ARC_IK_OBSERVED_IN_ANALYZED_TEXT", LatheNcConformanceCategory.ARC, LatheNcConformanceSeverity.NOTICE))

    def _check_numeric(self, lines: tuple[str, ...], add) -> None:  # type: ignore[no-untyped-def]
        invalid = False
        for line in lines:
            if line.startswith("("):
                continue
            if re.search(r"(?:\d|\.)(?:[Ee][+-]?\d+)", line) or re.search(r"\b[XZFUWIK]-0(?:\.0*)?(?=\s|$)", line):
                invalid = True
            for word, value in _NUMERIC.findall(line):
                fraction = value.lower().split("e", 1)[0].partition(".")[2]
                limit = 4 if word == "F" else 3
                if len(fraction) > limit:
                    invalid = True
        add(self._finding("NUMERIC_DETERMINISTIC_DECIMAL", LatheNcConformanceCategory.NUMERIC_FORMAT, not invalid))

    def _check_operations(self, lines: tuple[str, ...], add) -> None:  # type: ignore[no-untyped-def]
        tool_indices = [index for index, line in enumerate(lines) if _TOOL_CALL.fullmatch(line)]
        structural_ok = bool(tool_indices)
        g99_ok = bool(tool_indices)
        for position, start in enumerate(tool_indices):
            end = tool_indices[position + 1] if position + 1 < len(tool_indices) else len(lines) - 2
            segment = lines[start:end]
            def find(predicate) -> int | None:  # type: ignore[no-untyped-def]
                return next((i for i, value in enumerate(segment) if predicate(value)), None)
            coolant = find(lambda value: value == "M8")
            spindle = find(lambda value: bool(_SPINDLE.fullmatch(value)))
            approach = find(lambda value: value.startswith("G0 G54 ") or value.startswith("G0 G55 "))
            cut = find(lambda value: bool(_CUT.match(value)))
            coolant_off = find(lambda value: value == "M9")
            reference = find(lambda value: value == "G28 U0 W0")
            structural_ok = structural_ok and None not in {coolant, spindle, approach, cut, coolant_off, reference}
            if None not in {coolant, spindle, approach, cut, coolant_off, reference}:
                structural_ok = structural_ok and bool(coolant < spindle < approach < cut < coolant_off < reference)
            if cut is None:
                g99_ok = False
            else:
                g99_ok = g99_ok and (segment[cut].startswith("G99 ") or (cut > 0 and segment[cut - 1] == "G99"))
            if position < len(tool_indices) - 1:
                structural_ok = structural_ok and "M01" in segment
        add(self._finding("OPERATION_SAMPLE_ORDER", LatheNcConformanceCategory.MOTION, structural_ok))
        if g99_ok:
            add(self._finding("G99_FIRST_CUT_ACTIVATION", LatheNcConformanceCategory.FEED_MODE, True))
        else:
            add(LatheNcConformanceFinding("G99_NOT_AT_FIRST_CUT_ACTIVATION", LatheNcConformanceCategory.FEED_MODE, LatheNcConformanceSeverity.WARNING))

    def _check_deviations_and_coverage(self, lines: tuple[str, ...], strategy_ids: tuple[str, ...], add) -> None:  # type: ignore[no-untyped-def]
        if "(UNVERIFIED OUTPUT - CHECK BEFORE MACHINE USE)" in lines:
            add(LatheNcConformanceFinding("INTENTIONAL_SAFE_DEVIATION_WARNING_HEADER", LatheNcConformanceCategory.SAFETY_DEVIATION, LatheNcConformanceSeverity.NOTICE))
        if "M05" in lines:
            add(LatheNcConformanceFinding("INTENTIONAL_SAFE_DEVIATION_SPINDLE_STOP", LatheNcConformanceCategory.SAFETY_DEVIATION, LatheNcConformanceSeverity.NOTICE))
        add(LatheNcConformanceFinding("SAMPLE_FEATURE_NOT_REPRESENTABLE_CURRENT_IR_ARC_IK", LatheNcConformanceCategory.UNSUPPORTED_SAMPLE_FEATURE, LatheNcConformanceSeverity.NOTICE))
        add(LatheNcConformanceFinding("BASIC_POST_DWELL_SYNTAX_UNDEFINED", LatheNcConformanceCategory.UNSUPPORTED_SAMPLE_FEATURE, LatheNcConformanceSeverity.NOTICE))
        if any(item in self.contract.no_owner_sample_coverage_strategies for item in strategy_ids):
            add(LatheNcConformanceFinding("CONTRACT_DERIVED_NO_OWNER_SAMPLE_COVERAGE", LatheNcConformanceCategory.UNSUPPORTED_SAMPLE_FEATURE, LatheNcConformanceSeverity.NOTICE))


__all__ = [
    "DEFAULT_LATHE_SAMPLE_CONTRACT_V1",
    "ExternalSampleDiscoveryReport",
    "ExternalSampleDiscoveryStatus",
    "ExternalSampleFileResult",
    "LatheNcConformanceAnalyzerV1",
    "LatheNcConformanceCategory",
    "LatheNcConformanceFinding",
    "LatheNcConformanceReport",
    "LatheNcConformanceSeverity",
    "LatheNcConformanceStatus",
    "LatheNcLineClassification",
    "LatheSampleContractV1",
    "LatheSampleSignature",
    "discover_external_samples",
    "lathe_sample_contract_v1",
]
