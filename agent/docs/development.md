# 开发者指南

本文档面向需要扩展本项目的开发者，介绍如何添加新工具、新指令、新插件。

---

## 一、添加新工具

### 步骤 1：创建工具模块

在 `src/tools/` 下新建 `.py` 文件：

```python
"""模块描述。"""

TOOLS_MY = [
    {
        "type": "function",
        "function": {
            "name": "my_tool",
            "description": "工具描述（LLM 会读取这段来决定何时调用）",
            "parameters": {
                "type": "object",
                "properties": {
                    "param1": {
                        "type": "string",
                        "description": "参数说明",
                    },
                },
                "required": ["param1"],
            },
        },
    },
]


def execute(name: str, args: dict) -> str | None:
    if name == "my_tool":
        return _do_my_tool(args["param1"])
    return None


def _do_my_tool(param1: str) -> str:
    """核心逻辑。"""
    return f"处理结果: {param1}"
```

### 步骤 2：注册到系统

在 `src/tools/__init__.py` 的 `_BUILTIN_MODULES` 中添加：

```python
(".my_module", "TOOLS_MY", "execute"),
```

### 步骤 3：标注安全等级

在 `src/safety.py` 的 `TOOL_SAFETY` 字典中添加：

```python
"my_tool": SafetyLevel.RISKY,
```

参考：
- **SAFE**: 只读操作（search_web, read_file, git_status）
- **RISKY**: 写入但可逆（write_file, run_command, git_commit）
- **DANGEROUS**: 破坏性不可逆（git_push）

### 步骤 4：添加显示标签（可选）

在 `src/main.py` 的 `TOOL_LABELS` 中添加中文标签。

### 步骤 5：写测试

在 `tests/` 下添加测试文件。

---

## 二、添加新指令

### 使用装饰器

在 `src/commands.py` 中：

```python
@builtin("/hello", description="示例指令", usage="/hello <名称>")
def _cmd_hello(r: CmdResult, _msgs, args, _sid) -> None:
    name = " ".join(args) if args else "世界"
    r.message = f"你好，{name}！"
```

支持多别名：

```python
@builtin_multi(["/hi", "/hey"], description="打招呼")
```

### CmdResult 字段参考

| 字段 | 类型 | 说明 |
|------|------|------|
| `message` | str | 显示给用户的消息 |
| `exit` | bool | 是否退出程序 |
| `save` | bool | 是否保存会话 |
| `clear` | bool | 是否清除上下文 |
| `load` | str | 加载的会话 ID |
| `retry` | bool | 是否重试最后一条 |
| `delay` | float | 延迟秒数 |

---

## 三、添加新插件

### 下载/创建插件脚本

```python
# plugins/my_plugin.py

DESCRIPTION = "我的插件"

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "my_plugin_tool",
            "description": "...",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]

def execute(name: str, args: dict) -> str | None:
    if name == "my_plugin_tool":
        return "结果"
    return None
```

放到 `plugins/` 目录下，执行 `discover` 或重启即可自动加载。

---

## 四、测试规范

```bash
# 运行所有测试
pytest

# 运行单个文件
pytest tests/test_safety.py

# 运行单个测试类
pytest tests/test_safety.py::TestCheckTool

# 运行单个测试
pytest tests/test_safety.py::TestCheckTool::test_safe_tool_always_allowed

# 带覆盖率
pytest --cov --cov-report=term
```

已有 93 个测试用例，新代码请至少覆盖核心逻辑。

---

## 五、常用命令

```bash
make install     # 安装依赖
make test        # 运行测试
make lint        # 代码检查
make lint-fix    # 自动修复
make run         # 启动 Agent
make precommit   # 运行 pre-commit
make check       # 项目规范扫描
```

---

## 六、CI/CD

每次 push/PR 到 master 自动运行：
1. **Lint**: ruff check src/
2. **Tests**: pytest -v（93 用例）
3. **Spec**: check_project 评分（低于 80 分报错）

配置在 `.github/workflows/ci.yml`。
