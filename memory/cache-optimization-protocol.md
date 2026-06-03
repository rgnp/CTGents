---
name: cache-optimization-protocol
description: Cache 优化五条硬规则（v2.2，写入 research
metadata:
  type: strategy
  updated: 2026-06-02T03:16:04Z
---

Cache 优化五条硬规则（v2.2，写入 research-assistant-mode.md §八）：

1. 写入合并：多次 edit_file_lines → 一次 write_file。构造完整内容后一次写入。
2. 写入延迟：非关键写入（meta更新、日志）攒到对话断点批量执行。例外：.py 工具写完后要验证时可立即写入+运行。
3. 读取最小化：需要论文信息先看 KNOWLEDGE_INDEX 的一句话概要；需要 Gap 先看 INDEX 表格；上下文已有绝不重读。
4. 读取批量化：多个独立 read_file 并行调用，不要串行。
5. 对话结束自检：查多余读取、重复编辑、非必要立即写入、写入总数是否超5次。

缓存杀手排名：🥇分散写入 🥈碎片化读写交替 🥉频繁小粒度编辑 4.冗余读取。

预期效果：flash 91%→95%+, pro 84%→92%+, 总 89%→94%+
