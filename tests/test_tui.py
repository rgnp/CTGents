"""TUI 纯函数 + 构造冒烟。

交互行为（流式/滚动/markdown 实时渲染）靠用户终端实测——无 async 测试框架，
且线程+定时器的 run_test 冒烟易 flaky（本项目对测试门稳定性有惨痛教训），
故只钉死可同步、确定的部分：状态行、工具格式化、App 能构造。
"""

from src.cache_context import CacheContext
from src.tui import CTGentsTUI, _fmt_tool, _status_line, _strip_user_wrappers


class TestStripUserWrappers:
    def test_strips_preread_wrapper(self):
        assert _strip_user_wrappers("[预读]x\n── 用户问题 ──\n真问题") == "真问题"

    def test_strips_job_notice_wrapper(self):
        assert _strip_user_wrappers("【后台作业完成】d\n\n【用户消息】\n继续") == "继续"

    def test_plain_unchanged(self):
        assert _strip_user_wrappers("普通消息") == "普通消息"


class TestEchoConversation:
    """加载会话只回放干净对话：跳过 tool 结果 / system 注入 / 空 tool-call 消息。"""

    def test_skips_tool_and_system_noise(self):
        import asyncio

        from textual.widgets import Markdown

        async def go():
            ctx = CacheContext()
            app = CTGentsTUI(ctx, None, [])
            async with app.run_test() as pilot:
                await pilot.pause()
                ctx.log = [
                    {"role": "system", "content": "内部注入"},
                    {"role": "user", "content": "问题A"},
                    {"role": "assistant", "content": None,
                     "tool_calls": [{"id": "1", "function": {"name": "x"}}]},
                    {"role": "tool", "tool_call_id": "1", "content": "TOOLDUMP巨量"},
                    {"role": "assistant", "content": "**回复B**"},
                ]
                app.query_one("#transcript").remove_children()
                app._echo_conversation()
                await pilot.pause()
                assert len(list(app.query(".user"))) == 1, "只 1 条用户消息"
                assert len(list(app.query(Markdown))) == 1, "只有有文字的 assistant 渲染"

        asyncio.run(go())


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


class TestStartupPicker:
    """会话选择搬进 TUI（不再先闪老 CLI）。"""

    def test_sessions_enter_picking_mode_then_new(self):
        import asyncio

        async def go():
            app = CTGentsTUI(CacheContext(), None, ["a", "b"])
            async with app.run_test() as pilot:
                await pilot.pause()
                assert app._picking is True, "有历史会话应进选择模式"
                await pilot.press("enter")   # 空=新会话
                await pilot.pause()
                assert app._picking is False

        asyncio.run(go())

    def test_no_sessions_no_picker(self):
        import asyncio

        async def go():
            app = CTGentsTUI(CacheContext(), None, [])
            async with app.run_test() as pilot:
                await pilot.pause()
                assert app._picking is False

        asyncio.run(go())


class TestIdleEsc:
    """空闲按 Esc 不是中断：清输入、不刷'已请求中断'。"""

    def test_idle_esc_clears_input_no_notice(self):
        import asyncio

        async def go():
            app = CTGentsTUI(CacheContext(), None, [])
            async with app.run_test() as pilot:
                await pilot.pause()
                inp = app.query_one("#prompt")
                inp.value = "half typed"
                assert app._busy is False
                await pilot.press("escape")
                await pilot.pause()
                assert inp.value == "", "空闲 Esc 应清空输入"

        asyncio.run(go())


class TestAppConstruction:
    def test_construct_holds_ctx_and_empty_pipe(self):
        ctx = CacheContext()
        app = CTGentsTUI(ctx, "sid0")
        assert app.ctx is ctx
        assert app.session_id == "sid0"
        assert app.final_session_id == "sid0"
        assert len(app._events) == 0
        assert app._busy is False

    def test_mount_layout(self):
        """挂载后布局：状态栏在输入框【下面】、输入框只有上下两根线（无左右框）。

        run_test 仅挂载、不提交输入（不起后台线程）→ 确定性、非 flaky。
        """
        import asyncio

        async def go():
            app = CTGentsTUI(CacheContext(), None)
            async with app.run_test() as pilot:
                await pilot.pause()
                p = app.query_one("#prompt")
                s = app.query_one("#status")
                assert s.region.y >= p.region.y + p.region.height, "状态栏应在输入框下面"
                assert p.styles.border_top[0] == "solid", "输入框上边应有线"
                assert p.styles.border_bottom[0] == "solid", "输入框下边应有线"
                assert p.styles.border_left[0] == "", "输入框左边不应有框"
                assert p.styles.border_right[0] == "", "输入框右边不应有框"

        asyncio.run(go())
