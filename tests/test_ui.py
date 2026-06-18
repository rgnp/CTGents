"""终端渲染层 ui.py 测试。

核心不变量：非 TTY 路径输出与改造前逐字节一致——脚本/测试/eval harness
消费 REPL 输出时不能被颜色码或徽标污染。
"""

import io
from contextlib import redirect_stdout

import src.ui as ui


def _force_non_tty(monkeypatch):
    monkeypatch.setattr(ui, "_IS_TTY", False)


class TestNonTtyByteIdentical:
    def test_agent_stream_plain(self, monkeypatch):
        _force_non_tty(monkeypatch)
        on_token, has_output = ui.agent_display()
        assert not has_output()
        buf = io.StringIO()
        with redirect_stdout(buf):
            on_token("你好")
            on_token("，世界")
        assert buf.getvalue() == "Agent: 你好，世界"
        assert has_output()

    def test_tool_call_plain(self, monkeypatch):
        _force_non_tty(monkeypatch)
        buf = io.StringIO()
        with redirect_stdout(buf):
            ui.tool_call("读取文件", "path=a.py")
        assert buf.getvalue() == "  [读取文件] path=a.py\n"

    def test_recent_history_plain(self, monkeypatch):
        _force_non_tty(monkeypatch)
        buf = io.StringIO()
        with redirect_stdout(buf):
            ui.recent_history([("You", "Q"), ("Agent", "A")])
        assert buf.getvalue() == "─" * 40 + "\nYou: Q\nAgent: A\n" + "─" * 40 + "\n"

    def test_banner_plain(self, monkeypatch):
        _force_non_tty(monkeypatch)
        buf = io.StringIO()
        with redirect_stdout(buf):
            ui.banner("hi")
        assert buf.getvalue() == "hi\n"

    def test_prompt_message_plain(self, monkeypatch):
        _force_non_tty(monkeypatch)
        assert ui.prompt_message() == "You: "


class TestTtyDoesNotCrash:
    """TTY 路径强制走 rich，至少不抛、且产出非空（颜色码因环境而异，不断言具体字节）。"""

    def test_tool_call_tty_no_crash(self, monkeypatch):
        monkeypatch.setattr(ui, "_IS_TTY", True)
        monkeypatch.setattr(ui, "_console", None)
        from rich.console import Console
        cap = Console(file=io.StringIO(), force_terminal=True, width=80)
        monkeypatch.setattr(ui, "_c", lambda: cap)
        ui.tool_call("run", "x=1")
        out = cap.file.getvalue()
        assert "run" in out and "x=1" in out

    def test_agent_stream_tty_no_crash(self, monkeypatch):
        monkeypatch.setattr(ui, "_IS_TTY", True)
        from rich.console import Console
        cap = Console(file=io.StringIO(), force_terminal=True, width=80)
        monkeypatch.setattr(ui, "_c", lambda: cap)
        on_token, has_output = ui.agent_display()
        on_token("hello")
        assert "Agent" in cap.file.getvalue()
        assert "hello" in cap.file.getvalue()
        assert has_output()
