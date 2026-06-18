"""Claude 式全屏 TUI（Textual）—— 底部常驻输入/状态条 + 上方滚动 transcript + 实时 markdown。

架构纪律：
- 复用唯一咽喉 run_agent_turn（对话/任务分支 + 审计都在里头），TUI 只把"输出去向"
  换成 widget——绝不另起一套 agent 循环（项目踩过多入口绕审计的坑）。
- agent 一轮跑在后台线程（LLM 阻塞），线程只往线程安全 deque 推事件；UI 线程用
  set_interval 排空 deque、在主线程改 widget。杜绝跨线程动 UI。
- 纯展示层：不碰缓存/审计/落盘逻辑（落盘仍由 run_agent_turn 内部 on_progress 做）。

启动失败（终端不支持等）由 main 兜底回退行式 REPL，见 main.run()。
"""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Input, Markdown, Static

if TYPE_CHECKING:
    from .cache_context import CacheContext


def _fmt_tool(name: str, args: dict) -> tuple[str, str]:
    """工具调用 → (标签, 截断后的参数明细)，与 main._on_tool 同款。"""
    from .tools._tool_meta import TOOL_LABELS
    label = TOOL_LABELS.get(name, name)
    detail = " ".join(f"{k}={v}" for k, v in args.items())
    if len(detail) > 80:
        detail = detail[:77] + "..."
    return label, detail


def _status_line(ctx, session_id: str) -> str:
    """底部状态条文本（纯文本版，与 status_bar._build 同源、不带 prompt_toolkit HTML）。"""
    segs: list[str] = []
    try:
        from .config import MAX_CONTEXT_TOKENS
        from .tools.tokens import count_messages_tokens
        used = count_messages_tokens(ctx.all)
        pct = used / MAX_CONTEXT_TOKENS * 100 if MAX_CONTEXT_TOKENS else 0
        segs.append(f"ctx {pct:.0f}%")
    except Exception:
        pass
    try:
        from .llm import get_cache_stats
        t = (get_cache_stats(session_id) or {}).get("total", {})
        pt = t.get("prompt_tokens", 0)
        if pt > 0:
            segs.append(f"cache {t.get('cache_hit_tokens', 0) / pt * 100:.0f}%")
    except Exception:
        pass
    try:
        from .tools.exec import running_job_count
        n = running_job_count()
        if n:
            segs.append(f"⏳{n} 后台")
    except Exception:
        pass
    try:
        from .tasks import has_unfinished, read_current
        if has_unfinished():
            title = next((ln.lstrip("#").strip() for ln in read_current().splitlines()
                          if ln.lstrip("#").strip()), "")[:24]
            if title:
                segs.append(f"▶ {title}")
    except Exception:
        pass
    return "  │  ".join(segs) if segs else "就绪"


class CTGentsTUI(App):
    """全屏聊天 TUI。会话选择仍在 main() 里做完，这里只接 (ctx, session_id) 跑交互。"""

    CSS = """
    Screen { background: $surface; }
    #transcript { padding: 0 1; }
    .user { color: $success; text-style: bold; margin: 1 0 0 0; }
    .agent { margin: 0 0 0 2; }
    .tool { color: $text-muted; margin: 0 0 0 2; }
    .meta { color: $text-muted; margin: 0 0 0 2; }
    .err  { color: $error; margin: 0 0 0 2; }
    #bottombar { dock: bottom; height: auto; }
    /* 输入框：去掉左右框、只留上下两根线（Claude 式）；线常驻可见、聚焦更亮 */
    #prompt {
        border: none;
        border-top: solid $primary-darken-1;
        border-bottom: solid $primary-darken-1;
        background: $surface;
        height: 3;
        padding: 0 1;
    }
    #prompt:focus {
        border-top: solid $primary;
        border-bottom: solid $primary;
    }
    /* 状态栏放在输入框【下面】 */
    #status { height: 1; color: $text-muted; background: $panel; padding: 0 1; }
    """

    BINDINGS = [
        Binding("escape", "interrupt", "中断", show=True),
        Binding("ctrl+c", "quit", "退出", show=True, priority=True),
    ]

    def __init__(self, ctx: CacheContext, session_id: str | None,
                 sessions: list[str] | None = None):
        super().__init__()
        self.ctx = ctx
        self.session_id = session_id
        self._sessions = sessions or []     # 历史会话列表，启动时在 TUI 内选（不再走老 CLI）
        self._picking = False               # True=正在选会话，下一条输入当编号解释
        self._events: deque = deque()      # 后台线程→UI 线程的有序事件管道（单产单消）
        self._cur_md: Markdown | None = None
        self._cur_text = ""
        self._dirty = False
        self._busy = False
        self._pending_notices: list[str] = []
        self.final_session_id = session_id  # 退出时回传给 main 做收尾

    # ── 布局 ──
    def compose(self) -> ComposeResult:
        yield VerticalScroll(id="transcript")
        with Vertical(id="bottombar"):   # 输入框在上、状态栏在下，整体 dock 底部
            yield Input(placeholder="输入消息  ·  /help 指令  ·  Esc 中断  ·  Ctrl+C 退出", id="prompt")
            yield Static(_status_line(self.ctx, self.session_id or ""), id="status")

    def on_mount(self) -> None:
        if self._sessions and self.session_id is None:
            self._show_picker()
        else:
            self._echo_recent()
        self.query_one("#prompt", Input).focus()
        self.set_interval(0.08, self._drain_events)    # 排空事件 → 渲染
        self.set_interval(0.8, self._refresh_status)    # 状态条
        self.set_interval(1.0, self._drain_jobs)        # 后台作业完成通知

    def _show_picker(self, cap: int = 20) -> None:
        """在 TUI 内列历史会话供选择（取代启动前的老 CLI 选择）。"""
        from .session import get_session_name
        self._mount("历史会话（输入编号加载，直接回车=新会话）：", "meta")
        for i, sid in enumerate(self._sessions[:cap], 1):
            self._mount(f"  [{i}] {get_session_name(sid)}", "meta")
        if len(self._sessions) > cap:
            self._mount(f"  …共 {len(self._sessions)} 个，更早的进去后用 /sessions + /load", "meta")
        self._picking = True

    def _do_pick(self, text: str) -> None:
        self._picking = False
        try:
            idx = int(text) - 1
            if 0 <= idx < len(self._sessions):
                self._apply_load(self._sessions[idx])
                return
        except ValueError:
            pass
        self.query_one("#transcript").remove_children()
        self._mount("新会话开始，直接输入消息即可。", "meta")

    # ── 输入 ──
    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        event.input.value = ""
        if self._picking:
            self._do_pick(text)
            return
        if not text or self._busy:
            return
        self._mount("你 " + text, "user")
        if text.startswith("/"):
            self._handle_command(text)
        else:
            if self._pending_notices:
                text = ("【后台作业完成】\n" + "\n\n".join(self._pending_notices)
                        + "\n\n【用户消息】\n" + text)
                self._pending_notices.clear()
            self._run_turn(text)

    # ── 指令（控制面，同步处理；effect 复用 dispatch_cmd 的 CmdResult）──
    def _handle_command(self, text: str) -> None:
        from .commands import dispatch as dispatch_cmd
        try:
            r = dispatch_cmd(text, self.ctx, self.session_id)
        except Exception as e:  # noqa: BLE001
            self._mount(f"指令出错: {e}", "err")
            return
        if getattr(r, "exit", False):
            self.action_quit()
            return
        if r.message:
            self._mount(r.message, "meta")
        if r.save:
            from .session import save_session
            self.session_id = save_session(self.ctx.all, self.session_id)
            self._mount(f"会话已保存: [{self.session_id}]", "meta")
        if r.load:
            self._apply_load(r.load)
        if r.clear:
            self._apply_clear(r)
        if r.retry:
            last = self.ctx.last_user_content() or ""
            if last:
                self._run_turn(last)

    def _apply_load(self, target: str) -> None:
        from .main import _append_volatile_context
        from .session import load_session
        self.ctx.clear_log()
        self.ctx.log.extend(load_session(target))
        _append_volatile_context(self.ctx)
        self.session_id = target
        from . import status_bar
        status_bar.reset()
        self.query_one("#transcript").remove_children()
        self._echo_recent()
        self._mount(f"已加载会话 [{target}]", "meta")

    def _apply_clear(self, r) -> None:
        from .main import _append_volatile_context, _make_prefix_msgs
        self.ctx.clear_log()
        self.ctx.rebuild_prefix(_make_prefix_msgs())
        if r.save:
            self.session_id = None
            from . import status_bar
            from .session_pins import clear_pins
            from .tasks import reset_gaps_cache
            clear_pins()
            reset_gaps_cache()
            status_bar.reset()
        _append_volatile_context(self.ctx)
        self.query_one("#transcript").remove_children()
        self._mount("上下文已清除", "meta")

    # ── 一轮 agent 驱动：后台线程跑唯一咽喉 run_agent_turn ──
    def _run_turn(self, text: str) -> None:
        self._busy = True
        self.query_one("#prompt", Input).disabled = True
        self._agent_worker(text)

    @work(thread=True, exclusive=True)
    def _agent_worker(self, text: str) -> None:
        from . import main as _main
        from . import ui
        ev = self._events

        def make_display():
            started = [False]

            def on_token(tok: str) -> None:
                started[0] = True
                ev.append(("token", tok))

            return on_token, (lambda: started[0])

        disp = ui.Display(
            make_display=make_display,
            on_tool=lambda name, args: ev.append(("tool", *_fmt_tool(name, args))),
            on_status=lambda msg: ev.append(("status", msg)),
            on_footer=lambda f: ev.append(("footer", f)),
            end_message=lambda: ev.append(("end",)),
        )
        try:
            self.session_id = _main.run_agent_turn(
                self.ctx, text, self.session_id, display=disp)
            self.final_session_id = self.session_id
        except Exception as e:  # noqa: BLE001
            ev.append(("error", f"{type(e).__name__}: {e}"))
        finally:
            ev.append(("done",))

    # ── 事件排空（UI 线程）──
    async def _drain_events(self) -> None:
        transcript = self.query_one("#transcript", VerticalScroll)
        changed = False
        while True:
            try:
                kind, *rest = self._events.popleft()
            except IndexError:
                break
            if kind == "token":
                self._cur_text += rest[0]
                self._dirty = True
            elif kind in ("tool", "status", "footer", "error", "end", "done"):
                await self._flush_md(transcript)   # 先把在写的 agent 段定格
                if kind == "tool":
                    self._mount(f"→ {rest[0]}" + (f"  {rest[1]}" if rest[1] else ""), "tool")
                elif kind in ("status", "footer"):
                    self._mount(rest[0], "meta")
                elif kind == "error":
                    self._mount(f"💥 {rest[0]}", "err")
                elif kind == "done":
                    self._busy = False
                    inp = self.query_one("#prompt", Input)
                    inp.disabled = False
                    inp.focus()
                changed = True
        if self._dirty:
            await self._flush_md(transcript, finalize=False)
            changed = True
        if changed:
            transcript.scroll_end(animate=False)

    async def _flush_md(self, transcript, finalize: bool = True) -> None:
        """把累计的 agent 文本渲染进当前 Markdown widget；finalize=True 则收尾、下段另起。"""
        if self._cur_text:
            if self._cur_md is None:
                self._cur_md = Markdown(self._cur_text, classes="agent")
                await transcript.mount(self._cur_md)
            else:
                await self._cur_md.update(self._cur_text)
            self._dirty = False
        if finalize:
            self._cur_md = None
            self._cur_text = ""

    # ── 辅助 ──
    def _mount(self, text: str, cls: str) -> None:
        w = Static(text, classes=cls)
        self.query_one("#transcript", VerticalScroll).mount(w)
        self.query_one("#transcript", VerticalScroll).scroll_end(animate=False)

    def _echo_recent(self, count: int = 4) -> None:
        msgs = [m for m in self.ctx.all if m.get("role") != "system"]
        if not msgs:
            return
        for m in msgs[-count * 2:]:
            content = (m.get("content") or "")
            if len(content) > 300:
                content = content[:300] + "…"
            if m["role"] == "user":
                self._mount("你 " + content, "user")
            else:
                self._mount(content, "meta")

    def _refresh_status(self) -> None:
        import contextlib
        with contextlib.suppress(Exception):
            self.query_one("#status", Static).update(_status_line(self.ctx, self.session_id or ""))

    def _drain_jobs(self) -> None:
        try:
            from .tools.exec import drain_finished_jobs
            for notice in drain_finished_jobs():
                self._mount(notice, "meta")
                self._pending_notices.append(notice)
        except Exception:
            pass

    # ── 动作 ──
    def action_interrupt(self) -> None:
        if not self._busy:
            # 空闲时 Esc 不是中断：清空输入框，不刷"已请求中断"
            self.query_one("#prompt", Input).value = ""
            return
        try:
            from .llm import request_interrupt
            request_interrupt()
            self._mount("[已请求中断]", "meta")
        except Exception:
            pass


def run_tui(ctx: CacheContext, session_id: str | None,
            sessions: list[str] | None = None) -> str | None:
    """启动 TUI，阻塞直到退出；返回最终 session_id 供 main 做收尾。"""
    from . import main as _main
    _main._under_tui = True
    app = CTGentsTUI(ctx, session_id, sessions)
    try:
        app.run()
    finally:
        _main._under_tui = False
    return app.final_session_id
