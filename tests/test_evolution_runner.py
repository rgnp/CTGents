"""evolution_runner.py tests."""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import src.evolution_runner as runner
import src.evolve as ev


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True,
                   capture_output=True, text=True)


def _init_repo(repo: Path) -> None:
    """初始化一个带一次基线提交的真 git repo（回滚测试需真 git 行为，不 mock）。"""
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "kept.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")


def _redirect_runner(tmp_path, monkeypatch) -> None:
    run_root = tmp_path / "evolution"
    monkeypatch.setattr(runner, "RUN_ROOT", run_root)
    monkeypatch.setattr(runner, "RUNS_DIR", run_root / "runs")
    monkeypatch.setattr(runner, "ACTIVE_RUN_FILE", run_root / "active.json")
    # complete 会写进化档案；隔离避免污染真实 evolution.jsonl
    monkeypatch.setattr(ev, "EVOLVE_DIR", run_root)
    monkeypatch.setattr(ev, "EVOLVE_LOG", run_root / "evolution.jsonl")


def test_start_evolution_run_creates_state_and_prompt(tmp_path, monkeypatch):
    _redirect_runner(tmp_path, monkeypatch)

    start = runner.start_evolution_run("优化自进化", root=tmp_path)

    state_path = Path(start.run.state_path)
    patch_path = Path(start.run.patch_path)
    assert state_path.exists()
    assert patch_path.exists()
    # runner 不再注入 prompt，只返回值摘要
    assert hasattr(start, "summary")
    assert "runner 已启动" in start.summary
    assert not hasattr(start, "prompt"), "EvolutionRunStart 不应有 prompt 字段"
    assert runner.load_active_evolution_run().run_id == start.run.run_id


def test_record_validation_result_advances_active_run(tmp_path, monkeypatch):
    _redirect_runner(tmp_path, monkeypatch)
    start = runner.start_evolution_run("验证回写", root=tmp_path)

    updated = runner.record_validation_result(["src/example.py"], "all pass", True)

    assert updated is not None
    assert updated.run_id == start.run.run_id
    assert updated.phase == runner.EvolutionPhase.DECISION.value
    assert updated.validations[0]["passed"] is True


def test_complete_evolution_run_clears_active_pointer(tmp_path, monkeypatch):
    _redirect_runner(tmp_path, monkeypatch)
    start = runner.start_evolution_run("完成闭环", root=tmp_path)

    done = runner.complete_evolution_run(start.run.run_id, runner.RunnerStatus.PASSED)

    assert done.status == runner.RunnerStatus.PASSED.value
    assert done.phase == runner.EvolutionPhase.COMPLETE.value
    assert runner.load_active_evolution_run() is None


def test_starting_new_run_archives_stale_active(tmp_path, monkeypatch):
    """失败侧接线：旧 active run（从未提交）被新 run 顶掉时必须归档为 partial，不静默蒸发。

    回归：complete_evolution_run 只在提交成功(PASSED)被调，FAILED/STOPPED 全项目无人触发 →
    放弃的 run 永远 ACTIVE、被覆盖蒸发 → 档案只录成功、success_rate 假、学不到失败。
    """
    _redirect_runner(tmp_path, monkeypatch)
    first = runner.start_evolution_run("第一个目标", root=tmp_path)
    # 第一个 run 没提交就启动第二个 → 第一个应被归档为 partial
    second = runner.start_evolution_run("第二个目标", root=tmp_path)

    # active 指向新 run，旧 run 不再 active
    active = runner.load_active_evolution_run()
    assert active is not None and active.run_id == second.run.run_id

    # 旧 run 状态变 STOPPED，且进了进化档案（outcome=partial）——失败侧那只眼接通了
    stale = runner.load_evolution_run(first.run.run_id)
    assert stale.status == runner.RunnerStatus.STOPPED.value
    recent = ev.get_last_n(5)
    assert any(r.get("goal") == "第一个目标" and r.get("outcome") == "partial"
               for r in recent), f"放弃的 run 应入档案为 partial，实得: {recent}"


def test_clean_start_archives_nothing(tmp_path, monkeypatch):
    """无 active run 时启动不归档任何东西（不误造 partial 噪声）。"""
    _redirect_runner(tmp_path, monkeypatch)
    runner.start_evolution_run("唯一目标", root=tmp_path)
    assert ev.get_last_n(5) == []


# ── ② 失败回滚：仅 owned paths，不碰启动前 WIP ──────────────────

def test_revert_restores_tracked_and_removes_new(tmp_path, monkeypatch):
    """回滚：已跟踪改动还原到 HEAD，本轮新建的未跟踪文件删除。"""
    _redirect_runner(tmp_path, monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    start = runner.start_evolution_run("回滚测试", root=repo)

    (repo / "kept.py").write_text("x = 999  # 本轮乱改\n", encoding="utf-8")
    (repo / "newfile.py").write_text("brand new\n", encoding="utf-8")

    summary = runner.revert_run_owned_paths(start.run, root=repo)

    assert (repo / "kept.py").read_text(encoding="utf-8") == "x = 1\n", "tracked 改动应还原到 HEAD"
    assert not (repo / "newfile.py").exists(), "本轮新建文件应被删除"
    assert "kept.py" in summary["reverted"]
    assert "newfile.py" in summary["removed"]
    assert summary["errors"] == []


def test_revert_spares_startup_dirty(tmp_path, monkeypatch):
    """启动前已脏的文件（先前 WIP）不归本轮 → 回滚绝不碰它。"""
    _redirect_runner(tmp_path, monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    # 启动前就改脏 kept.py（先前 WIP）
    (repo / "kept.py").write_text("preexisting WIP\n", encoding="utf-8")
    start = runner.start_evolution_run("xx", root=repo)
    # 本轮又改它
    (repo / "kept.py").write_text("run changed more\n", encoding="utf-8")

    runner.revert_run_owned_paths(start.run, root=repo)

    assert (repo / "kept.py").read_text(encoding="utf-8") == "run changed more\n", \
        "启动前已脏的文件不归本轮，回滚不得动它（防一锅端）"


def test_abort_active_run_reverts_and_archives(tmp_path, monkeypatch):
    """abort：回滚 owned + 归档 partial + 清 active 指针，一步干净放弃。"""
    _redirect_runner(tmp_path, monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    start = runner.start_evolution_run("放弃这个", root=repo)
    (repo / "kept.py").write_text("bad change\n", encoding="utf-8")

    summary = runner.abort_active_run(root=repo)

    assert summary is not None and summary["run_id"] == start.run.run_id
    assert (repo / "kept.py").read_text(encoding="utf-8") == "x = 1\n"
    assert runner.load_active_evolution_run() is None, "abort 后 active 指针应清空"
    assert any(r.get("outcome") == "partial" for r in ev.get_last_n(5)), "应入档案为 partial"


def test_abort_no_active_returns_none(tmp_path, monkeypatch):
    _redirect_runner(tmp_path, monkeypatch)
    assert runner.abort_active_run() is None
