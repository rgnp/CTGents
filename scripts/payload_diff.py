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


def _verdict(prev_msgs, cur_msgs, k, prev_prompt, hit) -> str:
    """判决：尾部浮动 / 纯追加 / 服务端吃掉 / 真改历史（沿用真公共前缀对账逻辑）。"""
    prev_tail = _trailing_system_count(prev_msgs)
    conv_boundary = len(prev_msgs) - prev_tail
    tail_est = _est_tokens_msgs(prev_msgs[conv_boundary:]) if prev_tail else 0

    if k < conv_boundary:
        return f"❌真改历史@对话第{k}条"
    if prev_prompt <= 0:
        return "(上次无 usage，无法对账)"
    floating = prev_tail > 0 and k < len(prev_msgs)
    expected = prev_prompt - (tail_est if floating else 0)
    gap = expected - hit
    slack = max(64, int(expected * 0.05))
    tag = f"尾部浮动{tail_est:,}est/轮" if floating else "纯追加"
    if gap <= slack:
        return f"✅一致({tag},本该≈{expected:,})"
    return f"⚡服务端吃掉~{gap:,}({tag};本该命中{expected:,}/实{hit:,})"


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

        k = _common_prefix_msgs(pmsgs, cmsgs)
        hitpct = f"{hit / prompt * 100:4.0f}%" if prompt else "  - "
        verdict = _verdict(pmsgs, cmsgs, k, prev_prompt, hit)

        # 判决行
        print(f"  #{req:<3d} 条数 {len(pmsgs)}→{len(cmsgs)}  前{k}条同  "
              f"命中 {hit:>7,}/{prompt:<7,}({hitpct})  {verdict}")

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

        # first_diff：路径 + 前后内容（仅在判决非「纯追加一致」或有 hash/节点变化时展开细节）
        field = "role" if (k < len(pmsgs) and k < len(cmsgs)
                           and pmsgs[k].get("role") != cmsgs[k].get("role")) else "content"
        path = f"messages[{k}].{field}" if k < min(len(pmsgs), len(cmsgs)) else f"messages[{k}](追加尾)"
        interesting = ("✅一致(纯追加" not in verdict
                       or prev.get("tools_hash") != cur.get("tools_hash")
                       or prev.get("system_fingerprint") != cur.get("system_fingerprint"))
        print(f"       first_diff @ {path}")
        if interesting:
            po = pstr[:lcp_chars]  # noqa: F841  (保留可读：分叉点即 lcp_chars 处)
            print(f"         旧: {_window(pstr, lcp_chars)}")
            print(f"         新: {_window(cstr, lcp_chars)}")
        print()

    print("读法（归因决策树，详见 CACHE_SPIKE_DIAGNOSIS.md）：")
    print("  tools_hash ⚠     → 工具表变了,前缀整体作废,锅在 tools 不在 messages。")
    print("  sysfp ⚠          → 请求路由到别的后端节点,那节点没这段缓存=服务端未命中,非客户端。")
    print("  full_lcp 高但命中低 → 客户端发的前缀字节相同、服务端却没命中 = 服务端淘汰(客户端无责)。")
    print("  ❌真改历史        → 对话内某条被原地改/删,first_diff 那条就是根因,往生产路径查。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
