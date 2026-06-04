# AGENTS.md

## 常用命令

```
py -m pytest tests/              # 全部测试
py -m pytest tests/xxx.py -v     # 单个文件
py -m pytest tests/ -k "关键词"   # 筛选
py src/main.py                   # 启动 agent
py src/self_portrait.py          # 自画像（架构/工具/测试/覆盖率）
py src/self_portrait.py --short  # 一行摘要
py src/self_portrait.py --health # 坏代码检测
ruff check src/                  # lint
ruff format src/                 # 格式化
```

## 项目结构

用 `py src/self_portrait.py` 查看实时结构。关键文件：

```
llm.py              对话循环、模型路由、工具调度
coverage_gate.py    函数级关联测试门禁（tier_0/1/2/3: 0%/45%/60%/75%）
guard.py            崩溃自愈 + 文件保护
cache_context.py    三段式上下文
commands.py         指令系统
safety.py           工具安全等级 + auto/manual 模式 + 会话信任
evolve.py           进化档案
validate.py         三阶段验证（AST→pytest→覆盖率）
tools/              17 个工具模块
```

## Git

- 提交前: `ruff check src/` + `docs_sync_check`
- 推送前: 测试全绿

---

## AI 行为约束

> 每条规则都必须是可执行、可验证的操作指令，不是建议。

### 修改代码前

1. **先读后改** — 调用 `write_file` 或 `edit_file_lines` 前，必须先用 `read_file` 读取目标文件当前内容。文件可能被之前的编辑改变。

2. **理解全貌** — 修改函数前查引用：`grep_code` 找调用者，`rag_query` 搜相关逻辑。

3. **并行读取** — 多个不相关的文件一次发出多个 `read_file`，系统自动并行。

### 修改代码后

4. **自动跑测试** — 改完立即跑，不等用户提醒：
   - 单文件 → `py -m pytest tests/test_xxx.py -v`
   - 多文件 → `evolve_validate(changed_files=[...])`

5. **lint 干净** — `ruff check src/` 零错误再提交。

6. **覆盖率不降** — 新增代码用 `evolve_check_access(filepath, touched_functions=[...])` 验证有测试保护。

### 外部 API

7. **不凭记忆调 API** — 训练数据是 6-18 个月前的。调任何外部 API 前先用 `search_web` 查最新文档。

### 编码硬约束

8. **禁止裸 except** — 不允许 `except:` 或 `except Exception: pass`。指定异常类型，至少记日志。

9. **禁止魔法数字** — 数字常量用模块级命名常量（如 `TIMEOUT = 30`，不是 `timeout=30` 到处散落）。

10. **类型提示** — 所有公开函数必须有完整的参数和返回类型注解。

11. **函数 ≤ 50 行** — 超过则拆分。

12. **DRY** — 相同逻辑出现两次以上，提取为公共函数。

### 沟通

13. **不提工具名** — 对用户说"我修改了文件"，不说"我用 write_file 写了文件"。

14. **不知道就查** — 不编造 API 参数或路径。不确定时 `search_web` 或 `grep_code`。

15. **完成三问** — 每个任务结束前自问：测试通过？lint 干净？覆盖率没降？全满足再说"好了"。
