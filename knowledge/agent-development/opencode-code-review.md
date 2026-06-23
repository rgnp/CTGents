# OpenCode 深度审查：代码与逻辑问题

> 审查日期: 2026-06-22
> 目标: 不是"它做了什么"，而是"它什么地方有问题"
> 项目: anomalyco/opencode (commit 23fd5907b)

---

## 一、System Context 模块问题

### 1.1 🔴 空渲染直接崩溃（index.ts L305-308）

```typescript
function requireText(key, kind, text) {
  if (text.length === 0) throw new Error(`System context source ${key} rendered an empty ${kind}`)
  return text
}
```

如果某个 source 返回空字符串（比如环境变量没设置、文件不存在），`baseline()` 或 `update()` 返回空字符串 → `requireText` 抛异常 → 整轮对话崩溃。

**我们的版本应该怎么避免：** source 的 baseline/update 为 None 时直接跳过（不注入），而不是抛异常。

### 1.2 🟡 Unavailable source 静默失败（L230-233）

```typescript
for (const entry of entries) {
  if (entry._tag === "Unavailable") continue  // 静默跳过
  const stored = getSnapshot(previous, entry.key)
  if (!stored) continue  // 新源但不可用 → 静默跳过
```

当一个 source 第一次加载失败时，LLM 完全不知道「有个上下文源加载失败了」。加载失败和没有这个 source 在 LLM 看来没有区别。

### 1.3 🟡 Removed 消息是强制的（L271）

```typescript
if (removed === undefined) throw new Error(...)
```

每个 source 必须有 `removed` 回调，否则卸载时会崩溃。但实际上不是所有 source 都需要卸载通知——一个只在会话开始时加载一次就不再变的 source，卸载时不需要通知 LLM 说「它不在了」。

### 1.4 🟡 多个 update 合并成一块无结构文本（L294）

```typescript
function render(parts) {
  return parts.join("\n\n")
}
```

如果 5 个 source 同时变更，LLM 收到的是 5 段文本用空行拼在一起，没有「这里有几个独立变更」的结构信息。LLM 可能漏掉中间的某条变更。

---

## 二、Tool 系统问题

### 2.1 🔴 非字符串输出静默丢失（tool.ts L107）

```typescript
?? (typeof output === "string"
  ? [{ type: "text", text: output }]
  : [])  // 非 string → 返回空数组，结果不发给 LLM
```

如果 tool 的 execute 返回一个对象而非字符串，并且没有定义 `toModelOutput`，那么 **tool 执行成功了但 LLM 永远看不到结果**。这是一个静默数据丢失。

**例子：** 一个 tool 返回 `{ files: 3, bytes: 1024 }`（对象）且没设置 `toModelOutput` → `[]` → LLM 以为 tool 返回了空。

### 2.2 🟡 Stale call 检测只覆盖栈顶（registry.ts L49-53）

```typescript
const registration =
  local.get(input.call.name)?.at(-1)?.registration ?? applications.entries().get(input.call.name)
```

stale 检测只查栈顶的 identity。如果工具被替换了两次（旧→新→更新），用第一个 identity 调用时返回 `"Stale tool call"`，但用第二个 identity 调用时也返回 `"Stale tool call"`（因为栈顶是第三个）。LLM 无从知道哪个版本是当前的。

### 2.3 🟡 whollyDisabled 只检查全局 deny（registry.ts L131-134）

```typescript
function whollyDisabled(action, rules) {
  const rule = rules.findLast((rule) => Wildcard.match(action, rule.action))
  return rule?.resource === "*" && rule.effect === "deny"
}
```

只检查是否有针对所有资源（`resource: "*"`）的全局 deny 规则。如果规则是 `{ action: "read", resource: "src/secret/*", effect: "deny" }`（特定目录），`whollyDisabled` 返回 false，工具仍然出现在 LLM 的可用工具列表中。LLM 以为可以调用，到执行时才被拒绝。

---

## 三、Session Runner 问题

### 3.1 🟡 位置检查导致中间结果丢失（llm.ts L171-172）

```typescript
if (session.location.directory !== location.directory ||
    session.location.workspaceID !== location.workspaceID)
  return yield* Effect.interrupt
```

同一轮内如果用户切换了工作目录，这轮对话被中断。但此时可能已经有 tool 执行了一半——Bash 可能已执行、文件可能已写入。这些副作用不会被回滚，但 LLM 看到的是中断后的结果，不知道哪些 tool 执行了哪些没执行。

### 3.2 🟡 Effect.die 作为控制流（llm.ts L155-156, L325-339）

```typescript
const continueAfterCompaction = step => new TurnTransitionError({ _tag: "ContinueAfterCompaction", step })
// ...
return yield* Effect.die(continueAfterCompaction(currentStep))  // 用 die 做 goto
```

用 `Effect.die()`（致命错误）来跳转到压缩后继续执行，然后在调用方用 `instanceof TurnTransitionError` 拦截。这是用异常做控制流。如果中间有其他 catch 插进来，会把它当成真崩溃处理。

### 3.3 🟡 FiberSet 每轮新建（llm.ts L175）

每轮新创建一个 `FiberSet`，但旧的 fiber 可能还没完成。如果前一轮启动了一个长耗时工具（如大型 web-fetch、bash sleep），新的 fiber set 和旧的没有关联，无法跟踪或取消这些孤儿工具。

---

## 四、权限系统问题

### 4.1 🟡 多资源评估太粗糙（permission.ts L185-186）

```typescript
const effects = input.resources.map(resource => evaluate(input.action, resource, all).effect)
const effect = effects.includes("deny") ? "deny" : effects.includes("ask") ? "ask" : "allow"
```

如果一个操作涉及多个资源（如 `read file1 file2`），评估结果是 `deny` 只要有任何一个资源被 deny。这意味着无法表达「部分资源允许，部分 deny」——要么全放行，要么全拒绝。

### 4.2 🟡 默认"ask"可能产生疲劳（permission.ts L108-111）

```typescript
rulesets.flat()
  .findLast(...) ?? { action, resource: "*", effect: "ask" }
```

默认是 `ask`（询问用户）。如果一个 Agent 的工具没有配置权限规则，每次调用都会弹出确认框。这在开发阶段可以接受，但在高频操作（如连续 `read` 10 个文件）时会让用户疯狂点确认。

---

## 五、编译时代码质量问题

### 5.1 🟢 次要: run-coordinator.ts L57-58 重复 delete

```typescript
active.delete(key)          // 第一次删除（L56）
if (successor === undefined) active.delete(key)  // 第二次删除（L58）
```

两次 `active.delete(key)`，第一次已经删了，第二次是空操作。不造成错误，但说明代码可能有清理逻辑残留。

### 5.2 🟢 注释: 发现一处 TODO 超过 25 行（tool/tools.ts）

```
TODO: Port the remaining launch-follow-up leaves deliberately: edit fuzzy
parity, task, LSP, repo_clone, repo_overview, plan_exit, and Rune/code mode.
```

这个 TODO 列出了 8 项未实现的功能，占用了大约 25 行。说明这个模块还是半成品。

---

## 六、总结：最值得注意的三个问题

| 优先级 | 问题 | 影响 |
|--------|------|------|
| 🔴 | tool 返回对象时结果静默丢失 | 数据丢失，LLM 不知道工具执行结果 |
| 🔴 | 空 context 渲染直接崩溃 | 一个 source 返回空字符串就整轮崩 |
| 🟡 | Effect.die 做控制流 | 异常拦截链出问题时难以调试 |
