"""Core/project boundary tests."""

import src.paths as paths


def test_runtime_names_resolve_to_personal_workspace():
    assert paths.resolve_runtime_path("knowledge/paper/x.md") == (
        paths.WORKSPACE_ROOT / "knowledge" / "paper" / "x.md"
    )
    assert paths.resolve_runtime_path("tasks/current.md") == paths.TASKS_DIR / "current.md"


def test_core_names_remain_in_core_project():
    assert paths.resolve_runtime_path("src/main.py") == paths.CORE_ROOT / "src" / "main.py"
    assert paths.resolve_runtime_path("docs/architecture.md") == (
        paths.CORE_ROOT / "docs" / "architecture.md"
    )


def test_display_path_hides_absolute_workspace_root(tmp_path, monkeypatch):
    workspace = tmp_path / "personal"
    monkeypatch.setattr(paths, "WORKSPACE_ROOT", workspace)
    artifact = workspace / "knowledge" / "note.md"
    assert paths.display_path(artifact) == "knowledge/note.md"


def test_ensure_workspace_creates_only_runtime_skeleton(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(paths, "KNOWLEDGE_DIR", tmp_path / "knowledge")
    monkeypatch.setattr(paths, "MEMORY_DIR", tmp_path / "memory")
    monkeypatch.setattr(paths, "SESSIONS_DIR", tmp_path / "sessions")
    monkeypatch.setattr(paths, "STATS_DIR", tmp_path / "stats")
    monkeypatch.setattr(paths, "TASKS_DIR", tmp_path / "tasks")

    assert paths.ensure_workspace() == tmp_path
    assert (tmp_path / "tasks" / "current.md").is_file()
    assert (tmp_path / "tasks" / "archive").is_dir()
    assert not (tmp_path / "src").exists()
