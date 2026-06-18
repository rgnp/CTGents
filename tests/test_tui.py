"""像素 TUI（多屏 + NES 主题）测试。

纯函数确定性钉死；多屏流程/交互用 headless run_test（轮询到目标屏，避免计时 flaky），
不入慢测。SplashScreen.MIN_SECONDS 置 0 跳过开屏最短等待。
"""

import asyncio

from src.cache_context import CacheContext
from src.tui import (
    ChatScreen,
    CTGentsApp,
    SaveSelectScreen,
    SplashScreen,
    _banner,
    _fmt_tool,
    _status_line,
    _strip_user_wrappers,
)


async def _wait_screen(app, pilot, name: str, ticks: int = 40) -> bool:
    for _ in range(ticks):
        if type(app.screen).__name__ == name:
            return True
        await pilot.pause(0.05)
    return type(app.screen).__name__ == name


# ── 纯函数 ──
class TestPureHelpers:
    def test_fmt_tool(self):
        label, detail = _fmt_tool("read_file", {"path": "a.py"})
        assert detail == "path=a.py" and label

    def test_fmt_tool_truncates(self):
        _, detail = _fmt_tool("x", {"k": "v" * 200})
        assert len(detail) <= 80 and detail.endswith("...")

    def test_strip_preread(self):
        assert _strip_user_wrappers("[预读]\n── 用户问题 ──\n真问题") == "真问题"

    def test_strip_job_notice(self):
        assert _strip_user_wrappers("【后台作业完成】d\n\n【用户消息】\n继续") == "继续"

    def test_strip_plain(self):
        assert _strip_user_wrappers("普通") == "普通"

    def test_status_line_string(self):
        assert isinstance(_status_line(CacheContext(), ""), str)

    def test_banner_five_rows_hollow(self):
        b = _banner("CTGENTS")
        assert b.count("\n") == 4          # 5 行
        assert "║" in b and "═" in b       # 镂空轮廓（box-drawing）
        assert b.startswith("  ")          # 斜体：首行有右移缩进


# ── 多屏流程 ──
class TestScreenFlow:
    def test_splash_to_select_to_chat(self, monkeypatch):
        monkeypatch.setattr(SplashScreen, "MIN_SECONDS", 0.0)

        async def go():
            app = CTGentsApp(CacheContext(), None, ["a", "b"])
            async with app.run_test() as pilot:
                await pilot.pause()
                # MIN_SECONDS=0 时开屏可能已秒切，不强求当下仍在 splash
                assert await _wait_screen(app, pilot, "SaveSelectScreen"), "应切到存档选择"
                from textual.widgets import ListView
                lv = app.screen.query_one("#saves", ListView)
                lv.index = len(app.sessions)   # NEW GAME（最后一项）
                await pilot.pause()
                await pilot.press("enter")
                assert await _wait_screen(app, pilot, "ChatScreen"), "选定后应进聊天屏"
                assert app.ctx.prefix, "开屏后台应已初始化 ctx"

        asyncio.run(go())

    def test_no_sessions_skip_select(self, monkeypatch):
        monkeypatch.setattr(SplashScreen, "MIN_SECONDS", 0.0)

        async def go():
            app = CTGentsApp(CacheContext(), None, [])   # 无存档
            async with app.run_test() as pilot:
                await pilot.pause()
                assert await _wait_screen(app, pilot, "ChatScreen"), "无存档应直接进聊天（新游戏）"

        asyncio.run(go())


class TestSaveSelect:
    def test_has_new_game_item(self, monkeypatch):
        monkeypatch.setattr(SplashScreen, "MIN_SECONDS", 0.0)

        async def go():
            app = CTGentsApp(CacheContext(), None, ["x"])
            async with app.run_test() as pilot:
                await pilot.pause()
                assert await _wait_screen(app, pilot, "SaveSelectScreen")
                from textual.widgets import ListView
                lv = app.screen.query_one("#saves", ListView)
                names = [c.name for c in lv.children]
                assert "__new__" in names and "x" in names

        asyncio.run(go())


# ── 聊天屏 ──
class TestChatScreen:
    def _fresh_chat_app(self, monkeypatch):
        monkeypatch.setattr(SplashScreen, "MIN_SECONDS", 0.0)
        return CTGentsApp(CacheContext(), None, [])   # 无存档 → 直接进聊天

    def test_idle_esc_clears_no_interrupt(self, monkeypatch):
        app = self._fresh_chat_app(monkeypatch)

        async def go():
            async with app.run_test() as pilot:
                await pilot.pause()
                assert await _wait_screen(app, pilot, "ChatScreen")
                inp = app.screen.query_one("#prompt")
                inp.value = "half typed"
                assert app.screen._busy is False
                await pilot.press("escape")
                await pilot.pause()
                assert inp.value == ""

        asyncio.run(go())

    def test_echo_conversation_skips_noise(self, monkeypatch):
        app = self._fresh_chat_app(monkeypatch)

        async def go():
            from textual.widgets import Markdown
            async with app.run_test() as pilot:
                await pilot.pause()
                assert await _wait_screen(app, pilot, "ChatScreen")
                app.ctx.log = [
                    {"role": "system", "content": "注入"},
                    {"role": "user", "content": "问题A"},
                    {"role": "assistant", "content": None,
                     "tool_calls": [{"id": "1", "function": {"name": "x"}}]},
                    {"role": "tool", "tool_call_id": "1", "content": "TOOLDUMP"},
                    {"role": "assistant", "content": "**回复B**"},
                ]
                app.screen.query_one("#transcript").remove_children()
                app.screen._echo_conversation()
                await pilot.pause()
                assert len(list(app.screen.query(".user"))) == 1
                assert len(list(app.screen.query(Markdown))) == 1

        asyncio.run(go())

    def test_agent_turn_streams_markdown(self, monkeypatch):
        import src.main as main_mod
        monkeypatch.setattr(SplashScreen, "MIN_SECONDS", 0.0)
        monkeypatch.setattr(
            main_mod, "run_agent_turn",
            lambda c, t, sid, *, display=None: (
                display.make_display()[0]("**hi**"), display.end_message(), "sidX")[-1])
        app = CTGentsApp(CacheContext(), None, [])

        async def go():
            from textual.widgets import Markdown
            async with app.run_test() as pilot:
                await pilot.pause()
                assert await _wait_screen(app, pilot, "ChatScreen")
                app.screen.query_one("#prompt").value = "hello"
                await pilot.press("enter")
                for _ in range(20):
                    await pilot.pause(0.05)
                assert len(list(app.screen.query(Markdown))) >= 1
                assert app.final_session_id == "sidX"

        asyncio.run(go())


# 屏类可导入（构造冒烟，防 import 级回归）
def test_screens_importable():
    assert SplashScreen and SaveSelectScreen and ChatScreen
