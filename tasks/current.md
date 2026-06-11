# 主动进化：诊断层

把 tracker 的原始异常翻译成可行动的诊断——"慢了 5.3x"变成"慢在哪、能不能修"。

## 计划

- [x] Step 1: 创建 `src/diagnostics.py` — 诊断模块
  - [x] 读工具实现，识别慢/失败的模式（subprocess 超时/无缓存/顺序执行等）
  - [x] 生成 DiagnosticResult（原因 + 受影响文件 + 建议 + 置信度）
  - [x] 写单元测试（24 个）
- [x] Step 2: 接入链路（在 make_task_context_message 中实时诊断，不走 reflection JSON 存储——保持诊断与代码同步）
- [x] Step 3: 升级 `tasks.make_task_context_message` — 展示诊断而非裸数字
- [x] Step 4: 全量回归 + lint 全绿

## 完成总结
- 计划 4 步 → 实际 4 步（1 次 edit 行号错位 + 重复代码修复）
- 教训: edit_file_lines 替换大段时容易残留旧行、漏掉 return None；write_file 会更快。
- 新增: src/diagnostics.py（262 行）+ tests/test_diagnostics.py（24 测试）
- 修改: src/tasks.py（诊断接入），tests/test_tasks.py（隔离 tracker mock）
