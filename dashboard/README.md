# CTGents 监控面板（只读 MVP）

与 agent 进程**完全解耦**的可观测层。它只读磁盘上的 artifact，不注入 agent 上下文、不碰前缀缓存、不参与 LLM 循环。

## 运行

```bash
python -m dashboard.server          # 默认 127.0.0.1:8765
python -m dashboard.server --port 9000
```

然后浏览器打开 `http://127.0.0.1:8765`。页面每 5 秒自动刷新。

**agent 随便重启**——本进程读盘上的文件，互不牵连（无共享生命周期）。起一次挂那儿即可。

## 看什么

| 面板 | 数据源 | 复用 / 读盘 |
|---|---|---|
| 前缀缓存命中率（#1 目标） | `stats/{sid}.json` | 直接读盘 |
| 门通行证审计（绕门会暴露） | git HEAD 树 vs 通行证 | 复用 `src.gate_audit.head_gate_notice` |
| 进化时间线 | `git log` | subprocess |
| 记忆 lessons（指纹/遭遇次数）| `memory/*.md` | 复用 `src.tools.memory._split_frontmatter` |
| 野心 ambitions | `tasks/ambitions.md` | 复用 `src.tasks.read_ambitions` |
| 改进方向 gaps | `.gap_cache.json` | 复用 `src.gaps._load_gap_cache`（不触发 5s 冷算）|
| 当前任务 + 进度 | `tasks/current.md` | 复用 `src.tasks` 读函数 |

## 设计约束（别破坏）

1. **只读优先**：MVP 不放任何触发按钮（跑进化 / `/fix`）。先做"观测"，控制后置——加按钮 = 给 agent 加新的可被改写/出错的面，违背"开发期保持精简"。
2. **进程解耦**：`agent 不 import、不启动本 server`；本 server 单向读盘，不往 agent 推任何东西。维持这条 = 重启零负担、缓存零影响。
3. **不拉入 LLM 栈**：缓存命中率刻意直接读 `stats/{sid}.json`，不 import `src.llm`（那会把 API 客户端栈拉进监控进程）。
4. **不重写解析**：非平凡解析（frontmatter / gap 缓存 / 门审计）复用 `src` 的读函数，不另起一套 = 不制造漂移。
5. **排除出进化 scope**：`dashboard/` 不应进入 agent 的自进化改写范围——你正用它看 agent，别让 agent 重写自己的仪表盘。
