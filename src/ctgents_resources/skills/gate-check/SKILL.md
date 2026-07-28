# Gate Check — 交付前门禁

> 铁律（from Claude Code "Verification Before Completion"）：
> "Identify the verification command, run it fresh, read the full output,
>  then make your claim with evidence."
> 不靠推理说"应该行了"，不靠部分检查说"差不多了"。

---

## 触发时机

`task_done` 之前。不是每次小操作都走——简单事实（"这个文件存哪了"）跳过。
以下情况**必须走**：
- 有代码改动
- 有分析结论产出
- 有文件创建/修改
- current.md 步骤标了 `gate:`

---

## 流程（四步）

### 第 1 步：定位当前步骤

读 `tasks/current.md`，找第一个 `- [ ]` 未完成步骤。如果全部 `[x]`，看最后一步的 gate。

如果 current.md 为空或没有步骤 → 跳过（无 gate 可查）。

### 第 2 步：解析 gate 条件

gate 写在步骤行末尾，格式：

```markdown
- [ ] 步骤描述 → gate: 条件1；条件2；条件3
```

用 `；` 分号分隔多个条件。gate 也可以写在步骤下方缩进块里。

如果没有 gate 行 → 不做 gate-check，直接放行。

### 第 3 步：逐条验证

对每个 gate 条件，问三个问题：

1. **这个条件需要什么证据？**（文件存在？测试通过？数据提取完成？原文段落已引用？）
2. **证据在哪？** 把路径、命令输出、论文行号、文件内容都找出来。
3. **证据通过吗？** 不是"应该通过"——是亲眼看到通过。

验证类型与对应动作：
- **"产出 X 文件"** → read_file 或 find_files 确认文件存在且有实质内容
- **"测试通过"** → run_command 跑一遍，读完整输出
- **"引用论文 X 段落"** → read_file 确认段落确实被读了、不是凭记忆
- **"指标达到 Y"** → run_python 或 run_command 实测，不凭推理
- **"无冲突"** → grep_code 搜 imports 确认兼容

### 第 4 步：判決

- ✅ 全部 gate 通过 → 报告 "gate-check passed"，可以 `task_done`
- ❌ 有 gate 未通过 → 报告哪个条件没通过、缺什么证据
- ⚠️ 无法验证（比如需要用户亲自跑） → 报告哪些需要用户确认

---

## 输出格式

```
gate-check 结果（步骤: "读 ResWorld 消融实验"）

  ✅ gate 1: 产出 3 个可验证的发现 — knowledge/paper-deep-read/resworld-ablation.md 存在，含 3 条发现
  ❌ gate 2: 附论文行号 — 第 2 条发现缺少原文行号引用
  ⚠️  gate 3: 与 GraphWorld 对比 — 需要用户确认对比维度是否充分

判定: ❌ 不通过。修复 gate 2 后重试。
```

---

## 边界

- gate 条件不可验证（如"用户满意"）→ ⚠️ 标记，不阻塞
- current.md 无 gate 行 → 跳过，直接放行（不是所有任务都需要 gate-check）
- 多个未完成步骤时 → 只查第一个未完成步骤的 gate
