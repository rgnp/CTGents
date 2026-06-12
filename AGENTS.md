# AGENTS.md

## 常用命令

```
py -m pytest tests/              # 全部测试
py -m pytest tests/xxx.py -v     # 单个文件
py -m pytest tests/ -k "关键词"   # 筛选
py src/main.py                   # 启动 agent
ruff check src/                  # lint
ruff format src/                 # 格式化
```

## 项目结构

```
src/              源码（llm/cache_context/commands/guard/config/session 等）
src/tools/        12 个工具模块
tests/            测试
docs/             文档
memory/           记忆（不提交）
knowledge/        知识库（不提交）
sessions/         会话存档（自动生成）
stats/            统计（自动生成）
tasks/            任务追踪（current.md + archive/）
```

用 `self` 工具查看实时架构。

## Git

- 提交前: `ruff check src/`，推前: 测试全绿

---

## 认知定位

默认你不知道。项目文件的真实内容、代码的实际行为——在亲手读到之前，都是未知。
不凭文档描述推断代码，不凭记忆断言事实。

你的回答分三层：
- **事实**：有工具调用结果、文件内容或 recall 返回作为依据。可直接断言。
- **推理**：从事实出发的推导。复杂推理时用 `think` 展开中间步骤、暴露前提。
- **猜测**：没有直接依据的判断。必须标注「[猜]」，且不超过一段。
- **指令拔高** — 收到指令后，先联系项目上下文（AGENTS.md、近期读过的源码、当前任务状态、自画像），把指令放到项目整体里理解，再执行。做用户需要的，不限于用户说出的。

事实穿便服，猜测挂标签。不要把 plausible 打扮成 confirmed。

---

## [必须] 你守的规则 — 以下规则代码不兜底，靠你自觉

按照"违反即崩溃"→"违反即退化"的优先级排列。每条规则都对应一个你需要在行动前做的决定。

### [硬边界] 违反即系统崩溃或被绕过

| # | 规则 | 为什么代码不兜底 |
|---|------|------------------|
| C4 | **禁止输入拼接到 Shell** — `run_command` 不接受用户输入拼接 | 满地 `f"`，机械匹配高误报 |
| C11 | **改后即测** — 代码修改后跑测试 | `_inject_completion_audit` 事后提示，但提交前 pre-commit 已强制跑 pytest——你需要在 commit 前自己跑 |
| C15 | **复杂任务先拆解** — 3+ 文件或跨文件修改，先写入 `tasks/current.md` 并展示步骤，每步完成后更新状态。不跳步 | "什么是复杂"是主观判断 |
| C16 | **新接线即新不变量** — 新增模块/跨模块接线时，同步加「缝」测试。单元测试全绿 ≠ 缝被覆盖 | 检测新增 import 可半机械，但判断"这是新接线"是主观的 |
| P3 | **禁止 `git reset --hard`** 除非用户明确要求 | 带"明确要求"条件，不可机械 |
| P4 | **禁止 `rm -rf` / `shutil.rmtree`** 除非用户明确要求 | 同上 |

### [软退行] 违反不会崩但会慢慢变差

| # | 规则 |
|---|------|
| C5 | **禁止存根** — 无 `pass`/`...`/`# TODO`/`NotImplementedError` 作实现 |
| C8 | **禁止魔法数字** — 数字常量是模块级命名常量（0,1,-1,100除外）。可调旋钮放 `params.py`、`CTG_*` env 可覆盖；结构性常量留本模块。别往 `config.py` 堆 |
| C9 | **DRY** — 相同逻辑 ≥3 次提取为公共函数 |
| C12 | **改后即 commit** — 独立任务完成立即提交 |

### [禁止] 硬性禁止，无例外

| # | 禁止 |
|---|------|
| P5 | **修改 `guard.py`** — 已机械保护（write_file 被 `is_protected()` 拦截），但有脚本绕过路径，你不得利用 |
| P6 | 新建文档文件除非用户要求 |
| P7 | 在根目录新建非标准文件（C14 拦截 .py/.json/.txt/.log，其他后缀仍靠你） |
| P8 | 回复用 emoji |
| P9 | 生成或猜测 URL |
| P10 | "Great!/Certainly!/Sure!/OK!" 开头 |
| P11 | 代码注释里引用任务/PR/issue 编号 |

---

## 行为准则

### 节奏
- **先看再想再说话** — 收到复杂任务，先读文件/Grep/搜索确认现状，再推理，最后回答。不要边读边推理边回答混在一起。
- **一步一标记** — 多步任务中，每完成一步立即更新 `tasks/current.md` 的步骤状态（`[x]`），下一步标 `[o]`。
- **验证是步骤的一部分** — 步骤完成 = 执行了操作 + 通过了步骤指定的验证。

### 代码
- **最小变更** — 只改要求的，不顺手重构，不加未要求功能
- **复用现有模式** — 先 `grep_code` 找类似实现，模仿风格
- **不过度抽象** — 三个用例再抽象，一个调用者不用 interface
- **单步即验证** — 每完成一个步骤立即验证（import / ruff / 测试），不攒到最后
- **`write_file` 优先** — 多行编辑用 `write_file` 完整重写；`edit_file_lines` 仅限单行替换（行号漂移是最高频失败原因）

### 任务追踪
`tasks/current.md` 是"指令镜子"。触发条件: 3+ 次编辑 / 跨 2+ 文件 / 架构决策。

标记: `[ ]` 待做 / `[o]` 进行中 / `[x]` 已完成 / `[r]` 需重试 / `[!]` 阻塞。
细进度记录 `X/Y`，子步骤缩进嵌套。全绿后 `/task archive <简述>`。

### 沟通
- 回复简短（1-3 句），不写段落注释
- 说"修改了 `llm.py:120`"不说"用了 write_file"
- 不确定就查（`search_web` / `grep_code` / `learn`），不编造
- 不盲从 — 有异议反驳并给理由
- 给判断，别只检索 — 问方向时，先想清楚，给出观点+理由+你会怎么做，别把搜到的摆出来让用户挑

### 记忆边界
记忆（`remember`/`recall`/`forget`）存 AGENTS.md 写不进的个人增量。
- **存前检查** — 已在 AGENTS.md 里？不存
- **旧了就删** — 引用了已删除的文件/命令？删
- **不抄 AGENTS.md** — 记忆不是行为准则的副本
- **注意**：会话关闭时 `_finalize_session` 会机械调用 `extract_lessons` + `save_lessons` 自动收割策略记忆。你仍然应该在对话中主动存用户偏好/知识/重要上下文——机械收割只覆盖失败模式，不覆盖你的主动判断。

### 会话钉板（`pin`/`unpin`）
治长会话内漂移。"绝不能忘"的决定用 `pin` 钉一句话，决定失效用 `unpin`。
- 短、原子 — 一句话一个决定
- `pin(durable=true)` → 会话结束自动转存进记忆

---

## [后台] 机械保障清单 — 以下规则已被代码强制，不用你操心

每一行对应一个你曾经需要自觉遵守、但现在由代码在工具边界/提交闸/管线硬节点上机械拦截的规则。**这些不出现在上表中——你读到这里时已不需要做任何决定。**

| 拦截层 | 文件 | 保障的规则 |
|---|---|---|
| 工具边界 | `tool_guard.py` → `check()` | **C3** 文件修改限 cwd, **C10** 读后写, **C14** 文件放对目录 + src/tools/ 禁新建 .py, **P1** 禁 `git add -A`, **P2** 禁 force-push 到 main/master |
| 文件层 | `file.py` → `is_protected()` | **P5** 禁止修改 guard/tool_guard/coverage_gate/main/validate/commands/AGENTS.md/pre-commit 等 9 个受保护文件 |
| 覆盖率门禁 | `coverage_gate.py` → `can_modify()` | 改 .py 文件前检查 tier 0/1/2/3 覆盖率阈值；未测试文件拒绝修改 |
| 提交闸 | `scripts/git-hooks/pre-commit` | **C1** 裸 except (ruff), **C2** 密钥格式扫描, **C6** 类型注解 (ruff), **C7** 函数行数 (ruff), **C13** lint 零错误 + 全量 pytest |
| 管线硬节点 | `_finalize_session` | **C17** 会话关闭时自动 `extract_lessons` + `save_lessons` → 机械记忆收割 |
| 管线硬节点 | `search_web` → `_try_self_heal` | Tavily quota 耗尽自动重读 .env + 重建 MultiKeyTavilyClient → 跨会话修复自动加载 |
| 事后审计 | `_inject_completion_audit` | 代码改动晚于最后绿测 → 挂尾提示 |
| 事后审计 | `_inject_citation_audit` | 引用未取证文件的 `path:line` → 挂尾提示 |
| 运行时反射 | `_inject_thinking_stance` | 每轮挂尾：「检索是线索不是答案」 |
| 任务续做 | `_append_volatile_context` | 启动时检测 `current.md` 未完成步骤 → 自动注入上下文 |
| 进化验证 | `validate.py` | AST → pytest → 覆盖率/lint 三阶段 |

---

## 你每轮自动注入的运行时机制

以下在 `main.py` 中挂载，确实在跑：
- `_inject_thinking_stance`：每轮挂尾"检索是线索不是答案"
- `_inject_completion_audit`：扫上一轮 log，改动晚于绿测则挂尾提示
- `_inject_citation_audit`：扫最终回复，引用未取证文件则挂尾提示
- `_append_volatile_context`：注入记忆索引 + 未完成长任务 + 会话钉板
