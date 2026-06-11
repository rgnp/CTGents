# 当前长任务

## 目标
_summarize_via_llm 走 _invoke_llm → 统计可见

## 步骤
- [x] _invoke_llm 加 tools 参数（None=默认工具, [] =无工具）
- [x] _summarize_via_llm 用 _invoke_llm 替代直调 client.chat.completions.create
- [o] 提交
