"""测试 exec 模块：安全检测、命令执行辅助函数。"""

import shlex

import src.tools.exec as exec_mod
from src.tools.exec import _check_git_hook_bypass, _is_blocked, _truncate_output, run_command


class TestIsBlocked:
    """测试命令黑名单检测。"""

    def test_blocked_rm_rf(self):
        assert _is_blocked("rm -rf /")[0]
        assert _is_blocked("rm -rf /*")[0]

    def test_blocked_shutdown(self):
        assert _is_blocked("shutdown -s -t 0")[0]
        assert _is_blocked("reboot")[0]
        assert _is_blocked("poweroff")[0]

    def test_blocked_format(self):
        assert _is_blocked("format C: /Q")[0]

    def test_blocked_sudo(self):
        assert _is_blocked("sudo rm -rf /")[0]

    def test_safe_commands(self):
        assert not _is_blocked("pip install requests")[0]
        assert not _is_blocked("git status")[0]
        assert not _is_blocked("npm test")[0]
        assert not _is_blocked("pytest -v")[0]
        assert not _is_blocked("ls -la")[0]
        assert not _is_blocked("echo hello")[0]
        assert not _is_blocked("python -c 'print(1)'")[0]

    def test_case_insensitive(self):
        assert _is_blocked("RM -RF /")[0]
        assert _is_blocked("Shutdown /s")[0]

    def test_blocked_dd(self):
        assert _is_blocked("dd if=/dev/zero of=/dev/sda")[0]

    def test_empty_command(self):
        assert not _is_blocked("")[0]


class TestTruncateOutput:
    """测试输出截断。"""

    def test_short_output_no_truncation(self):
        text = "hello world"
        result = _truncate_output(text, max_len=100)
        assert result == text
        assert "(已截断" not in result

    def test_long_output_truncated(self):
        text = "a" * 1000
        result = _truncate_output(text, max_len=100)
        assert len(result) < 200  # 截断
        assert "已截断" in result
        assert "1000" in result

    def test_exact_boundary(self):
        text = "x" * 100
        result = _truncate_output(text, max_len=100)
        assert result == text
        assert "(已截断" not in result

    def test_one_over_boundary(self):
        text = "y" * 101
        result = _truncate_output(text, max_len=100)
        assert "已截断" in result
        assert "101" in result

    def test_empty_string(self):
        result = _truncate_output("", max_len=100)
        assert result == ""

    def test_newlines_in_long_output(self):
        """换行符不影响截断逻辑。"""
        text = "line1\n" + "a" * 500 + "\nline2"
        result = _truncate_output(text, max_len=100)
        assert "已截断" in result


def _parts(cmd: str) -> list[str]:
    return shlex.split(cmd)


class TestGitHookBypassGuard:
    """操守牙：绕过质量门钩子的 git 命令必须被拦截。

    背景：50c78c3 等 3 个提交用 --no-verify 绕门、6 红测试入库。
    根因是 timeout < 门时长（正道死路），但绕门本身也必须有牙拦。
    """

    def test_commit_no_verify_blocked(self):
        assert _check_git_hook_bypass(_parts("git commit --no-verify -m msg"))

    def test_commit_n_shorthand_blocked(self):
        """-n 是 --no-verify 的短旗。"""
        assert _check_git_hook_bypass(_parts("git commit -n -m msg"))

    def test_push_no_verify_blocked(self):
        assert _check_git_hook_bypass(_parts("git push --no-verify"))

    def test_hookspath_override_blocked(self):
        assert _check_git_hook_bypass(_parts("git -c core.hooksPath=/dev/null commit -m msg"))

    def test_normal_commit_allowed(self):
        assert _check_git_hook_bypass(_parts("git commit -m msg")) == ""

    def test_normal_push_allowed(self):
        """`push -n` 是 dry-run（无害），不拦。"""
        assert _check_git_hook_bypass(_parts("git push -n origin main")) == ""

    def test_non_git_command_with_flag_allowed(self):
        """精确性：别的程序的 --no-verify 旗不归这颗牙管。"""
        assert _check_git_hook_bypass(_parts("sometool --no-verify input.txt")) == ""

    def test_run_command_rejects_and_teaches(self):
        """拦截消息必须给出正道（加大 timeout / 报告用户），不只说不行。"""
        out = run_command("git commit --no-verify -m msg")
        assert "拦截" in out
        assert "timeout" in out or "超时" in out
        assert "用户" in out


class TestGitCommitTimeoutFloor:
    """正道修通：git commit 的 timeout 自动抬到地板值（门要跑 ~40s+）。"""

    def _capture_timeout(self, monkeypatch):
        seen: dict = {}

        def fake_run(*args, **kwargs):
            seen["timeout"] = kwargs.get("timeout")
            raise FileNotFoundError  # 短路真实执行
        monkeypatch.setattr(exec_mod.subprocess, "run", fake_run)
        return seen

    def test_commit_timeout_raised_to_floor(self, monkeypatch):
        seen = self._capture_timeout(monkeypatch)
        run_command("git commit -m msg", timeout=10)
        assert seen["timeout"] == exec_mod.RUNTIME.git_commit_timeout_floor

    def test_commit_timeout_above_floor_kept(self, monkeypatch):
        seen = self._capture_timeout(monkeypatch)
        floor = exec_mod.RUNTIME.git_commit_timeout_floor
        run_command("git commit -m msg", timeout=floor + 60)
        assert seen["timeout"] == floor + 60

    def test_non_commit_git_timeout_untouched(self, monkeypatch):
        seen = self._capture_timeout(monkeypatch)
        run_command("git status", timeout=10)
        assert seen["timeout"] == 10
