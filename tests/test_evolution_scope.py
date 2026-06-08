"""测试进化提交圈定：只提交本轮真正改动的文件，排除启动前已脏的无关文件。

防的是 7da964e 那类事故：一个"补 docstring"的提交把启动前已脏的
evolution_runner.py 一并砍了 82 行。
"""

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import src.evolution_runner as er
import src.tools.git as gitmod
from src.evolution_runner import EvolutionRun, run_owned_paths


def _fake_status(stdout: str):
    return types.SimpleNamespace(stdout=stdout, exit_code=0)


def _make_run(dirty: list[str], root: str = "/proj") -> EvolutionRun:
    return EvolutionRun(
        run_id="evo-test", goal="g", root=root, run_dir="x",
        patch_path="p", state_path="s",
        preflight={"dirty_files": dirty},
    )


def test_run_owned_excludes_preflight_dirty(monkeypatch):
    """启动前已脏的文件不算本轮所有 → 排除。"""
    run = _make_run(["M src/commands.py", "M src/evolution_runner.py"])
    monkeypatch.setattr(er, "_run_git", lambda root, args: _fake_status(
        " M src/commands.py\n M src/tools/file.py\n?? src/tools/paper.py\n"
    ))
    owned = run_owned_paths(run)
    assert owned == ["src/tools/file.py", "src/tools/paper.py"]
    assert "src/commands.py" not in owned  # 启动前已脏（先前 WIP）→ 不背锅


def test_run_owned_all_new_when_clean_start(monkeypatch):
    """干净启动 → 当前所有变更都归本轮。"""
    run = _make_run([])
    monkeypatch.setattr(er, "_run_git", lambda root, args: _fake_status(
        " M src/tools/file.py\n"
    ))
    assert run_owned_paths(run) == ["src/tools/file.py"]


def test_run_owned_handles_rename(monkeypatch):
    """重命名取新名做比对。"""
    run = _make_run([])
    monkeypatch.setattr(er, "_run_git", lambda root, args: _fake_status(
        "R old.py -> src/new.py\n"
    ))
    assert run_owned_paths(run) == ["src/new.py"]


def test_scope_filters_when_active_run(monkeypatch):
    """有 active run → 暂存范围被限定为本轮文件，无关脏文件被剔除。"""
    run = _make_run(["M src/commands.py"])
    monkeypatch.setattr(er, "load_active_evolution_run", lambda: run)
    monkeypatch.setattr(er, "run_owned_paths", lambda r: ["src/tools/file.py"])
    monkeypatch.setattr(er, "append_run_event", lambda *a, **k: None)
    scoped = gitmod._scope_paths_to_active_run(["src/tools/file.py", "src/commands.py"])
    assert scoped == ["src/tools/file.py"]


def test_scope_noop_without_active_run(monkeypatch):
    """无 active run → 暂存范围不变（普通提交行为不受影响）。"""
    monkeypatch.setattr(er, "load_active_evolution_run", lambda: None)
    paths = ["src/a.py", "src/b.py"]
    assert gitmod._scope_paths_to_active_run(paths) == paths


def test_scope_records_audit_event(monkeypatch):
    """剔除无关文件时记一条审计事件（可追溯）。"""
    run = _make_run(["M src/commands.py"])
    recorded = {}
    monkeypatch.setattr(er, "load_active_evolution_run", lambda: run)
    monkeypatch.setattr(er, "run_owned_paths", lambda r: ["src/tools/file.py"])
    monkeypatch.setattr(er, "append_run_event",
                        lambda rid, name, data: recorded.update({"name": name, "data": data}))
    gitmod._scope_paths_to_active_run(["src/tools/file.py", "src/commands.py"])
    assert recorded["name"] == "commit_scoped"
    assert recorded["data"]["skipped_preexisting_dirty"] == ["src/commands.py"]
