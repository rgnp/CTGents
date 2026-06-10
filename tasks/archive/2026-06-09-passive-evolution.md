# 被动进化三层：感知→分析→执行

- [x] Step 1: 摸排已有资产
- [x] Step 2: 感知层 — `src/tracker.py` + 钩入 `_execute_tool_batch()`
  - ✅ tracker.py: JSONL 记录、线程安全、atexit 自清理、聚合/基线/异常检测
  - ✅ llm.py: `_tracked_execute_tool` 包裹串行+并行调用路径
  - ✅ llm.py: `run_conversation` 入口 `set_session()`
  - ✅ 测试: tests/test_tracker.py (7 用例)
- [x] Step 3: 分析层 — 会话结束自动反思 + 异常检测
- [x] Step 4: 执行层 — 接通进化档案，主动提议
- [x] Step 5: 端到端验证 + 提交
