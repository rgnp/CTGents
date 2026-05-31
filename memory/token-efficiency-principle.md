---
name: token-efficiency-principle
description: 设计任何新功能时必须考虑 token 效率：
metadata:
  type: strategy
  updated: 2026-05-31T12:18:53Z
---

设计任何新功能时必须考虑 token 效率：
1. 所有 LLM 调用的 messages 必须走 CacheContext，让前缀部分可被 DeepSeek 缓存
2. 不可变的系统提示 → prefix；变化的对话 → log
3. 工具结果超过 2000 字符自动截断
4. 子代理等独立上下文场景：创建独立 CacheContext，复用 prefix 缓存
5. 能本地做的事不动 LLM（如 suggest、reflect、tracker 都是纯文件扫描）
