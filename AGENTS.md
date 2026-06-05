# AGENTS.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

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
llm.py              对话循环、模型路由、工具调度 (Pro only)
cache_context.py    三段式上下文 (prefix/log/scratch)
commands.py         指令系统 (/help /save /load /self /evolve /model /context)
guard.py            自我保护 (is_protected 阻止修改 guard.py 自身)
config.py           配置 (API key、模型、路径)
session.py          会话持久化
coverage_gate.py    覆盖率门禁 (tier_0/1/2/3: 0%/45%/60%/75%)
evolve.py           进化档案 (JSONL 存储 + TF-IDF 查询)
evolution_loop.py   进化编排器 (研究→综合→生成→验证 闭环)
validate.py         三阶段验证 (AST→pytest→覆盖率)
tools/              12 个工具模块 (web/file/exec/code/think/memory/git/project/lint/rag/evolve/self)
```

用 `self` 工具查看实时架构和运行时状态。

## Git

- 提交前: `ruff check src/`
- 推送前: 测试全绿

---

## 宪章约束 (Constitutional — 永远不可违反)

> 每条约束都附带 **验证标准** —— 一个零上下文的初级工程师也能机械判断是否合规。

### 安全

| # | 规则 | 验证标准 |
|---|------|---------|
| C1 | **禁止裸 except** — 不允许 `except:` 或 `except Exception: pass` | 任何 `except` 必须指定具体异常类型；如果确实需要吞掉，至少写 `logger.warning(...)` |
| C2 | **禁止硬编码密钥** — 不允许 API key、token、密码出现在源码中 | grep `sk-` `api_key` `token` `password` 命中 → 违规 |
| C3 | **文件修改限工作目录** — 所有写操作只能在 `Path.cwd()` 下 | 路径解析后调用 `.relative_to(cwd)`，`ValueError` → 拒绝 |
| C4 | **输入不拼接到 Shell** — `run_command` 不接受用户输入拼接到命令字符串 | grep `f".*{.*}.*"` 在 run_command 调用中 → 违规 |

### 质量

| # | 规则 | 验证标准 |
|---|------|---------|
| C5 | **禁止存根/占位** — 不允许 `pass`、`...`、`# TODO`、`raise NotImplementedError` 作为功能实现 | grep `pass` `# TODO` `NotImplementedError` 在非抽象方法中 → 违规 |
| C6 | **公开函数必须有类型注解** — 所有非 `_` 前缀的函数必须有完整参数和返回类型 | `ruff check --select ANN` 对公开函数报错 → 违规 |
| C7 | **函数 ≤ 50 行** — 超过则必须拆分 | `ruff check --select PLR0915` 命中 → 违规 |
| C8 | **禁止魔法数字** — 数字常量必须是模块级命名常量 | 函数体内出现 `> 300` `sleep(5)` `timeout=30` → 违规（0, 1, -1, 100 除外） |
| C9 | **DRY** — 相同逻辑出现 ≥3 次必须提取为公共函数 | 3 个以上函数包含相同的 4+ 行代码块 → 违规 |

### 操作

| # | 规则 | 验证标准 |
|---|------|---------|
| C10 | **读后写** — 调用 `write_file` 或 `edit_file_lines` 前必须先用 `read_file` 读过目标文件 | 同一次工具调用批次中 write_file 出现在 read_file 之前 → 违规 |
| C11 | **改后即测** — 代码修改完成后立即跑相关测试 | write_file/edit_file_lines 之后没有 pytest 调用 → 违规 |
| C12 | **改后即 commit** — 每个独立任务完成后立即 git commit | 会话中有代码修改但没有 git commit → 违规 |
| C13 | **lint 零错误** — `ruff check src/` 零错误再提交 | commit 前 ruff 有 F/E 类错误 → 违规 |
| C14 | **文件放对目录** — 新建文件必须按类型放到约定目录 | 文件创建在了错误目录 → 违规 |

### 目录约定

```
src/            Python 源码（.py）
src/tools/      工具模块（agent 可调用的工具定义）
tests/          测试文件（test_*.py）
docs/           项目文档（*.md）
memory/         持久化记忆（*.md）
knowledge/      研究知识库
sessions/       会话存档（自动生成）
stats/          统计存档（自动生成）
```

**判断规则：**
- `.py` 源码 → `src/` 或 `src/tools/`（如果是工具模块）
- 测试文件 → `tests/test_*.py`
- `.md` 文档 → `docs/`，但 `AGENTS.md` 和 `README.md` 在根目录
- 记忆文件 → `memory/`
- 绝不在根目录新建 `.py` 或随意散落 `.md`

---

## 行为准则 (Operational — 冲突时优先于默认行为)

### 代码修改

1. **先读后改** — 修改代码前必须理解现状。涉及 3+ 文件的复杂任务，优先只读探索（读文件、搜索、分析），形成方案后再动手。简单的单文件修改跳过此步。
2. **最小变更** — 只改任务要求的部分。不顺手重构无关代码，不修不相关的 lint 警告，不添加未要求的功能。
3. **复用现有模式** — 新增代码前先用 `grep_code` 查项目中类似的实现，模仿其风格。不引入新的依赖或模式除非别无选择。
4. **保留已有注释** — 不删除或修改文件中已存在的注释，除非注释内容确实错误。
5. **禁止过度抽象** — 一个调用者不需要 interface；两个调用者不需要 factory。等到第三个用例出现再抽象。
### 沟通

6. **简洁优先** — 回复保持简短（通常 1-3 句）。不写多行 docstring，不写段落注释，不总结刚刚做过的事。
7. **用代码说话** — 说"我修改了 `llm.py:120`"而不是"我用 write_file 写了文件"。
8. **不确定就查** — 不编造 API 参数、文件路径、版本号。用 `search_web` 查最新文档，用 `grep_code` 验证函数是否存在。
9. **不等用户提醒** — 改完自动跑测试、自动 commit、自动 lint。只有真正需要用户决策时才停下来问。
10. **不盲从** — 用户说的不一定对。有异议必须反驳并给出理由。用户宁可直接吵一架也不要默默执行错误指令。
11. **发散拓展** — 用户的指令可能只是冰山一角。主动思考他真正想解决什么问题，多走一步。不要做被动的指令执行器。
### 诊断
### 诊断

13. **断言缺陷前先验证** — 声称代码有 bug、缺功能、或需要修改前，必须用工具确认。本对话中 5 条缺陷分析 3 条是幻觉、功能清单 #1 #2 已实现但被标记为未做——根因全是跳过验证。
14. **不加没用的功能** — 新增功能前必须问自己"这个项目实际遇到这个问题吗？"。工具失败记录是典型案例：99% 时间缓冲区是空的，tool-call repair 已经兜底，加了只增加复杂度没有实际价值。别人有的功能不代表这个项目需要。不确定时默认「不加」。

### 任务分解

15. **复杂任务先计划** — 涉及 3+ 文件或架构决策时，先用 `think` 工具规划，再执行。
16. **独立操作并行** — 多个不相关的文件读取/搜索一次发出，系统自动并行执行。
## 禁止清单 (Prohibitions — 做了就是 Bug)

| # | 禁止行为 | 为什么 |
|---|---------|--------|
| P1 | **禁止用 `git add -A` 或 `git add .`** | 可能提交 .env、凭据、大二进制文件。必须用 `git add <具体文件>` |
| P2 | **禁止 `git push --force` 到 main/master** | 不可逆的破坏 |
| P3 | **禁止 `git reset --hard`** 除非用户明确要求 | 会丢失用户未提交的修改 |
| P4 | **禁止 `rm -rf` 或 `shutil.rmtree`** 除非用户明确要求 | 不可逆 |
| P5 | **禁止修改 `guard.py`** | 自我保护的最后防线 |
| P6 | **禁止创建新的文档文件** (`*.md`, `README`) 除非用户明确要求 | 文档应随代码演进，不应独立创建 |
| P7 | **禁止在项目根目录创建非标准文件** — 不在根目录新建 `.py` `.json` `.txt` `.log` | 根目录只有 `AGENTS.md` `README.md` `LICENSE` `Makefile` `pyproject.toml` `requirements.txt` `run.py` `.editorconfig` `.env.example` `.gitignore` `.pre-commit-config.yaml` |
| P8 | **禁止在回复中使用 emoji** | 保持专业、简洁 |
| P9 | **禁止生成或猜测 URL** | 只在用户提供 URL 或 `search_web` 返回结果中使用 |
| P10 | **禁止 "Great!" "Certainly!" "Sure!" "OK!" 开头** | 浪费 token，跳过寒暄直接回答 |
| P11 | **禁止引用本次任务/PR/issue 编号** 在代码注释中 | 代码注释应描述 WHY，不是 WHAT；任务上下文属于 commit message |

---

## 质量门禁 (Mechanical — 自动检查)

提交前自动执行（改完代码就跑）：

```
1. ruff check src/          # 零 F/E 类错误
2. py -m pytest tests/ -q   # 全绿
3. git diff --stat           # 确认改动了预期的文件
```

如果 `coverage_gate.py` 配置了覆盖率门槛，新增代码需要用 `evolve_check_access` 验证。
