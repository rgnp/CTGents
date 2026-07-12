"""像素 TUI 测试。

纯函数确定性钉死；多屏流程/交互用 headless run_test（轮询到目标屏，避免计时 flaky），
不入慢测。SplashScreen.MIN_SECONDS 和 GLOW_DURATION 置 0 跳过开屏动画。
"""

import asyncio

import pytest

from src.cache_context import CacheContext
from src.tui import (
    ChatScreen,
    CTGentsApp,
    SaveSelectScreen,
    SplashScreen,
    _banner_plain,
    _banner_rows,
    _expand_file_mentions,
    _fmt_tool,
    _status_line,
    _strip_user_wrappers,
)

# 测试模式：跳过所有开屏动画等待
SplashScreen.MIN_SECONDS = 0.0
SplashScreen.GLOW_DURATION = 0.0

pytestmark = pytest.mark.slow


async def _wait_screen(app, pilot, name: str, ticks: int = 20) -> bool:
    """轮询直到目标屏出现（默认 20×0.05=1s 兜底），替代长盲等。"""
    for _ in range(ticks):
        if type(app.screen).__name__ == name:
            return True
        await pilot.pause(0.05)
    return type(app.screen).__name__ == name


class TestSplashLogoVisible:
    """回归：开屏 Logo 必须有可见尺寸。

    曾因 CSS `#logo_area{width:auto}` + 子 `Static{width:100%}` 循环依赖塌成
    0×0 而整块隐身——而当时的测试只验"挂载/切屏"、不验尺寸，导致两次"修复
    Logo 不显示"都过测试却没真修好。此测试断言可见尺寸，堵住这类静默回归。
    """

    def test_logo_renders_nonzero_size(self):
        async def go():
            app = CTGentsApp(CacheContext(), None, [])
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                for _ in range(10):
                    await pilot.pause(0.05)
                if type(app.screen).__name__ != "SplashScreen":
                    return
                la = app.screen.query_one("#logo_area")
                assert la.size.width > 0 and la.size.height > 0, \
                    f"开屏 Logo 尺寸为 {la.size}，应可见"

        asyncio.run(go())


# ── 纯函数 ──
class TestPureHelpers:
    def test_fmt_tool_known_pretty(self):
        """已知工具按类型出精炼标签（关键信息 · 分隔，无装饰 emoji），不 dump 全部参数。"""
        _, d_read = _fmt_tool("read_file", {"path": "a.py"})
        assert d_read == "a.py"                  # 无 📄，素文本
        _, d_grep = _fmt_tool("grep_code", {"pattern": "foo", "path": "src"})
        assert d_grep == "foo · src"             # 无 🔍📁，· 分隔
        _, d_test = _fmt_tool("run_command", {"command": "pytest -q"})
        assert d_test == "$ pytest -q"          # $ 是约定文本记号，保留

    def test_fmt_tool_unknown_caps_value(self):
        """未知工具回退 k=v，单值超长截到 60 防一行刷屏。"""
        _, detail = _fmt_tool("x", {"k": "v" * 200})
        assert detail == "k=" + "v" * 60 + "…"

    def test_result_block_multiline_and_cap(self):
        """⎿ 多行结果：首行带 ⎿ 其余缩进；超 max_lines 标 … +N 行；空→完成。"""
        from src.tui import ChatScreen
        b = ChatScreen._result_block("line1\nline2", max_lines=6)
        assert b.startswith("⎿ line1") and "  line2" in b
        many = "\n".join(f"L{i}" for i in range(10))
        b2 = ChatScreen._result_block(many, max_lines=3)
        assert "⎿ L0" in b2 and "… +7 行" in b2 and "L3" not in b2
        assert ChatScreen._result_block("   \n  ") == "⎿ 完成"

    def test_render_result_diff_colored_and_stats(self):
        """含 ```diff 块 → 返回 Rich Text(非纯串) + (+N −M) 统计；普通结果无统计。"""
        from rich.text import Text
        diff = "已写入: a.py\n变更:\n```diff\n@@ -1 +1,2 @@\n-old\n+new1\n+new2\n```"
        renderable, stats = ChatScreen._render_result("write_file", diff)
        assert isinstance(renderable, Text)
        assert stats == "(+2 −1)"             # 2 加 1 减
        plain, no_stats = ChatScreen._render_result("run_command", "普通输出\n第二行")
        assert isinstance(plain, str) and no_stats == ""

    def test_render_result_read_gives_line_count(self):
        """读取类结果：只回行数结论，不 dump 文件头。"""
        numbered = "\n".join(f"{i:4d}|line{i}" for i in range(1, 6))
        r, stats = ChatScreen._render_result("read_file", numbered)
        assert r == "⎿ 5 行" and stats == ""
        # 错误信息原样透出（非行号正文）
        err, _ = ChatScreen._render_result("read_file", "文件不存在: x.py")
        assert "文件不存在" in err

    def test_render_result_grep_gives_hit_count(self):
        """搜索结果：命中数 + 文件数摘要。"""
        out = "src/a.py:10:foo()\nsrc/a.py:20:foo2\nsrc/b.py:3:foo3"
        r, _ = ChatScreen._render_result("grep_code", out)
        assert "3 处" in r and "2 个文件" in r

    def test_expand_file_mentions(self, tmp_path):
        """@<存在的文件> → 内容附在消息后 + 返回已附列表；不存在的 @ 原样不动。"""
        f = tmp_path / "note.txt"
        f.write_text("hello content", encoding="utf-8")
        msg = f"看看 @{f} 这个文件"
        expanded, attached = _expand_file_mentions(msg)
        assert attached == [str(f)]
        assert "hello content" in expanded and f"### 引用文件 {f}" in expanded
        assert expanded.startswith(msg)       # 原文保留在前
        # 不存在 → 不变、空附件
        out, att = _expand_file_mentions("没有 @/nope/missing.xyz 文件")
        assert att == [] and out == "没有 @/nope/missing.xyz 文件"

    def test_expand_file_mentions_truncates_large(self, tmp_path):
        """超 400 行文件 → 截断并标注。"""
        big = tmp_path / "big.txt"
        big.write_text("\n".join(f"line{i}" for i in range(600)), encoding="utf-8")
        expanded, attached = _expand_file_mentions(f"@{big}")
        assert attached == [str(big)]
        assert "截断前 400 行" in expanded
        assert "line399" in expanded and "line400" not in expanded

    def test_strip_preread(self):
        assert _strip_user_wrappers("[预读]\n── 用户问题 ──\n真问题") == "真问题"

    def test_strip_job_notice(self):
        assert _strip_user_wrappers("【后台作业完成】d\n\n【用户消息】\n继续") == "继续"

    def test_strip_plain(self):
        assert _strip_user_wrappers("普通") == "普通"

    def test_status_line_string(self):
        assert isinstance(_status_line(CacheContext(), ""), str)

    def test_banner_rows_8_lines(self):
        rows = _banner_rows("CTGENT")
        assert len(rows) == 8
        assert "█" in rows[0]
        assert "▓" in rows[0]

    def test_banner_plain_no_markup(self):
        b = _banner_plain("CTGENT")
        assert b.count("\n") == 7          # 8 行
        assert "[" not in b                 # 无 markup 标记
        assert "█" in b


# ── 多屏流程 ──
class TestScreenFlow:
    def test_splash_to_select_to_new_game(self, monkeypatch):
        """开屏 → 存档选择（有存档）→ 选 NEW GAME → 进聊天。"""
        monkeypatch.setattr("src.session.get_session_name", lambda sid: f"会话-{sid}")

        async def go():
            app = CTGentsApp(CacheContext(), None, ["a"])
            async with app.run_test() as pilot:
                for _ in range(20):
                    await pilot.pause(0.1)
                    if app.screen.query("#load_game"):
                        break
                from textual.widgets import Button
                app.screen.query_one("#load_game", Button).press()
                await pilot.pause()
                assert await _wait_screen(app, pilot, "SaveSelectScreen"), "应切到存档选择"
                # 按 + NEW GAME 按钮
                app.screen.query_one("#newbtn", Button).press()
                await pilot.pause()
                assert await _wait_screen(app, pilot, "ChatScreen"), "选定后应进聊天屏"
                assert app.ctx.prefix, "开屏后台应已初始化 ctx"

        asyncio.run(go())

    def test_no_sessions_skip_select(self, monkeypatch):
        async def go():
            app = CTGentsApp(CacheContext(), None, [])   # 无存档
            async with app.run_test() as pilot:
                # 等按钮出现
                for _ in range(40):
                    await pilot.pause(0.1)
                    if app.screen.query("#new_game"):
                        break
                from textual.widgets import Button
                app.screen.query_one("#new_game", Button).press()
                await pilot.pause()
                assert await _wait_screen(app, pilot, "ChatScreen"), "无存档→新存档→直接进聊天"

        asyncio.run(go())


class TestSaveSelect:
    def test_has_new_game_button(self, monkeypatch):
        monkeypatch.setattr("src.session.get_session_name", lambda sid: f"会话-{sid}")

        async def go():
            app = CTGentsApp(CacheContext(), None, ["x"])
            async with app.run_test() as pilot:
                for _ in range(20):
                    await pilot.pause(0.1)
                    if app.screen.query("#load_game"):
                        break
                from textual.widgets import Button
                app.screen.query_one("#load_game", Button).press()
                await pilot.pause()
                assert await _wait_screen(app, pilot, "SaveSelectScreen")
                btn = app.screen.query_one("#newbtn", Button)
                assert btn.label.plain == "+ NEW GAME"
                from textual.widgets import ListView
                lv = app.screen.query_one("#saves", ListView)
                names = [c.name for c in lv.children]
                assert "x" in names

        asyncio.run(go())


# ── 聊天屏 ──
class TestChatScreen:
    @staticmethod
    def _fresh_chat_app():
        """创建无存档的 app 实例。"""
        return CTGentsApp(CacheContext(), None, [])

    async def _enter_chat(self, app, pilot):
        """等开屏按钮出现 → 点「新存档」→ 进聊天屏。"""
        for _ in range(20):
            await pilot.pause(0.1)
            if app.screen.query("#new_game"):
                break
        from textual.widgets import Button
        app.screen.query_one("#new_game", Button).press()
        await pilot.pause()
        assert await _wait_screen(app, pilot, "ChatScreen")

    def test_busy_keeps_prompt_typeable_and_preserves_text(self, monkeypatch):
        """跑动时输入框不禁用、busy 下回车不清空也不提交——修"按 Esc 后不能打字"。"""
        async def go():
            app = self._fresh_chat_app()
            async with app.run_test() as pilot:
                await self._enter_chat(app, pilot)
                s = app.screen
                p = s.query_one("#prompt")
                s._busy = True                       # 模拟 agent 在跑
                p.text = "正在编辑的下一句"
                p.focus()
                await pilot.pause()
                assert p.disabled is False, "跑动时输入框不应被禁用"
                s.action_submit_prompt()             # busy 下回车
                assert p.text == "正在编辑的下一句", "busy 下回车应保留正在编辑的文本"

        asyncio.run(go())

    def test_idle_esc_clears_no_interrupt(self, monkeypatch):
        async def go():
            app = self._fresh_chat_app()
            async with app.run_test() as pilot:
                await self._enter_chat(app, pilot)
                inp = app.screen.query_one("#prompt")
                inp.text = "half typed"
                assert app.screen._busy is False
                await pilot.press("escape")
                await pilot.pause()
                assert inp.text == ""

        asyncio.run(go())

    def test_echo_conversation_skips_noise(self, monkeypatch):
        async def go():
            app = self._fresh_chat_app()
            async with app.run_test() as pilot:
                await self._enter_chat(app, pilot)
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
                # 用户=› 前缀块(.user-msg)，agent 回复挂一个 CTGents 头(.agent-name)；
                # 工具结果/system 注入仍被跳过(不渲染)。
                assert len(list(app.screen.query(".user-msg"))) == 1
                assert len(list(app.screen.query(".agent-name"))) == 1

        asyncio.run(go())

    def test_echo_replays_reasoning_and_tools_as_folded(self, monkeypatch):
        """重启回放：持久化的 _reasoning + tool_calls 还原成默认折叠的 Collapsible。"""
        async def go():
            app = self._fresh_chat_app()
            from textual.widgets import Collapsible
            async with app.run_test() as pilot:
                await self._enter_chat(app, pilot)
                app.ctx.log = [
                    {"role": "user", "content": "问题"},
                    {"role": "assistant", "content": None, "_reasoning": "我先想想",
                     "tool_calls": [{"id": "1", "function": {"name": "read_file",
                                     "arguments": '{"path": "a.py"}'}}]},
                    {"role": "tool", "tool_call_id": "1", "content": "DUMP"},
                    {"role": "assistant", "content": "答复", "_reasoning": "再想一下"},
                ]
                app.screen.query_one("#transcript").remove_children()
                app.screen._echo_conversation()
                await pilot.pause()
                cols = list(app.screen.query(Collapsible))
                # 2 思考折叠，工具不再渲染
                assert len(cols) == 2, f"应有 2 个折叠块(思考)，实得 {len(cols)}"
                assert all(c.collapsed for c in cols), "回放的折叠块应默认折叠"

        asyncio.run(go())

    def test_prune_keeps_last_three_turns(self, monkeypatch):
        """组件累积多轮后剪枝：只留最近 3 轮用户头 + 顶部折叠提示。

        "越用越卡"的真因=组件只增不减；_run_turn 前 _prune_transcript 守住上限，
        只动显示层（不碰 ctx.log）。
        """
        async def go():
            app = self._fresh_chat_app()
            from textual.widgets import Label, Markdown, Static
            async with app.run_test() as pilot:
                await self._enter_chat(app, pilot)
                s = app.screen
                t = s.query_one("#transcript")
                t.remove_children()
                await pilot.pause()
                for i in range(6):                       # 模拟 6 轮
                    t.mount(Static(f"› 你 {i}", classes="user-msg"))
                    t.mount(Label(f"CTGents {i}", classes="agent-name"))
                    t.mount(Markdown(f"答复{i}", classes="msg-body"))
                await pilot.pause()
                s._prune_transcript()
                await pilot.pause()
                users = list(s.query(".user-msg"))
                assert len(users) == 3, f"应只剩最近 3 轮用户块，实得 {len(users)}"
                assert "你 3" in str(users[0].render()), "最早保留的应是第 3 轮"
                markers = [w for w in s.query(Static) if "已折叠" in str(w.render())]
                assert len(markers) == 1, "应有一行折叠提示"

        asyncio.run(go())

    def test_task_panel_shows_hides_windows(self, monkeypatch):
        """有未完成任务 → 面板显示(窗口≤4跟随活跃步)；无步骤/全做完 → 隐藏。"""
        import src.tasks as tasks

        def set_steps(steps, unfinished=True):
            monkeypatch.setattr(tasks, "task_steps", lambda: steps)
            monkeypatch.setattr(tasks, "has_unfinished", lambda: unfinished)

        async def go():
            app = self._fresh_chat_app()
            async with app.run_test() as pilot:
                await self._enter_chat(app, pilot)
                s = app.screen
                panel = s.query_one("#taskpanel")
                # 3 步、有未完成 → 显示
                set_steps([("✅", "一"), ("🔄", "二"), ("⬜", "三")])
                s._refresh_task_panel()
                await pilot.pause()
                assert not panel.has_class("hidden")
                assert "1/3" in str(panel.render()) and "二" in str(panel.render())
                # 8 步 → 只显窗口 4 + 「还有 N」滑动指示，不全列
                set_steps([("✅", f"步{i}") for i in range(3)]
                          + [("🔄", "步3")] + [("⬜", f"步{i}") for i in range(4, 8)])
                s._refresh_task_panel()
                r = str(panel.render())
                assert "↑" in r and "↓ 还有" in r and "步7" not in r  # 窗口化、尾部未全列
                # 全做完(无未完成) → 收起，不赖着
                set_steps([("✅", "一"), ("✅", "二")], unfinished=False)
                s._refresh_task_panel()
                await pilot.pause()
                assert panel.has_class("hidden")
                # 无步骤 → 收起
                set_steps([], unfinished=False)
                s._refresh_task_panel()
                assert panel.has_class("hidden")

        asyncio.run(go())

    def test_at_file_autocomplete(self, monkeypatch):
        """输入 @partial → 弹出匹配文件下拉；接受 → 回填 @path；无 @ → 隐藏。"""
        async def go():
            app = self._fresh_chat_app()
            async with app.run_test() as pilot:
                await self._enter_chat(app, pilot)
                s = app.screen
                s._ac_files_cache = ["src/tui.py", "src/tasks.py", "README.md"]  # 受控列表
                popup = s.query_one("#ac_popup")
                prompt = s.query_one("#prompt")
                prompt.text = "看 @t"
                s._ac_update(prompt.text)
                await pilot.pause()
                assert s._ac_active and not popup.has_class("hidden")
                assert s._ac_options == ["src/tui.py", "src/tasks.py"]  # README 无 t、被排除
                # ↓ 切换选中
                assert s._ac_consume_key("down") and s._ac_index == 1
                # 接受 → 回填 @选中路径 + 空格，关闭下拉
                s._ac_index = 0
                s._ac_accept()
                assert prompt.text.startswith("看 @src/tui.py ")
                assert not s._ac_active and popup.has_class("hidden")
                # 无 @ → 隐藏
                prompt.text = "没有引用"
                s._ac_update(prompt.text)
                assert not s._ac_active and popup.has_class("hidden")

        asyncio.run(go())

    def test_code_block_renders_as_fence(self):
        """输出的 ```代码块渲染成 MarkdownFence（语法高亮载体；CSS 给它对比底色）。"""
        async def go():
            from textual.widgets import Markdown
            from textual.widgets._markdown import MarkdownFence
            app = self._fresh_chat_app()
            async with app.run_test() as pilot:
                await self._enter_chat(app, pilot)
                t = app.screen.query_one("#transcript")
                md = Markdown(classes="msg-body")
                await t.mount(md)
                await md.update("```python\ndef foo(x):\n    return x + 1\n```")
                await pilot.pause()
                assert len(list(app.screen.query(MarkdownFence))) >= 1, "```块应渲染成 MarkdownFence"

        asyncio.run(go())

    def test_slash_command_autocomplete(self, monkeypatch):
        """打 / → 命令补全下拉(带描述)；接受 → 回填 /命令 ；完整命令名时 Enter 直接运行。"""
        async def go():
            app = self._fresh_chat_app()
            async with app.run_test() as pilot:
                await self._enter_chat(app, pilot)
                s = app.screen
                prompt = s.query_one("#prompt")
                prompt.text = "/he"
                s._ac_update(prompt.text)
                await pilot.pause()
                assert s._ac_active and s._ac_mode == "cmd"
                assert "/help" in s._ac_options                       # 前缀匹配
                assert any(lbl.startswith("/help ") for lbl in s._ac_labels)  # 标签带描述
                s._ac_index = s._ac_options.index("/help")
                s._ac_accept()
                assert prompt.text == "/help " and not s._ac_active   # 回填 + 关闭
                # 纯 / → 列全部命令
                prompt.text = "/"
                s._ac_update(prompt.text)
                assert s._ac_active and len(s._ac_options) > 3

        asyncio.run(go())

    def test_at_file_autocomplete_windows_many(self, monkeypatch):
        """匹配数 > 一屏 → 窗口化，↓ 滑动，显示『还有 N 个』。"""
        async def go():
            app = self._fresh_chat_app()
            async with app.run_test() as pilot:
                await self._enter_chat(app, pilot)
                s = app.screen
                s._ac_files_cache = [f"src/mod{i:02d}.py" for i in range(30)]  # 30 个都含 mod
                popup = s.query_one("#ac_popup")
                prompt = s.query_one("#prompt")
                prompt.text = "@mod"
                s._ac_update(prompt.text)
                await pilot.pause()
                assert len(s._ac_options) == 30        # 存全部（≤50），不再只 10
                first = str(popup.render())
                assert "↓ 还有" in first and "↑ 还有" not in first  # 在顶部，只有下方更多
                for _ in range(29):                    # 一路 ↓ 到底
                    s._ac_consume_key("down")
                assert s._ac_index == 29
                assert "↑ 还有" in str(popup.render())  # 滑到底，上方有更多

        asyncio.run(go())

    def test_interrupt_soft_then_terminate(self, monkeypatch):
        """Esc(运行中)=软中断置 pending → 本轮 done 进中断态(断点线+状态栏) → 再 Esc=终止。"""
        async def go():
            app = self._fresh_chat_app()
            async with app.run_test() as pilot:
                await self._enter_chat(app, pilot)
                s = app.screen
                s._busy = True
                s.action_interrupt()                 # 软中断
                assert s._interrupt_pending is True
                s._events.append(("done",))          # 本轮真正收尾
                for _ in range(6):
                    await pilot.pause(0.05)
                assert s._interrupted is True and s._interrupt_pending is False
                brks = [str(b.render()) for b in s.query(".brk")]
                assert any("推理已暂停" in b for b in brks), brks
                status = str(s.query_one("#status").render())
                assert "已中断" in status and "Enter 继续" in status
                s.action_interrupt()                 # 再按 Esc = 硬终止
                assert s._interrupted is False
                brks = [str(b.render()) for b in s.query(".brk")]
                assert any("已终止" in b for b in brks), brks

        asyncio.run(go())

    def test_interrupt_resume_drives_continue_turn(self, monkeypatch):
        """中断态下空 Enter = 继续：喂续跑指令、退出中断态。"""
        import src.main as main_mod
        seen = []
        monkeypatch.setattr(
            main_mod, "run_agent_turn",
            lambda c, t, sid, *, display=None: (seen.append(t), display.end_message(), sid)[-1])

        async def go():
            app = self._fresh_chat_app()
            async with app.run_test() as pilot:
                await self._enter_chat(app, pilot)
                s = app.screen
                s._interrupted = True                 # 直接置中断态
                p = s.query_one("#prompt")
                p.text = ""
                p.focus()
                await pilot.pause()
                s.action_submit_prompt()              # 空 Enter → 继续
                for _ in range(10):
                    await pilot.pause(0.05)
                assert s._interrupted is False
                assert seen and "继续" in seen[0], seen
                # 续跑指令不能用"工作"这类词（"继续刚才的工作"会让 agent 去读任务状态、丢上下文）
                assert "工作" not in seen[0], seen

        asyncio.run(go())

    def test_interrupt_resume_carries_instruction(self, monkeypatch):
        """中断态下带文字回车 = 按指示继续：指示进续跑 prompt + 回显用户那条。"""
        import src.main as main_mod
        seen = []
        monkeypatch.setattr(
            main_mod, "run_agent_turn",
            lambda c, t, sid, *, display=None: (seen.append(t), display.end_message(), sid)[-1])

        async def go():
            app = self._fresh_chat_app()
            async with app.run_test() as pilot:
                await self._enter_chat(app, pilot)
                s = app.screen
                s._interrupted = True
                prompt = s.query_one("#prompt")
                prompt.text = "改用中文"
                prompt.focus()
                await pilot.pause()
                s.action_submit_prompt()             # 直接触发，避开按键路由的不确定性
                for _ in range(10):
                    await pilot.pause(0.05)
                assert s._interrupted is False
                assert seen and "改用中文" in seen[0], seen
                # 用户那条按 › 前缀块(.user-msg)回显
                assert any("改用中文" in str(u.render()) for u in s.query(".user-msg"))

        asyncio.run(go())

    def test_agent_turn_streams_markdown(self, monkeypatch):
        import src.main as main_mod
        monkeypatch.setattr(
            main_mod, "run_agent_turn",
            lambda c, t, sid, *, display=None: (
                display.make_display()[0]("**hi**"), display.end_message(), "sidX")[-1])

        async def go():
            app = CTGentsApp(CacheContext(), None, [])
            from textual.widgets import Markdown
            async with app.run_test() as pilot:
                await self._enter_chat(app, pilot)
                p = app.screen.query_one("#prompt")
                p.text = "hello"
                p.focus()
                await pilot.pause()
                app.screen.action_submit_prompt()
                for _ in range(20):
                    await pilot.pause(0.05)
                assert len(list(app.screen.query(Markdown))) >= 1
                assert app.final_session_id == "sidX"

        asyncio.run(go())


# 屏类可导入（构造冒烟，防 import 级回归）
def test_screens_importable():
    assert SplashScreen and SaveSelectScreen and ChatScreen
