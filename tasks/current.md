# 记忆腐败治疗后续：防止忘记归档

- [x] Step 1: tasks.py 加 `is_all_done()` + `create_task()` + 修复 `[r]`/`[!]` 不进 `has_unfinished` 的 bug
  - [x] 加 `_ALL_NOT_DONE_MARKERS` 覆盖 `[ ] [o] [r] [!]`
  - [x] 加 `is_all_done()`：文件存在、非空、全是 `[x]`
  - [x] `make_task_context_message` 启动时检测 `is_all_done()` → 自动归档
  - [x] 加 `create_task()`：写 current.md 自动追加归档步骤
  - [x] 验证：import 通过 + ruff 零错 + pytest 25/25
- [x] Step 2: 跑全量测试 + 提交
- [ ] 归档 current.md → tasks/archive/
