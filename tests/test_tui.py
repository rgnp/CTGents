"""TUI 纯函数 + 构造冒烟。

交互行为（流式/滚动/markdown 实时渲染）靠用户终端实测——无 async 测试框架，
且线程+定时器的 run_test 冒烟易 flaky（本项目对测试门稳定性有惨痛教训），
故只钉死可同步、确定的部分：状态行、工具格式化、App 能构造。
"""

from src.cache_context import CacheContext
from src.tui import CTGentsTUI, _fmt_tool, _status_line


class TestFmtTool:
    def test_known_label_and_detail(self):
        label, detail = _fmt_tool("read_file", {"path": "a.py"})
        assert detail == "path=a.py"
        assert isinstance(label, str) and label

    def test_detail_truncated(self):
        _, detail = _fmt_tool("x", {"k": "v" * 200})
        assert len(detail) <= 80
        assert detail.endswith("...")

    def test_unknown_tool_falls_back_to_name(self):
        label, _ = _fmt_tool("__no_such_tool__", {})
        assert label == "__no_such_tool__"


class TestStatusLine:
    def test_empty_ctx_returns_string(self):
        s = _status_line(CacheContext(), "")
        assert isinstance(s, str) and s  # 至少 "就绪" 或 "ctx 0%"

    def test_never_raises_on_weird_session(self):
        # 内部各段都 try 包裹，状态行永不拖垮 UI
        s = _status_line(CacheContext(), "no-such-session")
        assert isinstance(s, str)


class TestAppConstruction:
    def test_construct_holds_ctx_and_empty_pipe(self):
        ctx = CacheContext()
        app = CTGentsTUI(ctx, "sid0")
        assert app.ctx is ctx
        assert app.session_id == "sid0"
        assert app.final_session_id == "sid0"
        assert len(app._events) == 0
        assert app._busy is False

    def test_compose_yields_core_widgets(self):
        from textual.widgets import Input, Static
        app = CTGentsTUI(CacheContext(), None)
        widget_types = {type(w) for w in app.compose()}
        # transcript(VerticalScroll) + 状态条(Static) + 输入(Input)
        assert Input in widget_types
        assert Static in widget_types
