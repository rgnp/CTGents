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
