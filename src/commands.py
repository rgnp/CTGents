"""指令系统。结构化注册：提供 name/description/usage/handler 即可。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .cache_context import CacheContext
from .config import SESSION_DIR
from .session import delete_session, get_session_name, list_sessions

if TYPE_CHECKING:
    from collections.abc import Callable

# ═══════════════════════════════════════════════════════════════
# 数据结构
# ═══════════════════════════════════════════════════════════════

@dataclass
class CmdResult:
    message: str = ""
    exit: bool = False
    save: bool = False
    clear: bool = False
    load: str = ""
    retry: bool = False


@dataclass
class Command:
    """指令描述。提供这几个字段，系统自动处理帮助和分发。"""

    name: str
    description: str = ""
    usage: str = ""
    handler: Callable[[CmdResult, CacheContext, list[str], str | None], None] | None = None


# 内部注册表
_registry: list[Command] = []
_handlers: dict[str, Callable] = {}


def _add_cmd(cmd: Command) -> None:
    _registry.append(cmd)
    if cmd.handler:
        _handlers[cmd.name] = cmd.handler
        if cmd.name.startswith("/") and len(cmd.name) > 1:
            _handlers.setdefault(cmd.name[1:], cmd.handler)


# ── 给内置命令用的装饰器 ──

def builtin(name: str, description: str = "", usage: str = ""):
    def deco(fn):
        _add_cmd(Command(name=name, description=description, usage=usage, handler=fn))
        return fn
    return deco


def builtin_multi(names: list[str], description: str = "", usage: str = ""):
    def deco(fn):
        for name in names:
            _add_cmd(Command(name=name, description=description, usage=usage, handler=fn))
        return fn
    return deco


def command_completions() -> list[tuple[str, str]]:
    """[(主名, 描述)] 去重列表（同 handler 的别名合并、取首个），供 TUI / 命令补全下拉用。"""
    seen: set[int] = set()
    out: list[tuple[str, str]] = []
    for cmd in _registry:
        hid = id(cmd.handler)
        if hid in seen:
            continue
        seen.add(hid)
        out.append((cmd.name, cmd.description))
    return out

# ═══════════════════════════════════════════════════════════════
# 内置指令
# ═══════════════════════════════════════════════════════════════

@builtin_multi(["/exit", "/quit", "/q"], description="退出程序")
def _cmd_exit(r: CmdResult, _ctx, _args, _sid) -> None:
    r.exit = True


@builtin_multi(["/help", "/h", "/?"], description="显示指令列表")
def _cmd_help(r: CmdResult, _ctx, _args, _sid) -> None:
    # 按 handler 去重，同 handler 的别名合并显示
    seen: dict[int, list[Command]] = {}
    for cmd in _registry:
        hid = id(cmd.handler)
        seen.setdefault(hid, []).append(cmd)

    # 核心命令（优先展示）vs 进阶命令
    _core = {"/exit", "/help", "/clear", "/sessions", "/load", "/new", "/model"}

    def _make_cmd_block(groups):
        lines = []
        for group in sorted(groups, key=lambda g: g[0].name):
            primary = group[0]
            aliases = [c.name for c in group[1:]]
            name_display = f"{primary.name}（{'、'.join(aliases)}）" if aliases else primary.name
            lines.append(f"  {name_display:<20} {primary.description}")
            if primary.usage:
                lines.append(f"  {'':<20} 用法: {primary.usage}")
        return lines

    all_groups = list(seen.values())
    core = [g for g in all_groups if g[0].name in _core]
    advanced = [g for g in all_groups if g[0].name not in _core]

    lines = ["指令列表：\n"]
    lines.append("── 常用 ──")
    lines.extend(_make_cmd_block(core))

    lines.append("")
    lines.append("── 进阶 — 上下文管理 · 工具 · 诊断 ──")
    lines.extend(_make_cmd_block(advanced))

    lines.append("")
    lines.append("── 快捷键（聊天界面）──")
    lines.append("  Esc      中断/取消")
    lines.append("  Ctrl+Q   退出程序")
    lines.append("  Ctrl+↑/↓ 翻历史消息")
    lines.append("  Ctrl+R   回看本会话全部历史")
    lines.append("  Ctrl+Y   复制最后一个代码块")
    lines.append("  Ctrl+L   滚到底部")
    lines.append("  Enter    发送消息")
    lines.append("")
    lines.append("提示：输入 / 开头的消息被视为指令，否则直接与 agent 对话。")
    r.message = "\n".join(lines)


@builtin_multi(["/clear", "/c"], description="清除对话上下文")
def _cmd_clear(r: CmdResult, ctx, _args, _sid) -> None:
    ctx.clear_log()
    r.save = True
    r.clear = True
    r.message = "上下文已清除"


@builtin_multi(["/delete", "/rm"], description="删除历史会话", usage="/delete <编号>")
def _cmd_delete(r: CmdResult, _ctx, args, _sid) -> None:
    if not args:
        r.message = "用法: /delete <编号>"
        return
    sessions = list_sessions()
    try:
        idx = int(args[0]) - 1
        if 0 <= idx < len(sessions):
            sid = sessions[idx]
            if sid == _sid:
                r.message = "不能删除当前会话，请先 /new 或 /load 切换到其他会话"
                return
            name = get_session_name(sid)
            delete_session(sid)
            r.message = f"已删除会话: {name}"
        else:
            r.message = f"无效编号，共 {len(sessions)} 个会话"
    except ValueError:
        r.message = f"无效编号: {args[0]}"


@builtin_multi(["/sessions", "/ls"], description="列出历史会话")
def _cmd_sessions(r: CmdResult, _ctx, _args, _sid) -> None:
    sessions = list_sessions()
    if not sessions:
        r.message = "没有历史会话"
        return
    lines = ["历史会话："]
    for i, sid in enumerate(sessions, 1):
        name = get_session_name(sid)
        try:
            sp = os.path.join(SESSION_DIR, sid, "summary.txt")
            with open(sp, encoding="utf-8") as f:
                preview = f.read()[:50].replace("\n", " ")
        except Exception:
            preview = ""
        marker = "← 当前" if sid == _sid else ""
        display = name if name != sid else sid
        lines.append(f"  [{i}] {display}  {marker}")
        if preview:
            lines.append(f"         {preview}")
    r.message = "\n".join(lines)


@builtin_multi(["/rename", "/name"], description="给当前会话命名（存档列表更好认）",
               usage="/rename <名字>")
def _cmd_rename(r: CmdResult, _ctx, args, _sid) -> None:
    if not _sid:
        r.message = "当前还没有会话（先发一条消息，会话自动创建后再命名）"
        return
    name = " ".join(args).strip()
    if not name:
        r.message = "用法: /rename <名字>"
        return
    from .session import save_session_name
    save_session_name(_sid, name)
    r.message = f"已把当前会话命名为「{name}」"


@builtin("/load", description="切换会话", usage="/load <编号>")
def _cmd_load(r: CmdResult, _ctx, args, _sid) -> None:
    if not args:
        r.message = "用法: /load <编号>"
        return
    sessions = list_sessions()
    try:
        idx = int(args[0]) - 1
        if 0 <= idx < len(sessions):
            r.load = sessions[idx]
            r.save = True
            r.message = f"切换到: {get_session_name(r.load)}"
        else:
            r.message = f"无效编号，共 {len(sessions)} 个会话"
    except ValueError:
        r.message = f"无效编号: {args[0]}"


@builtin("/new", description="新建会话（自动保存当前）")
def _cmd_new(r: CmdResult, _ctx, _args, _sid) -> None:
    r.save = True
    r.clear = True



# ═══════════════════════════════════════════════════════════════
# 模型指令
# ═══════════════════════════════════════════════════════════════

@builtin("/model", description="查看/切换 LLM 模型", usage="/model [pro|flash]")
def _cmd_model(r: CmdResult, _ctx, args, _sid) -> None:
    from .llm import get_current_model_name, list_models, switch_model
    if not args:
        current = get_current_model_name()
        r.message = f"当前模型: {current}\n" + list_models()
        return
    ok, msg = switch_model(args[0])
    r.message = msg
    if ok:
        r.save = True


# ═══════════════════════════════════════════════════════════════
# 状态指令
# ═══════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════
# 上下文诊断指令
# ═══════════════════════════════════════════════════════════════

@builtin("/context", description="上下文诊断：前缀/对话/尾部注入 + 缓存命中率；/context prefix 打印前缀全文")
def _cmd_context(r: CmdResult, ctx, _args, _sid) -> None:
    """精简版：前缀缓存结构 + 尾部注入清单 + API 命中率。

    `/context prefix` → 打印前缀每条消息的全文（肉眼核实冻结进前缀的确切内容，
    如 AGENTS.md / 记忆索引含用户画像 / 长期目标），默认仍只显示分块大小摘要。
    """
    from .config import MAX_CONTEXT_TOKENS
    from .llm import _live_context_tokens
    from .params import CONTEXT
    from .tools.tokens import count_context_tokens

    if not hasattr(ctx, 'all'):
        r.message = "需要 CacheContext。"
        return

    if _args and _args[0].lower().startswith("prefix"):
        _dump_prefix_content(r, ctx)
        return

    all_msgs = ctx.all
    log_msgs = ctx.log
    # 主指标 = 实际发送体积（折叠后），舒适区真正管的就是这个；原文累计/上限作参考。
    live_tokens = _live_context_tokens(ctx)
    raw_tokens = count_context_tokens(all_msgs)   # 折叠前、含工具 schema(~5600)
    low, high = CONTEXT.comfort_zone_low, CONTEXT.comfort_zone_high

    if live_tokens >= high:
        status = "⚠️ 超舒适区（将触发有损摘要拉回）"
    elif live_tokens >= low:
        status = "✅ 舒适区"
    else:
        status = "🟢 充裕"
    saved = max(0, raw_tokens - live_tokens)

    lines = [
        "══ 上下文诊断 ══",
        "",
        f"  发送体积: {live_tokens:,}  舒适区 [{low:,}–{high:,}]  {status}",
        f"  原文累计: {raw_tokens:,} / 上限 {MAX_CONTEXT_TOKENS:,}"
        + (f"（折叠省 {saved:,}）" if saved else ""),
        f"  消息:    {len(all_msgs)} 条",
        "",
        "── 前缀（始终命中）──",
    ]

    _append_prefix_section(lines, ctx)
    _append_log_section(lines, log_msgs)
    _append_cache_section(lines, ctx, _sid)

    r.message = "\n".join(lines)


def _dump_prefix_content(r: CmdResult, ctx) -> None:
    """打印缓存前缀每条消息的全文——让用户肉眼核实到底有什么被冻结进了前缀。

    默认 /context 只给分块大小摘要（_append_prefix_section）；这里给全文，按需调用。
    """
    if not getattr(ctx, "prefix", None):
        r.message = "前缀为空（新会话尚未构建前缀，或无 AGENTS.md/记忆/长期目标）。"
        return
    from .tools.tokens import estimate_tokens
    out = ["══ 前缀全文（会话开始冻结、始终命中）══"]
    for i, m in enumerate(ctx.prefix):
        label = m.get("_label", "前缀")
        content = m.get("content", "")
        out.append("")
        out.append(f"── [{i + 1}] {label}  {len(content):,} 字符 ~{estimate_tokens(content):,} tok ──")
        out.append(content)
    out.append("")
    out.append(f"哈希: {ctx.prefix_hash}")
    r.message = "\n".join(out)


def _append_prefix_section(lines: list[str], ctx) -> None:
    """在前缀段追加 prefix 内容清单。

    标签取每条消息自带的 _label（在 main._make_*_message 处定义）——不写死位置,
    前缀构成变了(增删某条)也不会错位(对照:曾因删日期、位置标签整体错位一格)。
    """
    from .tools import get_tools
    from .tools.tokens import estimate_tokens, tools_schema_tokens
    for i, m in enumerate(ctx.prefix):
        content = m.get("content", "")
        label = m.get("_label", "前缀")
        lines.append(f"  [{i + 1}] {label:<16} {len(content):,} 字符  ~{estimate_tokens(content):,} tok")
    # 工具 schema 也是每轮必发、被缓存的稳定前缀(不在 messages 里、但计入 prompt_tokens)
    lines.append(f"  [tools] 工具 schema      ({len(get_tools())} 个)        ~{tools_schema_tokens():,} tok")
    lines.append(f"  哈希: {ctx.prefix_hash}")


def _append_log_section(lines: list[str], log_msgs: list[dict]) -> None:
    """在对话段追加非 system 角色的消息计数。"""
    roles: dict[str, int] = {}
    for m in log_msgs:
        if m.get("role") != "system":
            roles[m["role"]] = roles.get(m["role"], 0) + 1
    lines.append("")
    lines.append("── 对话体（旧轮命中 / 新轮 miss）──")
    for role in ("user", "assistant", "tool"):
        n = roles.get(role, 0)
        bar = "█" * min(n, 40) if n else "—"
        lines.append(f"  {role:<10} {n:>3} 条  {bar}")


def _append_cache_section(lines: list[str], ctx, _sid: str | None) -> None:
    """在 API 缓存段追加命中率 + miss 归因 + 每请求明细。无请求数据则跳过。

    miss 三块归因让人一眼看到钱花哪（尾部为每请求实测、非估算）：
      冷启动   = 首请求无缓存的非尾部部分（一次性，命中≈0 才算）
      尾部注入 = 各请求实际尾部 token 之和（工具循环 skip_volatile 的请求记 0）
      对话增量 = 残差（工具结果/读文件/生成）
    调用结构 = 轮首(带尾) vs 循环内(跳尾) 的请求数与 miss 分布（架构视角）。
    每请求明细揪「突刺」并给取证判词：前沿哈希变→旧消息被改写(我们 bug)；
    payload 稳而命中塌→服务端淘汰；间隔大→疑 TTL。靠 _payload_fingerprint 的 n/fe/g。
    """
    from .llm import get_cache_stats
    cache = get_cache_stats(_sid)
    t = cache.get("total", {}) if isinstance(cache, dict) else {}
    reqs = t.get("requests", 0)
    if reqs == 0:
        lines.append("")
        lines.append("── API 缓存（本会话累计）──")
        lines.append("  本会话暂无 API 请求记录（先问一句再看）。")
        return
    prompt = t.get("prompt_tokens", 0)
    hit = t.get("cache_hit_tokens", 0)
    miss = prompt - hit
    hit_pct = hit / prompt * 100 if prompt > 0 else 0

    lines.append("")
    lines.append("── API 缓存（本会话累计）──")
    lines.append(f"  请求:    {reqs} 次")
    bar_len = 22
    hit_bars = int(bar_len * hit_pct / 100)
    bar = "█" * hit_bars + "░" * (bar_len - hit_bars)
    lines.append(f"  本会话命中率:  {bar}  {hit_pct:.1f}%")
    lines.append(f"           (命中 {hit:,} / 输入 {prompt:,} tok)")
    lines.append(f"  miss 合计: {miss:,} tok")
    completion = t.get("completion_tokens", 0)
    if completion:
        lines.append(f"  输出合计: {completion:,} tok")

    history = cache.get("models", {}).get("pro", {}).get("history", []) or []
    _append_fingerprint_summary(lines, history)

    # ── miss 归因（逐请求拆分，不在聚合层估算）──
    # 尾部在 payload 末尾：一条只要有 miss，尾部整段必落在 miss 区，故 tail_miss=min(t,miss)；
    # 残差=对话增量（工具输出/读文件/生成，新内容只付一次）。首请求大面积未命中=冷启动(一次性)。
    cold = tail_total = body = isolated = 0
    prev = None
    for idx, e in enumerate(history):
        mi = e.get("p", 0) - e.get("h", 0)
        if _is_isolated_single_shot(e, prev):  # 会话收割等独立上下文，必 miss、非主会话冷启动
            isolated += mi
            prev = e
            continue
        if idx == 0 and e.get("h", 0) < e.get("p", 0) * 0.5:  # 首请求大半没命中 = 冷启动
            cold += mi
            prev = e
            continue
        ti = min(e.get("t", 0), mi)
        tail_total += ti
        body += mi - ti
        prev = e
    n_tail = sum(1 for e in history if e.get("t", 0) > 0)
    lines.append("")
    lines.append("  miss 归因:")
    if cold:
        lines.append(f"    冷启动    {cold:>9,}  (首请求无缓存，一次性)")
    if isolated:
        lines.append(f"    隔离单发  {isolated:>9,}  (会话收割等独立上下文调用，必 miss、一次性)")
    lines.append(f"    尾部注入  {tail_total:>9,}  ({n_tail}/{len(history)} 请求带尾部，循环内其余跳过)")
    lines.append(f"    对话增量  {body:>9,}  (工具输出/读文件/生成，新内容只付一次)")

    # ── 每请求明细（揪突刺 + 取证）──
    if history:
        shown = history[-15:]
        base = len(history) - len(shown)
        lines.append("")
        lines.append("  每请求明细（揪突刺 + 取证）:")
        prev = history[base - 1] if base > 0 else None
        for i, e in enumerate(shown, start=base + 1):
            p, h = e.get("p", 0), e.get("h", 0)
            m = p - h
            pct = h / p * 100 if p else 0
            kind = "T" if e.get("t", 0) > 0 else "L"
            c = e.get("c")
            out = f" 出{c:>5,}" if c is not None else ""
            ch = e.get("ch", 0)
            extra = f" ch{ch:>7,}" + (f" g{e.get('g', 0)}s n{e.get('n', 0)}" if "fe" in e else "")
            if e.get("th_chg"):
                extra += " ⚠tools变"  # 工具表变了→前缀整体作废，突刺锅在 tools 不在 messages
            # 指纹变=换后端节点(那台没我们的缓存)，这次冷命中是路由所致、非淘汰
            if prev is not None and prev.get("fp") and e.get("fp") and e.get("fp") != prev.get("fp"):
                extra += f" ⚠换节点→{_fp_short(e.get('fp'))}"
            verdict = _spike_verdict(e, prev, p, h, pct)
            lines.append(
                f"    #{i:<3}[{kind}] 输入{p:>7,} 命中{h:>7,} miss{m:>7,} ({pct:>3.0f}%)"
                f"{out}{extra}{verdict}"
            )
            prev = e
        tail_note = f"仅显示最近 {len(shown)}/{len(history)} 次，" if base > 0 else ""
        lines.append(f"    （{tail_note}T=轮首带尾 L=循环内跳尾）")
        lines.append(
            "    ch=发送字符数  ·  取证: ▲前沿变=旧消息被改写 · ⚡服务端吃掉=发过的前缀仍 miss"
            " · ⚠换节点=路由到别的后端(那台无缓存) · 新内容=miss 在新后缀(预期内)"
        )
    else:
        lines.append("")
        lines.append("  每请求明细：本次更新后开始记录（旧会话仅有汇总）。")


def _fp_short(fp: str | None) -> str:
    """把 system_fingerprint 缩成可读短码：fp_9954b31ca7_prod… → 9954b31ca7。"""
    if not fp:
        return "—"
    s = fp[3:] if fp.startswith("fp_") else fp
    return (s.split("_", 1)[0] or fp)[:12]


def _append_fingerprint_summary(lines: list[str], history: list[dict]) -> None:
    """节点指纹(system_fingerprint)汇总：几个节点、切换几次。

    DeepSeek 按 user_id 命名空间在某台后端节点上存 KV 缓存；指纹变=请求被路由到
    另一台机器，那台没有我们的缓存 → 该次必冷命中。区分"换节点"与"被淘汰"(同节点
    但缓存被 LRU 清掉)是定位低命中的关键。DeepSeek 有时不返回指纹，此时这段不显示。
    """
    fps = [e.get("fp", "") for e in history if e.get("fp")]
    if not fps:
        return
    switches = sum(1 for a, b in zip(fps, fps[1:], strict=False) if a != b)
    uniq = len(set(fps))
    if uniq <= 1:
        lines.append(f"  节点指纹: 全程同一节点 {_fp_short(fps[-1])}（无换节点）")
    else:
        lines.append(f"  节点指纹: {uniq} 个节点、切换 {switches} 次 ⚠（换节点处必冷命中）")


def _is_isolated_single_shot(e: dict, prev: dict | None) -> bool:
    """隔离单发调用（如会话收割：system + 单条 user、tools=None、独立上下文）。

    这类调用走同一 record() 落进同一统计流，但它是另一份上下文：全新前缀服务端
    没见过，必然 0% 命中。判据 = payload 只 1~2 条非 sys 消息(n<=2) 且上一条是大对话
    (prev.n>=10)。主对话不可能从几百条消息塌回 1 条再弹回——压缩也只缩到几十条、不会到 2。
    区别于真·主会话冷启动(首请求、prev 为 None)，免得把合法隔离调用喊成"惊天异常"。
    """
    return e.get("n", 99) <= 2 and prev is not None and prev.get("n", 0) >= 10


def _spike_verdict(e: dict, prev: dict | None, p: int, h: int, pct: float) -> str:
    """突刺取证判词，靠 lcpr（与上条 payload 公共前缀比 = 本该命中的上限）定因：

      命中≈0 / 首请求大半没命中 → 冷启动（一次性）。
      前沿哈希变               → 靠前旧消息被改写（我们的 bug）。
      实命中率 << lcpr         → 我们发过、缓存过的前缀被服务端吃了（答"纯追加为何命中降"）。
      实命中率 ≈ lcpr 但偏低    → miss 全在新追加后缀（工具输出/读文件），预期内、非异常。
    旧格式 history（无 lcpr）退回 fe/间隔 粗判。命中率≥70% 视作健康、不标。
    """
    if _is_isolated_single_shot(e, prev):  # 独立上下文(评分者等)，必 miss、非主会话冷启动
        return "  隔离单发(独立上下文·必miss·一次性)"
    if prev is None and pct < 70:  # 真·首请求冷启动（无 prev，不可能是换节点）
        return "  冷启动"
    # 换节点优先于"同节点淘汰"：指纹变了→请求被路由到另一台后端节点，那台没我们的缓存→
    # 低/零命中是路由所致，不是淘汰、也不是改写。比"冷启动/服务端吃掉"更具体，故先判。
    if (prev is not None and prev.get("fp") and e.get("fp")
            and e.get("fp") != prev.get("fp") and pct < 90):
        return f"  ⚠换节点(路由到 {_fp_short(e.get('fp'))}、该节点无此缓存)"
    if h <= p * 0.05:  # 同节点（或无指纹）但命中≈0 = 缓存被淘汰，冷
        return "  冷启动(缓存被淘汰)"
    # 前沿（最早 3 条非 sys）变 = 旧消息被改写。但前 3 条尚未填满时 fe 本就随追加变化，
    # 不算改写——故要求上一条已有 ≥3 条中段消息（前沿已定型）才判，避免开头几轮误报。
    if (prev is not None and prev.get("n", 0) >= 3
            and prev.get("fe") and e.get("fe") and e.get("fe") != prev.get("fe")):
        return "  ▲前沿变(查改写)"
    lcpr = e.get("lcpr")
    if lcpr is not None and prev is not None and pct < 90:
        expected = lcpr * 100  # 本该命中率（%）= 与上条 payload 的公共前缀占比
        if pct < expected - 10:            # 实命中比"本该命中"低 10pp 以上 = 服务端吃了
            eaten = int((expected - pct) / 100 * p)
            return f"  ⚡服务端吃掉~{eaten:,}(本该{expected:.0f}%/实{pct:.0f}%)"
        if pct < 70:                       # 命中≈本该命中，miss 都在新后缀
            return "  新内容(工具/读文件，预期内)"
        return ""
    # 旧格式（无 lcpr）：信息不足，只标突刺、不强行定因
    if pct < 70:
        return "  ←突刺"
    return ""

# ═══════════════════════════════════════════════════════════════
# Psyche 指令
# ═══════════════════════════════════════════════════════════════

@builtin("/tools", description="加载/卸载/列出可选工具组（领域专用工具 load-on-demand）",
         usage="/tools              — 列出可选工具组及状态\n"
               "/tools load <组>    — 挂上工具组（如 research 文献工具）\n"
               "/tools unload <组>  — 卸下工具组")
def _cmd_tools(r: CmdResult, _ctx, args, _sid) -> None:
    from .tools import disable_tool_group, enable_tool_group, list_tool_groups
    if not args:
        r.message = list_tool_groups()
        return
    sub = args[0].lower()
    if sub == "load" and len(args) >= 2:
        r.message = enable_tool_group(args[1].lower())
    elif sub == "unload" and len(args) >= 2:
        r.message = disable_tool_group(args[1].lower())
    elif sub in ("list", "ls"):
        r.message = list_tool_groups()
    else:
        r.message = "用法: /tools [load|unload <组>]"


@builtin("/psyche", description="加载/卸载/列出 Psyche 认知框架",
         usage="/psyche load <name>  — 读取核心并注入上下文，位置固定，不影响缓存\n"
               "/psyche unload <name> — 从上下文移除\n"
               "/psyche list          — 查看当前已加载的 psyche")
def _cmd_psyche(r: CmdResult, ctx, _args, _sid) -> None:
    if not _args:
        r.message = "用法: /psyche load|unload|list [name]"
        return
    sub = _args[0].lower()

    from .psyche_bridge import inject_psyche, remove_psyche, status_text

    if sub == "list":
        r.message = status_text(ctx)
    elif sub == "load":
        if len(_args) < 2:
            r.message = "用法: /psyche load <name>"
            return
        name = _args[1].lower().replace(" ", "-")
        r.message = inject_psyche(ctx, name)
        if r.message.startswith("✅"):
            r.save = True
    elif sub == "unload":
        if len(_args) < 2:
            r.message = "用法: /psyche unload <name>"
            return
        name = _args[1].lower().replace(" ", "-")
        r.message = remove_psyche(ctx, name)
        if r.message.startswith("✅"):
            r.save = True
    else:
        r.message = f"未知子指令 '{sub}'。可用: load, unload, list"


@builtin("/compact", description="手动压缩上下文：驱逐旧对话换摘要（不必等 65% 自动触发）")
def _cmd_compact(r: CmdResult, ctx, _args, _sid) -> None:
    from .llm import MAX_CONTEXT_TOKENS, _compact_context
    from .tools.tokens import count_messages_tokens

    before = count_messages_tokens(ctx.all)
    _compact_context(ctx, "", force=True)
    after = count_messages_tokens(ctx.all)

    if after >= before:
        r.message = "无可压缩内容（对话太短或已是最简）。"
        return
    freed_pct = (before - after) / MAX_CONTEXT_TOKENS * 100
    r.save = True
    r.message = (
        f"已压缩：{before:,} → {after:,} tokens"
        f"（释放约 {freed_pct:.1f}% 上限空间）"
    )


@builtin("/task", description="查看/清空/归档当前长任务", usage="/task [clear | archive <简述>]")
def _cmd_task(r: CmdResult, _ctx, args, _sid) -> None:
    from .tasks import archive_current, clear_current, read_current

    if not args:
        text = read_current()
        r.message = text or "当前无长任务（tasks/current.md 为空）。"
        return
    sub = args[0].lower()
    if sub == "clear":
        r.message = clear_current()
    elif sub == "archive":
        r.message = archive_current(" ".join(args[1:]))
    else:
        r.message = "用法: /task [clear | archive <简述>]"


# ═══════════════════════════════════════════════════════════════
# 热加载 /reload
# ═══════════════════════════════════════════════════════════════


@builtin("/pulse", description="主动进化：检测可改进方向（自主心跳）",
         usage="/pulse — 扫描性能/静态双信号，列出优先改进方向")
def _cmd_pulse(r: CmdResult, _ctx, _args, _sid) -> None:
    from .gaps import detect_all_gaps, format_gap_report
    report = detect_all_gaps()
    r.message = format_gap_report(report)
    r.save = True


@builtin("/organs", description="器官生命体征：各内部机制上次跳动/疑似衰竭（只读，派生自产物）",
         usage="/organs — 扫各器官产物 mtime，列出哪个器官几个会话没跳了")
def _cmd_organs(r: CmdResult, _ctx, _args, _sid) -> None:
    from .organs import render_census
    r.message = render_census()


@builtin("/reload", description="热加载代码改动（指令+工具），无需重启")
def _cmd_reload(r: CmdResult, _ctx, _args, _sid) -> None:
    r.message = "reload 由 main.py 拦截处理，此 handler 仅供 /help 注册。"


# ═══════════════════════════════════════════════════════════════
# 自省 /self — Agent 查看自己的架构、工具、命令、插件
# ═══════════════════════════════════════════════════════════════

@builtin("/self", description="自省：查看自己的架构、工具、命令、插件全景")
def _cmd_self(r: CmdResult, _ctx, _args, _sid) -> None:
    """生成 Agent 自省全景（供 AI 读取，非人类 UI）。"""
    from .tools import get_tools

    parts: list[str] = []
    _append_arch_section(parts)
    _append_tools_section(parts, get_tools())
    _append_cmd_list_section(parts)
    r.message = "\n".join(parts)


def _append_arch_section(parts: list[str]) -> None:
    """追加架构概览：文件列表 + 职责说明。"""
    parts.append("## 架构")
    parts.append("src/")
    parts.append("  main.py           — 主循环：接收输入 → dispatch → LLM → 输出")
    parts.append("  commands.py       — 指令系统：/help /save /load /self 等 + dispatch")
    parts.append("  llm.py            — LLM 调用：模型选择、前缀缓存、流式输出")
    parts.append("  config.py         — 配置加载（session 目录、模型配置）")
    parts.append("  cache_context.py  — 三段式上下文 CacheContext（prefix/log/scratch）")
    parts.append("  session.py        — 会话持久化（保存/加载/列表）")
    parts.append("  guard.py          — 自我修改分级：不可变核/核心业务(安全带)/自由")
    parts.append("  tools/")
    parts.append("    __init__.py     — 工具注册表、execute_tool() 调度、热加载")
    parts.append("    file.py         — 文件类：read_file/write_file/edit_file_lines...")
    parts.append("    web.py          — 网络类：search_web/read_page")
    parts.append("    exec.py         — 执行类：run_command/run_python")
    parts.append("    code.py         — 代码搜索：grep_code")
    parts.append("    git.py          — Git 类：git_status/git_diff/git_commit/git_push...")
    parts.append("    project.py      — 项目类：scan_project/check_project/generate_agents_md...")
    parts.append("    think.py        — 思考工具：think（策略规划）")
    parts.append("    memory.py       — 记忆工具：remember/recall/forget")
    parts.append("    rag.py          — RAG 索引：rag_index/rag_query/rag_status")
    parts.append("    storm.py        — 去重引擎：同轮工具调用滑动窗口去重")
    parts.append("    lint.py         — 检查引擎：check_project（六维军规检查）")
    parts.append("    self.py         — 自我认知：self（结构化架构+运行时状态）")
    parts.append("docs/")
    parts.append("  AGENTS.md         — AI 操作手册")
    parts.append("tests/              — pytest 测试")
    parts.append("")


def _guess_tool_group(name: str) -> str:
    """根据工具名推断所属模块分组。"""
    if name.startswith("git_"):
        return "git"
    if name.startswith("rag_"):
        return "rag"
    if name in ("remember", "recall", "forget"):
        return "memory"
    if name in ("search_web", "read_page"):
        return "web"
    if name in ("read_file", "read_file_lines", "write_file", "edit_file_lines",
                "delete_file", "list_files", "count_lines"):
        return "file"
    if name in ("run_command", "run_python"):
        return "exec"
    if name == "grep_code":
        return "code"
    if name in ("scan_project", "check_project", "generate_agents_md", "docs_sync_check"):
        return "project"
    if name == "think":
        return "think"
    return "other"


def _append_tools_section(parts: list[str], all_tools: list[dict]) -> None:
    """追加工具清单：按模块分组，附描述。"""
    parts.append(f"## 工具（共 {len(all_tools)} 个）")
    groups: dict[str, list[str]] = {}
    name_to_desc: dict[str, str] = {}
    for t in all_tools:
        fn = t.get("function", {})
        n = fn.get("name", "?")
        name_to_desc[n] = fn.get("description", "")[:80]
        groups.setdefault(_guess_tool_group(n), []).append(n)
    for gname in sorted(groups.keys()):
        parts.append(f"  [{gname}]")
        for tn in sorted(groups[gname]):
            parts.append(f"    {tn}  — {name_to_desc.get(tn, '')}")
    parts.append("")


def _append_cmd_list_section(parts: list[str]) -> None:
    """追加指令清单：按 handler 去重，同 handler 别名合并。"""
    seen: dict[int, list[Command]] = {}
    for cmd in _registry:
        hid = id(cmd.handler)
        seen.setdefault(hid, []).append(cmd)
    parts.append(f"## 指令（共 {len(seen)} 个）")
    for group in sorted(seen.values(), key=lambda g: g[0].name):
        primary = group[0]
        aliases = [c.name for c in group[1:]]
        name_display = f"{primary.name}（{'、'.join(aliases)}）" if aliases else primary.name
        parts.append(f"  {name_display:<24} {primary.description}")
    parts.append("")


# ═══════════════════════════════════════════════════════════════
# 自跟踪 /stats — Agent 查看自己的工具调用统计
# ═══════════════════════════════════════════════════════════════


@builtin("/ambition", description="查看/管理野心清单（自己发现想做的事）",
         usage="/ambition [done <标题关键词>]")
def _cmd_ambition(r: CmdResult, _ctx, args, _sid) -> None:
    from .tasks import AMBITIONS_FILE, read_ambitions

    if not args:
        text = read_ambitions()
        r.message = text or "野心清单为空——你还没记下想做的事。直接告诉我就行，我来写。"
        return

    sub = args[0].lower()
    if sub == "done" and len(args) > 1:
        r.message = _mark_ambition_done(" ".join(args[1:]), AMBITIONS_FILE)
    else:
        r.message = "用法: /ambition 查看清单，/ambition done <关键词> 标记完成"


def _mark_ambition_done(keyword: str, file_path) -> str:
    """在野心清单中标记含关键词的标题段为完成。"""
    if not file_path.exists():
        return "野心清单为空。"
    text = file_path.read_text(encoding="utf-8")
    lines = text.split("\n")
    in_block = False
    block_start = -1
    new_lines = []
    for i, line in enumerate(lines):
        if line.startswith("## ") and keyword.lower() in line.lower():
            in_block = True
            block_start = i
        elif line.startswith("## ") and in_block:
            new_lines.append(line.replace("## ", "## ~~") + "~~ ✅ 已完成")
            in_block = False
        elif in_block:
            continue
        else:
            new_lines.append(line)
    if in_block:
        new_lines.append(lines[block_start].replace("## ", "## ~~") + "~~ ✅ 已完成")
    file_path.write_text("\n".join(new_lines), encoding="utf-8")
    return f"已标记 '{keyword}' 为完成。"



@builtin("/fix", description="处理方向发现中的第 N 个改进方向",
         usage="/fix <编号>  （如 /fix 3）")
def _cmd_fix(r: CmdResult, ctx, args, _sid) -> None:
    if not args:
        r.message = "用法: /fix <编号>  （如 /fix 3）。用启动时方向报告查看编号。"
        return
    try:
        n = int(args[0])
    except ValueError:
        r.message = f"无效编号: {args[0]}"
        return

    from .gaps import _make_fix_prompt, get_gap_by_index, get_last_report
    report = get_last_report()
    if report is None or not report.gaps:
        r.message = "暂无方向发现报告，先正常对话一轮让系统启动检测。"
        return
    gap = get_gap_by_index(n)
    if gap is None:
        r.message = f"编号 {n} 超出范围（当前共 {len(report.gaps)} 个方向）。"
        return

    prompt = _make_fix_prompt(gap, n)
    ctx.log.append({"role": "user", "content": prompt})
    r.retry = True
    r.save = True
    r.message = f"已启动方向 #{n}：{gap.detail[:80]}..."


# ═══════════════════════════════════════════════════════════════
# 意图路由：自然语言 → 命令（无模式统一交互）
# ═══════════════════════════════════════════════════════════════

_INTENT_ROUTES: list[tuple[str, str, str]] = [
    # (关键词, 命令, 说明)
    # 方向发现
    ("处理这些", "/fix", ""), ("看看第一个", "/fix 1", ""),
    ("处理 #", "/fix", ""), ("修 #", "/fix", ""),
    ("修这个", "/fix", ""), ("修第", "/fix", ""),
    # 自主心跳
    ("心跳", "/pulse", ""), ("自主心跳", "/pulse", ""),
    ("检测方向", "/pulse", ""), ("看看有什么问题", "/pulse", ""),
    # 教训
    ("记教训", "/lesson save", ""), ("记下教训", "/lesson save", ""),
    ("存教训", "/lesson save", ""), ("提取教训", "/lesson", ""),
    ("学了什么", "/lesson", ""),
    # 任务
    ("清空任务", "/task clear", ""), ("归档任务", "/task archive", ""),
    ("看任务", "/task", ""),
    # 野心
    ("看野心", "/ambition", ""), ("完成野心", "/ambition done", ""),
]

_ACTIVE_INTENT_MAP: dict[str, str] = {}


def _detect_intent(text: str) -> str | None:
    """从自然语言文本中检测意图，返回命令字符串或 None。"""
    low = text.lower().strip()
    for keyword, cmd, _desc in _INTENT_ROUTES:
        if keyword.lower() in low:
            # 提取参数（如 "处理 #3" → "/fix 3"）
            import re
            if "#" in keyword or "第" in keyword:
                m = re.search(r"[#第]\s*(\d+)", text)
                if m:
                    return f"{cmd} {m.group(1)}"
            return cmd
    return None

def dispatch(user_input: str, ctx: CacheContext, session_id: str | None) -> CmdResult:
    r = CmdResult()
    parts = user_input.split()
    if not parts:
        return r
    cmd = parts[0].lower()

    # ── 意图路由：非命令输入先检测自然语言意图 ──
    if not cmd.startswith("/"):
        intent = _detect_intent(user_input)
        if intent:
            cmd = intent.split()[0].lower()
            args = intent.split()[1:] + parts  # 意图参数 + 原始输入
        else:
            args = parts[1:]
    else:
        args = parts[1:]

    handler = _handlers.get(cmd)
    if handler:
        handler(r, ctx, args, session_id)
    return r
