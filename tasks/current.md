# 当前长任务

## 目标
工具结果 head+tail 截断 + LLM 压缩摘要 — 保留有效信息，砍掉无效信息

## 步骤

- [x] Step 1: params.py — tool_result_compress_threshold 1200→2400 ✓
- [x] Step 2: llm.py — _compress_tool_result 改为 head+tail（各半）+ 中间省略标记 ✓
- [x] Step 3: llm.py — _make_brief_summary 替换为 LLM 最小可行摘要（文件/命令/决策+待办） ✓
- [o] Step 4: 提交
