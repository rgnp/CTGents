# 当前任务

- [x] Step 1: 修复 Git/命令执行安全边界
  - [x] 移除 `git_commit` 默认 `git add -A`，改为具体文件暂存
  - [x] 提交前强制运行 `ruff check src/` 和 pytest
  - [x] 限制 `run_command` 的 shell 元字符风险
- [x] Step 2: 修复 lint 门禁
  - [x] 清理 F/E 级错误
  - [x] 调整 Ruff 配置与项目实际约束一致
  - [x] 跑 `ruff check src/`
- [x] Step 3: 收紧自进化流程与回归验证
  - [x] 更新 `/evolve` 安全提示，避免承诺未实现的硬回滚
  - [x] 跑相关测试与全量测试
  - [x] 确认 diff 只包含预期文件

## 完成总结
- 计划 3 步 → 实际 3 步（1 次回退重试）
- 教训: 验证命令也要先确认测试文件存在，避免把命令拼错误判成代码失败。
