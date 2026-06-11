# 主动进化接线：L2→L3 "/fix #N" 指令

## 计划
- [x] Step 1: `make_task_context_message` 接入 `detect_all_gaps` → agent 启动即见方向发现报告
- [o] Step 2: "/fix #N" 指令 — 从方向报告取第 N 个方向 → 生成 prompt → 走正常管线闭环
  - [ ] gaps.py 加 `get_last_report()` 缓存
  - [ ] commands.py 加 "/fix #N" → 解析 + 生成 prompt → r.retry 模式
  - [ ] 测试
- [ ] Step 3: 全量测试 + commit
