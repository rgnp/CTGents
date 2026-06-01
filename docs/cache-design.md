# DeepSeek 前缀缓存优化设计

> 基于 [DeepSeek-Reasonix](https://github.com/esengine/DeepSeek-Reasonix) 的架构调研。

---

## 背景问题

### 现状

DeepSeek 提供「自动前缀缓存」：连续两次 API 请求的**字节前缀完全一致**时，匹配到的缓存部分只收 **~10% 原价**。

但当前项目存在一个致命问题：

```python
# src/main.py — 缓存毒药
messages.insert(0, _make_env_message())  # ← 在开头插入，全量字节偏移
messages.insert(1, _make_project_context())  # ← 同样破坏缓存
```

`insert(0, ...)` 导致每次启动会话时，整个 `messages` 列表的每个字节都向后偏移，**DeepSeek 前缀缓存 100% 失效**。

### 目标

- 缓存命中率：~0% → **90%+**
- 长期会话 token 费用降低 **5-10 倍**
- 不改架构的情况下先做第一阶段，逐步推进

---

## 核心设计：三段式上下文

```
┌─────────────────────────────────────────────┐
│ IMMUTABLE PREFIX      ← 会话级固定           │
│ - 系统提示词                                 │  永不修改，哈希验证
│ - 工具定义 (TOOLS)                           │  缓存命中候选
│ - 环境信息 + 项目上下文                      │
├─────────────────────────────────────────────┤
│ APPEND-ONLY LOG        ← 每轮末尾追加        │
│ - [user₁]                                   │  前面的字节永不改变
│ - [assistant₁ + tool_calls]                 │  → 前缀缓存持续命中
│ - [tool_results₁]                            │
│ - [user₂] → ...                             │
├─────────────────────────────────────────────┤
│ VOLATILE SCRATCH       ← 不发给 API         │
│ - 思考过程（think 工具）                     │  只在内存中，
│ - 中间计划状态                               │  永不进入 API 请求正文
│ - 安全模式信息                               │  （但可影响行为）
└─────────────────────────────────────────────┘
```

### 构建规则

| **Prefix 不变** | 会话开始时计算一次 prefix hash。prefix 中的 `_volatile` 消息被 `send()` 跳过，不发给 API |
| **Log 只追加** | `messages` 永远只用 `.append()`，不使用 `.insert(0, ...)` 或 `.pop(0)` |
| **Scratch 不发给 API** | `_volatile` 标记的消息在 `send()` 中完全过滤（prefix 区跳过，log 区 system 消息放末尾不影响前缀） |
| **工具结果压缩** | 超过 3000 token 的工具结果在追加到 Log 前压缩为摘要 |
| **前缀内容不得含动态数据** | 禁止 `os.getcwd()`、时间戳等动态值出现在 prefix 中 → 跨会话缓存命中 |
---

### Phase 1：修复缓存毒药 ✅

> 已完成（2026-06-01）。三阶段全量实施。

| 任务 | 说明 | 状态 |
|------|------|------|
| `CacheContext` 三段式架构 | prefix/log/scratch 分区 + 哈希校验 | ✅ |
| `send()` 严格过滤 volatile | `_volatile` 标记的前缀消息完全不发给 API | ✅ |
| `_make_env_message()` 固化 | 移除 `os.getcwd()`，字节级稳定前缀 | ✅ |
| `_build_api_messages()` 向后兼容 | 旧代码无需修改 | ✅ |

**验证**：前缀哈希机制确保不可变 prefix 被意外修改时立即报错。
### Phase 2：工具结果压缩

### Phase 2：工具结果压缩 ✅

> 目标：减少后续轮次中携带的冗余工具结果。

| 任务 | 说明 | 状态 |
|------|------|:----:|
| `_compress_tool_result()` | 超过 3000 字符 → 截断 + 提示语（read_file/search_web 等有专属提示） | ✅ |
| 不压缩白名单 | git_status/git_diff/check_project 等短小工具不压缩 | ✅ |
| 接入对话循环 | 在 `run_conversation` 中 `truncate_to_budget` 之后调用 | ✅ |
| 测试 | 18 个单元测试覆盖边界/类型/白名单 | ✅ |

**验证**：`test_cache.py` 20 个测试全部通过。

---


**目标**：减少后续轮次中携带的冗余工具结果。

| 任务 | 说明 |
|------|------|
| `_truncate_tool_result()` | 工具结果 > 3000 token → 压缩为摘要（保留关键信息，省略详细输出） |
| 追加标记到 Log | 压缩后的结果附带提示：`[已压缩，原结果 X token，可用 read_file 重新读取]` |

### Phase 3：三段式上下文重构

**目标**：完全按照 Reasonix 的 Pillar 1 架构改造对话循环。

| 任务 | 说明 |
|------|------|
| `src/cache_context.py` | 新模块：`CacheContext` 类，管理 prefix/log/scratch 三段 |
| `CacheContext.prefix` | 会话开始计算一次，永不修改。包含 system prompt + tools |
| `CacheContext.append_log(entry)` | 唯一写入 Log 的接口 |
| `CacheContext.build_api_messages()` | 拼接 prefix + log（过滤 scratch），发给 API |
| `llm.py` 适配 | `run_conversation` 改用 `CacheContext` 而非裸 `list[dict]` |
| `session.py` 适配 | 会话保存/恢复兼容新的三段式结构 |

### Phase 4：并行分发 + 高级优化

| 任务 | 说明 |
|------|------|
| SAFE 工具并行 | read_file、git_status 等只读工具用 `concurrent.futures` 并行执行 |
| Storm 去重 | 相同 (tool, args) 在 3 轮滑动窗口内重复 → 跳过并追加反思提示 |
| Flatten schema | 参数 > 10 个的工具 schema 转扁平化点号表示 |

---

## 验收标准

| 阶段 | 验收条件 |
|------|----------|
| Phase 1 | 两轮相同对话，第二轮输入 token 低于第一轮 30%+ |
| Phase 2 | 10 轮工具调用会话，总输入 token 减少 40%+ |
| Phase 3 | 50 轮长会话，缓存命中率 > 80% |
| Phase 4 | 并行工具调用耗时减少 > 30% |

---

## 参考

- [DeepSeek-Reasonix 架构文档](https://github.com/esengine/DeepSeek-Reasonix/blob/main/docs/ARCHITECTURE.md)
- DeepSeek 前缀缓存文档：连续请求前缀匹配时，缓存命中部分按 ~10% 计费
