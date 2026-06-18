"""终端 UI 渲染层 — 集中聊天界面的样式，让 REPL 看着舒服。

设计约束（与 [[ctgents-context-cache]] / status_bar 一脉）：
- 纯展示层：绝不碰 agent / 缓存 / process_turn / 落盘，无状态。
- 非 TTY（管道 / 重定向 / 测试 / eval harness）一律退回裸 print，输出与
  改造前逐字节一致——不污染被脚本消费的输出。
- 轻量不抛：调用方已有 try 包裹，这里也尽量不抛，UI 永不拖垮 REPL。

流式与原子块都走同一个 rich Console（自动在 Windows 启用 VT 颜色），风格统一；
流式 token 用 soft_wrap=True 让终端自己折行、不重排已打印内容。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

# 启动时一次性判定：是否真接在终端上。非 TTY 时全部退回裸 print。
_IS_TTY = sys.stdout.isatty()
_console = None


@dataclass
class Display:
    """一轮 agent 驱动的【输出去向】。

    run_agent_turn 是唯一咽喉（对话/任务分支 + 审计都在里头），不能让 TUI 另起
    一套循环（会重蹈多入口绕审计的覆辙）。所以把输出抽成可注入的 Display：
    REPL 默认走 stdout，TUI 传一个写进 widget 的实例，循环本身一字不改。
    """

    make_display: Callable[[], tuple[Callable[[str], None], Callable[[], bool]]]
    on_tool: Callable[[str, dict], None]
    on_status: Callable[[str], None]   # 任务续跑的状态行
    on_footer: Callable[[str], None]   # 每轮收尾小结
    end_message: Callable[[], None]    # 一条消息流完后的收尾（stdout 下=换行）


def _c():
    """惰性单例 rich Console（仅 TTY 路径用到）。"""
    global _console
    if _console is None:
        from rich.console import Console
        _console = Console()
    return _console


def banner(text: str) -> None:
    """启动横幅。"""
    if not _IS_TTY:
        print(text)
        return
    from rich.panel import Panel
    _c().print(Panel(text, border_style="cyan", expand=False))


def agent_display():
    """返回 (on_token, has_output)：流式打印 agent 回复，带角色徽标 + 配色。

    非 TTY 时与改造前逐字节一致：'Agent: ' 前缀 + 裸 token 流。
    """
    started = False

    def on_token(token: str) -> None:
        nonlocal started
        if not started:
            if _IS_TTY:
                _c().print()
                _c().print("● Agent", style="bold cyan")
            else:
                sys.stdout.write("Agent: ")
                sys.stdout.flush()
            started = True
        if _IS_TTY:
            _c().print(token, end="", markup=False, highlight=False, soft_wrap=True)
        else:
            sys.stdout.write(token)
            sys.stdout.flush()

    def has_output() -> bool:
        return started

    return on_token, has_output


def tool_call(label: str, detail: str) -> None:
    """工具调用行（暗色，弱化存在感，不抢 agent 正文）。"""
    if not _IS_TTY:
        print(f"  [{label}] {detail}")
        return
    text = f"  → {label}" + (f"  {detail}" if detail else "")
    _c().print(text, style="dim", markup=False, highlight=False)


def recent_history(pairs: list[tuple[str, str]]) -> None:
    """会话加载时回显最近几轮。pairs=[(role_label, content), ...]。"""
    if not _IS_TTY:
        print("─" * 40)
        for role, content in pairs:
            print(f"{role}: {content}")
        print("─" * 40)
        return
    from rich.rule import Rule
    _c().print(Rule(style="dim"))
    for role, content in pairs:
        style = "bold green" if role == "You" else "bold cyan"
        _c().print(role, style=style, end="")
        _c().print(f"  {content}", markup=False, highlight=False)
    _c().print(Rule(style="dim"))


def prompt_message():
    """输入提示符的 formatted text（'You: '）。非 TTY 返回纯字符串。"""
    if not _IS_TTY:
        return "You: "
    from prompt_toolkit.formatted_text import HTML
    return HTML("<ansigreen><b>You</b></ansigreen>: ")
