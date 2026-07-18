"""Unit tests for versioned recent-project configuration."""

from hms_cadcam.project.recent_projects import RecentProjectsService


def test_recent_projects_are_unicode_deduplicated_and_limited(tmp_path) -> None:
    service = RecentProjectsService(tmp_path / "config", limit=2)
    first = tmp_path / "Dự án một.HMS"
    second = tmp_path / "Project two.HMS"
    third = tmp_path / "Project three.HMS"
    for path in (first, second, third):
        path.mkdir()

    service.add(first)
    service.add(second)
    service.add(first)
    assert [entry.path for entry in service.list()] == [first.resolve(), second.resolve()]
    service.add(third)
    assert [entry.path for entry in service.list()] == [third.resolve(), first.resolve()]
    assert "Dự án một.HMS" in (tmp_path / "config" / "recent_projects.json").read_text("utf-8")


def test_malformed_recent_file_is_nonfatal(tmp_path) -> None:
    config = tmp_path / "config"
    config.mkdir()
    (config / "recent_projects.json").write_text("{broken", encoding="utf-8")
    assert RecentProjectsService(config).list() == ()
