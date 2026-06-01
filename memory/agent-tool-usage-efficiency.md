---
name: agent-tool-usage-efficiency
description: 模型工具调用自检规则：
metadata:
  type: strategy
  updated: 2026-06-01T02:42:14Z
---

模型工具调用自检规则：
1. search_web 同一目标仓库/网站最多用 2 个关键词，优先用 site: 限定到目标域
2. read_page 前检查：a) 搜索结果摘要是否已包含足够信息 b) 此 URL 本次会话是否已读过（已读返回缓存）
3. run_python 仅用于数据计算/验证，不做 HTTP 请求（容易被墙，用 read_page 代替）
4. 自检回显和上下文统计不算"项目问题"，区分清楚 agent 工具行为 vs 项目代码问题
5. 研究类任务：先读最权威的一手文档（官方架构文档），摘要已覆盖核心内容则不继续展开
