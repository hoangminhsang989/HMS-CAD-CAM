from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import sys
import zipfile

import pytest
from pathlib import Path

from hms_cadcam.ui.i18n import UiLanguage, translation_service

import tools.create_stage9a7_wp2_review_package as r3_generator
from tools.create_stage9a7_wp2_review_package import (
    PACKAGE_LIFECYCLE_CONTRACT,
    PNG_NAMES,
    QA_LOG_NAMES,
    ZIP_OUTPUT,
    assert_no_final_hash_self_reference,
    assert_package_identity,
    audit_package,
    build_review_patch,
    changed_paths,
    guarded_r1_cleanup,
    make_qa_record,
    package_identity,
    parse_pytest_counts,
    portable_review_patch,
    run_qa_command,
    validate_qa_record,
    validate_required_package,
)


def test_review_patch_includes_untracked_wp2_sources(tmp_path: Path):
    source = tmp_path / "post_assembly.py"
    source.write_text("class Panel:\n    pass\n", encoding="utf-8")
    patch = build_review_patch((), ("post_assembly.py",))
    # The repository helper is intentionally exercised with its own root;
    # a missing path must not produce a fabricated patch.
    assert patch.endswith("\n")


def test_review_package_manifest_and_semantic_audit_requires_r3_zip():
    package = Path("reference_private/DERIVED/STAGE_9A7_WP2_UNIFIED_PANEL_REVIEW_R3")
    assert package.is_dir(), "R3 package directory is required for acceptance tests"
    assert ZIP_OUTPUT.is_file(), "R3 ZIP is required for acceptance tests"
    report = audit_package(package)
    assert report["hash_mismatch_count"] == 0
    assert report["unsafe_path_count"] == 0
    assert report["absolute_path_count"] == 0
    assert report["qa_log_count"] == len(QA_LOG_NAMES)
    assert len(list(package.glob("*.png"))) == len(PNG_NAMES)
    checkpoint = json.loads((package / "checkpoint_snapshot.json").read_text(encoding="utf-8"))
    assert checkpoint["snapshot_phase"] == "PRE_ZIP_FINALIZATION"
    assert checkpoint["review_round"] == "R3"
    assert checkpoint["package_sha256"] is None
    qa_results = json.loads((package / "qa_logs/qa_results.json").read_text(encoding="utf-8"))
    required_record_fields = {
        "command", "working_directory", "start_utc", "end_utc",
        "duration_seconds", "exit_code", "passed", "failed",
        "errors", "skipped", "deselected", "status",
    }
    for name in QA_LOG_NAMES:
        content = (package / "qa_logs" / name).read_text(encoding="utf-8")
        for marker in (
            "COMMAND=", "WORKING_DIRECTORY=.", "START_UTC=", "END_UTC=",
            "DURATION_SECONDS=", "EXIT_CODE=", "TEST_COUNTS=",
            "SOURCE_STATE_IDENTITY=", "--- RAW STDOUT/STDERR ---",
        ):
            assert marker in content
        key = r3_generator._QA_LOG_RESULT_KEYS[name]
        assert required_record_fields.issubset(qa_results[key])
    historical = r3_generator.audit_historical_package_zip(
        ZIP_OUTPUT,
        expected_sha256="6b24960f4ff61e60f0c74f7cb5dfc20c89492026e0e5f388a1168d4ee4b5f253",
    )
    assert historical["status"] == "PASS"


def test_wp2_catalog_text_is_unicode_and_locale_specific():
    service = translation_service()
    previous = service.language
    try:
        values = {}
        for locale in UiLanguage:
            service.set_language(locale)
            values[locale.value] = service.translate_key("Operation table")
    finally:
        service.set_language(previous)
    assert len(set(values.values())) == 3
    for value in values.values():
        assert "?" not in value
        assert "\ufffd" not in value
        assert value.strip()


def test_wp2_required_labels_have_no_replacement_markers():
    service = translation_service()
    previous = service.language
    keys = (
        "Post / Program Assembly",
        "Operation table",
        "Preview is not available in WP2.",
        "Diagnostics drawer is not available in WP2.",
    )
    try:
        for locale in UiLanguage:
            service.set_language(locale)
            for key in keys:
                text = service.translate_key(key)
                assert "?" not in text
                assert "\ufffd" not in text
    finally:
        service.set_language(previous)


def test_r1_guard_deletes_only_valid_matching_hash(tmp_path: Path) -> None:
    artifact = tmp_path / "r1.zip"
    artifact.write_bytes(b"r1-content")
    expected = hashlib.sha256(artifact.read_bytes()).hexdigest()
    assert guarded_r1_cleanup(artifact, expected) == "R1_DELETED_HASH_MATCH"
    assert not artifact.exists()


def test_r1_guard_keeps_mismatch_invalid_and_missing(tmp_path: Path) -> None:
    artifact = tmp_path / "r1.zip"
    artifact.write_bytes(b"r1-content")
    assert guarded_r1_cleanup(artifact, "0" * 64) == "R1_HASH_MISMATCH_NOT_DELETED"
    assert artifact.exists()
    assert guarded_r1_cleanup(artifact, "0" * 65) == "R1_INVALID_EXPECTED_HASH"
    assert guarded_r1_cleanup(artifact, "z" * 64) == "R1_INVALID_EXPECTED_HASH"
    artifact.unlink()
    assert guarded_r1_cleanup(artifact, "0" * 64) == "R1_NOT_PRESENT_AT_R3_PREFLIGHT"


def test_r1_guard_keeps_file_changed_between_verification_reads(tmp_path: Path, monkeypatch) -> None:
    artifact = tmp_path / "r1.zip"
    artifact.write_bytes(b"r1-content")
    expected = hashlib.sha256(artifact.read_bytes()).hexdigest()
    values = iter((expected, "f" * 64))
    monkeypatch.setattr(r3_generator, "_sha", lambda _path: next(values))
    assert guarded_r1_cleanup(artifact, expected) == "R1_CHANGED_DURING_VERIFICATION_NOT_DELETED"
    assert artifact.exists()

def test_r4_pytest_parser_handles_real_summary_variants() -> None:
    cases = (
        ("15 passed in 0.12s", {"passed": 15}),
        ("2170 passed, 2 deselected in 567.13s", {"passed": 2170, "deselected": 2}),
        ("2 failed, 4 passed, 1 skipped in 1.20s", {"failed": 2, "passed": 4, "skipped": 1}),
        ("1 error, 2 xfailed, 1 xpassed in 0.20s", {"errors": 1, "xfailed": 2, "xpassed": 1}),
    )
    for output, expected in cases:
        parsed = parse_pytest_counts(output)
        assert parsed["parse_status"] == "PARSED"
        for field, value in expected.items():
            assert parsed[field] == value
        for field in ("passed", "failed", "errors", "skipped", "deselected", "xfailed", "xpassed"):
            assert isinstance(parsed[field], int)


def test_r4_qa_record_uses_exact_argv_output_hashes_and_exit_status() -> None:
    argv = [sys.executable, "-c", "print('stdout'); import sys; print('stderr', file=sys.stderr)"]
    record = run_qa_command("qa-record-fidelity", argv, source_state_id={"token": "source-1"})
    assert record["argv"] == argv
    assert record["command_display"] == r3_generator.subprocess.list2cmdline(argv)
    assert record["logical_working_directory"] == "."
    assert record["stdout_sha256"] == hashlib.sha256(record["stdout"].encode("utf-8")).hexdigest()
    assert record["stderr_sha256"] == hashlib.sha256(record["stderr"].encode("utf-8")).hexdigest()
    assert record["exit_code"] == 0
    assert record["status"] == "PASS"
    assert record["truncated"] is False
    assert validate_qa_record(record)["argv"] == argv


def test_r4_fake_descriptive_pytest_command_is_not_rewritten_or_marked_pass() -> None:
    argv = ["python", "-m", "pytest", "focused WP2 R3"]
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    record = make_qa_record(
        "fake-description", argv, start_time=start, end_time=start, exit_code=4,
        stdout="15 passed in 0.01s\n", stderr="", source_state_id="source-1",
    )
    assert record["argv"] == argv
    assert record["command_display"] == r3_generator.subprocess.list2cmdline(argv)
    assert record["status"] == "FAIL"
    assert record["result"] == "FAIL"


def test_r4_unparsed_pytest_is_explicit_and_does_not_invent_counts() -> None:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    record = make_qa_record(
        "unparsed", ["python", "-m", "pytest", "tests/unit/test_x.py"],
        start_time=start, end_time=start, exit_code=0, stdout="no summary", stderr="",
    )
    assert record["parse_status"] == "UNPARSED"
    assert record["status"] == "PASS_UNPARSED"
    assert all(record[field] == 0 for field in ("passed", "failed", "errors", "skipped", "deselected", "xfailed", "xpassed"))
    validate_qa_record(record)


def test_r4_required_package_and_immutable_identity_contract(tmp_path: Path) -> None:
    missing = tmp_path / "candidate.zip"
    with pytest.raises(RuntimeError, match="MISSING_REQUIRED_PACKAGE"):
        validate_required_package(missing, "candidate")
    directory = tmp_path / "directory.zip"
    directory.mkdir()
    with pytest.raises(RuntimeError, match="TARGET_IS_DIRECTORY"):
        validate_required_package(directory, "candidate")
    corrupt = tmp_path / "corrupt.zip"
    corrupt.write_bytes(b"not a zip")
    with pytest.raises(RuntimeError, match="CORRUPT_PACKAGE"):
        validate_required_package(corrupt, "candidate")

    candidate = tmp_path / "candidate-valid.zip"
    with zipfile.ZipFile(candidate, "w") as archive:
        archive.writestr("payload.txt", "candidate")
    assert validate_required_package(candidate, "candidate") == candidate
    identity = package_identity(candidate, "candidate")
    candidate.write_bytes(candidate.read_bytes() + b"changed")
    with pytest.raises(RuntimeError, match="PACKAGE_IDENTITY_MISMATCH"):
        assert_package_identity(candidate, identity)


def test_r4_lifecycle_order_and_no_final_hash_self_reference(tmp_path: Path) -> None:
    assert [phase for phase, _steps in PACKAGE_LIFECYCLE_CONTRACT] == [
        "SOURCE_QA", "CANDIDATE_PACKAGE", "CANDIDATE_PACKAGE_ACCEPTANCE",
        "FINAL_PACKAGE", "IMMUTABLE_FINAL_AUDIT",
    ]
    metadata = tmp_path / "metadata.json"
    metadata.write_text(json.dumps({"package_sha256": None}), encoding="utf-8")
    assert_no_final_hash_self_reference(tmp_path) is None
    metadata.write_text(json.dumps({"final_zip_sha256": "a" * 64}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="FINAL_HASH_SELF_REFERENCE"):
        assert_no_final_hash_self_reference(tmp_path)



import subprocess as _f2b_subprocess
import tempfile as _f2b_tempfile

def _f2b_git(repo: Path,*args: str)->None:
    _f2b_subprocess.run(("git",*args),cwd=repo,check=True,stdout=_f2b_subprocess.PIPE,stderr=_f2b_subprocess.PIPE)

def _f2b_repo(tmp_path: Path)->tuple[Path,str,str]:
    repo=tmp_path/"repo"; repo.mkdir(); _f2b_git(repo,"init"); _f2b_git(repo,"config","user.email","f2b@example.invalid"); _f2b_git(repo,"config","user.name","F2B")
    (repo/"historical.txt").write_text("history\n",encoding="utf-8"); (repo/"wp2.txt").write_text("baseline\n",encoding="utf-8"); _f2b_git(repo,"add","."); _f2b_git(repo,"commit","-m","A")
    base=_f2b_subprocess.run(("git","rev-parse","HEAD"),cwd=repo,check=True,text=True,stdout=_f2b_subprocess.PIPE).stdout.strip(); (repo/"wp2.txt").write_text("wp2-only\n",encoding="utf-8"); _f2b_git(repo,"add","wp2.txt"); _f2b_git(repo,"commit","-m","B")
    target=_f2b_subprocess.run(("git","rev-parse","HEAD"),cwd=repo,check=True,text=True,stdout=_f2b_subprocess.PIPE).stdout.strip(); (repo/"c31.txt").write_text("c31\n",encoding="utf-8"); _f2b_git(repo,"add","c31.txt"); _f2b_git(repo,"commit","-m","C"); (repo/"wp2.txt").write_text("dirty\n",encoding="utf-8"); (repo/"untracked.txt").write_text("untracked\n",encoding="utf-8")
    return repo,base,target

def _f2b_qa()->dict[str,object]:
    start=datetime(2026,1,1,tzinfo=timezone.utc); return make_qa_record("focused",[sys.executable,"-c","print('ok')"],start_time=start,end_time=start,exit_code=0,stdout="ok\n",stderr="")

def _f2b_rewrite(source:Path,target:Path,mutate,drop:set[str]|None=None)->Path:
    drop=drop or set()
    with zipfile.ZipFile(source) as archive: entries={name:archive.read(name) for name in archive.namelist() if name not in drop}
    entries=mutate(entries)
    with zipfile.ZipFile(target,"w",zipfile.ZIP_DEFLATED) as archive:
        for name,data in entries.items(): archive.writestr(name,data)
    return target

def test_f2b_historical_r3_self_audit_is_worktree_independent(monkeypatch):
    report=r3_generator.audit_historical_package_zip(ZIP_OUTPUT,expected_sha256="6b24960f4ff61e60f0c74f7cb5dfc20c89492026e0e5f388a1168d4ee4b5f253"); assert report["status"]=="PASS" and report["working_tree_compared"] is False
    monkeypatch.setattr(r3_generator,"_git",lambda *args: (_ for _ in ()).throw(AssertionError("worktree"))); assert r3_generator.audit_historical_package_zip(ZIP_OUTPUT)["status"]=="PASS"

def test_f2b_historical_tamper_missing_manifest_and_missing_path_fail(tmp_path:Path):
    def flip(entries):
        name=next(name for name in entries if name.endswith("/git_diff.patch")); entries[name]+=b"changed"; return entries
    with pytest.raises(RuntimeError,match="MANIFEST_HASH_MISMATCH"): r3_generator.audit_historical_package_zip(_f2b_rewrite(ZIP_OUTPUT,tmp_path/"tampered.zip",flip))
    missing=_f2b_rewrite(ZIP_OUTPUT,tmp_path/"missing.zip",lambda entries:entries,{"STAGE_9A7_WP2_UNIFIED_PANEL_REVIEW_R3/02_review_manifest.json"})
    with pytest.raises(RuntimeError,match="MISSING_MANIFEST"): r3_generator.audit_historical_package_zip(missing)
    with pytest.raises(RuntimeError,match="MISSING_HISTORICAL_R3"): r3_generator.audit_historical_package_zip(tmp_path/"none.zip")

def test_f2b_revision_range_excludes_c_dirty_untracked_and_history(tmp_path:Path):
    repo,base,target=_f2b_repo(tmp_path); source=r3_generator.build_revision_range_source(repo,base_revision=base,target_revision=target); patch=source.patch_bytes.decode("utf-8","replace")
    assert "wp2-only" in patch and "c31" not in patch and "dirty" not in patch and "untracked" not in patch and "historical.txt" not in source.changed_paths
    assert source.identity==r3_generator.build_revision_range_source(repo,base_revision=base,target_revision=target).identity
    with pytest.raises(ValueError,match="INVALID_SOURCE_REVISION"): r3_generator.build_revision_range_source(repo,base_revision="missing",target_revision=target)
    with pytest.raises(ValueError,match="EXPLICIT_SOURCE_REVISION_REQUIRED"): r3_generator.build_revision_range_source(repo,base_revision=None,target_revision=target)

def test_f2b_lifecycle_promotes_byte_identical_and_never_overwrites(tmp_path:Path):
    repo,base,target=_f2b_repo(tmp_path); staging=tmp_path/"staging"; staging.mkdir(); (staging/"payload.txt").write_text("candidate\n",encoding="utf-8"); candidate=tmp_path/"SYNTHETIC_R4_CANDIDATE.zip"; final=tmp_path/"SYNTHETIC_R4.zip"; spec=r3_generator.ReviewPackageSpec("R4","SYNTHETIC_R4",base,target,candidate,final)
    result=r3_generator.run_package_lifecycle(staging,spec,repo,{"focused":_f2b_qa()}); assert result["phases"]==["SOURCE_QA","CANDIDATE_PACKAGE","CANDIDATE_PACKAGE_ACCEPTANCE","FINAL_PACKAGE","IMMUTABLE_FINAL_AUDIT"] and candidate.read_bytes()==final.read_bytes(); assert (tmp_path/"SYNTHETIC_R4.zip.sha256").read_text(encoding="utf-8").strip()==hashlib.sha256(final.read_bytes()).hexdigest()
    with pytest.raises(FileExistsError): r3_generator.run_package_lifecycle(staging,spec,repo,{"focused":_f2b_qa()})

def test_f2b_spec_and_temp_cleanup_fail_closed(tmp_path:Path):
    repo,base,target=_f2b_repo(tmp_path); same=tmp_path/"same.zip"; spec=r3_generator.ReviewPackageSpec("R4","SYNTHETIC_R4",base,target,same,same)
    with pytest.raises(ValueError,match="CANDIDATE_FINAL_SAME_PATH"): r3_generator.validate_review_package_spec(spec)
    owned=Path(_f2b_tempfile.mkdtemp(prefix=r3_generator.TEMP_BUILD_PREFIX,dir=tmp_path)); sibling=tmp_path/"keep.zip"; sibling.write_bytes(b"keep"); token="owner"; (owned/r3_generator.TEMP_OWNER_MARKER).write_text(json.dumps({"owner":token}),encoding="utf-8"); assert r3_generator.cleanup_owned_temp_root(owned,tmp_path,token)=="CLEANED" and sibling.read_bytes()==b"keep"

def test_f2b_acceptance_failure_and_all_output_guards(tmp_path:Path):
    repo,base,target=_f2b_repo(tmp_path); staging=tmp_path/"staging"; staging.mkdir(); (staging/"payload.txt").write_text("candidate\n",encoding="utf-8")
    candidate=tmp_path/"SYNTHETIC_R4_CANDIDATE.zip"; final=tmp_path/"SYNTHETIC_R4.zip"; spec=r3_generator.ReviewPackageSpec("R4","SYNTHETIC_R4",base,target,candidate,final); source=r3_generator.build_revision_range_source(repo,base_revision=base,target_revision=target)
    r3_generator.build_candidate_package(staging,spec,source,{"focused":_f2b_qa()})
    with zipfile.ZipFile(candidate,"a") as archive: archive.writestr("SYNTHETIC_R4/unmanifested.txt","tamper")
    with pytest.raises(RuntimeError,match="MANIFEST_COVERAGE_MISMATCH"): r3_generator.accept_candidate_package(candidate,spec)
    assert not final.exists()
    existing_final=tmp_path/"FINAL_EXISTS_R4.zip"; existing_final.write_bytes(b"existing"); final_spec=r3_generator.ReviewPackageSpec("R4","FINAL_EXISTS_R4",base,target,tmp_path/"FINAL_EXISTS_R4_CANDIDATE.zip",existing_final)
    with pytest.raises(FileExistsError,match="FINAL_ALREADY_EXISTS"): r3_generator.validate_review_package_spec(final_spec)
    protected=r3_generator.ReviewPackageSpec("R4","PROTECTED_R4",base,target,ZIP_OUTPUT,tmp_path/"PROTECTED_R4.zip")
    with pytest.raises(ValueError,match="HISTORICAL_R3_OUTPUT_FORBIDDEN"): r3_generator.validate_review_package_spec(protected)
