# 目标锚点
从 0→1 稳固性补全：3 个缺口依次修复，不改架构只补测试/消缺陷。

- [x] #3: `_archive_run` 的 `except Exception: pass` → `logger.warning` + 异常路径测试
- [o] #1: `_finalize_session` 收尾管线 — 零测试覆盖，补集成测试
- [ ] #4: `tasks.py` 内联诊断格式化 → 调 `format_diagnostics()` 合并 DRY
- [ ] 扫 tests/ 中写真实源文件/共享状态的测试（用户要求的额外检查）
- [ ] 归档 current.md → tasks/archive/
