"""evolution_runner.py tests."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import src.evolution_runner as runner
import src.evolve as ev


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
