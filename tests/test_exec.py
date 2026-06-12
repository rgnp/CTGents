"""测试 exec 模块：安全检测、命令执行、异步 job 管理。"""

import contextlib
import re
import shlex
import time

import src.tools.exec as exec_mod
from src.tools.exec import (
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


class TestGitCommitTimeoutFloor:
    def test_commit_timeout_raised(self, monkeypatch):
        seen: dict = {}

        def fake_run(*args, **kwargs):
            seen["timeout"] = kwargs.get("timeout")
            raise FileNotFoundError

        monkeypatch.setattr(exec_mod.subprocess, "run", fake_run)
        run_command("git commit -m msg", timeout=10)
        assert seen["timeout"] == exec_mod.RUNTIME.git_commit_timeout_floor

    def test_non_commit_timeout_untouched(self, monkeypatch):
        seen: dict = {}

        def fake_run(*args, **kwargs):
            seen["timeout"] = kwargs.get("timeout")
            raise FileNotFoundError

        monkeypatch.setattr(exec_mod.subprocess, "run", fake_run)
        run_command("git status", timeout=10)
        assert seen["timeout"] == 10


# ═══════════════════════════════════════
# 异步 job 测试
# ═══════════════════════════════════════

class TestRunAsync:
    def test_start_returns_job_id(self):
        result = run_async("python -c \"print('ok')\"", timeout=10)
        assert "已启动" in result
        job_id = _extract_job_id(result)
        assert job_id.startswith("job-")

    def test_poll_done(self):
        exec_mod._jobs.clear()
        result = run_async("python -c \"print('helloworld')\"", timeout=10)
        job_id = _extract_job_id(result)
        time.sleep(1.0)
        out = poll_job(job_id)
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
        time.sleep(1.0)
        out = poll_job(job_id)
        assert "exit=2" in out, f"job_id={job_id!r}, got: {out!r}"

    def test_job_cleanup_after_done(self):
        exec_mod._jobs.clear()
        result = run_async("python -c \"print('done')\"", timeout=10)
        job_id = _extract_job_id(result)
        time.sleep(0.5)
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


class TestJobExecute:
    def test_run_async_via_execute(self):
        out = exec_mod.execute("run_async", {"command": "python -c \"print('x')\"", "timeout": 10})
        assert "已启动" in out

    def test_poll_via_execute(self):
        exec_mod._jobs.clear()
        exec_mod.execute("run_async", {"command": "python -c \"print('y')\"", "timeout": 10})
        job_id = list(exec_mod._jobs.keys())[0]
        time.sleep(0.5)
        out = exec_mod.execute("poll", {"job_id": job_id})
        assert "y" in out, f"got: {out!r}"

    def test_unknown_tool_returns_none(self):
        assert exec_mod.execute("nonexistent", {}) is None
