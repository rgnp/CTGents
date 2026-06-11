# 记忆指纹合并：代码级兜底，防止同质散成 N 条

- [x] Step 1: `_remember` 加 fingerprint 合并逻辑
  - [x] `_find_by_fingerprint(fp)` — 扫描已有文件找同指纹
  - [x] `_merge_memory(existing, ...)` — 合并：更新内容、递增 times_encountered、刷新时间
  - [x] `_remember` 加 fingerprint 参数，存前先扫描合并
  - [x] TOOLS_MEMORY 加 fingerprint 可选参数
  - [x] `execute` 透传 fingerprint
  - [x] 验证: import + ruff + pytest 25/25 → 663/663 全量
- [x] Step 2: 给已有记忆补指纹
  - [x] `memory-self-merge` → `memory_self_merge`
  - [x] `innovation-is-problem-discovery` → `innovation_discovery`
- [x] Step 3: 提交
- [ ] 归档 current.md → tasks/archive/

## 完成总结
- 计划 3 步 → 实际 3 步（0 次回退）
- 教训: 代码改动在磁盘，但 agent 进程用的是内存中的旧代码——测试绿不代表运行时生效。下次改完核心代码后，重启 agent 才能验证端到端行为。
