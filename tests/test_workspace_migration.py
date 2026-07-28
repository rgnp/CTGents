"""Legacy workspace migration remains non-overwriting and reversible by default."""

from pathlib import Path

import pytest

from scripts.migrate_workspace import migrate


def test_default_migration_copies_without_removing_source(tmp_path: Path):
    core = tmp_path / "core"
    workspace = tmp_path / "personal"
    (core / "memory").mkdir(parents=True)
    (core / "memory" / "preference.md").write_text("keep", encoding="utf-8")

    actions = migrate(core, workspace)

    assert actions and actions[0].startswith("copied:")
    assert (core / "memory" / "preference.md").is_file()
    assert (workspace / "memory" / "preference.md").read_text(encoding="utf-8") == "keep"


def test_migration_refuses_to_overwrite_existing_target(tmp_path: Path):
    core = tmp_path / "core"
    workspace = tmp_path / "personal"
    (core / "tasks").mkdir(parents=True)
    (workspace / "tasks").mkdir(parents=True)

    with pytest.raises(FileExistsError):
        migrate(core, workspace)


def test_move_mode_transfers_directory(tmp_path: Path):
    core = tmp_path / "core"
    workspace = tmp_path / "personal"
    (core / "knowledge").mkdir(parents=True)
    (core / "knowledge" / "note.md").write_text("note", encoding="utf-8")

    migrate(core, workspace, move=True)

    assert not (core / "knowledge").exists()
    assert (workspace / "knowledge" / "note.md").is_file()
