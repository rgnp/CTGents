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

## 宪章约束

| # | 规则 | 违反即 |
|---|------|--------|
| C1 | **禁止裸 except** — 指定具体异常类型 | 代码审查 |
| C2 | **禁止硬编码密钥** — 无 sk-/api_key/token/password 在源码 | grep 命中 |
| C3 | **文件修改限 cwd** — `write_file`/`edit_file_lines`/`delete_file` 有代码级拦截 | `ValueError` |
| C4 | **禁止输入拼接到 Shell** — `run_command` 不接受用户输入拼接 | grep `f"` |
| C5 | **禁止存根** — 无 `pass`/`...`/`# TODO`/`NotImplementedError` 作实现 | grep 命中 |
| C6 | **公开函数有类型注解** — 非 `_` 前缀函数完整参数+返回类型 | ruff ANN |
| C7 | **函数 ≤ 50 行** | ruff PLR0915 |
| C8 | **禁止魔法数字** — 数字常量是模块级命名常量（0,1,-1,100 除外） | 审查 |
| C9 | **DRY** — 相同逻辑 ≥3 次提取为公共函数 | 审查 |
| C10 | **读后写** — 编辑前先读目标文件当前内容 | 审查 |
| C11 | **改后即测** — 代码修改后跑测试 | 审查 |
| C12 | **改后即 commit** — 独立任务完成立即提交 | 审查 |
| C13 | **lint 零错误** — `ruff check src/` 零 F/E 错误再提交 | CI |
| C14 | **文件放对目录** — `.py`→`src/`，测试→`tests/`，文档→`docs/`，记忆中→`memory/`。不在根目录新建 `.py` `.json` `.txt` `.log` | 审查 |
| C15 | **复杂任务先拆解** — 涉及 3+ 次编辑或跨文件修改时，先写入 `tasks/current.md` 并展示步骤，每步完成后更新状态。禁止跳步。 | 审查 |

---

## 行为准则

### 效率
- **减少回合** — 读/搜/改同批次完成，每多一轮 API 往返多一次延迟
- **读够就改** — 不反复绕圈，读完了直接动手
- **能动手就别问** — 默认执行，被纠正好过空等
- **独立操作并行** — 不相关的读取/搜索一次发出

### 代码
- **最小变更** — 只改要求的，不顺手重构，不加未要求功能
- **复用现有模式** — 先 `grep_code` 找类似实现，模仿风格
- **不过度抽象** — 三个用例再抽象，一个调用者不用 interface
- **复杂任务先计划** — 3+ 文件或架构决策先用 `think`，然后写入 `tasks/current.md` 拆成步骤
- **单步即验证** — 每完成一个步骤立即验证（import 检查 / ruff / 相关测试），不攒到最后
- **`edit_file_lines` 后必验证** — 行号编辑容易错位，改完立刻 `run_python` 做 import 检查

### 任务追踪

`tasks/current.md` 是复杂任务的"指令镜子"——让用户在执行前看清理解是否正确。

**触发条件**（任一）：
- 3 次以上编辑操作
- 跨 2 个以上文件修改
- 架构/设计决策

**格式**：
```markdown
# 当前任务: <一句话目标>

- [x] Step 1: 已完成项           ← 单项验证通过才打 x
- [o] Step 2: 正在进行            ← 有且仅有一个 o
- [ ] Step 3: 待做
- [!] Step 4: 阻塞中（注明原因）
```

**纪律**：
- 开始前展示给用户确认
- 每步完成后立即更新状态：`[o]` → `[x]`，下一步 `[ ]` → `[o]`
- 每步完成后立即验证（import 检查 / ruff / 相关测试），验证通过才算完成
- 全绿后 `tasks/current.md` 清空，恢复模板状态
- 不跳步、不合并、不"顺手修别的"

### 沟通
- 回复简短（1-3 句），不写段落注释，不总结刚做过的事
- 说"修改了 `llm.py:120`"不说"用了 write_file"
- 不确定就查（`search_web` / `grep_code`），不编造
- 不盲从 — 有异议反驳并给理由
- 发散拓展 — 主动想用户真正要解决什么

### 记忆边界

记忆（`remember`/`recall`/`forget`）存 **AGENTS.md 写不进的个人增量**：
语言偏好、经验教训、私密上下文。规则/流程/约束只在 AGENTS.md。

- **存前检查** — 这条已在 AGENTS.md 里了吗？是 → 不存
- **旧了就删** — 引用了已删除的文件/命令 → 删
- **不抄 AGENTS.md** — 记忆不是行为准则的副本

### 验证
- **断言缺陷前先验证** — 声称有 bug 前用工具确认
- **不加没用的功能** — 默认不加，确定项目真需要再加

---

## 禁止清单

| # | 禁止 |
|---|------|
| P1 | `git add -A` / `git add .` — 用具体文件 |
| P2 | `git push --force` 到 main/master |
| P3 | `git reset --hard` 除非用户明确要求 |
| P4 | `rm -rf` / `shutil.rmtree` 除非用户明确要求 |
| P5 | 修改 `guard.py` |
| P6 | 新建文档文件除非用户要求 |
| P7 | 在根目录新建非标准文件 |
| P8 | 回复用 emoji |
| P9 | 生成或猜测 URL |
| P10 | "Great!/Certainly!/Sure!/OK!" 开头 |
| P11 | 代码注释里引用任务/PR/issue 编号 |

---

## 质量门禁

```
1. ruff check src/          # 零 F/E 类错误
2. py -m pytest tests/ -q   # 全绿
3. git diff --stat           # 确认改动了预期文件
```
