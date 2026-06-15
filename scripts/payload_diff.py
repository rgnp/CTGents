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
        pure_append = (k == len(pmsgs))  # 上次整个 payload 是这次的前缀

        hitpct = f"{hit / prompt * 100:4.0f}%" if prompt else "  - "
        head = f"  #{req:<3d} {len(pmsgs):>6d}  {len(cmsgs):>6d}    前{k:>2d}条同   {hit:>7,}/{prompt:<7,}({hitpct})"

        if not pure_append:
            # 前缀在第 k 条断 = 旧消息被改/删/插 → 代码的锅
            old = pmsgs[k] if k < len(pmsgs) else None
            new = cmsgs[k] if k < len(cmsgs) else None
            print(head + f"  ❌代码改了历史@第{k}条")
            if old is not None:
                print(f"        旧[{old.get('role')}] {_head(old.get('content'))}")
            if new is not None:
                print(f"        新[{new.get('role')}] {_head(new.get('content'))}")
            continue

        # 纯追加：本该命中 ≈ 上次真实 prompt_tokens（不估算）
        expected = prev_prompt
        if expected <= 0:
            print(head + "  (上次无 usage，无法对账)")
            continue
        gap = expected - hit
        # 命中按 64-token 块向下取整，留一块容差；再留 5% 余量抵噪声
        slack = max(64, int(expected * 0.05))
        if gap <= slack:
            print(head + f"  ✅一致(本该≈{expected:,})")
        else:
            est_added = estimate_tokens("".join(_msg_sig(m) for m in cmsgs[k:]))
            print(head + f"  ⚡服务端吃掉~{gap:,}(本该命中{expected:,}/实{hit:,};本轮新增约{est_added:,}est)")

    print()
    print("读法：全是 ✅/⚡ 而无 ❌ ⟹ 我们只追加、从不改历史，命中塌陷是服务端淘汰；")
    print("　　　出现 ❌ ⟹ 代码在改旧消息，那一条就是塌陷根因，往那条的生产路径查。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
