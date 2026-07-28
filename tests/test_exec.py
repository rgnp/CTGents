"""测试 exec 模块：安全检测、命令执行、异步 job 管理。"""

import contextlib
import re
import shlex
import time
from types import SimpleNamespace

import src.tools.exec as exec_mod
from src.tools.exec import (
    _check_git_destructive,
    _check_git_hook_bypass,
    _is_blocked,
    _truncate_output,
    poll_job,
    run_async,
    run_command,
)


def _extract_job_id(text: str) -> str:
    m = re.search(r"(job-[a-f0-9]+)", text)
    assert m, f"未找到 job_id: {text}"
    return m.group(1)


def _poll_until_done(job_id: str, timeout: float = 5.0, interval: float = 0.05) -> str:
    """轮询直到 job 完成，替代 time.sleep 盲等。"""
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        out = exec_mod.poll_job(job_id)
        if "仍在运行" not in out:
            return out
        time.sleep(interval)
    return exec_mod.poll_job(job_id)


def _drain_until(timeout: float = 5.0, interval: float = 0.05) -> list[str]:
    """轮询直到 drain_finished_jobs 有结果，替代 time.sleep 盲等。"""
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        notices = exec_mod.drain_finished_jobs()
        if notices:
            return notices
        time.sleep(interval)
    return exec_mod.drain_finished_jobs()


class TestIsBlocked:
    def test_blocked_rm_rf(self):
        assert _is_blocked("rm -rf /")[0]

    def test_blocked_shutdown(self):
        assert _is_blocked("shutdown -s -t 0")[0]

    def test_safe_commands(self):
        assert not _is_blocked("git status")[0]
        assert not _is_blocked("pytest -v")[0]

    def test_case_insensitive(self):
        assert _is_blocked("RM -RF /")[0]


class TestTruncateOutput:
    def test_short_output_no_truncation(self):
        assert _truncate_output("hello", max_len=100) == "hello"

    def test_long_output_truncated(self):
        text = "a" * 1000
        result = _truncate_output(text, max_len=100)
        assert "已截断" in result

    def test_exact_boundary(self):
        text = "x" * 100
        assert _truncate_output(text, max_len=100) == text


def _parts(cmd: str) -> list[str]:
    return shlex.split(cmd)


def _captured_run_command_timeout(monkeypatch, command: str, timeout: int) -> int:
    """捕获 Popen.communicate 收到的 timeout，不启动真实子进程。"""
    seen: dict[str, int] = {}

    class FakeProcess:
        returncode = 0

        def communicate(self, *, timeout):
            seen["timeout"] = timeout
            return b"", b""

    monkeypatch.setattr(exec_mod.subprocess, "Popen", lambda *_a, **_k: FakeProcess())
    run_command(command, timeout=timeout)
    return seen["timeout"]


class TestGitHookBypassGuard:
    def test_commit_no_verify_blocked(self):
        assert _check_git_hook_bypass(_parts("git commit --no-verify -m msg"))

    def test_normal_commit_allowed(self):
        assert _check_git_hook_bypass(_parts("git commit -m msg")) == ""

    def test_non_git_allowed(self):
        assert _check_git_hook_bypass(_parts("sometool --no-verify")) == ""

    def test_run_command_rejects(self):
        out = run_command("git commit --no-verify -m msg")
        assert "拦截" in out


class TestGitDestructiveGuard:
    """拦"毁工作区/丢未提交工作"的 git——自驱事故根因（reset --hard 冲掉未提交的活）。"""

    def test_reset_hard_blocked(self):
        assert _check_git_destructive(_parts("git reset --hard HEAD"))

    def test_clean_force_blocked(self):
        assert _check_git_destructive(_parts("git clean -fd"))

    def test_checkout_discard_blocked(self):
        assert _check_git_destructive(_parts("git checkout -- ."))

    def test_restore_worktree_blocked(self):
        assert _check_git_destructive(_parts("git restore src/main.py"))

    def test_stash_clear_blocked(self):
        assert _check_git_destructive(_parts("git stash clear"))

    def test_safe_git_allowed(self):
        # 这些不毁未提交工作，必须放行
        assert _check_git_destructive(_parts("git reset HEAD~1")) == ""      # 软重置(留改动)
        assert _check_git_destructive(_parts("git checkout -b feature")) == ""
        assert _check_git_destructive(_parts("git restore --staged f.py")) == ""  # 仅取消暂存
        assert _check_git_destructive(_parts("git clean -n")) == ""          # dry-run
        assert _check_git_destructive(_parts("git status")) == ""

    def test_run_command_rejects_reset_hard(self):
        out = run_command("git reset --hard HEAD")
        assert "拦截" in out and "未提交" in out

    def test_run_async_rejects_reset_hard(self):
        out = run_async("git reset --hard HEAD")
        assert "拦截" in out


class TestGitCommitTimeoutFloor:
    def test_commit_timeout_raised(self, monkeypatch):
        assert _captured_run_command_timeout(monkeypatch, "git commit -m msg", 10) == \
            exec_mod.RUNTIME.git_commit_timeout_floor

    def test_non_commit_timeout_untouched(self, monkeypatch):
        assert _captured_run_command_timeout(monkeypatch, "git status", 10) == 10


class TestTestTimeoutFloor:
    """pytest 命令超时地板：慢测试不被默认 timeout 杀掉，免去 async+反复 poll。"""

    def _captured_timeout(self, monkeypatch, command, timeout):
        return _captured_run_command_timeout(monkeypatch, command, timeout)

    def test_pytest_timeout_raised(self, monkeypatch):
        assert self._captured_timeout(monkeypatch, "pytest -q", 30) == \
            exec_mod.RUNTIME.test_timeout_floor

    def test_python_m_pytest_raised(self, monkeypatch):
        assert self._captured_timeout(monkeypatch, "python -m pytest tests/", 30) == \
            exec_mod.RUNTIME.test_timeout_floor

    def test_explicit_higher_timeout_kept(self, monkeypatch):
        """显式给了更大的 timeout 不被地板压低（max 语义）。"""
        big = exec_mod.RUNTIME.test_timeout_floor + 100
        assert self._captured_timeout(monkeypatch, "pytest -q", big) == big

    def test_non_test_command_untouched(self, monkeypatch):
        assert self._captured_timeout(monkeypatch, "echo hi", 10) == 10

    def test_run_async_pytest_floored(self, monkeypatch):
        seen: dict = {}

        def fake_start(_command, timeout, _workdir):
            seen["timeout"] = timeout
            return "job-test"

        monkeypatch.setattr(exec_mod, "_start_job", fake_start)
        exec_mod.run_async("pytest -q", timeout=30)
        assert seen["timeout"] == exec_mod.RUNTIME.test_timeout_floor


# ═══════════════════════════════════════
# 异步 job 测试
# ═══════════════════════════════════════

class TestRunAsync:
    def test_start_returns_job_id(self):
        result = run_async("python -c \"print('ok')\"", timeout=10)
        assert "已后台启动" in result
        job_id = _extract_job_id(result)
        assert job_id.startswith("job-")

    def test_poll_done(self):
        exec_mod._jobs.clear()
        result = run_async("python -c \"print('helloworld')\"", timeout=10)
        job_id = _extract_job_id(result)
        out = _poll_until_done(job_id)
        assert "helloworld" in out, f"job_id={job_id!r}, got: {out!r}"

    def test_poll_nonexistent_job(self):
        result = poll_job("job-deadbeef")
        assert "不存在" in result or "过期" in result

    def test_blocked_command_rejected(self):
        result = run_async("rm -rf /", timeout=10)
        assert "拦截" in result

    def test_failing_command(self):
        exec_mod._jobs.clear()
        result = run_async("python -c \"raise SystemExit(2)\"", timeout=10)
        job_id = _extract_job_id(result)
        out = _poll_until_done(job_id)
        assert "exit=2" in out, f"job_id={job_id!r}, got: {out!r}"

    def test_job_cleanup_after_done(self):
        exec_mod._jobs.clear()
        result = run_async("python -c \"print('done')\"", timeout=10)
        job_id = _extract_job_id(result)
        _poll_until_done(job_id)
        poll_job(job_id)
        result2 = poll_job(job_id)
        assert "不存在" in result2 or "过期" in result2

    def test_evicted_running_job_is_killed(self):
        """泄漏不变量：驱逐仍在跑的 job 必须杀进程——否则孤儿活进程堆积卡死机器（70 进程根因）。"""
        exec_mod._jobs.clear()
        result = run_async("python -c \"__import__('time').sleep(60)\"", timeout=120)
        job_id = _extract_job_id(result)
        proc = exec_mod._jobs[job_id]["proc"]
        assert proc.poll() is None, "前置：job 应仍在跑"
        orig = exec_mod._JOB_MAX_COUNT
        try:
            exec_mod._JOB_MAX_COUNT = 0  # 强制上限驱逐
            exec_mod._job_cleanup()
            assert job_id not in exec_mod._jobs, "应被驱逐出追踪"
            assert proc.poll() is not None, "被驱逐的运行中进程必须已被杀，不留孤儿"
        finally:
            exec_mod._JOB_MAX_COUNT = orig
            with contextlib.suppress(Exception):
                proc.kill()


class TestDrainFinishedJobs:
    """V1 通知模型：REPL 回合间收割已完成作业 → 通知，取代 agent poll 忙等。"""

    def test_drain_returns_finished_and_removes(self):
        exec_mod._jobs.clear()
        result = run_async("python -c \"print('drained-ok')\"", timeout=10)
        job_id = _extract_job_id(result)
        notices = _drain_until()
        assert any("drained-ok" in n and job_id in n for n in notices), notices
        assert job_id not in exec_mod._jobs, "收割后必须从 _jobs 移除"

    def test_drain_skips_running(self):
        exec_mod._jobs.clear()
        result = run_async("python -c \"__import__('time').sleep(30)\"", timeout=60)
        job_id = _extract_job_id(result)
        try:
            assert exec_mod.drain_finished_jobs() == [], "仍在跑的作业不该被收割"
            assert job_id in exec_mod._jobs
            assert exec_mod.running_job_count() >= 1
        finally:
            with contextlib.suppress(Exception):
                exec_mod._jobs[job_id]["proc"].kill()
            exec_mod._jobs.clear()

    def test_drain_marks_failed(self):
        exec_mod._jobs.clear()
        run_async("python -c \"raise SystemExit(3)\"", timeout=10)
        notices = _drain_until()
        assert any("exit=3" in n and "❌" in n for n in notices), notices


class TestAsyncVerificationReceipts:
    @staticmethod
    def _fake_process(exit_code):
        class FakeProcess:
            returncode = exit_code

            def poll(self):
                return self.returncode

        return FakeProcess()

    def _prepare(self, monkeypatch, tmp_path, exit_code):
        import src.verification_receipts as receipts

        exec_mod._jobs.clear()
        monkeypatch.setattr(exec_mod, "_JOB_LOG_DIR", tmp_path)
        monkeypatch.setattr(
            exec_mod.subprocess,
            "Popen",
            lambda *_args, **_kwargs: self._fake_process(exit_code),
        )
        monkeypatch.setattr(receipts, "workspace_fingerprint", lambda *_args: "frozen")
        captured = []

        def fake_record(command, workdir, code, output, *, expected_fingerprint=None):
            captured.append((command, str(workdir), code, output, expected_fingerprint))
            return SimpleNamespace(passed=code == 0)

        monkeypatch.setattr(receipts, "record_verification", fake_record)
        return captured

    def test_poll_records_success_once(self, monkeypatch, tmp_path):
        captured = self._prepare(monkeypatch, tmp_path, 0)
        job_id = exec_mod._start_job("pytest -q", 30, str(tmp_path))

        first = poll_job(job_id)
        second = poll_job(job_id)

        assert "验证回执: 已记录（通过）" in first
        assert "不存在" in second or "过期" in second
        assert len(captured) == 1
        assert captured[0][0] == "pytest -q"
        assert captured[0][2] == 0
        assert captured[0][4] == "frozen"

    def test_automatic_drain_records_failure_evidence(self, monkeypatch, tmp_path):
        captured = self._prepare(monkeypatch, tmp_path, 2)
        job_id = exec_mod._start_job("pytest -q", 30, str(tmp_path))

        notices = exec_mod.drain_finished_jobs()

        assert len(notices) == 1
        assert job_id in notices[0]
        assert "验证回执: 已记录（失败证据）" in notices[0]
        assert captured[0][2] == 2

    def test_changed_workspace_is_reported_without_receipt(self, monkeypatch, tmp_path):
        import src.verification_receipts as receipts

        self._prepare(monkeypatch, tmp_path, 0)
        monkeypatch.setattr(receipts, "record_verification", lambda *_args, **_kwargs: None)
        job_id = exec_mod._start_job("pytest -q", 30, str(tmp_path))

        result = poll_job(job_id)

        assert "验证回执: 未记录" in result
        assert "工作区变化或写入失败" in result

    def test_sync_timeout_transfer_keeps_start_fingerprint(self, monkeypatch, tmp_path):
        exec_mod._jobs.clear()
        monkeypatch.setattr(exec_mod, "_JOB_LOG_DIR", tmp_path)
        monkeypatch.setattr(exec_mod, "_verification_fingerprint", lambda _command: "start-state")

        class TimeoutThenFinish:
            returncode = 0
            calls = 0

            def communicate(self, timeout=None):
                self.calls += 1
                if self.calls == 1:
                    raise exec_mod.subprocess.TimeoutExpired("pytest", timeout)
                return b"passed", b""

            def poll(self):
                return self.returncode

        monkeypatch.setattr(
            exec_mod.subprocess,
            "Popen",
            lambda *_args, **_kwargs: TimeoutThenFinish(),
        )

        result = run_command("pytest -q", timeout=1, workdir=str(tmp_path))
        job_id = _extract_job_id(result)

        assert exec_mod._jobs[job_id]["verification_fingerprint"] == "start-state"
        exec_mod._kill_all_jobs()


class TestJobExecute:
    def test_run_async_via_execute(self):
        out = exec_mod.execute("run_async", {"command": "python -c \"print('x')\"", "timeout": 10})
        assert "已后台启动" in out

    def test_poll_via_execute(self):
        exec_mod._jobs.clear()
        exec_mod.execute("run_async", {"command": "python -c \"print('y')\"", "timeout": 10})
        job_id = list(exec_mod._jobs.keys())[0]
        out = _poll_until_done(job_id)
        assert "y" in out, f"got: {out!r}"


class TestPollLongPoll:
    """long-poll：poll 内部阻塞等到完成或等够预算，避免 agent ~1s 一次忙等长任务。

    回归：取消 poll 去重后，poll 立即返回"运行中"→ agent 紧贴着反复 poll（每次=一整个
    LLM 往返、烧前缀缓存）。改成内部阻塞后，运行中的作业等够预算才返回、完成则立即返回。
    """

    def test_running_job_waits_bounded(self, monkeypatch):
        """运行中的作业：等约 poll_wait_seconds 才返回（不立即返回、也不傻等到作业结束）。"""
        from types import SimpleNamespace
        exec_mod._jobs.clear()
        monkeypatch.setattr(exec_mod, "RUNTIME", SimpleNamespace(poll_wait_seconds=0.3))
        job_id = exec_mod._start_job(
            "python -c \"__import__('time').sleep(30)\"", timeout=120, workdir=None)
        try:
            t0 = time.time()
            out = exec_mod.poll_job(job_id)
            dur = time.time() - t0
            assert "仍在运行" in out, f"got: {out!r}"
            assert 0.25 <= dur <= 5.0, f"应等约 0.3s（非立即、非傻等 30s），实际 {dur:.2f}s"
        finally:
            exec_mod._kill_all_jobs()

    def test_finished_job_returns_immediately(self, monkeypatch):
        """已完成的作业：poll 立即返回结果，不傻等满预算。"""
        from types import SimpleNamespace
        exec_mod._jobs.clear()
        monkeypatch.setattr(exec_mod, "RUNTIME", SimpleNamespace(poll_wait_seconds=30))
        job_id = exec_mod._start_job(
            "python -c \"print('fin')\"", timeout=60, workdir=None)
        t0 = time.monotonic()
        out = _poll_until_done(job_id)
        dur = time.monotonic() - t0
        assert "fin" in out, f"got: {out!r}"
        assert dur < 5.0, f"作业已完成应立即返回，不应傻等满 30s 预算，实际 {dur:.2f}s"

    def test_unknown_tool_returns_none(self):
        assert exec_mod.execute("nonexistent", {}) is None
