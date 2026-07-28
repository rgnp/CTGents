"""回填历史会话摘要（LLM 版）。

老会话（session_summary 上线前）没有摘要文件，跨会话索引对它们是瞎的；
已有的规则版摘要话题噪声多（文件名/测试残渣）、中文话题缺席。本脚本用
LLM 层重新生成，让前缀会话索引的"识别锚"质量一致。

用法（会调 DeepSeek Flash，每场一次，花钱但极少）:
  python scripts/backfill_summaries.py             # 只补缺（无摘要的会话）
  python scripts/backfill_summaries.py --force     # 全量重生成（规则版升级 LLM 版）
  python scripts/backfill_summaries.py --limit 3   # 最多处理 N 场（先试跑看质量）
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.session import list_sessions, load_session  # noqa: E402
from src.session_summary import (  # noqa: E402
    _KNOWLEDGE_SESSIONS_DIR,
    extract_summary,
    write_summary,
)


def main() -> None:
    ap = argparse.ArgumentParser(description="回填历史会话摘要（LLM 版）")
    ap.add_argument("--force", action="store_true", help="已有摘要也重新生成")
    ap.add_argument("--limit", type=int, default=0, help="最多处理 N 场（0=不限）")
    args = ap.parse_args()

    processed = skipped = failed = 0
    for sid in list_sessions():  # 新 → 旧
        if args.limit and processed >= args.limit:
            break
        out = _KNOWLEDGE_SESSIONS_DIR / f"{sid}.md"
        if out.exists() and not args.force:
            skipped += 1
            continue
        try:
            messages = load_session(sid)
        except Exception as e:
            print(f"✗ {sid} 读取失败: {e}")
            failed += 1
            continue
        try:
            summary = extract_summary(messages, use_llm=True)
            path = write_summary(sid, summary)
        except Exception as e:
            print(f"✗ {sid} 摘要失败: {e}")
            failed += 1
            continue
        processed += 1
        if path:
            mark = summary.get("source", "?")
            topics = "、".join(summary["topics"][:5]) if isinstance(
                summary["topics"], list) else str(summary["topics"])[:80]
            unf = f"｜未竟: {summary['unfinished'][:50]}" if summary.get("unfinished") else ""
            print(f"✓ {sid} [{mark}] {topics}{unf}")
        else:
            print(f"- {sid} 空会话，跳过")

    print(f"\n完成: 生成 {processed}、已有跳过 {skipped}、失败 {failed}")
    if processed and not args.force:
        print("提示: 旧的规则版摘要想一并升级质量，加 --force 重跑。")


if __name__ == "__main__":
    main()
