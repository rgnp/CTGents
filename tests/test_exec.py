"""测试 exec 模块：安全检测、命令执行辅助函数。"""

from src.tools.exec import _is_blocked, _truncate_output


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
