# 防止双指纹机制撞车

# 目标锚点
memory.py 的 `_find_by_fingerprint` 扫到 lesson.py 写的文件，LLM 一旦传 lesson.py 的指纹值就会覆盖 19 次积累的结构化教训。需要隔离两个系统的 fingerprint 命名空间。

- [o] Step 1: `_find_by_fingerprint` 跳过 lesson.py 文件（metadata 有 `severity`）
  - [ ] 改 `_find_by_fingerprint`：扫描时检查 `meta.get("severity")`
  - [ ] 验证：import + ruff + pytest
- [ ] Step 2: 改 `remember` 工具 schema 示例，从 `tool_arg_error` 改为无害值
- [ ] Step 3: 在两个文件加注释标记命名空间边界
- [ ] Step 4: 提交
- [ ] 归档 current.md → tasks/archive/
