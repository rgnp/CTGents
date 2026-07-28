# 核心项目与个人工作区

CTGents 明确区分两种所有权：

| 区域 | 所有内容 | Git 策略 |
|---|---|---|
| 核心项目 | `src/`、`tests/`、`docs/`、内置 psyche/skills | 可公开发布 |
| 个人工作区 | `memory/`、`knowledge/`、`sessions/`、`tasks/`、`stats/` | 默认本地或私有 |

## 选择工作区

优先级：

1. 环境变量或 `.env` 中的 `CTG_WORKSPACE_DIR`
2. 源码检出目录里被 Git 忽略的 `.ctg-workspace` 指针文件
3. 默认 `~/.ctgents`

`.ctg-workspace` 只写一行绝对路径，例如：

```text
D:\CTGents-workspace
```

程序会自动使用以下虚拟路径：

```text
knowledge/...  → <workspace>/knowledge/...
memory/...     → <workspace>/memory/...
sessions/...   → <workspace>/sessions/...
tasks/...      → <workspace>/tasks/...
stats/...      → <workspace>/stats/...
```

其他相对路径仍属于当前代码项目。因此 Agent 可以在项目中编辑 `src/main.py`，同时把
`knowledge/paper/x.md` 写入个人工作区。

## 个人工作区版本控制

个人工作区可以建立独立私有 Git 仓库，但默认不应连接公开远程：

- `memory/`、知识笔记 Markdown 可按需进入私有版本控制；
- `sessions/`、`stats/`、回执 JSONL 和索引属于可再生或敏感运行数据，默认忽略；
- PDF 等大文件默认忽略，确需版本化时显式配置 Git LFS；
- 外部研究代码保留自己的 Git 仓库，放在被忽略的 `projects/` 下。

测试必须把所有运行时路径重定向到 pytest 临时目录，不得读取或污染真实个人工作区。
