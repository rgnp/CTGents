"""像素游戏风全屏 TUI（Textual）：开屏 → 存档选择 → 聊天，三屏 + NES 配色主题。

流程：
- 开屏 SplashScreen：像素大字 CTGENTS + loading 动画；后台线程做 ctx 初始化
  （rebuild_prefix + volatile），至少显示 ~1.2s 给"开屏动画感"，完成后切存档选择。
- 存档选择 SaveSelectScreen：居中存档列表，↑↓ 切换、Enter 选定，含 + NEW GAME；
  无底部对话线/状态栏（只有进聊天屏后才出现）。
- 聊天 ChatScreen：滚动 transcript + 底部输入 + 状态栏；实时 markdown 渲染。

架构纪律（不变）：
- 复用唯一咽喉 run_agent_turn（注入 ui.Display），不另起 agent 循环。
- agent 一轮跑线程 worker，只往线程安全 deque 推事件；UI 线程 set_interval 排空、
  在主线程改 widget。
- TUI 下置 main._under_tui=True 禁 msvcrt Esc 监听（Textual 自管 stdin）。

启动失败由 main 兜底回退行式 REPL。
"""

from __future__ import annotations

import contextlib
import time
from collections import deque
from typing import TYPE_CHECKING

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.theme import Theme
from textual.widgets import Button, Input, Label, ListItem, ListView, Markdown, Static

if TYPE_CHECKING:
    from .cache_context import CacheContext


# ── 红白机 8-bit 配色主题 ──
NES_THEME = Theme(
    name="nes",
    primary="#3cbcfc",      # 亮青（FC 招牌蓝）
    secondary="#f878f8",    # 品红
    accent="#fc9838",       # 橙
    foreground="#fcfcfc",
    background="#0d0d20",
    surface="#1a1a3a",
    panel="#202050",
    success="#58d854",
    warning="#f8d800",
    error="#f83800",
    dark=True,
)

# ── 像素大字 banner：密度抗锯齿 + 行渐变着色 + 投影 ──
# 字形：8×8 密度矩阵。密度 0=空格, 1=░, 2=▓, 3=█。
# 六字母 CTGENT，SplashScreen 和 SaveSelectScreen 两处复用。
_GLYPHS: dict[str, list[list[int]]] = {
    "C": [
        [0,1,2,3,3,3,2,1],
        [1,3,3,2,1,0,0,0],
        [2,3,1,0,0,0,0,0],
        [3,2,0,0,0,0,0,0],
        [3,2,0,0,0,0,0,0],
        [2,3,1,0,0,0,0,0],
        [1,3,3,2,1,0,0,0],
        [0,1,2,3,3,3,2,1],
    ],
    "T": [
        [3,3,3,3,3,3,3,3],
        [0,0,2,3,3,2,0,0],
        [0,0,1,3,3,1,0,0],
        [0,0,0,3,3,0,0,0],
        [0,0,0,3,3,0,0,0],
        [0,0,0,3,3,0,0,0],
        [0,0,0,3,3,0,0,0],
        [0,0,0,2,2,0,0,0],
    ],
    "G": [
        [0,1,2,3,3,3,2,1],
        [1,3,3,2,1,0,0,0],
        [2,3,1,0,0,0,0,0],
        [3,2,0,0,1,3,2,0],
        [3,2,0,0,0,2,3,1],
        [2,3,1,0,0,1,3,2],
        [1,3,3,2,0,2,3,1],
        [0,1,2,3,3,3,1,0],
    ],
    "E": [
        [3,3,3,3,3,3,3,3],
        [3,2,0,0,0,0,0,0],
        [3,1,0,0,0,0,0,0],
        [3,3,3,3,3,2,0,0],
        [3,2,0,0,0,0,0,0],
        [3,1,0,0,0,0,0,0],
        [3,2,0,0,0,0,0,0],
        [3,3,3,3,3,3,3,3],
    ],
    "N": [
        [3,3,0,0,0,0,3,3],
        [3,3,2,0,0,0,3,3],
        [3,3,3,2,0,0,3,3],
        [3,3,1,3,2,0,3,3],
        [3,3,0,2,3,2,3,3],
        [3,3,0,0,2,3,3,3],
        [3,3,0,0,0,2,3,3],
        [3,3,0,0,0,0,3,3],
    ],
}

_GLYPH_H = 8           # 字形高度
_GLYPH_GAP = 3         # 字母间距
_BANNER_LEAD = [4, 3, 3, 2, 2, 1, 1, 0]  # 斜体右倾（每行缩进）
_DENSITY_CHAR = {0: " ", 1: "░", 2: "▓", 3: "█"}

# 行渐变：从上到下 8 行，亮青 → 深蓝
_GRADIENT = [
    "#a8e8ff", "#78d8ff", "#4dc8f8", "#38b8ee",
    "#30a8de", "#2e98ce", "#2c88be", "#1a78b0",
]
_SHADOW_COLOR = "#0a1a3a"  # 投影色（深海军蓝）


def _build_canvas(text: str) -> list[list[str]]:
    """共享管线：密度矩阵 → canvas（含投影标记 '·'）。"""
    rows_density: list[list[int]] = [[] for _ in range(_GLYPH_H)]
    for ch in text:
        glyph = _GLYPHS.get(ch)
        if glyph is None:
            continue
        for i in range(_GLYPH_H):
            rows_density[i].extend(glyph[i] + [0] * _GLYPH_GAP)

    w = max((len(r) for r in rows_density), default=0)
    if w == 0:
        return [[""]] * _GLYPH_H

    canvas = [[" "] * (w + _BANNER_LEAD[i]) for i in range(_GLYPH_H)]
    for i in range(_GLYPH_H):
        offset = _BANNER_LEAD[i]
        for j, d in enumerate(rows_density[i]):
            canvas[i][offset + j] = ("░" if d == 1 else "▓" if d == 2 else "█" if d == 3 else " ")

    # 右下投影
    for i in range(_GLYPH_H - 1):
        limit = len(canvas[i + 1]) - 1
        for j in range(limit):
            if canvas[i][j] != " " and canvas[i + 1][j + 1] == " ":
                canvas[i + 1][j + 1] = "·"

    return canvas


def _banner_rows(text: str) -> list[str]:
    """返回 8 行 Textual markup 的列表，供开屏逐行动画使用。

    每行用紧凑标记——同色连续字符合并为一个颜色标签，大幅减少标签数。
    """
    canvas = _build_canvas(text)
    result: list[str] = []
    for i in range(_GLYPH_H):
        body_color = _GRADIENT[i]
        parts: list[str] = []
        cur_color = None
        cur_text: list[str] = []

        def _flush(out: list[str]) -> None:
            nonlocal cur_color, cur_text
            if cur_text:
                out.append(f"[{cur_color}]{''.join(cur_text)}[/]")
                cur_text = []
                cur_color = None

        for ch in canvas[i]:
            if ch in ("█", "▓", "░"):
                if cur_color != body_color:
                    _flush(parts)
                    cur_color = body_color
                cur_text.append(ch)
            elif ch == "·":
                if cur_color != _SHADOW_COLOR:
                    _flush(parts)
                    cur_color = _SHADOW_COLOR
                cur_text.append("░")
            else:
                _flush(parts)
                parts.append(" ")
        _flush(parts)
        result.append("".join(parts).rstrip())
    return result


def _banner_plain(text: str) -> str:
    """返回纯文本 banner（无 markup），供 SaveSelectScreen 的 Static 组件使用。

    Textual Static 对超密逐字符 Rich 标记渲染异常——此函数完全剔除标记，
    靠 CSS 的 color 属性统一着色。
    """
    canvas = _build_canvas(text)
    rows = []
    for row in canvas:
        line = "".join(ch if ch != "·" else "░" for ch in row)
        rows.append(line.rstrip())
    return "\n".join(rows)


# ── 纯函数辅助 ──
def _fmt_tool(name: str, args: dict) -> tuple[str, str]:
    """工具调用 → (标签, 截断后的参数明细)。"""
    from .tools._tool_meta import TOOL_LABELS
    label = TOOL_LABELS.get(name, name)
    detail = " ".join(f"{k}={v}" for k, v in args.items())
    if len(detail) > 80:
        detail = detail[:77] + "..."
    return label, detail


def _strip_user_wrappers(content: str) -> str:
    """剥掉 process_turn 给用户消息加的内部包裹（预读/后台作业完成），回放时只留真问题。"""
    if "── 用户问题 ──" in content:
        content = content.split("── 用户问题 ──", 1)[1].strip()
    if "【用户消息】" in content:
        content = content.split("【用户消息】", 1)[1].strip()
    return content


def _status_line(ctx, session_id: str) -> str:
    """底部状态条文本（纯文本版，与 status_bar._build 同源）。"""
    segs: list[str] = []
    if session_id:
        try:
            from .session import get_session_name
            segs.append(f"📁 {get_session_name(session_id)}")
        except Exception:
            pass
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


# ═══════════════════════════════════════════════════════
# 开屏
# ═══════════════════════════════════════════════════════
class SplashScreen(Screen):
    """逐行动画展示 CTGENT Logo → 双选项按钮（新存档 / 继续游玩）。

    - ctx 初始化后台线程并行跑，不阻塞动画
    - 动画完成 + ctx 就绪后显示按钮
    - MIN_SECONDS 可置 0（测试用）
    """

    MIN_SECONDS = 0.0       # 测试可调，默认 0（动画本身已有节奏）
    ROW_INTERVAL = 0.10     # 每行间隔（秒）

    CSS = """
    SplashScreen { align: center middle; background: $background; }
    #splashwrap { width: auto; height: auto; align: center middle; }
    #logo_area { width: auto; height: auto; margin-bottom: 1; }
    #logo_area > Static { width: auto; height: 1; }
    #buttons { width: 100%; height: auto; align: center middle; margin-top: 1; }
    #buttons Button {
        width: 12; height: 1; border: none; margin: 0 2;
        content-align: center middle; background: $surface; color: $primary;
    }
    #buttons Button:hover { background: $primary; color: $background; }
    """

    def compose(self) -> ComposeResult:
        # Logo 在上、两个按钮并排在下方居中（对称分布在 CTGENT 中线两侧）
        with Vertical(id="splashwrap"):
            yield Vertical(id="logo_area")
            yield Horizontal(id="buttons")

    def on_mount(self) -> None:
        self._rows = _banner_rows("CTGENT")
        self._revealed = 0
        self._ctx_ready = False
        self._buttons_shown = False
        self._timer = self.set_interval(self.ROW_INTERVAL, self._reveal_row)
        self._init_ctx()

    def _reveal_row(self) -> None:
        if self._revealed < len(self._rows):
            row_text = self._rows[self._revealed]
            with contextlib.suppress(Exception):
                self.query_one("#logo_area").mount(Static(row_text, markup=True))
            self._revealed += 1
        if self._revealed >= len(self._rows):
            self._timer.stop()
            self._try_show_buttons()

    def _try_show_buttons(self) -> None:
        if self._buttons_shown or not self._ctx_ready or self._revealed < len(self._rows):
            return
        self._buttons_shown = True
        with contextlib.suppress(Exception):
            btn_area = self.query_one("#buttons", Horizontal)
            btn_area.mount(Button("新游戏", id="new_game"))
            btn_area.mount(Button("继续游玩", id="load_game"))
            self.query_one("#new_game", Button).focus()

    @work(thread=True)
    def _init_ctx(self) -> None:
        from . import main as _main
        try:
            self.app.ctx.rebuild_prefix(_main._make_prefix_msgs())
            _main._append_volatile_context(self.app.ctx)
        except Exception:
            pass
        with contextlib.suppress(Exception):
            import src.llm  # noqa: F401
        self._ctx_ready = True
        self.app.call_from_thread(self._try_show_buttons)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "new_game":
            self.app.goto_chat(None)
        elif event.button.id == "load_game":
            self.app.goto_select()


# ═══════════════════════════════════════════════════════
# 存档选择
# ═══════════════════════════════════════════════════════
class SaveSelectScreen(Screen):
    CSS = """
    SaveSelectScreen { align: center middle; background: $background; }
    #selectwrap { width: auto; height: auto; align: center top; }
    #savebox { border: round $primary; padding: 1 2; width: 56; height: auto; background: $surface; }
    #savetitle { color: $accent; text-style: bold; content-align: center middle; margin-bottom: 1; }
    #saves { height: auto; max-height: 14; background: $surface; }
    #saves > ListItem { padding: 0 1; color: $foreground; }
    #saves > ListItem.--highlight { background: $primary; color: $background; text-style: bold; }
    #savehint { color: $primary-darken-1; content-align: center middle; margin-top: 1; }
    """

    def compose(self) -> ComposeResult:
        from .session import get_session_name
        items: list[ListItem] = []
        for sid in self.app.sessions:
            items.append(ListItem(Label(get_session_name(sid)), name=sid))
        items.append(ListItem(Label("+ NEW GAME"), name="__new__"))
        with Vertical(id="selectwrap"), Vertical(id="savebox"):
            yield Static("◆ SELECT  SAVE ◆", id="savetitle")
            yield ListView(*items, id="saves")
            yield Static("↑↓ 选择   ·   Enter 进入   ·   Ctrl+C 退出", id="savehint")

    def on_mount(self) -> None:
        self.query_one("#saves", ListView).focus()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        name = event.item.name
        self.app.goto_chat(None if name == "__new__" else name)


# ═══════════════════════════════════════════════════════
# 聊天
# ═══════════════════════════════════════════════════════
class ChatScreen(Screen):
    CSS = """
    ChatScreen { background: $background; }
    #transcript { padding: 0 1; }
    #transcript > * { width: 100%; }
    .user { color: $accent; text-style: bold; margin: 1 0 0 0; }
    .agent { margin: 0 0 0 2; }
    .tool { color: $secondary; margin: 0 0 0 2; }
    .meta { color: $primary-darken-1; margin: 0 0 0 2; }
    .err  { color: $error; margin: 0 0 0 2; }
    #bottombar { dock: bottom; height: auto; }
    #prompt {
        border: none;
        border-top: solid $primary-darken-1;
        border-bottom: solid $primary-darken-1;
        background: $surface;
        height: 3;
        padding: 0 1;
    }
    #prompt:focus { border-top: solid $primary; border-bottom: solid $primary; }
    #status { height: 1; color: $primary; background: $panel; padding: 0 1; }
    """

    BINDINGS = [
        Binding("escape", "interrupt", "中断", show=True),
        Binding("ctrl+c", "quit", "退出", show=True, priority=True),
        Binding("ctrl+l", "clear_screen", "清屏", show=False),
        Binding("up", "arrow_up", "上一条", show=False, priority=True),
        Binding("down", "arrow_down", "下一条", show=False, priority=True),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._events: deque = deque()
        self._cur_md: Markdown | None = None
        self._cur_text = ""
        self._dirty = False
        self._busy = False
        self._pending_notices: list[str] = []
        self._history: list[str] = []
        self._history_idx: int = -1
        self._history_draft: str = ""
        self._status_cache: tuple[int, int, str] = (-1, -1, "")

    # ── 布局 ──
    def compose(self) -> ComposeResult:
        yield VerticalScroll(id="transcript")
        with Vertical(id="bottombar"):
            yield Input(placeholder="输入消息  ·  /help 指令  ·  Esc 中断  ·  Ctrl+C 退出", id="prompt")
            yield Static("", id="status")

    def on_mount(self) -> None:
        self._load_pending()
        self._refresh_status()
        self.query_one("#prompt", Input).focus()
        self.set_interval(0.03, self._drain_events)
        self.set_interval(0.5, self._refresh_status)
        self.set_interval(0.5, self._drain_jobs)

    def _load_pending(self) -> None:
        """进聊天屏时按存档选择结果加载会话（NEW=不加载，沿用 splash 初始化的空会话）。"""
        sid = self.app.pending_load
        if not sid:
            return
        from .main import _append_volatile_context
        from .session import load_session
        self.app.ctx.clear_log()
        self.app.ctx.log.extend(load_session(sid))
        _append_volatile_context(self.app.ctx)
        self.app.session_id = sid
        self.app.final_session_id = sid
        self._echo_conversation()

    # ── 输入 ──
    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        event.input.value = ""
        if not text or self._busy:
            return
        self._history.append(text)
        self._history_idx = -1
        self._history_draft = ""
        self._mount("你 " + text, "user")
        if text.startswith("/"):
            self._handle_command(text)
        else:
            if self._pending_notices:
                text = ("【后台作业完成】\n" + "\n\n".join(self._pending_notices)
                        + "\n\n【用户消息】\n" + text)
                self._pending_notices.clear()
            self._run_turn(text)

    # ── 指令 ──
    def _handle_command(self, text: str) -> None:
        from .commands import dispatch as dispatch_cmd
        try:
            r = dispatch_cmd(text, self.app.ctx, self.app.session_id)
        except Exception as e:  # noqa: BLE001
            self._mount(f"指令出错: {e}", "err")
            return
        if getattr(r, "exit", False):
            self.app.action_quit()
            return
        if r.message:
            self._mount(r.message, "meta")
        if r.save:
            from .session import save_session
            self.app.session_id = save_session(self.app.ctx.all, self.app.session_id)
            self.app.final_session_id = self.app.session_id
            self._mount(f"会话已保存: [{self.app.session_id}]", "meta")
        if r.load:
            self._apply_load(r.load)
        if r.clear:
            self._apply_clear(r)
        if r.retry:
            last = self.app.ctx.last_user_content() or ""
            if last:
                self._run_turn(last)

    def _apply_load(self, target: str) -> None:
        from .main import _append_volatile_context
        from .session import load_session
        self.app.ctx.clear_log()
        self.app.ctx.log.extend(load_session(target))
        _append_volatile_context(self.app.ctx)
        self._status_cache = (-1, -1, "")
        self.app.session_id = target
        self.app.final_session_id = target
        from . import status_bar
        status_bar.reset()
        self.query_one("#transcript").remove_children()
        self._echo_conversation()
        self._refresh_status()

    def _apply_clear(self, r) -> None:
        from .main import _append_volatile_context, _make_prefix_msgs
        self.app.ctx.clear_log()
        self.app.ctx.rebuild_prefix(_make_prefix_msgs())
        if r.save:
            self.app.session_id = None
            from . import status_bar
            from .session_pins import clear_pins
            from .tasks import reset_gaps_cache
            clear_pins()
            reset_gaps_cache()
            status_bar.reset()
        _append_volatile_context(self.app.ctx)
        self._status_cache = (-1, -1, "")
        self.query_one("#transcript").remove_children()
        self._mount("上下文已清除", "meta")

    # ── 一轮 agent 驱动 ──
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
            self.app.session_id = _main.run_agent_turn(
                self.app.ctx, text, self.app.session_id, display=disp)
            self.app.final_session_id = self.app.session_id
        except Exception as e:  # noqa: BLE001
            ev.append(("error", f"{type(e).__name__}: {e}"))
        finally:
            ev.append(("done",))

    # ── 事件排空（UI 线程）──
    async def _drain_events(self) -> None:
        try:
            transcript = self.query_one("#transcript", VerticalScroll)
        except Exception:
            return
        changed = False
        while True:
            try:
                kind, *rest = self._events.popleft()
            except IndexError:
                break
            if kind == "token":
                self._cur_text += rest[0]
                self._dirty = True
            else:
                await self._flush_md(transcript)
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
        t = self.query_one("#transcript", VerticalScroll)
        t.mount(Label(text, classes=cls, markup=False))
        t.scroll_end(animate=False)

    def _mount_md(self, text: str) -> None:
        t = self.query_one("#transcript", VerticalScroll)
        t.mount(Markdown(text, classes="agent"))
        t.scroll_end(animate=False)

    def _echo_conversation(self) -> None:
        """加载会话后回放完整原对话：用户消息 + assistant 文字回复(markdown)，
        跳过 tool 结果 / system 注入 / 空的 tool-call 消息（那些是噪声）。
        """
        t = self.query_one("#transcript", VerticalScroll)
        for m in self.app.ctx.log:
            role = m.get("role")
            content = (m.get("content") or "").strip()
            if role == "user":
                text = _strip_user_wrappers(content)
                if text:
                    self._mount("你 " + text, "user")
            elif role == "assistant" and content:
                self._mount_md(content)
        t.scroll_end(animate=False)

    def _refresh_status(self) -> None:
        with contextlib.suppress(Exception):
            n_msgs = len(self.app.ctx.all)
            sid_hash = hash(self.app.session_id or "")
            if self._status_cache[0] != n_msgs or self._status_cache[1] != sid_hash:
                line = _status_line(self.app.ctx, self.app.session_id or "")
                self._status_cache = (n_msgs, sid_hash, line)
            else:
                line = self._status_cache[2]
            if self._busy:
                dot = "●" if int(time.monotonic() * 2) % 2 == 0 else "○"
                line = f"{dot} 思考中  │  {line}"
            self.query_one("#status", Static).update(line)

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
            self.query_one("#prompt", Input).value = ""
            return
        try:
            from .llm import request_interrupt
            request_interrupt()
            self._mount("[已请求中断]", "meta")
        except Exception:
            pass

    def action_clear_screen(self) -> None:
        with contextlib.suppress(Exception):
            self.query_one("#transcript", VerticalScroll).scroll_end(animate=False)

    def action_arrow_up(self) -> None:
        if self._busy or not self._history:
            return
        inp = self.query_one("#prompt", Input)
        if self._history_idx == -1:
            self._history_draft = inp.value
            self._history_idx = len(self._history) - 1
        elif self._history_idx > 0:
            self._history_idx -= 1
        else:
            return
        inp.value = self._history[self._history_idx]
        inp.cursor_position = len(inp.value)

    def action_arrow_down(self) -> None:
        if self._busy or not self._history:
            return
        inp = self.query_one("#prompt", Input)
        if self._history_idx == -1:
            return
        if self._history_idx < len(self._history) - 1:
            self._history_idx += 1
            inp.value = self._history[self._history_idx]
        else:
            self._history_idx = -1
            inp.value = self._history_draft
            self._history_draft = ""
        inp.cursor_position = len(inp.value)


# ═══════════════════════════════════════════════════════
# App
# ═══════════════════════════════════════════════════════
class CTGentsApp(App):
    """多屏协调 + NES 主题。共享状态（ctx/session_id/sessions）挂在 App 上，各屏经 self.app 取。"""

    BINDINGS = [Binding("ctrl+c", "quit", "退出", priority=True)]

    def __init__(self, ctx: CacheContext, session_id: str | None,
                 sessions: list[str] | None = None):
        super().__init__()
        self.ctx = ctx
        self.session_id = session_id
        self.sessions = sessions or []
        self.final_session_id = session_id
        self.pending_load: str | None = None

    def on_mount(self) -> None:
        self.register_theme(NES_THEME)
        self.theme = "nes"
        self.push_screen(SplashScreen())

    def goto_select(self) -> None:
        if self.sessions:
            self.switch_screen(SaveSelectScreen())
        else:
            self.goto_chat(None)   # 没存档：直接新游戏

    def goto_chat(self, load_sid: str | None) -> None:
        self.pending_load = load_sid
        self.switch_screen(ChatScreen())


def run_tui(ctx: CacheContext, session_id: str | None,
            sessions: list[str] | None = None) -> str | None:
    """启动 TUI，阻塞直到退出；返回最终 session_id 供 main 做收尾。"""
    from . import main as _main
    _main._under_tui = True
    app = CTGentsApp(ctx, session_id, sessions)
    try:
        app.run()
    finally:
        _main._under_tui = False
    return app.final_session_id
