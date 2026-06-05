# 当前任务

- [x] Step 1: 删除 evolution_loop 兼容层
  - [x] 移除 `src/evolution_loop.py`
  - [x] 将 prompt 测试迁到 `src.evolution_runner`
- [x] Step 2: 同步架构引用
  - [x] 更新覆盖率分层中的核心文件列表
  - [x] 更新自省中的进化系统文件描述
- [x] Step 3: 验证并提交
  - [x] 跑相关测试和 lint
  - [x] 跑全量门禁
  - [x] 归档任务并提交

## 完成总结
- 计划 3 步 → 实际 3 步（0 次回退重试）
- 教训: 删除兼容层前要同步覆盖率配置和自省，否则死代码会变成死引用。
