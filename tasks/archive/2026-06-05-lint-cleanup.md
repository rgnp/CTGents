# 当前任务: 清理预存 lint 问题 + 删 dead code

- [x] Step 1: diagnose
- [x] Step 2: 逐文件修复
  - [x] 2a. _tool_meta.py — 删 `import sys`
  - [x] 2b. rag.py — 删 `from typing import Any`
  - [x] 2c. rag.py — f-string → 普通字符串
  - [r] 2d. __init__.py:159 — `import src.commands` 假阳性（副作用导入），文件受 guard 保护
  - [x] 2e. tracker.py — 删除死模块（109行，0引用，0覆盖，未注册）
  - [x] 2f. self.py:213 — `f"---"` → `"---"`
- [x] Step 3: related_only 验证 → 35 passed
- [x] Step 4: 全量测试 → 306 passed

## 完成总结
- 计划 7 子步骤 → 实际 7（0 次回退重试）
- 教训: `edit_file_lines` 单行删除可靠；跨文件小改是任务追踪的最佳测试场景。