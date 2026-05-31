---
name: goal-command-design
description: /goal 命令设计决策：
metadata:
  type: knowledge
  updated: 2026-05-31T04:44:25Z
---

/goal 命令设计决策：
1. GoalRunner 用自己的最小上下文（不是 CacheContext），省 token
2. LLM 输出结构化 JSON：{"action":"tool_call|done|fail","tool":"...","args":{...},"reasoning":"..."}
3. 历史只保留最近 3 步完整记录，更早的压缩为摘要
4. 最多 50 轮迭代，支持 Esc 中断
5. 写完 src/ 下的文件后自动 importlib.reload
6. 工具调用直接走 execute_tool()，以 function.name / function.arguments 的 dict 形式传入（用 SimpleNamespace 包装）
