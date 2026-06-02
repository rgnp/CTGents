# DeepSeek 前缀缓存优化设计

> 基于 [DeepSeek-Reasonix](https://github.com/esengine/DeepSeek-Reasonix) 的架构调研。

---

## 背景

DeepSeek 提供「自动前缀缓存」：连续两次 API 请求的**字节前缀完全一致**时，匹配到的缓存部分只收 **~10% 原价**。

### 历史问题（已修复）

项目早期存在一个缓存毒药问题：

```python
# 旧代码 — 已废弃
messages.insert(0, _make_env_message())   # 在开头插入，全量字节偏移
messages.insert(1, _make_project_context()) # 同样破坏缓存
```

`insert(0, ...)` 导致每次启动会话时整个 `messages` 字节前缀偏移，**DeepSeek 前缀缓存 100% 失效**。此问题已通过三段式上下文重构解决（见 Phase 3）。

### 目标

- 缓存命中率：~0% → **90%+**
- 长期会话 token 费用降低 **5-10 倍**

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

**实现位置**：`src/cache_context.py` — `CacheContext` 类（218 行）

| 组件 | 说明 |
|------|------|
| `CacheContext.prefix` | 会话开始计算一次，永不修改。包含 system prompt + tools |
| `CacheContext.log` | 列表，支持 `append()` 追加对话轮次 |
| `CacheContext.scratch` | 内存状态，不参与 `send()` 构建 |
| `ctx.send()` | 拼接 prefix + log，校验 prefix 哈希完整性 |
| `ctx.all` | 返回 prefix + log 完整列表，用于持久化 |

---

## 实施状态

### Phase 1：上下文压缩压缩 ✅ 已完成

**目标**：减少后续轮次中携带的冗余工具结果。

| 任务 | 说明 | 状态 |
|------|------|:----:|
| `_compact_cache_context()` | 保留最近轮次，压缩历史到摘要（支持话题切换检测） | ✅ |
| 话题切换压缩 | 检测到换话题时追加边界标记，历史日志完整保留 | ✅ |
| 附录优化 | 采用 append-only 策略：不删旧消息，仅末尾追加摘要 | ✅ |
| 接入对话循环 | 在 `run_conversation` 中调用 | ✅ |
| 测试 | 单元测试覆盖边界/类型/白名单 | ✅ |

**实现位置**：`src/llm.py` — `_compact_context()` / `_compact_cache_context()`

---

### Phase 2：工具结果截断 🔄 待评估

**目标**：减少 Log 中大型工具结果的 token 占用。

| 任务 | 说明 |
|------|------|
| `_truncate_tool_result()` | 工具结果 > 3000 token → 压缩为摘要（保留关键信息，省略详细输出） |
| 追加标记到 Log | 压缩后的结果附带提示：`[已压缩，原结果 X token，可用 read_file 重新读取]` |

**注**：当前 `_compact_cache_context` 已包含基本的上下文保留策略，Phase 2 的单独截断逻辑暂未实现，视实际 token 开销情况决定是否补充。

---

### Phase 3：三段式上下文重构 ✅ 已完成

**目标**：完全按照 Reasonix 的 Pillar 1 架构改造对话循环。

| 任务 | 状态 |
|------|:----:|
| `src/cache_context.py` — `CacheContext` 类（218 行） | ✅ |
| `CacheContext.prefix` — 会话开始计算一次，永不修改 | ✅ |
| `ctx.log.append(entry)` — 唯一写入 Log 的接口 | ✅ |
| `ctx.send()` — 拼接 prefix + log（过滤 scratch） | ✅ |
| `llm.py` 适配 — `run_conversation` 改用 `CacheContext` | ✅ |
| `session.py` 适配 — 会话保存/恢复兼容三段式结构 | ✅ |
| 前缀哈希校验 — `PrefixIntegrityError` 防止意外修改 | ✅ |
| `stats()` 统计接口 — 三段 token 用量统计 | ✅ |

---

### Phase 4：并行分发 + 高级优化 ❌ 未开始

| 任务 | 说明 |
|------|------|
| SAFE 工具并行 | read_file、git_status 等只读工具用 `concurrent.futures` 并行执行 |
| Storm 去重 | 相同 (tool, args) 在 3 轮滑动窗口内重复 → 跳过并追加反思提示 |
| Flatten schema | 参数 > 10 个的工具 schema 转扁平化点号表示 |

---

## 当前效果

| 指标 | 优化前 | 当前状态 |
|------|--------|---------|
| 消息结构 | `insert(0, ...)` 破坏前缀 | 三段式 `CacheContext`，prefix 固定不变 |
| 前缀缓存 | 100% 失效 | prefix 哈希校验，缓存候选 |
| 历史压缩 | 无 | 话题切换时自动摘要压缩 |
| Phase 4 并行/去重 | — | 待实现 |

> ⚠️ **注意**：当前仅完成了架构改造（Phase 3）和基础压缩（Phase 1），实际的缓存命中效果取决于 DeepSeek 服务端对 prefix 的缓存策略。后续可通过对比 `send()` 返回的 token 用量数据来量化验证。
