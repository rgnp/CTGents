"""evolution_runner.py tests — persistent run state."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

import src.evolution_runner as er
from src.evolution_runner import (
    EvolutionPhase,
    RunnerStatus,
    _format_start_summary,
    complete_evolution_run,
    load_active_evolution_run,
    start_evolution_run,
)


@pytest.fixture(autouse=True)
def _isolate_evolution_state(tmp_path, monkeypatch):
    """把 runner 状态目录重定向到 tmp，避免测试污染真实 ~/.ctgents/evolution/。

    没有这层隔离时，每个调用 start_evolution_run 的测试都会在真实状态目录留下一个
    活跃 run（goal="给 count_lines 补 docstring"），被误读为"卡死的自进化 runner"。
    """
    runs = tmp_path / "runs"
    runs.mkdir()
    monkeypatch.setattr(er, "RUN_ROOT", tmp_path)
    monkeypatch.setattr(er, "RUNS_DIR", runs)
    monkeypatch.setattr(er, "ACTIVE_RUN_FILE", tmp_path / "active.json")


class TestEvolutionRunStart:
    """start_evolution_run 测试 — 不生成 prompt，只做后台记录。"""

    def test_start_creates_run_dir(self):
        start = start_evolution_run("测试目标: 补 docstring")
        run = start.run
        assert Path(run.run_dir).is_dir(), f"run_dir 应存在: {run.run_dir}"
        assert Path(run.state_path).exists(), f"state_path 应存在: {run.state_path}"

    def test_start_records_goal(self):
        start = start_evolution_run("补 file.py 的 count_lines docstring")
        assert start.run.goal == "补 file.py 的 count_lines docstring"

    def test_start_returns_summary(self):
        start = start_evolution_run("test goal")
        assert start.run.run_id in start.summary
        assert "runner 已启动" in start.summary

    def test_start_no_prompt_field(self):
        """EvolutionRunStart 不应有 prompt 字段。"""
        start = start_evolution_run("test")
        assert not hasattr(start, "prompt"), "EvolutionRunStart 不应包含 prompt"

    def test_active_run_persists(self):
        start = start_evolution_run("测试持久化")
        active = load_active_evolution_run()
        assert active is not None
        assert active.run_id == start.run.run_id


class TestEvolutionRunLifecycle:
    """Runner 生命周期测试。"""

    def test_complete_run_finalizes(self):
        start = start_evolution_run("测试完成")
        run = complete_evolution_run(start.run.run_id, RunnerStatus.PASSED, "成功")
        assert run.status == "passed"
        assert run.phase == "complete"

    def test_complete_run_clears_active(self):
        start = start_evolution_run("测试清除 active")
        complete_evolution_run(start.run.run_id, RunnerStatus.PASSED)
        assert load_active_evolution_run() is None

    def test_phase_enum(self):
        assert EvolutionPhase.RESEARCH.value == "research"
        assert EvolutionPhase.VERIFICATION.value == "verification"


class TestStartSummary:
    """_format_start_summary 测试。"""

    def test_summary_contains_key_info(self):
        start = start_evolution_run("给 count_lines 补 docstring")
        summary = _format_start_summary(start.run)
        assert "runner 已启动" in summary
        assert start.run.run_id in summary
        assert "给 count_lines 补 docstring" in summary
