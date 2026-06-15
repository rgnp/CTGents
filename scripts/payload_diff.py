"""相邻请求 payload 真公共前缀对账——一锤定音「命中塌陷是代码还是服务端」。

前提：先 `set CTG_DUMP_PAYLOADS=1` 跑一段带工具的对话，llm.py 会把每次真实发给
API 的 messages 原样落盘到 stats/payloads/<session>/req_NNNN.json（含真实 usage）。

本脚本逐消息比对相邻两次请求的 payload，输出每次请求：
  - 服务端报的命中（prompt_tokens / cache_hit_tokens）。
  - 真公共前缀：当前 payload 开头有多少条消息与上一次逐字节相同。
  - 「本该命中」基准——零估算：若是纯追加（上次整个 payload 是这次的前缀），
    那本该命中 ≈ 上次的真实 prompt_tokens（那整段之前发过、应已被缓存）。
  - 判决：
      纯追加 且 命中 ≈ 上次prompt  → 一致，服务端如实缓存。
      纯追加 但 命中 << 上次prompt  → ⚡服务端吃掉（按真 token 报差额）。
      前缀在第 k 条断（k<上次条数）→ 代码改了历史第 k 条，打印改了啥（我们的锅）。

注意：tools 字段未落盘（每次字节相同、是独立字段，不影响相邻 diff 结论）。

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


def _common_prefix_msgs(prev: list[dict], cur: list[dict]) -> int:
    """返回 cur 开头有多少条消息与 prev 逐字节相同。"""
    k = 0
    for a, b in zip(prev, cur, strict=False):
        if _msg_sig(a) == _msg_sig(b):
            k += 1
        else:
            break
    return k


def _head(s: str | None, n: int = 70) -> str:
    s = (s or "").replace("\n", "\\n")
    return s[:n] + ("…" if len(s) > n else "")


def _trailing_system_count(msgs: list[dict]) -> int:
    """末尾连续 system 消息条数 = send() 注入的「尾部牙」(行为牙/pinboard/task_ctx)。"""
    c = 0
    for m in reversed(msgs):
        if m.get("role") == "system":
            c += 1
        else:
            break
    return c


def _est_tokens_msgs(msgs: list[dict]) -> int:
    return estimate_tokens("".join(_msg_sig(m) for m in msgs))


def _pick_session() -> Path | None:
    if not _PAYLOAD_DIR.exists():
        return None
    subs = [d for d in _PAYLOAD_DIR.iterdir() if d.is_dir()]
    if not subs:
        return None
    # 挑里面最新落盘文件的那个会话
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
    print("  #  上次条数 本次条数  真公共前缀   服务端命中/输入        判决")
    print("  " + "-" * 78)

    for i in range(1, len(recs)):
        prev, cur = recs[i - 1], recs[i]
        pmsgs, cmsgs = prev.get("messages", []), cur.get("messages", [])
        u = cur.get("usage") or {}
        pu = prev.get("usage") or {}
        hit = u.get("cache_hit_tokens", 0)
        prompt = u.get("prompt_tokens", 0)
        prev_prompt = pu.get("prompt_tokens", 0)
        req = cur.get("req", i + 1)

        k = _common_prefix_msgs(pmsgs, cmsgs)
        prev_tail = _trailing_system_count(pmsgs)          # 上次尾部牙条数
        conv_boundary = len(pmsgs) - prev_tail              # 上次「对话/尾部」分界
        tail_msgs = pmsgs[conv_boundary:] if prev_tail else []
        tail_est = _est_tokens_msgs(tail_msgs)

        hitpct = f"{hit / prompt * 100:4.0f}%" if prompt else "  - "
        head = f"  #{req:<3d} {len(pmsgs):>6d}  {len(cmsgs):>6d}    前{k:>2d}条同   {hit:>7,}/{prompt:<7,}({hitpct})"

        if k < conv_boundary:
            # 断点落在对话内部（不是尾部边界）= 真·有旧对话消息被原地改/删/插 → 代码的锅
            old = pmsgs[k] if k < len(pmsgs) else None
            new = cmsgs[k] if k < len(cmsgs) else None
            print(head + f"  ❌真改历史@对话第{k}条")
            if old is not None:
                print(f"        旧[{old.get('role')}] {_head(old.get('content'))}")
            if new is not None:
                print(f"        新[{new.get('role')}] {_head(new.get('content'))}")
            continue

        # k >= conv_boundary：对话部分逐字节相同。断点要么是尾部浮动(prev_tail>0)、
        # 要么是真纯追加(prev_tail==0，如 NO_VOLATILE_TAIL 模式)。
        # 「本该命中」= 对话前缀，不含上次浮动的尾部牙：≈ 上次 prompt - 上次尾部 token。
        if prev_prompt <= 0:
            print(head + "  (上次无 usage，无法对账)")
            continue
        floating = prev_tail > 0 and k < len(pmsgs)
        expected = prev_prompt - (tail_est if floating else 0)
        gap = expected - hit
        slack = max(64, int(expected * 0.05))  # 64-token 块取整 + 5% 噪声余量
        tag = f"尾部浮动{tail_est:,}est/轮" if floating else "纯追加"
        if gap <= slack:
            print(head + f"  ✅一致({tag},本该≈{expected:,})")
        else:
            print(head + f"  ⚡服务端吃掉~{gap:,}({tag};本该命中{expected:,}/实{hit:,})")

    print()
    print("读法：")
    print("  ❌真改历史  = 对话内某条被原地改/删 → 代码 bug，往那条生产路径查（本轮重点排除项）。")
    print("  ✅尾部浮动  = 对话逐字节相同，只是尾部牙每轮飘到末尾、重发 ~Nest token；")
    print("              这是尾部注入的设计成本，不是 bug。开 CTG_NO_VOLATILE_TAIL=1 可消除。")
    print("  ⚡服务端吃掉 = 连对话前缀都没全命中，差额是 DeepSeek 淘汰的（常伴大间隔 g）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
