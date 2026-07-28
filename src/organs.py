"""器官生命体征 — 只读派生 CTGents 各内部机制的"上次跳动"。

为什么派生产物、不撒探针：push 式探针(record_organ_fire)放在器官触发点，
若那段代码本身是死路，探针同样不会跑——抓不到"声明了却没接线"这类衰竭。真相在**产物**：
0 个文件就是 0 个，不管代码怎么声称。本模块自动化"查声明的机制先看产物文件在不在"
那一步：扫每个器官的真实落盘产物的 mtime / 数量，跨目录共用文件 mtime 作统一时间轴
(同一时钟可比，不必解析时间戳)。

纯诊断只读：不写盘、不控制、不碰前缀缓存、不拉 LLM。新器官长出新产物时，在 census()
里加一行即可（不强求，漏了就是该器官在体征表里"缺席"——缺席本身也是信号）。
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .config import MEMORY_DIR, SESSION_DIR
from .paths import STATS_DIR

_ROOT = Path(__file__).resolve().parent.parent
_STATS_DIR = STATS_DIR
_MEMORY = Path(MEMORY_DIR)
_SESSIONS = Path(SESSION_DIR)


@dataclass
class OrganVital:
    """一个器官的体征：名/职责/最近产物时间/产物量/距上次跳动几个会话。"""

    name: str
    role: str
    last: float | None  # 最近产物 mtime；None = 从无产物
    count: int  # 产物数量 / 事件量
    sessions_ago: int  # 距上次跳动几个会话（0=最近会话；-1=从未）
    note: str = ""


def _mtimes(paths) -> list[float]:
    out: list[float] = []
    for p in paths:
        with contextlib.suppress(OSError):
            out.append(p.stat().st_mtime)
    return out


def _glob(d: Path, pattern: str) -> list[Path]:
    return list(d.glob(pattern)) if d.is_dir() else []


def _session_mtimes() -> list[float]:
    """会话心跳时间轴：sessions/ 下每个会话存档的 mtime（降序）。"""
    entries = list(_SESSIONS.iterdir()) if _SESSIONS.is_dir() else []
    return sorted(_mtimes(entries), reverse=True)


def _sessions_ago(last: float | None, timeline: list[float]) -> int:
    """有多少个会话比该器官最近一次产物更新——即"几个会话没跳了"。

    本会话退出时收尾产物写在 save_session 之后，mtime 更大，故
    本会话跳过的器官 sessions_ago=0。
    """
    if last is None:
        return -1
    return sum(1 for s in timeline if s > last)


def _memory_by_type(mem_type: str, exclude_prefix: str | None = None) -> list[Path]:
    """memory/ 下 frontmatter type 匹配的记忆文件（可排除某文件名前缀）。"""
    from .tools.memory import _split_frontmatter

    out: list[Path] = []
    for p in _glob(_MEMORY, "*.md"):
        if p.name == "MEMORY.md":
            continue
        if exclude_prefix and p.name.startswith(exclude_prefix):
            continue
        try:
            meta, _ = _split_frontmatter(p.read_text(encoding="utf-8"))
        except OSError:
            continue
        if meta.get("type") == mem_type:
            out.append(p)
    return out


def census() -> list[OrganVital]:
    """派生所有可观测器官的体征。只读。"""
    timeline = _session_mtimes()

    def vital(name: str, role: str, paths: list[Path],
              note: str = "", count: int | None = None) -> OrganVital:
        mt = _mtimes(paths)
        last = max(mt) if mt else None
        c = count if count is not None else len(mt)
        return OrganVital(name, role, last, c, _sessions_ago(last, timeline), note)

    # 已退役 reflection 文件可能仍残留在用户 stats/ 中，不把历史遗留误算为缓存统计。
    cache_files = [p for p in _glob(_STATS_DIR, "*.json")
                   if not p.name.endswith("_reflection.json")]
    strat = _memory_by_type("strategy")

    return [
        vital("工具追踪", "记录每次工具调用耗时/成败（tracker，唯一已插仪表的器官）",
              _glob(_STATS_DIR, "*_tools.jsonl")),
        vital("缓存统计", "每会话 prompt / cache hit-miss 用量",
              cache_files),
        vital("用户档案", "越用越懂你：记忆 type=user（agent 显式 remember，前缀全文注入）",
              [_MEMORY / "user-profile.md"],
              note="自动 harvest 已删——靠 agent 显式 remember 更新，久未跳=期间无更新非衰竭"),
        vital("策略记忆", "agent 显式 remember 的 type=strategy 记忆（每轮注入前缀）",
              strat, count=len(strat)),
        vital("会话存档", "会话落盘（save_session）",
              list(_SESSIONS.iterdir()) if _SESSIONS.is_dir() else []),
    ]


def _ago_str(mt: float | None) -> str:
    if mt is None:
        return "无产物"
    delta = datetime.now(UTC) - datetime.fromtimestamp(mt, UTC)
    if delta.days > 0:
        return f"{delta.days}天前"
    if delta.seconds >= 3600:
        return f"{delta.seconds // 3600}小时前"
    return f"{max(delta.seconds // 60, 0)}分钟前"


def render_census() -> str:
    """渲染体征表（按最久未跳动排前，一眼看出哪个器官衰竭）。"""
    organs = census()
    n_sessions = len(_session_mtimes())

    def sortkey(o: OrganVital) -> int:
        return 10 ** 9 if o.sessions_ago < 0 else o.sessions_ago

    lines = ["器官生命体征（派生自产物落盘，非探针；只读）", ""]
    for o in sorted(organs, key=sortkey, reverse=True):
        if o.sessions_ago < 0:
            mark, when = "🔴", "从未"
        elif o.sessions_ago == 0:
            mark, when = "🟢", "最近会话"
        elif o.sessions_ago <= 3:
            mark, when = "🟡", f"{o.sessions_ago} 会话前"
        else:
            mark, when = "🔴", f"{o.sessions_ago} 会话前"
        lines.append(f"{mark} {o.name}  上次跳动: {when}（{_ago_str(o.last)}）  量: {o.count}")
        lines.append(f"     {o.role}")
        if o.note:
            lines.append(f"     ⚠ {o.note}")
    lines.append("")
    lines.append(f"心跳基准: 已知 {n_sessions} 个会话存档。"
                 "🟢最近会话 / 🟡近 3 会话 / 🔴久未跳动或从未。"
                 "口径=最近产物 mtime vs 会话存档 mtime 时间轴。")
    with contextlib.suppress(Exception):
        from .work_receipts import format_work_status

        lines.extend(["", format_work_status()])
    return "\n".join(lines)
