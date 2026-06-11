# 当前长任务

## 目标
重做工具并行：eager execution — LLM 流式期间就启动 SAFE 工具

## 步骤

- [x] Step 1: params.py — eager_executor_workers (default 8) ✓
- [x] Step 2: llm.py — chat_stream 加 on_tool_ready + _invoke_llm_eager + run_conversation 接线 ✓
- [x] Step 3: tests/test_llm.py — mock 迁移 _invoke_llm → _invoke_llm_eager ✓
- [o] Step 4: 提交
