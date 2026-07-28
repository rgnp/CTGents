# 心跳：无人期自主推进探索前沿

心跳让 agent 在你不在的时候自己向前做事（搜集论文、摸方向），而不是等你输入。
设计原则是**干活多、说话少**：工作落文件（knowledge/ 卡片 + frontier 断点），
你只在回到主会话时收到一条合并摘要。

## 一分钟上手

1. **种方向**：编辑 `tasks/frontier.md`，在「## 方向」下用任务清单种一个方向，例如：

   ```markdown
   ## 方向

   ### 世界模型闭环评测
   - [ ] 扫 arXiv 近一月 closed-loop evaluation + world model 论文，筛出最相关 3 篇
   - [ ] 逐篇精读，写卡片到 knowledge/（方法/证据/边界/与已有卡片的矛盾）
   - [ ] 汇总这条缝的现状：谁在做、缺口在哪
   ```

2. **手动试一跳**（不用装调度）：REPL 里 `/heartbeat run`，或命令行
   `python -m src.heartbeat --force`。

3. **装上调度**（Windows 计划任务，每 30 分钟）：
   `powershell scripts/install_heartbeat_task.ps1`；
   或者开个常驻窗口：`python -m src.heartbeat --loop 1800`。

4. **收摘要并处置**：下次打开 CTGents 说任何话，第一轮会自动注入「心跳汇报」合并摘要。
   核对产物后用 `/heartbeat accept [说明]`、`revise [说明]` 或 `reject [说明]` 记录交还结果。
   `/heartbeat` 随时看活跃项、运行次数、自暂停、待送摘要和待处置交还。

## 行为契约

- **无活跃项 → 静默零成本**：frontier「方向」区没有 `[ ]`/`[o]` 项时，心跳直接退出，
  不叫醒 LLM。
- **一跳一项**：每次只领第一个活跃项，请求预算 `CTG_HEARTBEAT_WORKER_MAX_REQUESTS`
  （默认 25），收尾把断点写回 frontier——跨跳的连续性全靠这个文件。
- **psyche 硬加载**：worker 前缀固定注入 research psyche（无人期没人喊 /psyche load，
  这一步是代码保证不是散文提醒）。
- **出处闸**：写进 knowledge/ 的产出过 delegate_gate 机械核查（URL grounding +
  [已核] 必须真 read_page 过）；没过打回重试一次，仍不过如实写进摘要，不静默放行。
- **候选不转正**：worker 发现的新方向只能写进「候选方向」区，由你挪进「方向」区才生效。
- **自暂停**：连续 2 跳（`CTG_HEARTBEAT_STALL_LIMIT`）没改动 frontier → 自暂停 +
  摘要说明；你改动 frontier.md 后自动恢复。
- **每日上限**：`CTG_HEARTBEAT_MAX_RUNS_PER_DAY`（默认 16）兜底成本。
- **无人期白名单**：worker 只有 搜索/读页/读文件/写文件/论文扫描与精读/rag/think/learn
  + skill 运行时（psyche_catalog/load_psyche/activate_skill）+ 论文窄工具
  （fetch_paper 下载、transcribe_paper 转写），没有 run_command / run_python / git /
  删除 / remember——无人期不动系统状态、不写记忆。
- **统一工作回执**：每跳只有“出处闸通过且 frontier 真推进”才记 completed；否则记 failed。
  本跳 Knowledge 产物保存 SHA-256，digest 送达后等待用户 accept/revise/reject，不自动给自己验收。

## 跑 paper-pipeline（论文入库流水线）

心跳 worker 带 skill 运行时，可以无人跑 `paper-pipeline`：worker 自己
`load_psyche(paper-collection)` → `activate_skill(paper-pipeline, stage=resume)`，
按 `knowledge/paper/_pipeline_state.md` 断点续跑。原先阶段 1/2 的 run_python 即兴
代码已沉淀为 `fetch_paper` / `transcribe_paper` 窄工具（有人会话同样用它们）；
阶段 4A（关联索引重建）需要 run_python，无人期跳过、记入状态留给有人会话。

frontier 里种一项即可，例如：

```markdown
### 论文库补货
- [ ] 用 paper-pipeline（stage=resume）按 _pipeline_state.md 断点推进论文入库，
      一跳做完一个阶段批次就收尾记断点
```

## 旋钮（.env）

| 变量 | 默认 | 说明 |
|------|------|------|
| `CTG_HEARTBEAT_ENABLED` | 1 | 总开关 |
| `CTG_HEARTBEAT_WORKER_MAX_REQUESTS` | 25 | 每跳请求预算 |
| `CTG_HEARTBEAT_RETRY_MAX_REQUESTS` | 12 | 出处闸打回后的重试预算 |
| `CTG_HEARTBEAT_PSYCHE` | research | 硬加载的领域 psyche |
| `CTG_HEARTBEAT_STALL_LIMIT` | 2 | 连续无推进多少跳自暂停 |
| `CTG_HEARTBEAT_MAX_RUNS_PER_DAY` | 16 | 每日次数上限 |
| `CTG_HEARTBEAT_WORKER_TOOLS` | （见 params.py） | 白名单工具，逗号分隔 |

## 文件

- `tasks/frontier.md` — 探索前沿（方向 + 断点 + 候选区），唯一的活来源
- `tasks/heartbeat/state.json` — 运行状态（今日次数/连续空转/暂停时间）
- `tasks/heartbeat/digest-pending.md` — 待送摘要（主会话消费后归档）
- `tasks/heartbeat/digest-archive.md` — 已送摘要存档
- `tasks/heartbeat/lock` — 防重叠锁（陈旧 2 小时自动抢占）

## 与既有机制的关系

- `/pulse` 是"检测本项目可改进方向"的静态扫描，心跳是"无人期推进科研探索"的
  执行环，两者无共享状态。
- `tasks/current.md` 是你在场时的长任务；`tasks/frontier.md` 是无人期的探索前沿。
  刻意分开：心跳不碰 current.md，避免和你在场的工作互相踩。
- worker 隔离与出处闸复用 delegate（tools/delegate.py、delegate_gate.py）的同一套
  机制与铁律。
