"""相邻请求 canonical_request 取证对账——判「客户端 payload 变了」还是「服务端缓存未命中」。

前提：先 `set CTG_DUMP_PAYLOADS=1` 跑一段带工具的对话，llm.py 会把每次真实发给 API 的
canonical_request（model/messages/tools/max_tokens/...）+ usage + system_fingerprint +
messages_hash/tools_hash/request_hash 落盘到 stats/payloads/<session>/req_NNNN.json。

本脚本逐对相邻请求输出：
  判决行   —— 真公共前缀条数 + 服务端命中 + ✅尾部浮动/⚡服务端吃掉/❌真改历史（含「本该命中」基准）。
  取证行   —— full_lcp_ratio（字符级最长公共前缀占比）、messages_hash/tools_hash/
              system_fingerprint 变没变、trailing_system_tokens 变化。
  first_diff —— 路径（messages[k].field）+ 分叉点前后内容（旧/新各 ~300 字符）。

归因决策树见 CACHE_SPIKE_DIAGNOSIS.md。

用法：
  python scripts/payload_diff.py                # 自动挑最新会话
  python scripts/payload_diff.py <session_id>   # 指定会话
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from src.tools.tokens import estimate_tokens  # noqa: E402

_PAYLOAD_DIR = _ROOT / "stats" / "payloads"


def _msg_sig(m: dict) -> str:
    """一条消息的规范序列化：键排序，含 role/content/tool_calls/tool_call_id 全字段。"""
    return json.dumps(m, sort_keys=True, ensure_ascii=False)


def _get_messages(rec: dict) -> list[dict]:
    """从 rec 取 messages：优先 canonical_request（新格式），回退顶层 messages（旧格式）。"""
    canon = rec.get("canonical_request")
    if isinstance(canon, dict) and "messages" in canon:
        return canon["messages"] or []
    return rec.get("messages", []) or []


def _common_prefix_msgs(prev: list[dict], cur: list[dict]) -> int:
    """返回 cur 开头有多少条消息与 prev 逐字节相同。"""
    k = 0
    for a, b in zip(prev, cur, strict=False):
        if _msg_sig(a) == _msg_sig(b):
            k += 1
        else:
            break
    return k


def _char_lcp(a: str, b: str) -> int:
    """两字符串最长公共前缀的字符数。"""
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


def _head(s: str | None, n: int = 70) -> str:
    s = (s or "").replace("\n", "\\n")
    return s[:n] + ("…" if len(s) > n else "")


def _window(s: str, off: int, before: int = 100, after: int = 200) -> str:
    """取 s 在 off 附近的窗口（换行转义），标出分叉点。"""
    lo = max(0, off - before)
    seg = s[lo:off + after].replace("\n", "\\n")
    pre = "…" if lo > 0 else ""
    return f"{pre}{seg}"


def _trailing_system_count(msgs: list[dict]) -> int:
    """末尾连续 system 消息条数 = send() 注入的「尾部牙」(行为牙/pinboard/task_ctx)。"""
    c = 0
    for m in reversed(msgs):
        if m.get("role") == "system":
            c += 1
        else:
            break
    return c


def _trailing_system_tokens(msgs: list[dict]) -> int:
    n = _trailing_system_count(msgs)
    tail = msgs[len(msgs) - n:] if n else []
    return estimate_tokens("".join(_msg_sig(m) for m in tail))


def _est_tokens_msgs(msgs: list[dict]) -> int:
    return estimate_tokens("".join(_msg_sig(m) for m in msgs))


def _pick_session() -> Path | None:
    if not _PAYLOAD_DIR.exists():
        return None
    subs = [d for d in _PAYLOAD_DIR.iterdir() if d.is_dir()]
    if not subs:
        return None
    return max(subs, key=lambda d: max((f.stat().st_mtime for f in d.glob("req_*.json")), default=0))


def _load(session_dir: Path) -> list[dict]:
    recs = []
    for f in sorted(session_dir.glob("req_*.json")):
        try:
            recs.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception as e:
            print(f"  (跳过损坏文件 {f.name}: {e})")
    recs.sort(key=lambda r: r.get("req", 0))
    return recs


def _delta(label: str, a, b, changed_note: str = "") -> str:
    """格式化一个字段的变化：不变打 ✓，变了打 ⚠ 并附 a→b。"""
    if a == b:
        return f"{label} ✓{a if a is not None else 'n/a'}"
    return f"{label} ⚠{a}→{b}{changed_note}"


def _client_prefix_status(prev_msgs, cur_msgs, prev_rec, cur_rec) -> tuple[str, int]:
    """纯结构地（只看字节/哈希，不掺 token 数）判断客户端这一轮对前缀做了什么。

    返回 (status, k)，status ∈：
      tools_changed   —— tools 表变了，DeepSeek 前缀里 tools 排在 messages 前，整段前缀作废。
      history_mutated —— first_diff 落在对话中段（messages[0..conv_boundary-1]），旧消息被原地改/删。
      tail_only_changed —— 对话部分逐字节相同，只有末尾浮动的 system 尾部牙不同。
      pure_append     —— 上一轮整个 payload 是这一轮的逐字节前缀，只追加、没动任何旧内容。
    优先级：tools > history > tail > pure（前者一旦成立就盖过后者）。
    """
    pth, cth = prev_rec.get("tools_hash"), cur_rec.get("tools_hash")
    k = _common_prefix_msgs(prev_msgs, cur_msgs)
    if pth and cth and pth != cth:
        return "tools_changed", k
    conv_boundary = len(prev_msgs) - _trailing_system_count(prev_msgs)
    if k < conv_boundary:
        return "history_mutated", k
    if k == len(prev_msgs):
        return "pure_append", k
    return "tail_only_changed", k


def _verdict(status, prev_msgs, k, prev_prompt, hit) -> str:
    """据 client_prefix_status 出判词。关键纪律：
    - 「服务端缓存未命中」**只在 pure_append 下**判（结构最干净、任何超出追加尾的 miss 都无客户端解释）；
    - 缺口 token **是估算不是事实**——基准「上轮 prompt」本身会随 cache hit/miss 漂移（见报告第四节），
      故文案点名「按上轮prompt估算」「缺口≈」，不写「服务端吃掉=N」这种硬数字。
    """
    if status == "tools_changed":
        return "⚠客户端tools表变化(前缀整体作废,命中掉属预期,非服务端)"
    if status == "history_mutated":
        return f"❌客户端改历史@对话第{k}条(命中掉因前缀被改,非服务端)"
    if prev_prompt <= 0:
        return "(上次无 usage，无法对账)"
    if status == "tail_only_changed":
        tail_est = _est_tokens_msgs(prev_msgs[len(prev_msgs) - _trailing_system_count(prev_msgs):])
        return (f"✅对话前缀未变(尾部浮动{tail_est:,}est/轮);命中异常不在此判服务端"
                "(浮动尾部本身造成部分 miss,归因不干净)")
    # pure_append：唯一干净判服务端的场景
    expected = prev_prompt  # 估算基准：上轮可复用前缀 ≈ 上轮 prompt（注意此基准会漂移）
    gap = expected - hit
    slack = max(64, int(expected * 0.05))
    if gap <= slack:
        return f"✅命中正常(纯追加,按上轮prompt估算可复用≈{expected:,},实命中{hit:,})"
    return (f"⚡旧前缀未全命中：纯追加，按上轮prompt估算可复用≈{expected:,}，"
            f"实命中{hit:,}，缺口≈{gap:,}（缺口为估算,非精确事实）")


def main() -> int:
    session_dir = _PAYLOAD_DIR / sys.argv[1] if len(sys.argv) > 1 else _pick_session()
    if not session_dir or not session_dir.exists():
        print("没有 payload 落盘数据。先 set CTG_DUMP_PAYLOADS=1 跑一段带工具的对话。")
        print(f"（查找目录：{_PAYLOAD_DIR}）")
        return 1

    recs = _load(session_dir)
    if len(recs) < 2:
        print(f"会话 {session_dir.name} 只有 {len(recs)} 条请求，无法做相邻对账（需 ≥2）。")
        return 1

    print(f"会话：{session_dir.name}　共 {len(recs)} 条请求\n")

    for i in range(1, len(recs)):
        prev, cur = recs[i - 1], recs[i]
        pmsgs, cmsgs = _get_messages(prev), _get_messages(cur)
        u, pu = cur.get("usage") or {}, prev.get("usage") or {}
        hit, prompt = u.get("cache_hit_tokens", 0), u.get("prompt_tokens", 0)
        prev_prompt = pu.get("prompt_tokens", 0)
        req = cur.get("req", i + 1)

        status, k = _client_prefix_status(pmsgs, cmsgs, prev, cur)
        hitpct = f"{hit / prompt * 100:4.0f}%" if prompt else "  - "
        verdict = _verdict(status, pmsgs, k, prev_prompt, hit)

        # 判决行：先报 client_prefix_status（纯结构、可证伪），再报命中与判词
        print(f"  #{req:<3d} 条数 {len(pmsgs)}→{len(cmsgs)}  前{k}条同  [{status}]")
        print(f"       命中 {hit:>7,}/{prompt:<7,}({hitpct})  {verdict}")

        # 取证行：full_lcp_ratio + 各 hash/fingerprint/尾部 delta
        pstr = json.dumps(pmsgs, sort_keys=True, ensure_ascii=False)
        cstr = json.dumps(cmsgs, sort_keys=True, ensure_ascii=False)
        lcp_chars = _char_lcp(pstr, cstr)
        full_lcp_ratio = lcp_chars / len(cstr) if cstr else 1.0
        msgs_d = _delta("msgs_hash", prev.get("messages_hash"), cur.get("messages_hash"))
        tools_d = _delta("tools_hash", prev.get("tools_hash"), cur.get("tools_hash"),
                         " ⟸前缀整体作废")
        fp_d = _delta("sysfp", prev.get("system_fingerprint"), cur.get("system_fingerprint"),
                      " ⟸节点路由变,疑缓存未命中主因")
        tail_pt, tail_ct = _trailing_system_tokens(pmsgs), _trailing_system_tokens(cmsgs)
        tail_d = f"尾部tok {tail_pt}→{tail_ct}" + ("" if tail_pt == tail_ct else " ⚠")
        print(f"       full_lcp {full_lcp_ratio:.3f} | {msgs_d} | {tools_d} | {fp_d} | {tail_d}")

        # first_diff：路径 + 前后内容（status 非纯追加、或有疑似服务端缺口、或 tools/节点变化时展开）
        field = "role" if (k < len(pmsgs) and k < len(cmsgs)
                           and pmsgs[k].get("role") != cmsgs[k].get("role")) else "content"
        path = f"messages[{k}].{field}" if k < min(len(pmsgs), len(cmsgs)) else f"messages[{k}](追加尾)"
        interesting = (status != "pure_append" or "⚡" in verdict
                       or prev.get("system_fingerprint") != cur.get("system_fingerprint"))
        print(f"       first_diff @ {path}")
        if interesting:
            po = pstr[:lcp_chars]  # noqa: F841  (保留可读：分叉点即 lcp_chars 处)
            print(f"         旧: {_window(pstr, lcp_chars)}")
            print(f"         新: {_window(cstr, lcp_chars)}")
        print()

    print("读法（先看 client_prefix_status，再谈命中；详见 CACHE_SPIKE_DIAGNOSIS.md）：")
    print("  client_prefix_status = 纯结构判断（只看字节/哈希，可证伪、可信）：")
    print("    tools_changed   → 工具表变了,前缀作废,命中掉属预期,锅在客户端 tools。")
    print("    history_mutated → 对话内某条被原地改/删,first_diff 那条即根因,往生产路径查。")
    print("    tail_only_changed → 对话前缀未变,只浮动尾部牙不同;命中异常不在此判服务端(归因不干净)。")
    print("    pure_append     → 只追加,没动旧内容;此时 hit 远低于上轮可复用前缀 = 服务端未命中。")
    print("  sysfp ⚠ → 路由到别的后端节点,那节点没这段缓存=服务端未命中佐证(DeepSeek 常不返回此字段)。")
    print("  注意：「服务端未命中」判定可信,但「缺口 N token」是按上轮 prompt 估算、非精确事实——")
    print("        DeepSeek 的 prompt_tokens 本身随 cache hit/miss 漂移(见报告第四节)。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
