"""文字终端全屏 TUI（Textual）：开屏 → 存档选择 → 聊天，三屏 + GitHub Dark 深色主题。

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
from textual.widgets import (
    Button,
    Collapsible,
    Label,
    ListItem,
    ListView,
    Markdown,
    Static,
    TextArea,
)

if TYPE_CHECKING:
    from .cache_context import CacheContext


# ── 深海月光 专属配色 ──
DARK_THEME = Theme(
    name="dark-pro",
    primary="#5b8af0",      # 钢蓝（agent，月光照在深海上）
    secondary="#7a88a6",    # 海雾灰（辅助文字）
    accent="#d4875e",       # 赤陶（用户，暖色对比像远处灯火）
    foreground="#dee4ed",   # 月光白（正文，带一丁点蓝）
    background="#0a101c",   # 深海蓝黑（最深）
    surface="#162231",      # 深海蓝（输入框，bg→surface +17 亮度）
    panel="#223548",        # 浅海蓝灰（状态栏/代码块，surface→panel +18 亮度）
    success="#4caf7a",      # 海藻绿
    warning="#d4a843",      # 金盏黄
    error="#e06c6c",        # 珊瑚红
    dark=True,
)

# ── 像素大字 banner：10×8 宽体，密度抗锯齿 + 行渐变着色 ──
# 字形：10×8 密度矩阵。密度 0=空格, 1=░, 2=▓, 3=█。
# 六字母 CTGENT，SplashScreen 和 SaveSelectScreen 两处复用。
_GLYPHS: dict[str, list[list[int]]] = {
    # C: 宽弧，开口适中
    "C": [
        [0,1,2,3,3,3,3,2,1,0],
        [1,3,3,2,1,0,0,0,0,0],
        [2,3,1,0,0,0,0,0,0,0],
        [3,3,2,0,0,0,0,0,0,0],
        [3,3,2,0,0,0,0,0,0,0],
        [2,3,1,0,0,0,0,0,0,0],
        [1,3,3,2,1,0,0,0,0,0],
        [0,1,2,3,3,3,3,2,1,0],
    ],
    # T: 3px 竖干 + 10px 横杆
    "T": [
        [3,3,3,3,3,3,3,3,3,3],
        [0,0,0,2,3,3,3,2,0,0],
        [0,0,0,1,3,3,3,1,0,0],
        [0,0,0,0,3,3,3,0,0,0],
        [0,0,0,0,3,3,3,0,0,0],
        [0,0,0,0,3,3,3,0,0,0],
        [0,0,0,0,3,3,3,0,0,0],
        [0,0,0,0,3,3,3,0,0,0],
    ],
    # G: 留白清晰的 G，crossbar 从右下切入
    "G": [
        [0,1,2,3,3,3,3,2,1,0],
        [1,3,3,2,1,0,0,0,0,0],
        [2,3,1,0,0,0,0,0,0,0],
        [3,3,2,0,0,0,0,1,2,0],
        [3,3,2,0,0,0,0,2,3,0],
        [2,3,1,0,0,0,0,1,3,0],
        [1,3,3,2,1,0,0,1,3,0],
        [0,1,2,3,3,3,3,2,1,0],
    ],
    # E: 全宽横杆，脊柱 2px
    "E": [
        [3,3,3,3,3,3,3,3,3,3],
        [3,2,0,0,0,0,0,0,0,0],
        [3,1,0,0,0,0,0,0,0,0],
        [3,3,3,3,3,3,3,3,2,0],
        [3,2,0,0,0,0,0,0,0,0],
        [3,1,0,0,0,0,0,0,0,0],
        [3,2,0,0,0,0,0,0,0,0],
        [3,3,3,3,3,3,3,3,3,3],
    ],
    # N: 对角线阶梯每行均匀递增 1px
    "N": [
        [3,3,0,0,0,0,0,0,3,3],
        [3,3,2,0,0,0,0,0,3,3],
        [3,3,3,2,0,0,0,0,3,3],
        [3,3,0,3,2,0,0,0,3,3],
        [3,3,0,0,3,2,0,0,3,3],
        [3,3,0,0,0,3,2,0,3,3],
        [3,3,0,0,0,0,3,2,3,3],
        [3,3,0,0,0,0,0,0,3,3],
    ],
}

_GLYPH_H = 8           # 字形高度
_GLYPH_GAP = 3         # 字母间距
_BANNER_LEAD = [3, 3, 2, 2, 1, 1, 0, 0]  # 斜体右倾（每 2 行均匀右移 1px）
_DENSITY_CHAR = {0: " ", 1: "░", 2: "▓", 3: "█"}
_BANNER_LEAD = [2, 1, 1, 1, 0, 0, 0, 0]  # 微斜右倾（每行递增约 1px）
# 行渐变：从上到下 8 行，月光银 → 深海蓝
_GRADIENT = [
    "#c8ddf0", "#a8c8e8", "#88b3e0", "#689ed8",
    "#5088c8", "#4072b0", "#305c98", "#204678",
]


def _build_canvas(text: str) -> list[list[str]]:
    """共享管线：密度矩阵 → canvas（纯像素，无投影）。"""
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
        line = "".join(row)
        rows.append(line.rstrip())
    return "\n".join(rows)


# ── 纯函数辅助 ──
def _fmt_tool(name: str, args: dict) -> tuple[str, str]:
    """工具调用 → (标签, 参数摘要)。每个工具只显示关键信息，不 dump 全部参数。"""
    from .tools._tool_meta import TOOL_LABELS
    label = TOOL_LABELS.get(name, name)

    if name == "grep_code":
        pat = args.get("pattern", "")
        path = args.get("path", "")
        detail = f"🔍 {pat}"
        if path:
            detail += f"  📁 {path}"
    elif name == "read_file":
        detail = f"📄 {args.get('path', '')}"
        start = args.get("start_line")
        end = args.get("end_line")
        if start is not None and end is not None:
            detail += f"  L{start}-L{end}"
        elif start is not None:
            detail += f"  L{start}-"
    elif name in ("write_file", "replace_in_file", "delete_file"):
        detail = f"📄 {args.get('path', '')}"
    elif name == "find_files":
        detail = f"🔍 {args.get('pattern', '')}"
        p = args.get("path", "")
        if p:
            detail += f"  📁 {p}"
    elif name == "list_files":
        p = args.get("path", "")
        detail = f"📁 {p}" if p else "📁 ."
    elif name in ("move_file", "copy_file"):
        detail = f"{args.get('src', '')}  →  {args.get('dst', '')}"
    elif name == "run_command":
        cmd = args.get("command", "").strip().replace("\n", " ")
        detail = f"$ {cmd[:100]}{'…' if len(cmd) > 100 else ''}"
    elif name == "run_python":
        code = args.get("code", "").strip().replace("\n", " ")
        detail = f"🐍 {code[:80]}{'…' if len(code) > 80 else ''}"
    elif name == "search_web":
        detail = f"🔍 {args.get('query', '')}"
    elif name == "read_page":
        detail = f"🌐 {args.get('url', '')}"
    elif name == "scan_papers":
        q = args.get("queries", "")
        detail = f"🔬 {q[:100]}" if q else "扫描论文"
    elif name in ("learn",):
        detail = f"📚 {args.get('topic', '')}"
    elif name in ("remember",):
        detail = f"💾 {args.get('name', '')}"
    elif name in ("recall",):
        detail = f"🔍 {args.get('query', '')}"
    elif name in ("analyze_paper", "cross_validate"):
        detail = f"📄 {args.get('title', '')[:100]}"
    elif name in ("git_status", "git_diff", "git_log", "git_branch"):
        detail = f"📊 {name.replace('git_', '')}"
    elif name == "git_commit":
        msg = args.get("message", "")
        detail = f"💾 {msg[:80]}" if msg else "提交"
    elif name == "git_push":
        detail = f"⬆ {args.get('branch', '当前分支')}"
    elif name == "run_async":
        cmd = args.get("command", "")
        detail = f"⏳ $ {cmd[:120]}{'…' if len(cmd) > 120 else ''}"
    elif name == "make_dir":
        detail = f"📁 {args.get('path', '')}"
    elif name == "think":
        thought = (args.get("thought") or args.get("content") or "").strip().replace("\n", " ")
        detail = f"💭 {thought[:80]}{'…' if len(thought) > 80 else ''}" if thought else "💭"
    else:
        parts = []
        for k, v in args.items():
            sv = str(v)
            if len(sv) > 60:
                sv = sv[:60] + "…"
            parts.append(f"{k}={sv}")
        detail = "  ".join(parts) if parts else "(无参数)"
    return label, detail




def _strip_user_wrappers(content: str) -> str:
    """剥掉 process_turn 给用户消息加的内部包裹（预读/后台作业完成），回放时只留真问题。"""
    if "── 用户问题 ──" in content:
        content = content.split("── 用户问题 ──", 1)[1].strip()
    if "【用户消息】" in content:
        content = content.split("【用户消息】", 1)[1].strip()
    return content


def _expand_file_mentions(text: str) -> tuple[str, list[str]]:
    """把消息里的 @<path> 展开成附带文件内容的块（Claude Code 式文件引用）。

    返回 (展开后的消息, 已附文件路径)。只展开真实存在的文件；单文件截到 400 行/20k 字。
    展开内容追加在原消息后（原文里的 @path 保留，用户回显与 agent 输入都看得见引用了啥）。
    """
    import re
    from pathlib import Path
    blocks: list[str] = []
    attached: list[str] = []
    seen: set[str] = set()
    for raw in re.findall(r'(?<![\w@])@([^\s@]+)', text):
        raw = raw.rstrip(".,;:，。；：")   # 去掉粘在路径尾的标点
        if not raw or raw in seen:
            continue
        p = Path(raw)
        if not p.is_file():
            continue
        try:
            content = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        seen.add(raw)
        attached.append(raw)
        lines = content.splitlines()
        truncated = len(lines) > 400 or len(content) > 20000
        if truncated:
            content = "\n".join(lines[:400])[:20000]
        note = "（截断前 400 行）" if truncated else ""
        blocks.append(f"### 引用文件 {raw}{note}\n```\n{content}\n```")
    if not blocks:
        return text, []
    return text + "\n\n" + "\n\n".join(blocks), attached


def _status_line(ctx, session_id: str) -> str:
    """底部状态条文本：模型·会话·上下文·状态，一眼看清当前情况。"""
    segs: list[str] = []
    try:
        from .llm import get_current_model_name
        segs.append(f"🤖 {get_current_model_name()}")
    except Exception:
        pass
    if session_id:
        try:
            from .session import get_session_name
            segs.append(f"📁 {get_session_name(session_id)}")
        except Exception:
            pass
    try:
        from .config import MAX_CONTEXT_TOKENS
        from .tools.tokens import count_context_tokens
        used = count_context_tokens(ctx.all)
        pct = used / MAX_CONTEXT_TOKENS * 100 if MAX_CONTEXT_TOKENS else 0
        # 显示近似 token 数 + 进度条
        readable = f"{used/1000:.1f}k" if used >= 1000 else str(used)
        bars = min(5, max(1, round(pct / 20)))
        bar = "▓" * bars + "░" * (5 - bars)
        color = "green" if pct < 50 else "yellow" if pct < 75 else "red"
        segs.append(f"🧠 {readable} [{color}]{bar}[/] {pct:.0f}%")
    except Exception:
        pass
    try:
        from .llm import get_cache_stats
        t = (get_cache_stats(session_id) or {}).get("total", {})
        pt = t.get("prompt_tokens", 0)
        if pt > 0:
            hit = t.get("cache_hit_tokens", 0) / pt * 100
            segs.append(f"⚡{hit:.0f}%命中")
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
    ROW_INTERVAL = 0.08     # 每行间隔（秒）—— 0.08s×8行≈0.64s，足够感知逐行浮现
    GLOW_DURATION = 1.5     # 呼吸渐亮持续时间（秒），测试可置 0 跳过

    CSS = """
    SplashScreen { align: center middle; background: $background; }
    #logo_area { width: auto; height: auto; }
    #logo_area > Static { width: auto; height: 1; }
    #buttons { width: auto; height: 3; min-height: 3; margin-top: 4; }
    #buttons Button {
        width: 16; height: 1; border: none; margin: 0 12; text-style: bold;
        background: transparent; color: $primary;
    }
    #buttons Button:hover { color: $accent; text-style: bold underline; }
    """

    def compose(self) -> ComposeResult:
        yield Vertical(id="logo_area")
        yield Horizontal(id="buttons")

    def on_mount(self) -> None:
        self._rows = _banner_rows("CTGENT")
        self._revealed = 0
        self._ctx_ready = False
        self._buttons_shown = False
        self._glow_done = False
        self._timer = self.set_interval(self.ROW_INTERVAL, self._reveal_row)
        self._init_ctx()

    def _reveal_row(self) -> None:
        if self._revealed < len(self._rows):
            row_text = self._rows[self._revealed]
            with contextlib.suppress(Exception):
                row = Static(row_text, markup=True)
                row.styles.opacity = 0.3  # 初始暗态，为呼吸渐亮做准备
                self.query_one("#logo_area").mount(row)
            self._revealed += 1
        if self._revealed >= len(self._rows):
            self._timer.stop()
            self._start_glow()

    def _start_glow(self) -> None:
        """全行揭示后，呼吸渐亮：从暗 0.3 → 满亮 1.0，然后显示按钮。"""
        dur = self.GLOW_DURATION
        for row in self.query("#logo_area > Static"):
            row.styles.animate("opacity", 1.0, duration=dur)
        if dur > 0:
            self.set_timer(dur, self._on_glow_done)
        else:
            self._on_glow_done()

    def _on_glow_done(self) -> None:
        self._glow_done = True
        self._try_show_buttons()

    def _try_show_buttons(self) -> None:
        # 按钮等 Logo 渐亮完成 + ctx 就绪。ctx 在后台建好，渐亮(1.5s)期间已就绪。
        if self._buttons_shown or not self._glow_done:
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
        with contextlib.suppress(Exception):
            self.app.ctx.rebuild_prefix(_main._make_prefix_msgs())
        self._ctx_ready = True                                # 按钮只需 prefix（~0.02s）即可显
        self.app.call_from_thread(self._try_show_buttons)
        # llm 预热（~1s）挪到按钮之后、后台补。挂尾机制已删，prefix 即全部上下文。
        with contextlib.suppress(Exception):
            import src.llm  # noqa: F401

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "new_game":
            self.app.goto_chat(None)
        elif event.button.id == "load_game":
            self.app.goto_select()


# ═══════════════════════════════════════════════════════
# 存档选择
# ═══════════════════════════════════════════════════════
class SaveSelectScreen(Screen):
    """存档选择屏：紧凑列表 + NEW GAME 按钮 + 删除功能。"""

    CSS = """
    SaveSelectScreen { align: center middle; background: $background; }
    #selectwrap { width: auto; height: auto; align: center top; }
    #savetitle {
        color: $primary; text-style: bold; content-align: center middle;
        margin-bottom: 1; border-top: solid $primary-darken-3; padding-top: 1;
    }
    #savebox {
        border: solid $primary; padding: 0 1; width: 38; height: auto;
        background: $surface;
    }
    #saves { height: auto; max-height: 16; background: $surface; }
    #saves > ListItem { padding: 0 1; min-height: 1; }
    #saves > ListItem:even { background: $panel; }
    #saves > ListItem:odd { background: $surface; }
    #saves > ListItem.--highlight {
        background: $primary; color: $background; text-style: bold;
    }
    #newbtn {
        width: 100%; border: none; background: transparent;
        color: $success; text-style: bold;
    }
    #newbtn:hover { color: $accent; text-style: bold underline; }
    #hint {
        color: $primary-darken-1; content-align: center middle;
    }
    .warn { color: $warning !important; text-style: bold; }
    """

    def __init__(self) -> None:
        super().__init__()
        self._confirm_delete: str | None = None

    def compose(self) -> ComposeResult:
        from .session import get_sessions_info
        sessions = sorted(self.app.sessions, reverse=True)
        if not sessions:
            self.app.goto_chat(None)
            return
        infos = get_sessions_info(sessions)
        # 计算最长名字，右对齐时间
        max_name = max((len(infos.get(s, {}).get("name", s)) for s in sessions), default=20)
        items: list[ListItem] = []
        for i, sid in enumerate(sessions):
            info = infos.get(sid, {"name": sid, "date": "", "time": ""})
            name = info["name"]
            time_text = f"{info['date']} {info['time']}".strip()
            prefix = "● " if i == 0 else "  "
            if time_text:
                # 填空格让时间右对齐，pad 保证对齐
                pad = max_name - len(name) + 2
                text = prefix + name + " " * max(pad, 1) + time_text
            else:
                text = prefix + name
            items.append(ListItem(Label(text, markup=False), name=sid))
        with Vertical(id="selectwrap"):
            yield Static("◆ SELECT  SAVE ◆", id="savetitle", markup=False)
            with Vertical(id="savebox"):
                yield ListView(*items, id="saves")
                yield Button("+ NEW GAME", id="newbtn")
            yield Static("↑↓ 选择  Enter 进入  d 删除  Ctrl+Q 退出", id="hint", markup=False)

    def on_mount(self) -> None:
        self.query_one("#saves", ListView).focus()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        self.app.goto_chat(event.item.name)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "newbtn":
            self.app.goto_chat(None)

    def key_d(self) -> None:
        lv = self.query_one("#saves", ListView)
        if lv.index is None:
            return
        target = lv.children[lv.index].name
        if self._confirm_delete == target:
            from .session import delete_session
            delete_session(target)
            self.app.sessions.remove(target)
            if not self.app.sessions:
                self.app.goto_chat(None)
            else:
                self.app.switch_screen(SaveSelectScreen())
        else:
            self._confirm_delete = target
            h = self.query_one("#hint", Static)
            h.update("⚠️ 确认删除？再按 d 确认，其他键取消")
            h.add_class("warn")

    def key_escape(self) -> None:
        if self._confirm_delete:
            self._reset_hint()

    def on_key(self, event) -> None:
        if self._confirm_delete and event.key not in ("d", "escape"):
            self._reset_hint()

    def _reset_hint(self) -> None:
        self._confirm_delete = None
        h = self.query_one("#hint", Static)
        h.update("↑↓ 选择  Enter 进入  d 删除  Ctrl+Q 退出")
        h.remove_class("warn")


# ═══════════════════════════════════════════════════════
# 聊天
# ═══════════════════════════════════════════════════════
class PromptArea(TextArea):
    """输入框：@ 文件补全激活时，把 ↑↓/Tab/Esc 交给 ChatScreen（拦在光标移动之前）。

    Enter 是 ChatScreen 的 priority 绑定、在本控件按键之前触发，故由 action_submit_prompt
    自查补全态处理；这里只拦非 priority 的导航键。
    """

    def on_key(self, event) -> None:
        screen = self.screen
        if (getattr(screen, "_ac_active", False)
                and event.key in ("up", "down", "tab", "escape")
                and screen._ac_consume_key(event.key)):
            event.prevent_default()
            event.stop()


class ChatScreen(Screen):
    CSS = """
    ChatScreen { background: $background; }
    #transcript { padding: 0 1; }
    #transcript > * { width: 100%; }
    /* ── 用户输入：› 前缀，暖色 ── */
    .user-msg { color: $accent; text-style: bold; margin: 1 0 0 0; padding: 0 0 0 1; }
    /* ── agent 名：每个用户轮只一次，思考/工具/输出全归其下 ── */
    .agent-name { color: $primary; text-style: bold; margin: 1 0 0 0; padding: 0 0 0 1; }
    /* ── 输出正文 ── */
    .msg-body { margin: 0 0 1 0; padding: 0 0 0 3; }
    /* ── 思考折叠区：缩进在 CTGents 之下，左竖线 ── */
    Collapsible { margin: 0 0 0 3; border: none; background: transparent; }
    .collapsible-chat {
        margin: 0 0 0 3; border-left: vkey $primary-darken-3;
        padding: 0 0 0 1; background: transparent;
    }
    .thinking { color: $secondary; }
    /* ── 工具调用：⏺ 行 + ⎿ 结果，缩进；进行中琥珀、完成蓝 ── */
    .tool-call { color: $primary-lighten-2; margin: 0 0 0 3; }
    .tool-call.running { color: $warning; }
    .tool-result { color: $secondary; margin: 0 0 0 5; text-style: dim; }
    /* ── 长任务 live TODO 面板（顶部常驻，无任务时隐藏）── */
    #taskpanel {
        dock: top; height: auto; padding: 0 1;
        background: $panel; color: $secondary;
        border-bottom: solid $primary-darken-2;
    }
    #taskpanel.hidden { display: none; }
    /* ── @ 文件补全下拉（输入框上方）── */
    #ac_popup {
        height: auto; max-height: 10; padding: 0 1;
        background: $panel; border: round $primary-darken-1;
    }
    #ac_popup.hidden { display: none; }
    /* ── 轮次分隔 ── */
    .turn-sep { color: $primary-darken-3; height: 1; margin: 1 0; }
    /* ── 元消息 ── */
    .meta { color: $secondary; margin: 0; }
    .sys-meta { color: $secondary; margin: 0; text-style: dim; }
    .err  { color: $error; margin: 0; }
    .brk  { color: $warning; text-style: bold; content-align: center middle; margin: 1 0; }
    /* ── 底部栏 ── */
    #bottombar { dock: bottom; height: auto; }
    #prompt {
        border: none; border-top: solid $primary-darken-1;
        border-bottom: solid $panel;
        background: $surface; height: auto;
        min-height: 1; max-height: 30vh; padding: 0 1;
    }
    #prompt:focus { border-top: solid $primary; border-bottom: solid $primary; }
    #prompt.has-text { border-top: solid $primary; }
    #status {
        height: 1; color: $primary; background: $panel; padding: 0 1;
    }
    #status.interrupted { color: $warning; text-style: bold; background: $surface; }
    """

    BINDINGS = [
        Binding("escape", "interrupt", "中断", show=True),
        # 退出用 ctrl+q；ctrl+c 让给 Textual 内置「选中文本→复制」(Screen.copy_text)，
        # 否则 priority 的 ctrl+c→quit 会抢在复制前触发，导致一按 ctrl+c 就退出、永远复制不了。
        Binding("ctrl+q", "quit", "退出", show=True),
        Binding("ctrl+l", "clear_screen", "清屏", show=False),
        Binding("ctrl+up", "arrow_up", "上一条", show=False),
        Binding("ctrl+down", "arrow_down", "下一条", show=False),
        Binding("enter", "submit_prompt", "发送", show=False, priority=True),
    ]

    # 流式重渲节流：攒 token 仍每 30ms(便宜),但整段 markdown/思考重渲最多 ~10fps。
    # 否则长回复时每帧重解析整段 markdown(33fps×越来越长)→ 越往后越卡。收尾必全渲。
    _LIVE_RENDER_INTERVAL = 0.10
    _SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"   # 运行中工具行的转圈帧（10fps 循环）

    def __init__(self) -> None:
        super().__init__()
        self._events: deque = deque()
        self._cur_md: Markdown | None = None
        self._cur_text = ""
        self._dirty = False
        # 思考(reasoning)流：到达即在 CTGents 头下挂折叠 Collapsible（实时展开，下阶段折起）。
        self._cur_reasoning = ""
        self._reasoning_box: Static | None = None
        self._reasoning_collapsible: Collapsible | None = None
        self._reasoning_dirty = False
        self._active_collapsible: Collapsible | None = None  # 当前展开的思考块，下阶段折起
        self._current_tool = ""           # 状态栏实时显示当前工具名
        self._pending_tool_labels: list[tuple[Label, str]] = []  # 已发起未出结果的工具行（FIFO 配对 ✓）
        self._spin_frame = 0              # 运行中工具行转圈帧索引
        # 每个用户轮只挂一次 CTGents 头（在首个 agent 事件挂出，所有循环的思考/工具/输出归其下）
        self._agent_header_mounted = False
        self._last_user_stamp = ""          # 本轮用户时间戳（供 agent header 降噪用）
        self._busy = False
        # 中断态：用户 Esc 截停本轮后进入。_interrupt_pending=已请求、等本轮真正收尾(done)；
        # _interrupted=已停稳、等用户 Enter 继续 / 再按 Esc 终止。
        self._interrupt_pending = False
        self._interrupted = False
        self._hard_kill = False      # 二次 Esc 升级：pending 态再按 Esc → 强制终止，不等 LLM yield
        self._pending_notices: list[str] = []
        self._history: list[str] = []
        self._history_idx: int = -1
        self._history_draft: str = ""
        self._status_cache: tuple[int, int, str] = (-1, -1, "")
        self._turn_started: float = 0.0
        self._turn_count: int = 0
        self._echo_max_turns: int = 3  # 回放最多渲染的轮数
        self._last_md_render: float = 0.0       # 流式 markdown 上次重渲时刻（节流）
        self._last_reason_render: float = 0.0   # 思考流上次重渲时刻（节流）
        self._pending_marker: Static | None = None  # 首 token 前的"思考中…"占位
        # @ 文件补全状态
        self._ac_active = False
        self._ac_options: list[str] = []
        self._ac_index = 0
        self._ac_at_pos = 0               # 当前 @ 在输入文本中的起始位置
        self._ac_files_cache: list[str] | None = None
    # ── 布局 ──
    def compose(self) -> ComposeResult:
        yield Static("", id="taskpanel", classes="hidden")   # 长任务 live TODO 清单
        yield VerticalScroll(id="transcript")
        with Vertical(id="bottombar"):
            yield Static("", id="ac_popup", classes="hidden")   # @ 文件补全下拉
            yield PromptArea(
                "",
                placeholder="> 输入消息（/help 查看指令；@ 引用文件）",
                id="prompt",
                soft_wrap=True,
                show_line_numbers=False,
            )
            yield Static("", id="status")

    def on_mount(self) -> None:
        self._load_pending()
        self._refresh_status()
        self.query_one("#prompt", TextArea).focus()
        self._refresh_task_panel()
        self.set_interval(0.03, self._drain_events)
        self.set_interval(0.1, self._tick_spinner)
        self.set_interval(0.5, self._refresh_status)
        self.set_interval(0.5, self._refresh_task_panel)
        self.set_interval(0.5, self._drain_jobs)

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        """内容非空时给输入框加 has-text 样式类 + 刷新 @ 文件补全。"""
        if event.text_area.id != "prompt":
            return
        p = self.query_one("#prompt", TextArea)
        if p.text.strip():
            p.add_class("has-text")
        else:
            p.remove_class("has-text")
        self._ac_update(p.text)

    # ── @ 文件补全 ──
    def _ac_file_list(self) -> list[str]:
        """项目内文件相对路径列表（首次扫描后缓存）。排除 .git/__pycache__ 等噪声。"""
        if self._ac_files_cache is None:
            from pathlib import Path
            root = Path.cwd()
            ignore = {".git", "__pycache__", ".venv", "venv", "node_modules",
                      ".pytest_cache", ".mypy_cache", ".ruff_cache"}
            out: list[str] = []
            with contextlib.suppress(Exception):
                for pth in root.rglob("*"):
                    if any(part in ignore for part in pth.parts):
                        continue
                    if pth.is_file():
                        out.append(str(pth.relative_to(root)).replace("\\", "/"))
                    if len(out) >= 4000:
                        break
            self._ac_files_cache = sorted(out)
        return self._ac_files_cache

    def _ac_update(self, text: str) -> None:
        """根据输入末尾的 @<partial> 刷新补全下拉；无则隐藏。"""
        import re
        m = re.search(r'(?:^|\s)@([^\s@]*)$', text)
        if m is None:
            if self._ac_active:
                self._ac_hide()
            return
        partial = m.group(1)
        self._ac_at_pos = m.start() + (0 if text[m.start()] == "@" else 1)
        files = self._ac_file_list()
        pl = partial.lower()
        if pl:
            from pathlib import Path
            matches = [f for f in files if pl in f.lower()]
            matches.sort(key=lambda f: (pl not in Path(f).name.lower(), len(f)))
        else:
            matches = files
        self._ac_options = matches[:10]
        if not self._ac_options:
            self._ac_hide()
            return
        self._ac_index = 0
        self._ac_active = True
        self._render_ac()

    def _render_ac(self) -> None:
        with contextlib.suppress(Exception):
            from rich.text import Text
            popup = self.query_one("#ac_popup", Static)
            t = Text()
            for i, path in enumerate(self._ac_options):
                sel = i == self._ac_index
                line = ("▶ " if sel else "  ") + path
                if i:
                    t.append("\n")
                t.append(line, style="bold #d4875e" if sel else "dim")
            popup.update(t)
            popup.remove_class("hidden")

    def _ac_hide(self) -> None:
        self._ac_active = False
        self._ac_options = []
        with contextlib.suppress(Exception):
            popup = self.query_one("#ac_popup", Static)
            popup.add_class("hidden")
            popup.update("")

    def _ac_consume_key(self, key: str) -> bool:
        """补全激活时处理导航键（PromptArea.on_key 调用）。返回 True=已消费。"""
        if not self._ac_active:
            return False
        if key == "down":
            self._ac_index = (self._ac_index + 1) % len(self._ac_options)
            self._render_ac()
        elif key == "up":
            self._ac_index = (self._ac_index - 1) % len(self._ac_options)
            self._render_ac()
        elif key == "tab":
            self._ac_accept()
        elif key == "escape":
            self._ac_hide()
        else:
            return False
        return True

    def _ac_accept(self) -> None:
        """把选中的文件路径插回输入框：替换末尾的 @<partial> 为 @<path> + 空格。"""
        if not self._ac_options:
            return
        chosen = self._ac_options[self._ac_index]
        with contextlib.suppress(Exception):
            ta = self.query_one("#prompt", TextArea)
            ta.text = ta.text[:self._ac_at_pos] + f"@{chosen} "
            ta.move_cursor(ta.document.end)
        self._ac_hide()

    def _load_pending(self) -> None:
        """进聊天屏时按存档选择结果加载会话（NEW=不加载，沿用 splash 初始化的空会话）。"""
        sid = self.app.pending_load
        if not sid:
            # 新游戏：显示欢迎消息（空状态设计）
            t = self.query_one("#transcript", VerticalScroll)
            t.mount(Static("你好，我是 CTGents。你可以输入消息开始对话，或输入 /help 查看可用指令。",
                           classes="sys-meta", markup=False))
            t.scroll_end(animate=False)
            return
        from .main import _apply_prefix
        from .session import load_session
        self.app.ctx.clear_log()
        self.app.ctx.log.extend(load_session(sid))
        _apply_prefix(self.app.ctx, sid)  # 复用该会话冻结前缀，重载不塌命中
        self.app.session_id = sid
        self.app.final_session_id = sid
        self._echo_conversation()

    # ── 输入 ──
    def action_submit_prompt(self) -> None:
        try:
            ta = self.query_one("#prompt", TextArea)
        except Exception:
            return
        if not ta.has_focus:
            return
        if self._ac_active:           # @ 补全激活：Enter = 接受选中项，不提交本轮
            self._ac_accept()
            return
        text = ta.text.strip()
        # 中断态：回车 = 继续被打断的事（不管带不带指示都算"继续"，统一走 resume）。
        # 带指示 → 接着做并按指示调整；不带 → 纯暂停再续。两者都不开新话题、不读任务状态。
        if self._interrupted and not self._busy:
            ta.clear()
            self._resume_after_interrupt(text)
            return
        if self._busy:
            return  # agent 还在跑：保留正在编辑的内容、不清空也不提交（按 Esc 截停后再发）
        ta.clear()
        if not text:
            return
        self._history.append(text)
        self._history_idx = -1
        self._history_draft = ""
        # 记录本轮时间戳（供 CTGents 头同分钟降噪）+ 重置 header 守卫
        self._last_user_stamp = time.strftime("%H:%M")
        self._agent_header_mounted = False
        self._mount_user_msg(text)
        if text.startswith("/"):
            self._handle_command(text)
        else:
            expanded, attached = _expand_file_mentions(text)   # @path → 附文件内容
            if attached:
                self._mount(f"📎 已附 {len(attached)} 个文件：{', '.join(attached)}", "meta")
            if self._pending_notices:
                expanded = ("【后台作业完成】\n" + "\n\n".join(self._pending_notices)
                            + "\n\n【用户消息】\n" + expanded)
                self._pending_notices.clear()
            self._run_turn(expanded)

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
            from .main import _save_ctx
            self.app.session_id = _save_ctx(self.app.ctx, self.app.session_id)
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
        from .main import _apply_prefix
        from .session import load_session
        self.app.ctx.clear_log()
        self.app.ctx.log.extend(load_session(target))
        _apply_prefix(self.app.ctx, target)  # 复用该会话冻结前缀，重载不塌命中
        self._status_cache = (-1, -1, "")
        self.app.session_id = target
        self.app.final_session_id = target
        from . import status_bar
        from .main import _session_state
        status_bar.reset()
        _session_state["task_reminded"] = False  # 切会话→新会话首轮重提醒未完成任务
        self.query_one("#transcript").remove_children()
        self._turn_count = 0
        self._agent_header_mounted = False
        self._reset_reasoning()
        self._active_collapsible = None
        self._interrupted = self._interrupt_pending = self._hard_kill = False
        self._echo_conversation()
        self._refresh_status()

    def _apply_clear(self, r) -> None:
        from .main import _make_prefix_msgs, _session_state
        self.app.ctx.clear_log()
        self.app.ctx.rebuild_prefix(_make_prefix_msgs())
        _session_state["task_reminded"] = False  # 清空后允许再次提醒
        if r.save:
            self.app.session_id = None
            from . import status_bar
            status_bar.reset()
        self._status_cache = (-1, -1, "")
        self.query_one("#transcript").remove_children()
        self._agent_header_mounted = False
        self._reset_reasoning()
        self._reset_tool_calls()
        self._active_collapsible = None
        self._interrupted = self._interrupt_pending = self._hard_kill = False
        self._mount("上下文已清除", "meta")

    # ── 一轮 agent 驱动 ──
    def _prune_transcript(self) -> None:
        """只保留最近 _echo_max_turns 轮的显示组件，删掉更早的。

        会话越用 transcript 里的 widget 越多 → Textual 重排/渲染越慢（"越用越卡"的真因）。
        只动显示层：ctx.log（喂 LLM 的真上下文）与会话存档不受影响，重载仍可见历史。
        每轮以"用户消息头"为起点；保留最后 keep 个起点及其之后的全部组件。
        """
        keep = self._echo_max_turns
        try:
            t = self.query_one("#transcript", VerticalScroll)
        except Exception:
            return
        children = list(t.children)
        user_idxs = [i for i, w in enumerate(children) if w.has_class("user-msg")]
        if len(user_idxs) <= keep:
            return
        cutoff = user_idxs[len(user_idxs) - keep]  # 第 keep-从后数 个用户头 = 保留起点
        survivor = children[cutoff]
        for w in children[:cutoff]:
            with contextlib.suppress(Exception):
                w.remove()
        # 顶部留一行折叠提示（完整记录仍在会话存档，未受影响）
        with contextlib.suppress(Exception):
            t.mount(Static("⋯ 更早的对话已折叠（完整记录在会话存档）", classes="meta", markup=False),
                    before=survivor)

    def _run_turn(self, text: str) -> None:
        self._prune_transcript()  # 开新一轮前剪掉 3 轮前的旧组件，防越用越卡
        self._busy = True
        self._turn_started = time.monotonic()
        self._refresh_status()  # 即时反馈：状态栏立即闪烁，消除空档
        # 首 token 前挂一条 dim 占位——状态栏已经变了，但 transcript 空白会让用户以为卡了
        t = self.query_one("#transcript", VerticalScroll)
        self._pending_marker = Static("⏳ …", classes="meta", markup=False)
        t.mount(self._pending_marker)
        t.scroll_end(animate=False)
        # 不禁用输入框——禁用会让"按 Esc 后到本轮真正收尾(done)之间"无法打字（中断在下一个
        # LLM 流检查点才生效，工具/网络期间可能好几秒）。保持可输入：用户随时能边跑边写下一句/
        # 截停指令；_busy 守卫负责"跑着时别提交新一轮"。
        self._agent_worker(text)

    @work(thread=True, exclusive=True)
    def _agent_worker(self, text: str) -> None:
        from . import main as _main
        from . import ui
        # 兜底：开屏 ctx 后台还没建好就被点进来发了第一句 → 这里(线程内)补建，保证有 prefix。
        if not self.app.ctx.prefix:
            with contextlib.suppress(Exception):
                self.app.ctx.rebuild_prefix(_main._make_prefix_msgs())
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
            on_reasoning=lambda chunk: ev.append(("reasoning", chunk)),
            on_tool_result=lambda name, result: ev.append(("tool_result", name, result)),
        )
        try:
            self.app.session_id = _main.run_agent_turn(
                self.app.ctx, text, self.app.session_id, display=disp)
            self.app.final_session_id = self.app.session_id
        except Exception as e:  # noqa: BLE001
            ev.append(("error", f"{type(e).__name__}: {e}"))
        finally:
            ev.append(("done",))

    def _clear_pending_marker(self) -> None:
        """首 token 到达或本轮结束：移除"思考中…"占位符。"""
        if self._pending_marker is not None:
            with contextlib.suppress(Exception):
                self._pending_marker.remove()
            self._pending_marker = None

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
            elif kind == "reasoning":
                self._cur_reasoning += rest[0]
                self._reasoning_dirty = True
            else:
                # 非 token/reasoning 事件 = 阶段切换：先封口当前思考/正文（强制全渲）。
                await self._flush_reasoning(transcript, force=True)
                await self._flush_md(transcript)
                if kind == "tool":
                    label, detail = rest
                    self._current_tool = label
                    self._collapse_active()             # 进工具阶段 → 折起上一个思考块
                    await self._ensure_agent_header(transcript)
                    text = f"⏺ {label}  {detail}".rstrip()
                    line = Label(text, classes="tool-call running", markup=False)
                    await transcript.mount(line)
                    self._pending_tool_labels.append((line, text))  # 等结果回来标 ✓
                elif kind == "tool_result":
                    result = rest[1] if len(rest) > 1 else ""
                    renderable, stats = self._render_result(result)
                    # FIFO 配对：最早发起的未完成工具行 → ⏺ 改 ✓、去进行中色、带 diff 统计
                    if self._pending_tool_labels:
                        lbl, text = self._pending_tool_labels.pop(0)
                        done_text = "✓" + text[1:] + (f"  {stats}" if stats else "")
                        with contextlib.suppress(Exception):
                            lbl.update(done_text)
                            lbl.remove_class("running")
                    await transcript.mount(Static(renderable, classes="tool-result", markup=False))
                    self._current_tool = ""
                elif kind == "footer":
                    self._mount_footer(rest[0])
                elif kind == "status":
                    self._mount(rest[0], "meta")
                elif kind == "error":
                    self._clear_pending_marker()
                    self._mount(f"💥 {rest[0]}", "err")
                elif kind == "end":
                    self._clear_pending_marker()
                    self._collapse_active()
                    self._reset_reasoning()
                    self._reset_tool_calls()
                    self._turn_count += 1
                elif kind == "done":
                    self._clear_pending_marker()  # 本轮结束，无论路径，确保占位不残留
                    self._collapse_active()
                    self._reset_tool_calls()
                    self._agent_header_mounted = False
                    self._busy = False
                    # 轮次分隔（全宽线 + 淡入动效）
                    if self._turn_count > 0:
                        try:
                            sep = Static("╌" * 40, classes="turn-sep", markup=False)
                            sep.styles.opacity = 0.0
                            transcript.mount(sep)
                            sep.styles.animate("opacity", 1.0, duration=0.3)
                            transcript.scroll_end(animate=False)
                        except Exception:
                            pass
                    self.query_one("#prompt", TextArea).focus()
                    if self._hard_kill:
                        self._hard_kill = False
                        self._interrupt_pending = False
                        self._mount("──────── ■ 已强制终止本轮 ────────", "brk")
                        self._status_cache = (-1, -1, "")
                    elif self._interrupt_pending:
                        self._enter_interrupted()
                changed = True
        if self._reasoning_dirty:
            await self._flush_reasoning(transcript)
            changed = True
        if self._dirty:
            await self._flush_md(transcript, finalize=False)
            changed = True
        if changed:
            # 用户手动翻看历史时不抢滚，只在底部时才自动跟随
            try:
                if transcript.scroll_y >= transcript.max_scroll_y - 3:
                    transcript.scroll_end(animate=False)
            except Exception:
                transcript.scroll_end(animate=False)

    async def _ensure_agent_header(self, transcript) -> None:
        """每个用户轮在首个 agent 事件（思考/工具/文字，谁先到）挂一次 CTGents 头。

        所有内部循环的思考/工具/输出都归这一个头之下——一轮绝不冒第二个 CTGents。
        """
        if self._agent_header_mounted:
            return
        self._clear_pending_marker()
        now = time.strftime("%H:%M")
        time_part = f"  ·  {now}" if now != self._last_user_stamp else ""
        await transcript.mount(Label(f"CTGents{time_part}", classes="agent-name", markup=False))
        self._agent_header_mounted = True

    def _collapse_active(self) -> None:
        """折起当前展开的思考块（进入下一阶段：工具/输出/新思考时调）。"""
        if self._active_collapsible is not None:
            with contextlib.suppress(Exception):
                self._active_collapsible.collapsed = True
            self._active_collapsible = None

    def _tick_spinner(self) -> None:
        """每 0.1s 转一帧：把所有运行中（未出结果）工具行的首字符换成 spinner 帧。"""
        if not self._pending_tool_labels:
            return
        self._spin_frame = (self._spin_frame + 1) % len(self._SPINNER)
        ch = self._SPINNER[self._spin_frame]
        for lbl, text in self._pending_tool_labels:
            with contextlib.suppress(Exception):
                lbl.update(ch + text[1:])

    @staticmethod
    def _result_block(result: str, max_lines: int = 6, width: int = 100) -> str:
        """工具结果摘成 ⎿ 多行块：首行带 ⎿、其余缩进对齐；超 max_lines 标 … +N 行。"""
        raw = [ln.rstrip() for ln in (result or "").splitlines()]
        while raw and not raw[0].strip():
            raw.pop(0)
        while raw and not raw[-1].strip():
            raw.pop()
        if not raw:
            return "⎿ 完成"
        shown = raw[:max_lines]
        out = []
        for i, ln in enumerate(shown):
            s = ln[:width] + ("…" if len(ln) > width else "")
            out.append(("⎿ " if i == 0 else "  ") + s)
        extra = len(raw) - len(shown)
        if extra > 0:
            out.append(f"  … +{extra} 行")
        return "\n".join(out)

    @staticmethod
    def _render_result(result: str):
        """工具结果 → (可渲染对象, 统计串)。

        含 git diff（编辑工具已带 ```diff 块）→ 彩色 Rich Text（+绿 / -红 / @@青）
        + 统计 (+N −M)，给完成的 ✓ 行用；否则退回纯多行块字符串、无统计。
        """
        if "```diff" in result:
            from rich.text import Text
            body = result.split("```diff", 1)[1].split("```", 1)[0]
            lines = [ln for ln in body.splitlines() if ln.strip()]
            adds = sum(1 for ln in lines if ln.startswith("+"))
            dels = sum(1 for ln in lines if ln.startswith("-"))
            shown = lines[:40]
            text = Text()
            text.append("⎿ 变更", style="dim")
            for ln in shown:
                s = ln[:120]
                style = ("green" if ln.startswith("+") else "red" if ln.startswith("-")
                         else "cyan" if ln.startswith("@@") else "dim")
                text.append("\n  " + s, style=style)
            extra = len(lines) - len(shown)
            if extra > 0:
                text.append(f"\n  … +{extra} 行", style="dim")
            return text, f"(+{adds} −{dels})"
        return ChatScreen._result_block(result), ""

    async def _flush_md(self, transcript, finalize: bool = True) -> None:
        if self._cur_text:
            # 节流：流式期间整段 markdown 重渲很贵，限到 ~10fps；token 已在外层每 tick 攒好，
            # 跳过本次只是晚 ≤100ms 渲（_dirty 保持，下个 tick 再来）。finalize/封口必全渲。
            now = time.monotonic()
            if not finalize and self._cur_md is not None \
                    and (now - self._last_md_render) < self._LIVE_RENDER_INTERVAL:
                return
            self._last_md_render = now
            if self._cur_md is None:
                await self._ensure_agent_header(transcript)
                self._collapse_active()           # 正文开始 → 折起思考块
                self._cur_md = Markdown(self._cur_text, classes="msg-body")
                await transcript.mount(self._cur_md)
            else:
                await self._cur_md.update(self._cur_text)
            self._dirty = False
        if finalize:
            self._cur_md = None
            self._cur_text = ""

    async def _flush_reasoning(self, transcript, force: bool = False) -> None:
        """思考到达即在 CTGents 头下挂折叠 Collapsible（实时展开，下一阶段折起）。"""
        if not self._cur_reasoning:
            return
        if self._reasoning_collapsible is None:
            await self._ensure_agent_header(transcript)
            self._collapse_active()               # 新思考块前先折上一个
            self._reasoning_box = Static(self._cur_reasoning, classes="thinking", markup=False)
            box = Collapsible(self._reasoning_box, title="💭 思考", collapsed=False,
                              collapsed_symbol="▸ ", expanded_symbol="▾ ",
                              classes="collapsible-chat")
            await transcript.mount(box)
            self._reasoning_collapsible = box
            self._active_collapsible = box
        else:
            now = time.monotonic()
            if not force and (now - self._last_reason_render) < self._LIVE_RENDER_INTERVAL:
                return
            self._last_reason_render = now
            self._reasoning_box.update(self._cur_reasoning)
        self._reasoning_dirty = False

    def _reset_reasoning(self) -> None:
        self._cur_reasoning = ""
        self._reasoning_box = None
        self._reasoning_collapsible = None
        self._reasoning_dirty = False

    def _reset_tool_calls(self) -> None:
        """一条消息收尾：清当前工具名 + 待配对工具行（内联行无折叠块要收）。"""
        self._current_tool = ""
        self._pending_tool_labels.clear()

    # ── 辅助 ──
    def _mount(self, text: str, cls: str) -> None:
        t = self.query_one("#transcript", VerticalScroll)
        t.mount(Label(text, classes=cls, markup=False))
        t.scroll_end(animate=False)

    def _mount_user_msg(self, text: str) -> None:
        """用户输入：› 前缀单行块（极简版式，暖色）。submit/续跑/回放共用。"""
        t = self.query_one("#transcript", VerticalScroll)
        t.mount(Static(f"› {text}", classes="user-msg", markup=False))
        t.scroll_end(animate=False)

    def _mount_agent_name(self) -> None:
        """回放：挂一个 CTGents 头（与实时同样式，每个用户轮一次）。"""
        t = self.query_one("#transcript", VerticalScroll)
        stamp = time.strftime("%H:%M")
        t.mount(Label(f"CTGents  ·  {stamp}", classes="agent-name", markup=False))
        t.scroll_end(animate=False)

    def _mount_footer(self, text: str) -> None:
        """每轮收尾小结：弱色一行，直接用 status_bar 的文本。"""
        t = self.query_one("#transcript", VerticalScroll)
        t.mount(Label(text, classes="meta", markup=False))
        t.scroll_end(animate=False)

    def _echo_conversation(self) -> None:
        """加载会话后回放：与实时同结构——每个用户轮一个 CTGents 头，思考折叠 + 文字归其下。

        只显示最近 self._echo_max_turns 轮，超出部分折叠提示。
        """
        t = self.query_one("#transcript", VerticalScroll)
        all_msgs = self.app.ctx.log
        # 找所有 user 消息的位置，只保留最近 N 轮
        user_indices = [i for i, m in enumerate(all_msgs) if m.get("role") == "user"]
        n = self._echo_max_turns
        skip_users = max(0, len(user_indices) - n)
        skip_up_to = user_indices[skip_users] if skip_users > 0 and skip_users < len(user_indices) else 0

        if skip_up_to > 0:
            folded = len([m for m in all_msgs[:skip_up_to] if m.get("role") in ("user", "assistant")])
            t.mount(Static(f"⋯ 省略前 {len(user_indices) - n} 轮（{folded} 条消息）", classes="meta", markup=False))

        agent_named = False   # 一个用户轮只挂一次 CTGents 头（多个 assistant 循环归其下）
        for i, m in enumerate(all_msgs):
            if i < skip_up_to:
                continue
            role = m.get("role")
            content = (m.get("content") or "").strip()
            if role == "user":
                text = _strip_user_wrappers(content)
                if text:
                    self._mount_user_msg(text)
                    agent_named = False
            elif role == "assistant":
                reasoning = (m.get("_reasoning") or "").strip()
                if not (content or reasoning):
                    continue   # 纯 tool_calls 的 assistant（无文字无思考）跳过，不占位
                if not agent_named:
                    self._mount_agent_name()
                    agent_named = True
                if reasoning:
                    self._mount_collapsed("💭 思考", reasoning)
                if content:
                    t.mount(Markdown(content, classes="msg-body"))
                    t.scroll_end(animate=False)
        t.scroll_end(animate=False)

    def _mount_collapsed(self, title: str, body: str) -> None:
        """回放用：挂一个默认折叠的 Collapsible（思考/工具历史，折起不刷屏）。"""
        t = self.query_one("#transcript", VerticalScroll)
        t.mount(Collapsible(Static(body, classes="thinking", markup=False),
                            title=title, collapsed=True,
                            collapsed_symbol="▸ ", expanded_symbol="▾ ",
                            classes="collapsible-chat"))
        t.scroll_end(animate=False)

    def _refresh_status(self) -> None:
        with contextlib.suppress(Exception):
            n_msgs = len(self.app.ctx.all)
            # 把当前模型折进缓存键——切模型不改 n_msgs/sid，不折进去状态栏不会刷新到新模型名。
            try:
                from .llm import get_current_model_name
                model = get_current_model_name()
            except Exception:
                model = ""
            sid_hash = hash((self.app.session_id or "", model))
            if self._status_cache[0] != n_msgs or self._status_cache[1] != sid_hash:
                line = _status_line(self.app.ctx, self.app.session_id or "")
                self._status_cache = (n_msgs, sid_hash, line)
            else:
                line = self._status_cache[2]
            status = self.query_one("#status", Static)
            if self._busy:
                if self._hard_kill:
                    line = f"[red]●[/]  ⏹ 强制终止中…  │  {line}"
                elif self._interrupt_pending:
                    line = f"[yellow]●[/]  ⏹ 截停中…  │  {line}"
                else:
                    dot = "●" if int(time.monotonic() * 2) % 2 == 0 else "○"
                    elapsed = time.monotonic() - self._turn_started
                    tool_info = f"  │  {self._current_tool}" if self._current_tool else ""
                    tool_count = f"  🛠×{len(self._cur_tool_calls)}" if self._cur_tool_calls else ""
                    line = f"[yellow]{dot}[/]  思考中 {elapsed:.0f}s{tool_count}{tool_info}  │  {line}"
                status.remove_class("interrupted")
            elif self._interrupted:
                line = f"[red]●[/]  ⏸ 已中断  │  Enter 继续 · Esc 终止  │  {line}"
                status.add_class("interrupted")
            else:
                line = f"[green]●[/]  {line}"
                status.remove_class("interrupted")
            status.update(line)

    def _drain_jobs(self) -> None:
        try:
            from .tools.exec import drain_finished_jobs
            for notice in drain_finished_jobs():
                self._mount(notice, "meta")
                self._pending_notices.append(notice)
        except Exception:
            pass

    def _refresh_task_panel(self) -> None:
        """长任务 TODO 面板：读 current.md 步骤渲染成顶部常驻清单；无任务则隐藏。

        agent 用 update_plan/create_task 维护 current.md，这里只读渲染（每 0.5s），
        步骤完成/新增立即反映。纯只读，不碰 ctx/缓存。
        """
        try:
            panel = self.query_one("#taskpanel", Static)
        except Exception:
            return
        from .tasks import task_steps
        try:
            steps = task_steps()
        except Exception:
            steps = []
        if not steps:
            panel.add_class("hidden")
            return
        done = sum(1 for icon, _ in steps if icon == "✅")
        lines = [f"📋 任务进度  {done}/{len(steps)}"]
        for icon, label in steps:
            lines.append(f"  {icon} {label[:72]}")
        panel.update("\n".join(lines))
        panel.remove_class("hidden")

    # ── 动作 ──
    def action_interrupt(self) -> None:
        if self._ac_active:           # @ 补全激活：Esc = 关下拉，不中断本轮
            self._ac_hide()
            return
        # 截停中再按 Esc → 升级为强制终止：不等 LLM yield，done 时直接终止。
        if self._interrupt_pending and self._busy:
            self._hard_kill = True
            self._refresh_status()
            return
        # 运行中 → 软中断：截停本轮（让用户觉得是"被你截停"，不是程序断了）。
        if self._busy:
            with contextlib.suppress(Exception):
                from .llm import request_interrupt
                request_interrupt()
            self._interrupt_pending = True
            self._refresh_status()  # 状态栏即时变「⏹ 截停中…」，断点线等 done 收尾再补
            return
        # 已停在中断态 → 再按 Esc = 硬终止本轮。
        if self._interrupted:
            self._terminate_interrupted()
            return
        # 空闲 → 清空输入框。
        self.query_one("#prompt", TextArea).clear()

    def _enter_interrupted(self) -> None:
        """本轮已真正收尾(done) + 之前请求过中断 → 进入中断态：断点线 + 状态栏变文案。"""
        with contextlib.suppress(Exception):
            from .llm import clear_interrupt
            clear_interrupt()  # 卫生：本轮已收尾，确保中断标志不残留拖累下一轮 stream
        self._interrupt_pending = False
        self._interrupted = True
        stamp = time.strftime("%H:%M:%S")
        self._mount(f"──────── ⏸ 推理已暂停，现场已保存 · {stamp} ────────", "brk")
        self._status_cache = (-1, -1, "")
        self._refresh_status()
        with contextlib.suppress(Exception):
            self.query_one("#prompt", TextArea).placeholder = "⏸ 已中断 · Enter 继续 · 输入新消息重开"

    def _resume_after_interrupt(self, instruction: str = "") -> None:
        """中断态下回车：继续被打断的事。instruction 可空。

        关键：续跑指令不能用"工作/任务"这类词——会被理解成"恢复某个长任务"、
        转头去读项目/任务状态而丢掉刚才的对话（用户实测：说了"你好"暂停再续，
        agent 却去读项目状态）。措辞指向"上一条被打断的地方"，纯对话就接着对话、
        真长任务就接着那一步；带指示则在此基础上按指示调整。
        """
        self._exit_interrupted()
        if instruction:
            self._mount_user_msg(instruction)
            self._mount("──────── ▶ 按指示继续 ────────", "brk")
            prompt = (f"（我刚才打断了你一下。现在接着上一条被打断的地方继续，"
                      f"同时按这个来调整：{instruction}）")
        else:
            self._mount("──────── ▶ 继续 ────────", "brk")
            prompt = ("（我刚才打断了你一下，现在继续：从你上一条被打断的地方接着往下，"
                      "不用从头重来、也不用去读项目或任务状态。上一条若已说完，正常等我下一句即可。）")
        self._run_turn(prompt)

    def _terminate_interrupted(self) -> None:
        """中断态下再按 Esc：硬终止本轮（不毁任务文件，只结束这一轮、回到常规态）。"""
        self._exit_interrupted()
        self._mount("──────── ■ 已终止本轮 ────────", "brk")

    def _exit_interrupted(self) -> None:
        self._interrupted = False
        self._interrupt_pending = False
        self._hard_kill = False
        self._status_cache = (-1, -1, "")
        self._refresh_status()
        with contextlib.suppress(Exception):
            self.query_one("#prompt", TextArea).placeholder = "> 输入消息（/help 查看指令）"

    def action_clear_screen(self) -> None:
        with contextlib.suppress(Exception):
            self.query_one("#transcript", VerticalScroll).scroll_end(animate=False)

    def action_arrow_up(self) -> None:
        if self._busy or not self._history:
            return
        inp = self.query_one("#prompt", TextArea)
        if self._history_idx == -1:
            self._history_draft = inp.text
            self._history_idx = len(self._history) - 1
        elif self._history_idx > 0:
            self._history_idx -= 1
        else:
            return
        inp.text = self._history[self._history_idx]

    def action_arrow_down(self) -> None:
        if self._busy or not self._history:
            return
        inp = self.query_one("#prompt", TextArea)
        if self._history_idx == -1:
            return
        if self._history_idx < len(self._history) - 1:
            self._history_idx += 1
            inp.text = self._history[self._history_idx]
        else:
            self._history_idx = -1
            inp.text = self._history_draft
            self._history_draft = ""

    def action_copy_text(self) -> None:
        """复制选中文本，出错不回退 UI（防某些 widget 渲染异常导致崩溃）。"""
        try:
            from textual.actions import SkipAction
        except ImportError:
            return
        try:
            super().action_copy_text()
        except SkipAction:
            return  # 没选中文本→正常跳过，不给用户报错
        except Exception:  # noqa: BLE001
            import logging
            logging.getLogger("tui").warning("复制选中文本失败", exc_info=True)
            self._mount("复制失败（选中区域不支持）", "err")


# ═══════════════════════════════════════════════════════
# App
# ═══════════════════════════════════════════════════════
class CTGentsApp(App):
    """多屏协调 + Dark Pro 主题。共享状态（ctx/session_id/sessions）挂在 App 上，各屏经 self.app 取。"""

    # 退出用 ctrl+q（priority，输入框聚焦时也生效）；ctrl+c 留给 Textual 内置复制选中文本。
    BINDINGS = [Binding("ctrl+q", "quit", "退出", priority=True)]

    def __init__(self, ctx: CacheContext, session_id: str | None,
                 sessions: list[str] | None = None):
        super().__init__()
        self.ctx = ctx
        self.session_id = session_id
        self.sessions = sessions or []
        self.final_session_id = session_id
        self.pending_load: str | None = None

    def on_mount(self) -> None:
        self.register_theme(DARK_THEME)
        self.theme = "dark-pro"
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
